"""Durable local audit outbox; optional server-only Supabase replication."""
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

request_id = ContextVar('audit_request_id', default=None)
view_name = ContextVar('audit_view_name', default=None)
VIEWS = {'overview', 'theme_definition', 'portfolio_import', 'source_documents', 'analysis_review', 'evaluate'}
VIEW_ALIASES = {'theme': 'theme_definition', 'portfolio': 'portfolio_import',
                'sources': 'source_documents', 'analysis': 'analysis_review', 'review': 'analysis_review'}


def resolve_view(path='', record_kind=None, requested=None):
    requested = VIEW_ALIASES.get(requested, requested)
    if path == '/api/dashboard' or requested == 'dashboard':
        return 'dashboard'
    if requested in VIEWS:
        return requested
    endpoint = path.removeprefix('/api/')
    if endpoint in ('theme', 'theme/delete', 'generate'):
        return 'theme_definition'
    if endpoint in ('company', 'portfolio/import', 'portfolio_import', 'snapshot'):
        return 'portfolio_import'
    if endpoint in ('search',) or endpoint.startswith('source'):
        return 'source_documents'
    if endpoint == 'evaluate':
        return 'evaluate'
    if endpoint in ('analyze', 'analyze_all', 'case', 'case/check', 'check', 'review'):
        return 'analysis_review'
    return {'themes': 'theme_definition', 'companies': 'portfolio_import',
            'portfolio_imports': 'portfolio_import', 'snapshots': 'portfolio_import',
            'sources': 'source_documents', 'search_runs': 'source_documents',
            'cases': 'analysis_review', 'reviews': 'analysis_review', 'runs': 'analysis_review',
            'model_calls': 'analysis_review', 'provider_outputs': 'analysis_review',
            'model_outputs': 'analysis_review'}.get(record_kind, 'overview')
_SYNC_LOCK = threading.Lock()


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS audit_outbox (
        event_id TEXT PRIMARY KEY, body TEXT NOT NULL,
        synced_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)''')


def sanitize(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = key.lower()
            if any(part in normalized for part in ('api_key', 'secret', 'password', 'authorization', 'access_token', 'refresh_token')):
                result[key] = '[REDACTED]'
            elif key == 'content' and 'filename' in value and isinstance(item, str):
                # Upload base64 is not copied to the cloud; extracted passages are.
                result[key] = {'omitted': 'file content', 'sha256': hashlib.sha256(item.encode()).hexdigest()}
            else:
                result[key] = sanitize(item)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return value


def enqueue(conn, event_type, payload, record_kind=None, record_id=None, event_id=None):
    view = resolve_view(payload.get('path', ''), record_kind, view_name.get())
    if view == 'dashboard':
        return None
    body = {'view_name': view, 'event_id': event_id or str(uuid.uuid4()),
            'occurred_at': datetime.now(timezone.utc).isoformat(),
            'request_id': request_id.get(), 'event_type': event_type,
            'record_kind': record_kind, 'record_id': record_id,
            'payload': sanitize(payload)}
    conn.execute('INSERT INTO audit_outbox (event_id, body) VALUES (?, ?)',
                 (body['event_id'], json.dumps(body, allow_nan=False)))
    return body['event_id']


def configuration():
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SECRET_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    return url, key


def status(conn):
    url, key = configuration()
    pending, synced = conn.execute('SELECT sum(synced_at IS NULL), sum(synced_at IS NOT NULL) FROM audit_outbox').fetchone()
    failed = conn.execute('SELECT count(*) FROM audit_outbox WHERE synced_at IS NULL AND last_error IS NOT NULL').fetchone()[0]
    return {'configured': bool(url and key), 'pending': pending or 0,
            'synced': synced or 0, 'failed': failed}


def sync_once(database, batch_size=20, request_id_filter=None):
    url, key = configuration()
    if not url or not key:
        return {'sent': 0, 'configured': False}
    if urlparse(url).scheme != 'https' or not urlparse(url).hostname:
        raise ValueError('SUPABASE_URL must be an HTTPS project URL')
    with _SYNC_LOCK, sqlite3.connect(database, timeout=30) as conn:
        initialize(conn)
        query = 'SELECT event_id, body FROM audit_outbox WHERE synced_at IS NULL'
        params = []
        if request_id_filter is not None:
            query += " AND json_extract(body, '$.request_id')=?"
            params.append(request_id_filter)
        query += ' ORDER BY attempts,rowid LIMIT ?'
        params.append(batch_size)
        rows = conn.execute(query, params).fetchall()
        if not rows:
            return {'sent': 0, 'configured': True}
        headers = {'apikey': key, 'Content-Type': 'application/json',
                   'Prefer': 'resolution=ignore-duplicates,return=minimal'}
        # New sb_secret keys are API keys, not JWT bearer tokens.
        if not key.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer ' + key
        groups = {}
        skipped = []
        for eid, raw in rows:
            body = json.loads(raw)
            view = resolve_view(body.get('payload', {}).get('path', ''),
                                body.get('record_kind'), body.get('view_name'))
            if view == 'dashboard':
                skipped.append(eid)
                continue
            body['view_name'] = view
            groups.setdefault(view, []).append((eid, body))
        sent = 0
        errors = []
        for view, events in groups.items():
            request = Request(url + '/rest/v1/' + view + '?on_conflict=event_id',
                              data=json.dumps([body for _, body in events]).encode(), headers=headers, method='POST')
            try:
                with urlopen(request, timeout=15) as response:
                    response.read()
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                # Never persist headers or response bodies containing credentials.
                reason = f'HTTP {error.code}' if isinstance(error, HTTPError) else type(error).__name__
                conn.executemany('UPDATE audit_outbox SET attempts=attempts+1,last_error=? WHERE event_id=?',
                                 [(reason, eid) for eid, _ in events])
                errors.append(reason)
                continue
            sent_at = datetime.now(timezone.utc).isoformat()
            conn.executemany('UPDATE audit_outbox SET synced_at=?,attempts=attempts+1,last_error=NULL WHERE event_id=?',
                             [(sent_at, eid) for eid, _ in events])
            sent += len(events)
        # Older dashboard events are intentionally retired locally, never uploaded.
        conn.executemany('UPDATE audit_outbox SET synced_at=?,last_error=NULL WHERE event_id=?',
                         [(datetime.now(timezone.utc).isoformat(), eid) for eid in skipped])
        result = {'sent': sent, 'configured': True, 'skipped': len(skipped)}
        if errors:
            result['error'] = '; '.join(sorted(set(errors)))
        return result



def start_worker(database):
    stop = threading.Event()
    def work():
        while not stop.is_set():
            try:
                sync_once(database)
            except Exception:
                # SQLite remains authoritative and queued events remain retryable.
                pass
            stop.wait(15)
    thread = threading.Thread(target=work, name='supabase-audit-sync', daemon=True)
    thread.start()
    return stop
