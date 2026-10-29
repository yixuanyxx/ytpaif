"""Server-side Supabase REST and Storage access."""
import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


class SupabaseError(RuntimeError):
    pass


def configuration():
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SECRET_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    return url, key


def request(path, method='GET', payload=None, headers=None, query=None, raw=None):
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
    try:
        with urlopen(req, timeout=30) as response:
            data = response.read()
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
            f'Supabase request failed: {method} {path} (HTTP {error.code}{suffix})'
        ) from None
    except (URLError, TimeoutError, OSError) as error:
        raise SupabaseError(f'Supabase request failed ({type(error).__name__})') from None
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def upload_source(storage_path, content, extension):
    path = '/storage/v1/object/source-documents/' + quote(storage_path, safe='/')
    request(path, method='POST', headers={
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

    def _rows(self, kind):
        table = self._table(kind)
        query = {'select': 'payload,created_at', 'order': 'created_at.asc,id.asc'}
        result = []
        offset = 0
        while True:
            page = request('/rest/v1/' + table, query=query, headers={
                'Range-Unit': 'items',
                'Range': f'{offset}-{offset + 999}',
            })
            if not isinstance(page, list):
                raise SupabaseError(f'Supabase returned an invalid {kind} record list')
            result.extend(row['payload'] for row in page)
            if len(page) < 1000:
                return result
            offset += len(page)

    def all_records(self, kind):
        return self._rows(kind)

    def find(self, kind, record_id):
        if not record_id:
            return None
        table = self._table(kind)
        query = {'select': 'payload', 'id': 'eq.' + record_id, 'limit': '1'}
        rows = request('/rest/v1/' + table, query=query)
        return rows[0]['payload'] if rows else None

    def get(self, kind, record_id):
        value = self.find(kind, record_id)
        if value is None:
            raise ValueError(f'Unknown {kind} ID')
        return value

    def put(self, kind, record):
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
        query = {'on_conflict': 'id'}
        request('/rest/v1/' + table, method='POST', query=query, payload=payload,
                headers={'Prefer': 'resolution=merge-duplicates,return=minimal'})
        return record_payload

    def delete(self, kind, record_id):
        table = self._table(kind)
        query = {'id': 'eq.' + record_id}
        request('/rest/v1/' + table, method='DELETE', query=query)

    def status(self):
        url, key = configuration()
        return {'configured': bool(url and key), 'pending': 0, 'synced': 0, 'failed': 0}
