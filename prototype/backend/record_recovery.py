"""Authenticated recovery of application payloads exported from the SQL Editor."""
import base64
import csv
import io
import json
import os
from pathlib import Path
import time
import uuid


def directory():
    return Path(os.environ.get('RECORD_RECOVERY_DIR', str(Path(__file__).parent / 'data' / 'record-recovery')))


def parse_export(text, allowed):
    csv.field_size_limit(64 * 1024 * 1024)
    datasets = {}
    for row in csv.DictReader(io.StringIO(text.lstrip('\ufeff'))):
        kind = row.get('kind')
        if kind not in allowed or kind in datasets:
            raise ValueError('Unknown or duplicate recovery dataset')
        records = json.loads(base64.b64decode(row['records_base64']).decode('utf-8'))
        if not isinstance(records, list) or len(records) != int(row['record_count']):
            raise ValueError('Export record count does not match; recovery cancelled')
        ids = set()
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id']:
                raise ValueError('Export contains an invalid record')
            if record['id'] in ids:
                raise ValueError('Export contains duplicate record IDs')
            ids.add(record['id'])
        datasets[kind] = records
    if not datasets:
        raise ValueError('No recovery datasets found')
    return datasets


def save_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
            json.dump(value, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(store, text):
    datasets = parse_export(text, store._SPECIAL_TABLES)
    client = store._client()
    if type(client).__module__.startswith('fakeredis'):
        raise ValueError('Recovery requires the shared production Redis server')
    # Save the unmodified export before touching Redis. Never upload data to git.
    archive = directory() / ('export-' + str(time.time_ns()) + '.json')
    save_file(archive, datasets)
    staged = {}
    completed_stages = {}
    metadata = {r['kind']: r for r in csv.DictReader(io.StringIO(text.lstrip('\ufeff')))}
    for kind in list(datasets):
        total = int(metadata[kind].get('total_count') or len(datasets[kind]))
        if total < len(datasets[kind]):
            raise ValueError('Invalid total export count')
        if total > len(datasets[kind]):
            with store._locked(client, kind):
                path = directory() / ('stage-' + kind + '.json')
                stage = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'total': total, 'rows': {}}
                if stage['total'] != total:
                    raise ValueError('Recovery batch belongs to a different export')
                for record in datasets[kind]:
                    existing = stage['rows'].get(record['id'])
                    if existing is not None and existing != record:
                        raise ValueError('Conflicting recovery batches')
                    stage['rows'][record['id']] = record
                if len(stage['rows']) > total:
                    raise ValueError('Recovery batch exceeds expected total')
                save_file(path, stage)
                staged[kind] = {
                    'received': len(stage['rows']), 'expected': total,
                    'committed': bool(stage.get('committed')),
                }
                if stage.get('committed'):
                    # A browser retry may replay every row after the complete
                    # dataset was already committed. Validate the replay above,
                    # but do not rewrite and re-index the full library per row.
                    del datasets[kind]
                    continue
                if len(stage['rows']) < total:
                    del datasets[kind]
                else:
                    datasets[kind] = list(stage['rows'].values())
                    completed_stages[kind] = (path, stage)
    counts = {}
    for kind, exported in datasets.items():
        with store._locked(client, kind):
            current = store._cached(kind, ['all'])
            if current is not None:
                save_file(directory() / ('before-' + str(time.time_ns()) + '-' + kind + '.json'), current)
            rows = {r['id']: r for r in exported}
            # Existing primary records and pending edits take precedence over the backup.
            rows.update({r['id']: r for r in (current or {}).get('records', [])})
            for key in client.scan_iter(match=store._key(kind, ['pending-prefix']) + ':*'):
                action = json.loads(client.get(key))
                if action['record'] is None:
                    rows.pop(action['id'], None)
                else:
                    rows[action['id']] = action['record']
            records = list(rows.values())
            snapshot = {'at': time.time(), 'records': records}
            save_file(directory() / (kind + '.json'), snapshot)
            store._save(kind, ['all'], records)
            store._fallback.discard(kind)
            counts[kind] = len(records)
            if kind in completed_stages:
                path, stage = completed_stages[kind]
                stage['committed'] = True
                save_file(path, stage)
                staged[kind]['committed'] = True
    persistence = {'disk_recovery_copy': True}
    try:
        if client.config_get('appendonly').get('appendonly') != 'yes':
            client.config_set('appendonly', 'yes')
        client.config_set('maxmemory-policy', 'noeviction')
        persistence['aof_enabled'] = client.config_get('appendonly').get('appendonly') == 'yes'
        client.config_rewrite()
        persistence['configuration_saved'] = True
    except Exception:
        persistence['configuration_saved'] = False
    return {'counts': counts, 'staged': staged, 'persistence': persistence}
