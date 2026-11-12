"""Server-side Supabase REST and Storage access."""
import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


class SupabaseError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def configuration():
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SECRET_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    return url, key


def request(path, method='GET', payload=None, headers=None, query=None, raw=None, timeout=None):
    url, key = configuration()
    if not url or not key:
        raise SupabaseError('Configure SUPABASE_URL and SUPABASE_SECRET_KEY in the backend environment')
    parsed_url = urlparse(url)
    if parsed_url.scheme != 'https' or not parsed_url.hostname or parsed_url.path not in ('', '/'):
        raise SupabaseError('SUPABASE_URL must use HTTPS')

    target = url + path
    if query:
        target += '?' + urlencode(query, doseq=True)
    request_headers = {'apikey': key, 'Accept': 'application/json'}
    if not key.startswith('sb_secret_'):
        request_headers['Authorization'] = 'Bearer ' + key
    if payload is not None:
        request_headers['Content-Type'] = 'application/json'
    request_headers.update(headers or {})
    body = raw if raw is not None else (
        json.dumps(payload, allow_nan=False).encode() if payload is not None else None
    )
    req = Request(target, data=body, headers=request_headers, method=method)
    # A brief connection interruption can occur during the dashboard's many
    # independent reads. Retry safe GETs only; repeating a write could create
    # duplicate external effects when its response was lost.
    # A caller with an overall deadline needs one bounded attempt; retrying a
    # request whose socket timeout already consumes the remaining budget would
    # defeat the user-facing deadline.
    attempts = 1 if timeout is not None else (3 if method.upper() == 'GET' else 1)
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=30 if timeout is None else max(0.1, timeout)) as response:
                data = response.read()
            break
        except HTTPError as error:
            try:
                error_payload = json.loads(error.read()) if error.fp else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                error_payload = {}
            if not isinstance(error_payload, dict):
                error_payload = {}
            error_code = error_payload.get('code')
            error_message = error_payload.get('message', '')
            if error_code == '42P01' and 'audit_events' in error_message:
                raise SupabaseError(
                    f'Supabase request failed: {method} {path} (HTTP {error.code}, {error_code}). '
                    'A stale database trigger still references public.audit_events; '
                    'remove the trigger function or trigger that writes to the deleted audit table.'
                ) from None
            suffix = f', {error_code}' if error_code else ''
            raise SupabaseError(
                f'Supabase request failed: {method} {path} (HTTP {error.code}{suffix})',
                retryable=error.code in (402, 408, 429) or error.code >= 500,
            ) from None
        except (URLError, TimeoutError, OSError) as error:
            if attempt + 1 < attempts:
                time.sleep(0.25 * 2 ** attempt)
                continue
            cause = getattr(error, 'reason', None)
            detail = type(cause).__name__ if cause is not None else type(error).__name__
            raise SupabaseError(
                f'Supabase request failed: {method} {path} ({type(error).__name__}: {detail})',
                retryable=True,
            ) from None
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def upload_source(storage_path, content, extension):
    path = '/storage/v1/object/source-documents/' + quote(storage_path, safe='/')
    request(path, method='POST', headers={
        # Keep original HTML bytes as plain text: the existing private bucket
        # accepts PDF/plain text and must not execute downloaded active content.
        'Content-Type': 'application/pdf' if extension == '.pdf' else 'text/plain',
        'x-upsert': 'true',
    }, raw=content)


def upload_portfolio_file(storage_path, content, content_type):
    path = '/storage/v1/object/portfolio-files/' + quote(storage_path, safe='/')
    request(path, method='POST', headers={
        'Content-Type': content_type,
        'x-upsert': 'true',
    }, raw=content)


def delete_portfolio_file(storage_path):
    request('/storage/v1/object/portfolio-files', method='DELETE',
            payload={'prefixes': [storage_path]})


class SupabaseStore:
    _SPECIAL_TABLES = {
        'themes': 'themes',
        'portfolio_imports': 'portfolios',
        'sources': 'source_documents',
        'companies': 'companies',
        'snapshots': 'portfolio_snapshots',
        'search_runs': 'source_search_runs',
        'runs': 'analysis_runs',
        'cases': 'analysis_cases',
        'reviews': 'analysis_reviews',
        'model_outputs': 'analysis_model_outputs',
        'model_calls': 'analysis_model_calls',
        'provider_outputs': 'analysis_provider_outputs',
        'cache': 'ai_response_cache',
    }
    _RECORD_FIELDS = {
        'companies': ('name', 'bloomberg_ticker', 'sedol', 'jurisdiction', 'sector', 'country'),
        'snapshots': ('name', 'benchmark_name'),
        'search_runs': ('cutoff', 'reporting_period', 'results_count'),
        'runs': ('status', 'company_id', 'theme_id', 'task', 'model'),
        'cases': ('run_id', 'company_id', 'theme_id', 'review_status',
                  'classification_confidence', 'exposure_quality'),
        'reviews': ('case_id', 'decision', 'reviewer', 'reason', 'reviewed_at'),
        'model_outputs': ('run_id', 'stage'),
        'model_calls': ('task', 'model', 'status', 'finished_at'),
        'provider_outputs': (),
        'cache': ('response',),
    }
    _THEME_FIELDS = (
        'theme_description', 'included_activities', 'excluded_activities',
        'positive_examples', 'negative_examples', 'additional_information',
    )
    _THEME_PAYLOAD_FIELDS = _THEME_FIELDS + (
        'cutoff', 'geographic_scope', 'exposure_basis', 'reporting_period',
    )

    def _table(self, kind):
        try:
            return self._SPECIAL_TABLES[kind]
        except KeyError:
            raise ValueError(f'No Supabase table configured for {kind}') from None

    def _rows(self, kind, filters=None, deadline=None):
        """`deadline`, in seconds, bounds the *total* time across every page of
        a multi-page read -- on top of the per-request timeout/retries inside
        `request()` -- so a large or currently-slow table can't hold a caller
        (e.g. a user-facing request) indefinitely. None means unbounded,
        preserving existing behaviour for callers that need a complete read
        regardless of how long it takes (e.g. offline export tooling)."""
        table = self._table(kind)
        query = {'select': 'payload,created_at', 'order': 'created_at.asc,id.asc'}
        query.update(filters or {})
        result = []
        offset = 0
        start = time.monotonic() if deadline is not None else None
        while True:
            elapsed = time.monotonic() - start if deadline is not None else None
            if elapsed is not None and elapsed > deadline:
                raise SupabaseError(
                    f'Supabase read of {kind} exceeded its {deadline:.0f}s overall deadline '
                    f'after fetching {len(result)} row(s)', retryable=True)
            remaining = None if elapsed is None else deadline - elapsed
            page = request('/rest/v1/' + table, query=query, headers={
                'Range-Unit': 'items',
                'Range': f'{offset}-{offset + 999}',
            }, timeout=remaining)
            if not isinstance(page, list):
                raise SupabaseError(f'Supabase returned an invalid {kind} record list')
            result.extend(row['payload'] for row in page)
            if len(page) < 1000:
                return result
            offset += len(page)

    def all_records(self, kind, deadline=None):
        return self._rows(kind, deadline=deadline)

    def company_records(self, kind, company_id, deadline=None):
        """Read one company's records without downloading the entire library."""
        return self._rows(kind, {'company_id': 'eq.' + str(company_id)}, deadline=deadline)

    def find(self, kind, record_id):
        if not record_id:
            return None
        table = self._table(kind)
        query = {'select': 'payload', 'id': 'eq.' + record_id, 'limit': '1'}
        rows = request('/rest/v1/' + table, query=query)
        return rows[0]['payload'] if rows else None

    def find_many(self, kind, record_ids):
        """Fetch a small set of current records in one request."""
        ids = [str(record_id) for record_id in dict.fromkeys(record_ids or []) if record_id]
        if not ids:
            return []
        table = self._table(kind)
        # Application record IDs are server-generated tokens; reject other
        # values instead of interpolating them into PostgREST's `in` syntax.
        if any(not __import__('re').fullmatch(r'[A-Za-z0-9_-]+', record_id) for record_id in ids):
            raise ValueError('Invalid record ID')
        rows = request('/rest/v1/' + table, query={
            'select': 'payload', 'id': 'in.(' + ','.join(ids) + ')'
        })
        return [row['payload'] for row in rows or []]

    def get(self, kind, record_id):
        value = self.find(kind, record_id)
        if value is None:
            raise ValueError(f'Unknown {kind} ID')
        return value

    def _record_payload(self, kind, record):
        record_payload = dict(record)
        payload = {'id': record['id'], 'payload': record_payload}
        table = self._table(kind)
        if kind == 'sources':
            created_at = (
                record_payload.get('created_at')
                or record_payload.get('retrieved_at')
                or datetime.now(timezone.utc).isoformat()
            )
            payload.update(
                company=record_payload.get('company_name'),
                company_id=record_payload.get('company_id'),
                bloomberg_ticker=record_payload.get('bloomberg_ticker'),
                sedol=record_payload.get('sedol'),
                source_type=record_payload.get('source_type'),
                reporting_period=record_payload.get('period') or record_payload.get('reporting_period'),
                cutoff=record_payload.get('cutoff') or None,
                url=record_payload.get('url'),
                publication_date=record_payload.get('publication_date'),
                filename=record_payload.get('filename'),
                title=record_payload.get('title'),
                registry=record_payload.get('registry'),
                short_registry=record_payload.get('short_registry'),
                extension=record_payload.get('extension'),
                sha256=record_payload.get('sha256'),
                storage_path=record_payload.get('storage_path'),
                retrieved_at=record_payload.get('retrieved_at'),
                warnings=record_payload.get('warnings') or [],
                evidence=record_payload.get('evidence') or [],
                created_at=created_at,
            )
        else:
            if kind == 'themes':
                for field in self._THEME_PAYLOAD_FIELDS:
                    value = record_payload.get(field)
                    if isinstance(value, str):
                        value = value.strip() or None
                        record_payload[field] = value
                for field in self._THEME_FIELDS:
                    value = record_payload.get(field)
                    payload[field] = value
                payload.update(
                    name=record_payload.get('theme_name'),
                    theme_name=record_payload.get('theme_name'),
                    nodes=record_payload.get('nodes') or None,
                    approved=bool(record_payload.get('approved')),
                    updated_at=record_payload.get('updated_at'),
                    created_at=record_payload.get('created_at') or datetime.now(timezone.utc).isoformat(),
                )
            else:
                payload.update(
                    name=record.get('name'),
                    created_at=record.get('created_at') or record.get('uploaded_at') or datetime.now(timezone.utc).isoformat(),
                )
            if kind in self._RECORD_FIELDS:
                for field in self._RECORD_FIELDS[kind]:
                    if field == 'reporting_period':
                        payload[field] = record_payload.get('reporting_period') or record_payload.get('period')
                    elif field == 'reviewed_at':
                        payload[field] = record_payload.get('timestamp')
                    else:
                        payload[field] = record_payload.get(field)
                if kind == 'reviews' and not payload.get('decision'):
                    payload['decision'] = (
                        record_payload.get('revised_value') or {}
                    ).get('review_status')
                if kind == 'cache':
                    payload['created_at'] = record_payload.get('created_at') or datetime.now(timezone.utc).isoformat()
            if kind == 'portfolio_imports':
                payload.update(
                    snapshot_id=record_payload.get('snapshot_id'),
                    holdings_date=record_payload.get('holdings_date'),
                    benchmark_name=record_payload.get('benchmark_name'),
                    file_name=record_payload.get('file_name'),
                    file_storage_path=record_payload.get('file_storage_path'),
                    file_sha256=record_payload.get('file_sha256'),
                    file_size=record_payload.get('file_size'),
                    file_content_type=record_payload.get('file_content_type'),
                )
        if kind in ('search_runs', 'runs', 'cases', 'reviews', 'model_outputs',
                    'model_calls', 'provider_outputs', 'cache'):
            # These tables store descriptive metadata in payload, without a name column.
            payload.pop('name', None)
        return payload, record_payload

    def put(self, kind, record):
        payload, record_payload = self._record_payload(kind, record)
        request('/rest/v1/' + self._table(kind), method='POST', query={'on_conflict': 'id'}, payload=payload,
                headers={'Prefer': 'resolution=merge-duplicates,return=minimal'})
        return record_payload

    def put_many(self, kind, records):
        if not records:
            return []
        rows = [self._record_payload(kind, record) for record in records]
        request('/rest/v1/' + self._table(kind), method='POST', query={'on_conflict': 'id'},
                payload=[row[0] for row in rows],
                headers={'Prefer': 'resolution=merge-duplicates,return=minimal'})
        return [row[1] for row in rows]

    def delete(self, kind, record_id):
        table = self._table(kind)
        query = {'id': 'eq.' + record_id}
        request('/rest/v1/' + table, method='DELETE', query=query)

    def status(self):
        url, key = configuration()
        return {'configured': bool(url and key), 'pending': 0, 'synced': 0, 'failed': 0}
