"""Verify production Redis durability without printing application records."""
import hashlib
import json
import sys
import time
from pathlib import Path

import app  # Load the deployed backend environment.
import source_cache
from cached_store import CachedStore
from supabase_store import SupabaseStore


def fail(message):
    raise SystemExit(message)


def snapshot():
    client = source_cache._connection()
    if client is None or type(client).__module__.startswith('fakeredis'):
        fail('Shared Redis is unavailable')
    try:
        if client.config_get('appendonly').get('appendonly') != 'yes':
            fail('Redis AOF is not enabled')
        if client.config_get('maxmemory-policy').get('maxmemory-policy') != 'noeviction':
            fail('Redis maxmemory-policy is not noeviction')
        if not client.info('persistence').get('aof_enabled'):
            fail('Redis reports AOF as inactive')
    except SystemExit:
        raise
    except Exception as error:
        fail(f'Could not verify Redis persistence: {type(error).__name__}')

    store = CachedStore()
    result = {'captured_at': time.time(), 'datasets': {}, 'source_index_count': 0}
    records_by_kind = {}
    for kind in SupabaseStore._SPECIAL_TABLES:
        key = store._key(kind, ['all'])
        raw = client.get(key)  # Deliberately bypass disk recovery for this check.
        if raw is None:
            fail(f'Missing Redis application dataset: {kind}')
        if client.ttl(key) != -1:
            fail(f'Redis application dataset has an expiry: {kind}')
        try:
            rows = json.loads(raw)['records']
            ids = [row['id'] for row in rows]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            fail(f'Invalid Redis application dataset: {kind}')
        if len(ids) != len(set(ids)):
            fail(f'Duplicate record IDs in Redis dataset: {kind}')
        records_by_kind[kind] = rows
        result['datasets'][kind] = {
            'count': len(rows),
            'id_digest': hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest(),
        }

    ids = {kind: {row['id'] for row in rows} for kind, rows in records_by_kind.items()}
    for snapshot_record in records_by_kind['snapshots']:
        for holding in snapshot_record.get('holdings', []):
            if holding.get('company_id') not in ids['companies']:
                fail('Snapshot references a missing company')
    for case in records_by_kind['cases']:
        if case.get('company_id') and case['company_id'] not in ids['companies']:
            fail('Analysis case references a missing company')
        if case.get('theme_id') and case['theme_id'] not in ids['themes']:
            fail('Analysis case references a missing theme')
    for review in records_by_kind['reviews']:
        if review.get('case_id') and review['case_id'] not in ids['cases']:
            fail('Review references a missing analysis case')

    result['source_index_count'] = client.scard(source_cache._key('ids'))
    if result['source_index_count'] != result['datasets']['sources']['count']:
        fail('Redis source index does not match the source record snapshot')
    return result


def write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


if len(sys.argv) < 2 or sys.argv[1] not in ('capture', 'compare'):
    fail('Usage: production_redis_check.py capture|compare PATH [AFTER_PATH]')

if sys.argv[1] == 'capture':
    if len(sys.argv) != 3:
        fail('Usage: production_redis_check.py capture PATH')
    current = snapshot()
    write(sys.argv[2], current)
    print(json.dumps({
        'datasets': {kind: value['count'] for kind, value in current['datasets'].items()},
        'source_index_count': current['source_index_count'],
    }, sort_keys=True))
else:
    if len(sys.argv) != 4:
        fail('Usage: production_redis_check.py compare BEFORE_PATH AFTER_PATH')
    before = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    after = snapshot()
    write(sys.argv[3], after)
    before.pop('captured_at', None)
    after.pop('captured_at', None)
    if before != after:
        fail('Redis application records changed across restart')
    print(json.dumps({
        'datasets': {kind: value['count'] for kind, value in after['datasets'].items()},
        'source_index_count': after['source_index_count'],
    }, sort_keys=True))
