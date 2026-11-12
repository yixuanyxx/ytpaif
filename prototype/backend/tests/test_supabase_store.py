import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import supabase_store


class SupabasePersistenceTest(unittest.TestCase):
    def test_only_availability_errors_allow_cached_fallback(self):
        for status in (402, 408, 429, 500, 503, 401, 403, 404):
            with self.subTest(status=status):
                error = HTTPError('https://project.example', status, 'error', {}, io.BytesIO(b'{}'))
                with patch.object(supabase_store, 'urlopen', side_effect=error):
                    with self.assertRaises(supabase_store.SupabaseError) as caught:
                        supabase_store.request('/rest/v1/themes')
                self.assertEqual(caught.exception.retryable, status in (402, 408, 429, 500, 503))

    def test_all_records_exceeding_its_overall_deadline_fails_fast_and_retryable(self):
        """A bounded deadline must stop a large multi-page read from running
        indefinitely, on top of the per-request timeout/retries already
        inside `request()`. Each full (1000-row) page keeps the loop going;
        the deadline check -- not page emptiness -- is what ends this read."""
        full_page = [{'payload': {'id': str(i)}} for i in range(1000)]

        def send(request, **kwargs):
            return io.BytesIO(json.dumps(full_page).encode())

        # One call for the loop's start time, then one per deadline check:
        # two pages fetched within budget, the third exceeds it.
        clock = iter([0, 0, 5, 20])
        with patch.object(supabase_store, 'urlopen', side_effect=send), \
                patch.object(supabase_store.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(supabase_store.SupabaseError,
                                        r'exceeded its 10s overall deadline after fetching 2000 row'):
                caught = None
                try:
                    supabase_store.SupabaseStore().all_records('themes', deadline=10)
                except supabase_store.SupabaseError as error:
                    caught = error
                    raise
        self.assertTrue(caught.retryable)

    def test_all_records_without_a_deadline_is_unbounded_as_before(self):
        """deadline=None (the default) must behave exactly as before this
        change: a read that would exceed any particular deadline still
        completes normally when none is given."""
        page = [{'payload': {'id': '1'}}]

        def send(request, **kwargs):
            return io.BytesIO(json.dumps(page).encode())

        with patch.object(supabase_store, 'urlopen', side_effect=send), \
                patch.object(supabase_store.time, 'monotonic', side_effect=AssertionError('must not check a deadline')):
            self.assertEqual(supabase_store.SupabaseStore().all_records('themes'), [{'id': '1'}])

    def test_company_records_filters_the_library_on_the_server(self):
        with patch.object(supabase_store, 'request', return_value=[{'payload': {'id': 'source'}}]) as send:
            rows = supabase_store.SupabaseStore().company_records('sources', 'company_abc')
        self.assertEqual(rows, [{'id': 'source'}])
        self.assertEqual(send.call_args.kwargs['query']['company_id'], 'eq.company_abc')

    def test_bulk_source_upsert_uses_one_request_and_same_mapping(self):
        store = supabase_store.SupabaseStore()
        records = [{'id': str(i), 'company_id': 'a', 'source_type': 'annual_report',
                    'url': f'https://example.com/{i}', 'extraction_status': 'not_requested',
                    'evidence': []} for i in range(3)]
        with patch.object(supabase_store, 'request') as request:
            self.assertEqual(store.put_many('sources', records), records)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], '/rest/v1/source_documents')
        rows = request.call_args.kwargs['payload']
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['evidence'] == [] for row in rows))
        self.assertEqual([row['payload']['url'] for row in rows], [r['url'] for r in records])

    def setUp(self):
        self.env = patch.dict(os.environ, {
            'SUPABASE_URL': 'https://project.example',
            'SUPABASE_SECRET_KEY': 'sb_secret_test',
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_api_uses_secret_key_only_server_side_and_encodes_filters(self):
        requests = []

        def send(request, **kwargs):
            requests.append(request)
            return io.BytesIO(b'[{"payload":{"id":"theme-1"}}]')

        with patch.object(supabase_store, 'urlopen', side_effect=send):
            values = supabase_store.SupabaseStore().all_records('themes')
        self.assertEqual(values, [{'id': 'theme-1'}])
        request = requests[0]
        self.assertTrue(request.full_url.endswith('/rest/v1/themes?select=payload%2Ccreated_at&order=created_at.asc%2Cid.asc'))
        self.assertEqual(request.get_header('Apikey'), 'sb_secret_test')
        self.assertIsNone(request.get_header('Authorization'))

    def test_analysis_run_does_not_send_unsupported_name_column(self):
        record = {'id': 'run-test', 'status': 'classifying', 'task': 'taxonomy',
                  'name': 'Retained metadata'}
        with patch.object(supabase_store, 'request') as request:
            supabase_store.SupabaseStore().put('runs', record)
        row = request.call_args.kwargs['payload']
        self.assertNotIn('name', row)
        self.assertEqual(row['payload'], record)
        self.assertEqual(request.call_args.args[0], '/rest/v1/analysis_runs')

    def test_shared_analysis_values_survive_supabase_payload_roundtrip(self):
        record = {'id': 'case-shared', 'company_id': 'company', 'theme_id': 'theme',
                  'exposure_percent': 50, 'confidence_percent': 87, 'classification_confidence': 'high',
                  'confidence_basis': 'Cited segment revenue', 'analysis_scope_key': 'scope',
                  'estimate': {'point': .5}, 'case': {'period': 'FY2025'}}
        with patch.object(supabase_store, 'request', return_value=[]) as request:
            supabase_store.SupabaseStore().put('cases', record)
        saved = request.call_args.kwargs['payload']
        self.assertEqual(saved['payload']['exposure_percent'], 50)
        self.assertEqual(saved['payload']['confidence_percent'], 87)
        with patch.object(supabase_store, 'request', return_value=[saved]):
            restored = supabase_store.SupabaseStore().all_records('cases')[0]
        self.assertEqual(restored, record)

    def test_unnamed_tables_never_receive_a_name_column(self):
        store = supabase_store.SupabaseStore()
        for kind in ('search_runs', 'runs', 'cases', 'reviews', 'model_outputs',
                     'model_calls', 'provider_outputs', 'cache'):
            with self.subTest(kind=kind), patch.object(supabase_store, 'request') as request:
                record = {'id': 'test', 'name': 'Metadata only'}
                store.put(kind, record)
                row = request.call_args.kwargs['payload']
                self.assertNotIn('name', row)
                self.assertEqual(row['payload']['name'], 'Metadata only')

    def test_store_routes_each_record_kind_to_its_dedicated_table(self):
        requests = []

        def send(request, **kwargs):
            requests.append(request)
            return io.BytesIO(b'')

        store = supabase_store.SupabaseStore()
        with patch.object(supabase_store, 'urlopen', side_effect=send):
            store.put('themes', {'id': 'theme-1', 'theme_name': 'AI'})
            store.put('portfolio_imports', {
                'id': 'portfolio-1', 'name': 'Core', 'snapshot_id': 'snapshot-1',
                'holdings_date': '2026-10-01', 'benchmark_name': 'Reference',
                'file_name': 'core.xlsx', 'file_storage_path': 'portfolios/import-1.xlsx',
            })
            store.put('sources', {
                'id': 'source-1', 'company_id': 'company-1', 'company_name': 'Company',
                'bloomberg_ticker': '1234 HK', 'sedol': '1234567',
                'source_type': 'annual_report', 'period': 'FY2025',
                'cutoff': '2026-10-01', 'url': 'https://example.test/report',
                'publication_date': '2026-05-01', 'filename': 'report.pdf',
            })
            store.put('companies', {'id': 'company-1', 'name': 'Company'})
            store.put('snapshots', {'id': 'snapshot-1', 'name': 'Core Snapshot'})
            store.put('search_runs', {'id': 'search-1', 'period': 'FY2025'})
            store.put('runs', {'id': 'run-1', 'status': 'needs_review'})
            store.put('cases', {'id': 'case-1', 'review_status': 'needs_review'})
            store.put('reviews', {'id': 'review-1', 'decision': 'approved'})
            store.put('model_outputs', {'id': 'output-1', 'stage': 'extracting'})
            store.put('model_calls', {'id': 'call-1', 'status': 'completed'})
            store.put('provider_outputs', {'id': 'provider-1', 'response': {'choices': []}})
            store.put('cache', {'id': 'cache-1', 'response': {'nodes': []}})
        self.assertIn('/rest/v1/themes?', requests[0].full_url)
        self.assertIn('/rest/v1/portfolios?', requests[1].full_url)
        self.assertIn('/rest/v1/source_documents?', requests[2].full_url)
        expected_tables = (
            'companies', 'portfolio_snapshots', 'source_search_runs', 'analysis_runs',
            'analysis_cases', 'analysis_reviews', 'analysis_model_outputs',
            'analysis_model_calls', 'analysis_provider_outputs', 'ai_response_cache',
        )
        for request, table in zip(requests[3:], expected_tables):
            self.assertIn(f'/rest/v1/{table}?', request.full_url)
        self.assertEqual(json.loads(requests[1].data)['name'], 'Core')
        self.assertEqual(json.loads(requests[1].data)['file_name'], 'core.xlsx')
        source_row = json.loads(requests[2].data)
        self.assertEqual(source_row['payload']['id'], 'source-1')
        self.assertEqual(source_row['company'], 'Company')
        self.assertEqual(source_row['bloomberg_ticker'], '1234 HK')
        self.assertEqual(source_row['sedol'], '1234567')
        self.assertEqual(source_row['source_type'], 'annual_report')
        self.assertEqual(source_row['reporting_period'], 'FY2025')
        self.assertEqual(source_row['cutoff'], '2026-10-01')
        self.assertEqual(source_row['publication_date'], '2026-05-01')

    def test_unknown_record_kind_fails_instead_of_using_a_catch_all_table(self):
        with self.assertRaisesRegex(ValueError, 'No Supabase table configured for unknown'):
            supabase_store.SupabaseStore().all_records('unknown')

    def test_theme_optional_input_fields_are_null_in_supabase_row_and_payload(self):
        requests = []

        def send(request, **kwargs):
            requests.append(request)
            return io.BytesIO(b'')

        theme = {
            'id': 'theme-1',
            'theme_name': 'AI Infrastructure',
            'theme_description': '   ',
            'included_activities': 'AI chips',
            'excluded_activities': '',
            'positive_examples': None,
            'negative_examples': '   ',
            'additional_information': '',
            'cutoff': '',
            'geographic_scope': None,
            'exposure_basis': ' revenue ',
            'reporting_period': '',
            'nodes': [],
        }
        with patch.object(supabase_store, 'urlopen', side_effect=send):
            stored = supabase_store.SupabaseStore().put('themes', theme)
        row = json.loads(requests[0].data)
        self.assertEqual(row['theme_name'], 'AI Infrastructure')
        self.assertEqual(row['name'], 'AI Infrastructure')
        for field in ('theme_description', 'excluded_activities', 'positive_examples',
                      'negative_examples', 'additional_information'):
            self.assertIsNone(row[field], field)
            self.assertIsNone(row['payload'][field], field)
        for field in ('cutoff', 'geographic_scope', 'reporting_period'):
            self.assertNotIn(field, row)
            self.assertIsNone(row['payload'][field], field)
        self.assertNotIn('exposure_basis', row)
        self.assertEqual(row['payload']['exposure_basis'], 'revenue')
        self.assertEqual(row['included_activities'], 'AI chips')
        self.assertIsNone(row['nodes'])
        self.assertIsNone(stored['theme_description'])
        self.assertEqual(stored['theme_name'], 'AI Infrastructure')

    def test_http_errors_are_reported_without_response_body(self):
        error = HTTPError('https://project.example', 503, 'unavailable', {}, None)
        with patch.object(supabase_store, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(
                supabase_store.SupabaseError,
                r'GET /rest/v1/themes \(HTTP 503\)',
            ):
                supabase_store.request('/rest/v1/themes')

    def test_transient_get_connection_error_is_retried(self):
        with patch.object(supabase_store, 'urlopen', side_effect=[
                URLError(TimeoutError('temporary')), io.BytesIO(b'[]')]) as send, \
                patch.object(supabase_store.time, 'sleep') as pause:
            self.assertEqual(supabase_store.request('/rest/v1/themes'), [])
        self.assertEqual(send.call_count, 2)
        pause.assert_called_once_with(0.25)

    def test_persistent_get_error_names_table_but_write_is_not_retried(self):
        with patch.object(supabase_store, 'urlopen', side_effect=URLError(TimeoutError('temporary'))) as send, \
                patch.object(supabase_store.time, 'sleep') as pause:
            with self.assertRaisesRegex(supabase_store.SupabaseError,
                                        r'GET /rest/v1/themes \(URLError: TimeoutError\)'):
                supabase_store.request('/rest/v1/themes')
        self.assertEqual(send.call_count, 3)
        self.assertEqual(pause.call_count, 2)
        with patch.object(supabase_store, 'urlopen', side_effect=URLError('temporary')) as send:
            with self.assertRaisesRegex(supabase_store.SupabaseError,
                                        r'POST /rest/v1/themes \(URLError: str\)'):
                supabase_store.request('/rest/v1/themes', method='POST', payload={})
        send.assert_called_once()

    def test_missing_audit_table_from_trigger_points_to_cleanup_migration(self):
        body = io.BytesIO(json.dumps({
            'code': '42P01',
            'message': 'relation "public.audit_events" does not exist',
        }).encode())
        error = HTTPError('https://project.example', 404, 'not found', {}, body)
        with patch.object(supabase_store, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(
                supabase_store.SupabaseError,
                r'public\.audit_events.*remove the trigger function',
            ):
                supabase_store.request('/rest/v1/source_documents', method='POST', payload={})

    def test_missing_configuration_fails_explicitly(self):
        with patch.dict(os.environ, {'SUPABASE_URL': '', 'SUPABASE_SECRET_KEY': ''}):
            with self.assertRaisesRegex(supabase_store.SupabaseError, 'Configure SUPABASE_URL'):
                supabase_store.request('/rest/v1/themes')

    def test_source_upload_uses_private_bucket_and_upsert(self):
        captured = []

        def send(request, **kwargs):
            captured.append(request)
            return io.BytesIO(b'')

        with patch.object(supabase_store, 'urlopen', side_effect=send):
            supabase_store.upload_source('sources/source-1.pdf', b'%PDF-test', '.pdf')
        request = captured[0]
        self.assertTrue(request.full_url.endswith('/storage/v1/object/source-documents/sources/source-1.pdf'))
        self.assertEqual(request.get_header('X-upsert'), 'true')

    def test_portfolio_upload_uses_private_bucket_and_preserves_binary_data(self):
        requests = []

        def send(request, **kwargs):
            requests.append(request)
            return io.BytesIO(b'')

        with patch.object(supabase_store, 'urlopen', side_effect=send):
            supabase_store.upload_portfolio_file(
                'portfolios/import-1.xlsx', b'\x00excel', 'application/vnd.ms-excel'
            )
        request = requests[0]
        self.assertTrue(request.full_url.endswith('/storage/v1/object/portfolio-files/portfolios/import-1.xlsx'))
        self.assertEqual(request.get_header('Content-type'), 'application/vnd.ms-excel')
        self.assertEqual(request.get_header('X-upsert'), 'true')
        self.assertEqual(request.data, b'\x00excel')

    def test_portfolio_delete_removes_file_from_private_bucket(self):
        requests = []

        def send(request, **kwargs):
            requests.append(request)
            return io.BytesIO(b'')

        with patch.object(supabase_store, 'urlopen', side_effect=send):
            supabase_store.delete_portfolio_file('portfolios/import-1.xlsx')
        request = requests[0]
        self.assertEqual(request.full_url, 'https://project.example/storage/v1/object/portfolio-files')
        self.assertEqual(request.method, 'DELETE')
        self.assertEqual(json.loads(request.data), {'prefixes': ['portfolios/import-1.xlsx']})

if __name__ == '__main__':
    unittest.main()
