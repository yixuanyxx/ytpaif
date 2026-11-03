"""Redis temporary data storage for source-link metadata.

Supabase is the permanent data storage.
Redis is the temporary data storage during the active user session.

Flow:
1. When PM opens the app -> copy Supabase database over to Redis database.
2. During the session -> save all new/updated sources to Redis.
3. When PM logs out or closes browser tab -> copy Redis database to Supabase.
"""
import json
import os
import time

FRESH_FOR_SECONDS = 3600  # Stays active for session
_client = None

_SOURCE_FIELDS = (
    'id', 'company_id', 'company_name', 'bloomberg_ticker', 'sedol',
    'source_type', 'url', 'discovery_url', 'publication_date', 'period',
    'cutoff', 'title', 'filename', 'extension', 'registry', 'short_registry',
    'retrieved_at', 'link_status', 'storage_mode', 'link_verified_at',
    'content_type', 'sha256', 'storage_path', 'extraction_status',
    'financial_facts',
)


def _key(name):
    prefix = os.environ.get("REDIS_PREFIX", "ytpaif")
    return f'{prefix}:source-cache:{name}'


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

        pipe.set(_key('hydrated-at'), str(time.time()))
        pipe.execute()
        return True
    except Exception as e:
        print(f"Error copying Supabase to Redis: {e}", flush=True)
        return False


def save_sources_to_redis(sources):
    """Save/update sources in Redis temporary database and mark for sync."""
    client = _connection()
    if not client:
        return False
    try:
        pipe = client.pipeline(transaction=True)
        for source in sources:
            record = _record(source)
            source_id = record.get('id')
            if not source_id:
                continue
            pipe.set(_key(f'source:{source_id}'), json.dumps(record, separators=(',', ':')))
            pipe.sadd(_key('ids'), source_id)
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


def copy_redis_to_supabase(store, force_all=False):
    """Copy all sources from Redis temporary storage to Supabase permanent storage.
    
    Triggered when PM logs out or closes browser tab.
    """
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
        return 0


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
