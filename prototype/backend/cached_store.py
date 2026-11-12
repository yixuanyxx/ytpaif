"""Redis read cache; Supabase remains authoritative for all writes."""
import hashlib
import json
import time

import source_cache
from supabase_store import SupabaseError, SupabaseStore, configuration


class CachedStore(SupabaseStore):
    FRESH_SECONDS = 60

    def __init__(self):
        self._fallback = set()

    def _key(self, kind, query):
        digest = hashlib.sha256(json.dumps([configuration()[0], query], sort_keys=True).encode()).hexdigest()
        return source_cache._key(f'records:{kind}:{digest}')

    def _cached(self, kind, query):
        try:
            client = source_cache._connection()
            value = client.get(self._key(kind, query)) if client is not None else None
            return json.loads(value) if value else None
        except Exception:
            return None

    def _save(self, kind, query, records, at=None):
        try:
            client = source_cache._connection()
            if client is not None:
                # No expiry: a stale copy is useful during an extended outage.
                client.set(self._key(kind, query), json.dumps({
                    'at': time.time() if at is None else at, 'records': records,
                }, allow_nan=False))
        except Exception:
            pass  # A Redis outage must not break successful Supabase access.

    def _read(self, kind, query, load):
        cached = self._cached(kind, query)
        if cached is not None and time.time() - cached['at'] < self.FRESH_SECONDS:
            return cached['records']
        try:
            records = load()
        except SupabaseError as error:
            # Never mask permission/schema errors with previously accessible data.
            if not getattr(error, 'retryable', False):
                raise
            if cached is None:
                # A complete table snapshot can also satisfy a targeted read.
                complete = self._cached(kind, ['all'])
                if complete is not None and query[0] == 'ids':
                    cached = {'records': [r for r in complete['records'] if r['id'] in query[1]]}
                elif complete is not None and query[0] == 'company':
                    cached = {'records': [r for r in complete['records'] if r.get('company_id') == query[1]]}
            if cached is None:
                raise SupabaseError(
                    f'Supabase unavailable and no complete Redis copy exists for {kind}. '
                    'Restore Supabase access to load this data.', retryable=True,
                ) from error
            self._fallback.add(kind)
            return cached['records']
        self._fallback.discard(kind)
        self._save(kind, query, records)
        return records

    def all_records(self, kind):
        return self._read(kind, ['all'], lambda: super(CachedStore, self).all_records(kind))

    def company_records(self, kind, company_id):
        return self._read(kind, ['company', company_id],
                          lambda: super(CachedStore, self).company_records(kind, company_id))

    def find(self, kind, record_id):
        if not record_id:
            return None
        rows = self._read(kind, ['ids', [record_id]],
                          lambda: [r] if (r := super(CachedStore, self).find(kind, record_id)) else [])
        return rows[0] if rows else None

    def find_many(self, kind, record_ids):
        ids = sorted({str(rid) for rid in record_ids or [] if rid})
        if not ids:
            return []
        return self._read(kind, ['ids', ids],
                          lambda: super(CachedStore, self).find_many(kind, ids))

    def _changed(self, kind, records=(), deleted=None):
        complete = self._cached(kind, ['all'])
        try:
            client = source_cache._connection()
            if client is not None:
                keys = list(client.scan_iter(match=source_cache._key(f'records:{kind}:*')))
                if keys:
                    client.delete(*keys)
        except Exception:
            pass
        if complete is not None:
            changed = {r['id']: r for r in records}
            rows = [changed.pop(r['id'], r) for r in complete['records'] if r['id'] != deleted]
            rows.extend(changed.values())
            # Preserve snapshot age: a write does not refresh unrelated records.
            self._save(kind, ['all'], rows, at=complete['at'])

    def put(self, kind, record):
        saved = super().put(kind, record)
        self._changed(kind, [saved])
        return saved

    def put_many(self, kind, records):
        saved = super().put_many(kind, records)
        if saved:
            self._changed(kind, saved)
        return saved

    def delete(self, kind, record_id):
        super().delete(kind, record_id)
        self._changed(kind, deleted=record_id)

    def status(self):
        return {**super().status(), 'cached_fallback': bool(self._fallback),
                'stale_kinds': sorted(self._fallback)}
