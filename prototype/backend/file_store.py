"""Persistent primary storage for uploaded document bytes.

Redis stores application records and file metadata, never the uploaded binary
content.  The VPS filesystem is therefore the primary home for new source and
portfolio files.  A Redis-backed outbox keeps Supabase Storage as a retriable
off-site backup without making an outage reject an upload.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

import source_cache
import supabase_store


ROOT = Path(__file__).resolve().parent
BUCKETS = {'source-documents', 'portfolio-files'}


def directory():
    """Return the persistent document directory, creating it with safe mode."""
    root = Path(os.environ.get('APPLICATION_FILE_DIR', ROOT / 'data' / 'files')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _path(bucket, storage_path):
    if bucket not in BUCKETS:
        raise ValueError('Unknown document storage bucket')
    parts = PurePosixPath(str(storage_path)).parts
    if not parts or PurePosixPath(str(storage_path)).is_absolute() or '..' in parts or '.' in parts:
        raise ValueError('Invalid document storage path')
    root = directory().resolve()
    target = (root / bucket / Path(*parts)).resolve()
    if root not in target.parents:
        raise ValueError('Invalid document storage path')
    return target


def _queue_key(bucket, storage_path):
    digest = hashlib.sha256(f'{bucket}\0{storage_path}'.encode()).hexdigest()
    return source_cache._key('file-backup:' + digest)


def save(bucket, storage_path, content, content_type):
    """Atomically persist bytes and queue their off-site backup in Redis."""
    if not isinstance(content, bytes) or not content:
        raise ValueError('Document content is required')
    target = _path(bucket, storage_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    client = source_cache._connection()
    if client is None:
        raise supabase_store.SupabaseError('Redis primary datastore is unavailable', retryable=True)
    payload = {
        'bucket': bucket, 'storage_path': storage_path, 'content_type': content_type,
        'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
    }
    client.set(_queue_key(bucket, storage_path), json.dumps(payload, separators=(',', ':')))
    return payload


def sync_pending():
    """Copy queued local files to Supabase, leaving failed items durable."""
    client = source_cache._connection()
    if client is None:
        return 0
    synced = 0
    try:
        keys = list(client.scan_iter(match=source_cache._key('file-backup:*')))
    except Exception:
        return 0
    for key in keys:
        try:
            encoded = client.get(key)
            if not encoded:
                continue
            item = json.loads(encoded)
            target = _path(item['bucket'], item['storage_path'])
            content = target.read_bytes()
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                continue
            if item['bucket'] == 'source-documents':
                supabase_store.upload_source(item['storage_path'], content, target.suffix.lower())
            else:
                supabase_store.upload_portfolio_file(item['storage_path'], content, item['content_type'])
            # Do not acknowledge an older upload if a newer write replaced it.
            if client.get(key) == encoded:
                client.delete(key)
                synced += 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError, supabase_store.SupabaseError):
            continue
        except Exception:
            continue
    return synced


def status():
    client = source_cache._connection()
    try:
        pending = len(list(client.scan_iter(match=source_cache._key('file-backup:*')))) if client else 0
    except Exception:
        pending = 0
    return {'primary': 'filesystem', 'directory': str(directory()), 'pending_backup': pending}
