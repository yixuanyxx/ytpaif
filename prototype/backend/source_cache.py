"""Redis primary indexes for source-link metadata.

Supabase is the asynchronous backup for application records.
Uploaded binary documents remain separate objects in Supabase Storage.

Flow:
1. Restore a cold Redis source index from the durable Redis record snapshot.
2. Save all new/updated source metadata to Redis without expiry.
3. Retry pending Supabase backups during normal activity and logout.
"""
import json
import hashlib
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urldefrag

FRESH_FOR_SECONDS = 3600  # Stays active for session
_client = None

# Named background tasks currently in flight, guarded by _background_lock so
# a request thread never starts a second overlapping hydration/backup run.
# This is an in-process guard: the deployed app is a single Python process
# (ThreadingHTTPServer), so it is sufficient here; a multi-process deployment
# would need a distributed (Redis-backed) lock instead.
_background_lock = threading.Lock()
_background_tasks_running = set()


def run_once_in_background(name, fn):
    """Run `fn` in a daemon thread unless a task named `name` is already
    running, in which case this is a no-op. Always returns immediately --
    callers must never block on datastore work from the request thread.

    Returns True once a run is in flight (just started, or already running
    elsewhere). A failed run clears its own name on exit, so the very next
    call retries it.
    """
    with _background_lock:
        if name in _background_tasks_running:
            return True
        _background_tasks_running.add(name)

    def _run():
        try:
            fn()
        except Exception as e:
            print(f"Background task '{name}' failed: {e}", flush=True)
        finally:
            with _background_lock:
                _background_tasks_running.discard(name)

    threading.Thread(target=_run, daemon=True, name=f'bg-{name}').start()
    return True

_SOURCE_FIELDS = (
    'id', 'company_id', 'company_name', 'bloomberg_ticker', 'sedol',
    'source_type', 'url', 'discovery_url', 'publication_date', 'period',
    'cutoff', 'title', 'filename', 'extension', 'registry', 'short_registry',
    'retrieved_at', 'link_status', 'storage_mode', 'link_verified_at',
    'content_type', 'sha256', 'storage_path', 'extraction_status',
    'financial_facts', 'ai_confidence', 'ai_confidence_reason', 'ai_confidence_fallback', 'ai_confidence_basis',
)


def _key(name):
    prefix = os.environ.get("REDIS_PREFIX", "ytpaif")
    return f'{prefix}:source-cache:{name}'


def _summaries(sources):
    """Build compact source-library summaries without retaining document text."""
    counts = {}
    coverage = {}
    financial_sources = {}
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    for source in sources:
        company_id = source.get('company_id')
        source_type = source.get('source_type')
        if not company_id or not source_type:
            continue
        by_type = counts.setdefault(company_id, {})
        by_type[source_type] = by_type.get(source_type, 0) + 1
        facts = [
            {**fact, 'publication_date': fact.get('publication_date') or source.get('publication_date', '')}
            for fact in (source.get('financial_facts') or [])
            if fact.get('metric') in ('market_cap', 'revenue') and fact.get('display')
        ]
        if facts:
            financial_sources.setdefault(company_id, []).append({'id': source.get('id', ''), 'financial_facts': facts})
        if source.get('link_status') == 'verified' and source.get('url') and source.get('publication_date', '9999') <= today:
            year = str(source.get('period', ''))
            identity = hashlib.sha256(urldefrag(source['url'])[0].rstrip('/').encode()).hexdigest()
            coverage.setdefault(company_id, {}).setdefault(source_type, {}).setdefault(year, set()).add((identity, source.get('publication_date', '9999')))
    return [
        {'company_id': company_id, 'types': types, 'count': sum(types.values()),
         'verified': {kind: {year: sorted(urls) for year, urls in years.items()}
                      for kind, years in coverage.get(company_id, {}).items()},
         'financial_sources': financial_sources.get(company_id, [])}
        for company_id, types in counts.items()
    ]


def cached_source_summaries():
    """Return the precomputed summary snapshot, never loading every source."""
    client = _connection()
    if not client:
        return None
    try:
        value = client.get(_key('summaries'))
        return json.loads(value) if value else None
    except Exception:
        return None


def _record(source):
    """Normalize source record: document evidence and files stay in Supabase."""
    record = {}
    for field in _SOURCE_FIELDS:
        if field in source and source[field] is not None:
            record[field] = source[field]
    for field in ('id', 'company_id', 'source_type', 'company_name', 'url', 'publication_date', 'period'):
        if field in source and field not in record:
            record[field] = source[field]
    return record


def _connection():
    global _client
    if _client is not None:
        return _client
    url = os.environ.get('REDIS_URL', '').strip()
    if url:
        try:
            import redis
            client = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
                health_check_interval=30,
            )
            client.ping()
            _client = client
            return client
        except Exception:
            pass

    # Try local default Redis port
    try:
        import redis
        client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True,
                             socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        _client = client
        return client
    except Exception:
        pass

    # Embedded / in-memory Redis fallback using fakeredis
    try:
        import fakeredis
        _client = fakeredis.FakeRedis(decode_responses=True)
        return _client
    except Exception:
        return None


def is_hydrated():
    client = _connection()
    if not client:
        return False
    try:
        val = client.get(_key('hydrated-at'))
        return val is not None and len(str(val)) > 0
    except Exception:
        return False


def copy_supabase_to_redis(sources, force=False):
    """Copy all sources from Supabase database to Redis database on app start/open."""
    client = _connection()
    if not client:
        return False
    try:
        if not force and is_hydrated():
            return True

        previous_ids = list(client.smembers(_key('ids')))
        previous_index_keys = list(client.scan_iter(match=_key('company:*')))
        previous_ticker_keys = list(client.scan_iter(match=_key('ticker:*')))

        pipe = client.pipeline(transaction=True)
        if previous_ids:
            pipe.delete(*[_key(f'source:{source_id}') for source_id in previous_ids])
        pipe.delete(_key('ids'))
        pipe.delete(_key('pending_sync_ids'))
        if previous_index_keys:
            pipe.delete(*previous_index_keys)
        if previous_ticker_keys:
            pipe.delete(*previous_ticker_keys)

        for source in sources:
            record = _record(source)
            source_id = record.get('id')
            if not source_id:
                continue
            pipe.set(_key(f'source:{source_id}'), json.dumps(record, separators=(',', ':')))
            pipe.sadd(_key('ids'), source_id)
            company_id = record.get('company_id')
            source_type = record.get('source_type')
            ticker = record.get('bloomberg_ticker', '').strip().upper()
            if company_id:
                pipe.sadd(_key(f'company:{company_id}'), source_id)
                if source_type:
                    pipe.sadd(_key(f'company:{company_id}:{source_type}'), source_id)
            if ticker:
                pipe.sadd(_key(f'ticker:{ticker}'), source_id)
                base_t = ticker.replace(' EQUITY', '').strip()
                if base_t and base_t != ticker:
                    pipe.sadd(_key(f'ticker:{base_t}'), source_id)

        pipe.set(_key('summaries'), json.dumps(_summaries(sources), separators=(',', ':')))
        pipe.set(_key('hydrated-at'), str(time.time()))
        pipe.execute()
        return True
    except Exception as e:
        print(f"Error copying Supabase to Redis: {e}", flush=True)
        return False


def save_sources_to_redis(sources, mark_pending=True):
    """Save/update sources in Redis temporary database and mark for sync."""
    client = _connection()
    if not client:
        return False
    try:
        pipe = client.pipeline(transaction=True)
        # A batch update can alter counts, coverage, and financial facts. Do
        # not rebuild the complete library in the request that saved it.
        pipe.delete(_key('summaries'))
        for source in sources:
            record = _record(source)
            source_id = record.get('id')
            if not source_id:
                continue
            pipe.set(_key(f'source:{source_id}'), json.dumps(record, separators=(',', ':')))
            pipe.sadd(_key('ids'), source_id)
            if mark_pending:
                pipe.sadd(_key('pending_sync_ids'), source_id)
            company_id = record.get('company_id')
            source_type = record.get('source_type')
            ticker = record.get('bloomberg_ticker', '').strip().upper()
            if company_id:
                pipe.sadd(_key(f'company:{company_id}'), source_id)
                if source_type:
                    pipe.sadd(_key(f'company:{company_id}:{source_type}'), source_id)
            if ticker:
                pipe.sadd(_key(f'ticker:{ticker}'), source_id)
                base_t = ticker.replace(' EQUITY', '').strip()
                if base_t and base_t != ticker:
                    pipe.sadd(_key(f'ticker:{base_t}'), source_id)
        pipe.execute()
        return True
    except Exception as e:
        print(f"Error saving sources to Redis: {e}", flush=True)
        return False


def get_all_sources_from_redis():
    """Retrieve all source documents currently stored in Redis."""
    client = _connection()
    if not client:
        return None
    try:
        ids = sorted(client.smembers(_key('ids')))
        if not ids:
            return []
        values = client.mget([_key(f'source:{source_id}') for source_id in ids])
        return [json.loads(val) for val in values if val]
    except Exception as e:
        print(f"Error reading sources from Redis: {e}", flush=True)
        return None


def get_source(source_id):
    """Retrieve one source record from the active Redis session."""
    client = _connection()
    if not client or not source_id:
        return None
    try:
        value = client.get(_key(f'source:{source_id}'))
        return json.loads(value) if value else None
    except Exception:
        return None


def delete_source(source_id):
    """Remove a source from the active Redis session and pending sync queue."""
    client = _connection()
    if not client or not source_id:
        return False
    try:
        record = get_source(source_id)
        if not record:
            return False
        pipe = client.pipeline(transaction=True)
        pipe.delete(_key('summaries'))
        pipe.delete(_key(f'source:{source_id}'))
        pipe.srem(_key('ids'), source_id)
        pipe.srem(_key('pending_sync_ids'), source_id)
        company_id = record.get('company_id')
        source_type = record.get('source_type')
        ticker = record.get('bloomberg_ticker', '').strip().upper()
        if company_id:
            pipe.srem(_key(f'company:{company_id}'), source_id)
            if source_type:
                pipe.srem(_key(f'company:{company_id}:{source_type}'), source_id)
        if ticker:
            pipe.srem(_key(f'ticker:{ticker}'), source_id)
            base_ticker = ticker.replace(' EQUITY', '').strip()
            if base_ticker and base_ticker != ticker:
                pipe.srem(_key(f'ticker:{base_ticker}'), source_id)
        pipe.execute()
        return True
    except Exception as e:
        print(f"Error deleting Redis source: {e}", flush=True)
        return False


def get_sources_for_stock(company_id=None, ticker=None):
    """Retrieve source documents for a specific stock by company_id or ticker from Redis."""
    client = _connection()
    if not client:
        return []
    try:
        source_ids = set()
        if company_id:
            source_ids.update(client.smembers(_key(f'company:{company_id}')))
        if ticker:
            t_upper = ticker.strip().upper()
            source_ids.update(client.smembers(_key(f'ticker:{t_upper}')))
            t_base = t_upper.replace(' EQUITY', '').strip()
            if t_base != t_upper:
                source_ids.update(client.smembers(_key(f'ticker:{t_base}')))
            else:
                source_ids.update(client.smembers(_key(f'ticker:{t_base} EQUITY')))
        if not source_ids:
            return []
        values = client.mget([_key(f'source:{sid}') for sid in sorted(source_ids)])
        return [json.loads(val) for val in values if val]
    except Exception:
        return []


def get_source_summaries():
    """Return compact per-company summaries for the workspace landing view.

    Financial facts are included without document text so the source-library
    table can show market data before its expandable document rows are loaded.
    """
    cached = cached_source_summaries()
    if cached is not None:
        return cached
    sources = get_all_sources_from_redis()
    return _summaries(sources) if sources is not None else []


def start_background_summary_refresh():
    """Rebuild a missing summary snapshot without delaying workspace startup."""
    def _refresh():
        sources = get_all_sources_from_redis()
        client = _connection()
        if sources is not None and client:
            client.set(_key('summaries'), json.dumps(_summaries(sources), separators=(',', ':')))
    return run_once_in_background('source-summary-refresh', _refresh)


def copy_redis_to_supabase(store, force_all=False, raise_errors=False):
    """Retry queued Redis source-metadata backups to Supabase."""
    client = _connection()
    if not client:
        return 0
    try:
        pending_ids = list(client.smembers(_key('pending_sync_ids')))
        if not pending_ids:
            if not force_all:
                return 0
            all_ids = list(client.smembers(_key('ids')))
            if not all_ids:
                return 0
            pending_ids = all_ids

        values = client.mget([_key(f'source:{sid}') for sid in pending_ids])
        records = [json.loads(val) for val in values if val]
        if not records:
            return 0

        # Upsert in batches of 200 to Supabase
        batch_size = 200
        saved_count = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            store.put_many('sources', batch)
            saved_count += len(batch)

        # Clear pending sync set after successful persistence
        client.delete(_key('pending_sync_ids'))
        return saved_count
    except Exception as e:
        print(f"Error copying Redis to Supabase: {e}", flush=True)
        if raise_errors:
            raise
        return 0


def start_background_hydration(load_sources, on_success=None):
    """Start at most one Supabase -> Redis source hydration in the background.

    `load_sources` must raise (rather than return a degraded empty result) if
    the underlying read could not get real data, so a Supabase outage never
    gets mistaken for "there are no sources" and marked hydrated. On success,
    `on_success` runs after the Redis copy completes (e.g. to advance the
    worker-update cursor), matching the previous synchronous behaviour.

    Returns True once a hydration is in flight (just started, or already
    running); never blocks on the Supabase/Redis work itself.
    """
    def _hydrate():
        sources = load_sources()
        if not copy_supabase_to_redis(sources, force=True):
            return  # copy_supabase_to_redis already logged the failure.
        if on_success:
            on_success()

    return run_once_in_background('source-hydration', _hydrate)


def start_background_backup_retry(store):
    """Start at most one Redis -> Supabase backup retry in the background.

    Used by both the periodic `/api/sync` beacon and `/api/logout`, neither
    of which may block their request on a slow or unavailable Supabase.
    """
    def _retry():
        copy_redis_to_supabase(store)
        if hasattr(store, 'sync_backup'):
            store.sync_backup()

    return run_once_in_background('backup-retry', _retry)


# Backwards compatibility helpers
def warm_sources(sources, force=False):
    return copy_supabase_to_redis(sources, force=force)


def cached_sources():
    return get_all_sources_from_redis()


def load_or_warm(load_sources):
    cached = get_all_sources_from_redis()
    if cached is not None and is_hydrated():
        return cached
    sources = load_sources()
    copy_supabase_to_redis(sources, force=True)
    return sources


def upsert_sources(sources):
    return save_sources_to_redis(sources)
