"""Manual audit replication: python sync_audit.py [--backfill]."""
import argparse
import hashlib
import json
import uuid
import app
import audit_store


def backfill():
    """Queue idempotent current-record baselines; never invent historical events."""
    count = 0
    with app.db() as conn:
        for kind, rid, payload in conn.execute('SELECT kind,id,payload FROM records').fetchall():
            if kind == 'cache':
                continue
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'theme-lab-baseline:' + kind + ':' + rid + ':' + hashlib.sha256(payload.encode()).hexdigest()))
            if conn.execute('SELECT 1 FROM audit_outbox WHERE event_id=?', (event_id,)).fetchone():
                continue
            audit_store.enqueue(conn, 'record.baseline', {'current': json.loads(payload),
                                'note': 'Current record at audit setup; earlier edits were not captured.'}, kind, rid, event_id=event_id)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backfill', action='store_true', help='Also queue current SQLite records as baselines')
    parser.add_argument('--replay', action='store_true', help='Re-send all locally retained events to restore remote tables; existing IDs are ignored')
    args = parser.parse_args()
    if args.replay:
        with app.db() as conn:
            conn.execute('UPDATE audit_outbox SET synced_at=NULL,last_error=NULL')
    if args.backfill:
        print(f'Queued {backfill()} baseline events.')
    while True:
        result = audit_store.sync_once(app.DATA / 'research.sqlite')
        print(json.dumps(result))
        if result.get('error') or not (result.get('sent') or result.get('skipped')):
            break
    with app.db() as conn:
        print(json.dumps(audit_store.status(conn)))
    if result.get('error') or not result.get('configured'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
