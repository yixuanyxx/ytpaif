"""Redis primary records with a persistent, retryable Supabase backup outbox."""
import hashlib
import json
import threading
import time
from contextlib import contextmanager

import source_cache
from supabase_store import SupabaseError, SupabaseStore, configuration

_LOCAL_LOCK = threading.RLock()


class CachedStore(SupabaseStore):
    FRESH_SECONDS = 60  # Legacy cache compatibility; primary records do not expire.

    def __init__(self):
        self._fallback = set()
        self._backup_lock = threading.Lock()
        self._next_backup = 0

    def _key(self, kind, query):
        digest = hashlib.sha256(json.dumps([configuration()[0], query], sort_keys=True).encode()).hexdigest()
        return source_cache._key(f'records:{kind}:{digest}')

    def _client(self):
        client = source_cache._connection()
        if client is None:
            raise SupabaseError('Redis primary datastore is unavailable', retryable=True)
        return client

    @contextmanager
    def _locked(self, client, kind, blocking_timeout=10):
        # Shared Redis locks serialize hydration and mutations across workers.
        lock = (client.lock(self._key(kind, ['lock']), timeout=120, blocking_timeout=blocking_timeout)
                if hasattr(client, 'lock') else _LOCAL_LOCK)
        with lock:
            yield

    def _cached(self, kind, query):
        value = self._client().get(self._key(kind, query))
        if not value and query == ['all'] and kind in self._SPECIAL_TABLES:
            import record_recovery
            path = record_recovery.directory() / (kind + '.json')
            if path.is_file():
                snapshot = json.loads(path.read_text(encoding='utf-8'))
                self._save(kind, query, snapshot['records'], at=snapshot['at'])
                return snapshot
        return json.loads(value) if value else None

    def _save(self, kind, query, records, at=None):
        self._client().set(self._key(kind, query), json.dumps({
            'at': time.time() if at is None else at, 'records': records,
        }, allow_nan=False))

    def _overlay_pending(self, client, kind, complete):
        rows = {r['id']: r for r in complete['records']}
        for key in client.scan_iter(match=self._key(kind, ['pending-prefix']) + ':*'):
            raw_action = client.get(key)
            if not raw_action:
                continue
            action = json.loads(raw_action)
            if action['record'] is None:
                rows.pop(action['id'], None)
            else:
                rows[action['id']] = action['record']
        return rows

    def all_records(self, kind, deadline=None):
        self._table(kind)
        client = self._client()
        # Redis SET replaces the complete snapshot atomically. Once present it
        # is safe to read without the mutation/hydration lock; dashboard polls
        # must never queue behind a large source or model-output write.
        raw_snapshot = client.get(self._key(kind, ['all']))
        complete = json.loads(raw_snapshot) if raw_snapshot else None
        if complete is None:
            # A cold cache can require disk recovery or a Supabase hydration.
            # Only that exceptional path needs cross-process serialization.
            # `deadline` only applies to this fall-through Supabase read: once
            # warm (the common case, above), reads never touch Supabase at all.
            # The workspace route passes a short read deadline.  Never spend
            # longer than that waiting behind a hydration or mutation lock.
            # A request path must not wait behind a long background hydration.
            # The owner will atomically publish the completed snapshot; this
            # reader can return a retryable degraded result and try again.
            lock_wait = min(1, max(0.1, float(deadline))) if deadline is not None else 10
            with self._locked(client, kind, blocking_timeout=lock_wait):
                complete = self._cached(kind, ['all'])
                if complete is None:
                    try:
                        rows = super().all_records(kind, deadline=deadline)
                    except SupabaseError as error:
                        if not error.retryable:
                            raise
                        self._fallback.add(kind)
                        # A cold Redis cannot reconstruct historical backup records.
                        # Preserve local writes without claiming history is complete.
                        rows = []
                    else:
                        self._fallback.discard(kind)
                        self._save(kind, ['all'], rows)
                    complete = {'records': rows}
                if self._cached(kind, ['all']) is not None:
                    self._fallback.discard(kind)
                    # Persist the overlay before a successful backup clears
                    # pending actions; normal warm reads never rewrite it.
                    rows = self._overlay_pending(client, kind, complete)
                    self._save(kind, ['all'], list(rows.values()))
                    return list(rows.values())
                return list(self._overlay_pending(client, kind, complete).values())
        self._fallback.discard(kind)
        # `_mutate()` publishes the pending edit and its replacement snapshot
        # in one Redis transaction.  A warm snapshot therefore already
        # contains every local edit.  Scanning the outbox here used to turn
        # each workspace load into a remote SCAN for every table; with a large
        # source/analysis outbox that can hold `/api/state` long enough for the
        # browser to time out.  Keep the scan only on cold-cache recovery,
        # where it is required to merge edits made before a snapshot existed.
        return list(complete['records'])

    def company_records(self, kind, company_id):
        return [r for r in self.all_records(kind) if r.get('company_id') == company_id]

    def find(self, kind, record_id):
        if not record_id:
            return None
        action = self._cached_action(kind, record_id)
        if action is not None:
            return action['record']
        return next((r for r in self.all_records(kind) if r['id'] == record_id), None)

    def find_many(self, kind, record_ids):
        return [row for rid in dict.fromkeys(record_ids or []) if rid
                if (row := self.find(kind, rid)) is not None]

    def _pending_key(self, kind, record_id):
        return self._key(kind, ['pending-prefix']) + ':' + str(record_id)

    def _cached_action(self, kind, record_id):
        value = self._client().get(self._pending_key(kind, record_id))
        return json.loads(value) if value else None

    def _mutate(self, kind, records=(), deleted=None):
        self._table(kind)
        client = self._client()
        with self._locked(client, kind):
            snapshot = self._cached(kind, ['all'])
            rows = {r['id']: r for r in snapshot['records']} if snapshot else None
            pipe = client.pipeline(transaction=True)
            actions = [(r['id'], r) for r in records]
            if deleted is not None:
                actions.append((deleted, None))
            for rid, record in actions:
                pipe.set(self._pending_key(kind, rid), json.dumps({'id': rid, 'record': record}, allow_nan=False))
                if rows is not None:
                    if record is None:
                        rows.pop(rid, None)
                    else:
                        rows[rid] = record
            if rows is not None:
                pipe.set(self._key(kind, ['all']), json.dumps({'at': time.time(), 'records': list(rows.values())}, allow_nan=False))
            pipe.execute()
        self.request_backup()

    def put(self, kind, record):
        _, normalized = self._record_payload(kind, record)
        self._mutate(kind, [normalized])
        return normalized

    def put_many(self, kind, records):
        normalized = [self._record_payload(kind, r)[1] for r in records]
        if normalized:
            self._mutate(kind, normalized)
        return normalized

    def delete(self, kind, record_id):
        self._mutate(kind, deleted=record_id)

    def request_backup(self):
        if time.monotonic() < self._next_backup:
            return
        self._next_backup = time.monotonic() + 30
        threading.Thread(target=self.sync_backup, daemon=True, name='supabase-backup').start()

    def sync_backup(self):
        synced = 0
        if not self._backup_lock.acquire(blocking=False):
            return synced
        try:
            client = self._client()
            with self._locked(client, 'backup-worker'):
                for kind in self._SPECIAL_TABLES:
                    for key in client.scan_iter(match=self._key(kind, ['pending-prefix']) + ':*'):
                        value = client.get(key)
                        if not value:
                            continue
                        action = json.loads(value)
                        try:
                            if action['record'] is None:
                                super().delete(kind, action['id'])
                            else:
                                super().put(kind, action['record'])
                        except SupabaseError:
                            return synced
                        with self._locked(client, kind):
                            # A concurrent edit must remain queued, even if an older backup finished.
                            if client.get(key) != value or self._cached(kind, ['all']) is None:
                                continue
                            client.delete(key)
                            synced += 1
            # Binary documents are intentionally outside Redis. Their local
            # filesystem primary and Redis outbox use this same retry cadence.
            try:
                import file_store
                synced += file_store.sync_pending()
            except Exception:
                pass
            return synced
        except Exception:
            return synced  # Redis/network errors leave the persistent outbox intact.
        finally:
            self._backup_lock.release()

    def status(self):
        client = self._client()
        pending = sum(len(list(client.scan_iter(match=self._key(kind, ['pending-prefix']) + ':*')))
                      for kind in self._SPECIAL_TABLES)
        self.request_backup()
        return {**super().status(), 'primary': 'redis', 'pending': pending,
                'cached_fallback': bool(self._fallback) or bool(pending),
                'stale_kinds': sorted(self._fallback)}
