import io
import json
import unittest
from datetime import datetime, timezone
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import patch
import source_discovery as discovery


class FakeApp:
    def __init__(self, runs=None):
        self.records = {'search_runs': runs or [], 'sources': []}

    def all_records(self, kind):
        return self.records.setdefault(kind, [])

    def put(self, kind, record):
        records = self.records.setdefault(kind, [])
        for index, existing in enumerate(records):
            if existing.get('id') == record.get('id'):
                records[index] = record
                return record
        records.append(record)
        return record

    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def get(self, kind, record_id):
        return {'id': record_id, 'name': record_id}

    def ident(self, prefix):
        return f'{prefix}_test'


class DiscoveryTest(unittest.TestCase):
    def test_only_cited_urls_are_accepted(self):
        rows = [{'url': 'https://example.com/report', 'source_type': 'annual_report'},
                {'url': 'https://invented.example/report', 'source_type': 'annual_report'}]
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({'sources': rows}),
                  'annotations': [{'type': 'url_citation', 'url': rows[0]['url']}]}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())):
            result = discovery.grounded_search({'name': 'Example'}, ['annual_report'], 'FY2025', '2026-11-01', False, 5)
        self.assertEqual(result, rows[:1])

    def test_private_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            discovery.public_url('http://127.0.0.1/secrets')
        with patch.object(discovery.socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                discovery.public_url('https://example.com/private')

    def test_cancel_closes_persisted_run_without_a_worker(self):
        run = {'id': 'searchrun-stale', 'status': 'running'}
        app = FakeApp([run])
        result = discovery.cancel(app, {'id': run['id']})
        self.assertEqual(result['status'], 'cancelled')
        self.assertIn('No active worker', result['cancel_reason'])
        self.assertTrue(result['finished_at'])

    def test_cancel_signals_active_worker(self):
        run = {'id': 'searchrun-active', 'status': 'running'}
        app = FakeApp([run])
        cancel_event = discovery.threading.Event()
        with discovery._jobs_lock:
            discovery._jobs[run['id']] = cancel_event
        try:
            result = discovery.cancel(app, {'id': run['id']})
            self.assertTrue(cancel_event.is_set())
            self.assertEqual(result['status'], 'cancelling')
        finally:
            with discovery._jobs_lock:
                discovery._jobs.pop(run['id'], None)

    def test_state_recovery_closes_orphaned_runs(self):
        run = {'id': 'searchrun-orphan', 'status': 'running',
               'created_at': '2026-01-01T00:00:00+00:00'}
        app = FakeApp([run])
        discovery.recover_stale_runs(app)
        self.assertEqual(app.all_records('search_runs')[0]['status'], 'cancelled')
        self.assertTrue(app.all_records('search_runs')[0]['finished_at'])

    def test_worker_stops_before_search_when_cancelled(self):
        run = {'id': 'searchrun-cancelled', 'status': 'running', 'results': [],
               'results_count': 0, 'errors': [], 'missing': []}
        app = FakeApp([run])
        cancel_event = discovery.threading.Event()
        cancel_event.set()
        with patch.object(discovery, 'grounded_search') as search:
            discovery.worker(app, run, [], cancel_event)
        search.assert_not_called()
        self.assertEqual(run['status'], 'cancelled')

    def test_provider_rate_limit_fails_run_without_retrying_every_company(self):
        run = {'id': 'searchrun-rate-limited', 'status': 'running', 'results': [],
               'results_count': 0, 'errors': [], 'missing': [], 'period': None,
               'cutoff': '2026-11-01', 'latest_documents': True,
               'source_types': ['annual_report'], 'companies_completed': []}
        app = FakeApp([run])
        error = HTTPError('https://provider.example/search', 429, 'rate limited', {}, None)
        companies = [{'id': 'company-1', 'name': 'Example'},
                     {'id': 'company-2', 'name': 'Second'}]
        with patch.object(discovery, 'grounded_search', side_effect=error) as search:
            discovery.worker(app, run, companies)
        search.assert_called_once()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('HTTP 429', run['errors'][0]['reason'])
        self.assertEqual(run['companies_completed'], [])
        self.assertEqual(run['missing'], [{'company_id': 'company-1', 'source_type': 'annual_report'},
                                         {'company_id': 'company-2', 'source_type': 'annual_report'}])

    def test_rate_limit_retry_after_is_honored_once(self):
        headers = Message()
        headers['Retry-After'] = '0.001'
        error = HTTPError('https://provider.example/search', 429, 'rate limited',
                          headers, io.BytesIO(b'{"error":{"status":"RESOURCE_EXHAUSTED"}}'))
        with patch.object(discovery, 'grounded_search', side_effect=[error, ['result']]) as search:
            result = discovery._grounded_search_with_retry(
                {'name': 'Example'}, ['annual_report'], None, '2026-11-02',
                False, 5, discovery.threading.Event())
        self.assertEqual(result, ['result'])
        self.assertEqual(search.call_count, 2)

    def test_rate_limit_error_exposes_provider_reason_without_api_key(self):
        headers = Message()
        payload = {'error': {
            'status': 'RESOURCE_EXHAUSTED',
            'message': 'Quota exceeded for key secret-test-key'
        }}
        error = HTTPError('https://provider.example/search', 429, 'rate limited',
                          headers, io.BytesIO(json.dumps(payload).encode()))
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'secret-test-key'}):
            info = discovery._rate_limit_info(error)
        self.assertIn('RESOURCE_EXHAUSTED', info['reason'])
        self.assertIn('[redacted]', info['reason'])
        self.assertNotIn('secret-test-key', info['reason'])
        self.assertIn('no retry time', info['reason'])

    def test_start_uses_selected_source_types(self):
        app = FakeApp()
        data = {'company_ids': ['company-1'], 'source_types': ['annual_report'],
                'latest_documents': True}
        with patch.object(discovery.threading, 'Thread') as thread:
            try:
                run = discovery.start(app, data)['search_run']
            finally:
                with discovery._jobs_lock:
                    discovery._jobs.pop('searchrun_test', None)
        self.assertEqual(run['source_types'], ['annual_report'])
        thread.assert_called_once()

    def test_start_rejects_empty_or_unknown_source_types(self):
        for requested_types in ([], ['unknown']):
            with self.subTest(source_types=requested_types):
                with self.assertRaises(ValueError):
                    discovery.start(FakeApp(), {'company_ids': ['company-1'],
                                                'source_types': requested_types})

    def test_worker_searches_and_reports_only_requested_types(self):
        run = {'id': 'searchrun-narrow', 'status': 'running', 'results': [],
               'results_count': 0, 'errors': [], 'missing': [], 'period': None,
               'cutoff': '2026-11-01', 'latest_documents': True,
               'source_types': ['annual_report'], 'companies_completed': []}
        app = FakeApp([run])
        company = {'id': 'company-1', 'name': 'Example'}
        with patch.object(discovery, 'MAX_RUN_SECONDS', 0.01), \
                patch.object(discovery, 'grounded_search', return_value=[]) as search:
            discovery.worker(app, run, [company])
        self.assertEqual(search.call_args.args[1], ['annual_report'])
        self.assertEqual(run['missing'], [{'company_id': 'company-1', 'source_type': 'annual_report'}])
