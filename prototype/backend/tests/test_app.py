import base64
import hashlib
import importlib
import io
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import zipfile
from urllib.error import HTTPError
from test_fixtures import MemoryStore, demo
from test_source_cache import FakeRedis

class AppFlowTest(unittest.TestCase):
    def test_workspace_survives_supabase_outage_after_redis_is_populated(self):
        from cached_store import CachedStore
        from supabase_store import SupabaseStore, SupabaseError
        permanent = self.app.store
        self.app.store = CachedStore()
        with patch('source_discovery.recover_stale_runs'), patch.object(self.app, 'refresh_worker_sources'):
            with patch.object(SupabaseStore, 'all_records', side_effect=permanent.all_records):
                before = self.app.state()
            with patch.object(self.app.store, 'FRESH_SECONDS', -1), \
                    patch.object(SupabaseStore, 'all_records', side_effect=SupabaseError('HTTP 402', retryable=True)):
                after = self.app.state()
        for kind in ('themes', 'companies', 'cases', 'reviews', 'runs', 'snapshots', 'portfolio_imports'):
            self.assertEqual(after[kind], before[kind])
        self.assertTrue(after['storage']['cached_fallback'])

    def test_general_ai_falls_back_and_records_the_model_used(self):
        output = json.dumps({'choices': [{'message': {'content': '{}'}}]}).encode()
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test', 'LLM_MODEL': 'gemini-3.8-flash'}), \
                patch.object(self.app, 'urlopen', side_effect=[
                    HTTPError('https://provider', 503, 'Busy', {}, None), io.BytesIO(output)]) as send:
            run = {'usage': []}
            result = self.app.llm('taxonomy', {}, run)
        self.assertEqual(result, {})
        self.assertEqual(run['usage'][0]['model'], 'gemini-3.7-flash')
        self.assertTrue(run['usage'][0]['fallback_used'])
        self.assertEqual(self.app.all_records('model_calls')[0]['model'], 'gemini-3.7-flash')
        self.assertEqual(send.call_count, 2)

    def test_financial_populate_enqueues_without_running_ai_in_the_request(self):
        portfolio = {'id': 'financial-test', 'rows': [
            {'company_id': 'DEMO_A'}, {'company_id': 'DEMO_B'}, {'company_id': 'DEMO_MIXED'},
        ]}
        self.app.put('portfolio_imports', portfolio)
        queue = Mock()
        queue.enqueue.side_effect = lambda run, companies: run
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test'}), \
                patch('source_jobs.Queue', return_value=queue), patch('source_jobs.ensure_worker') as start, \
                patch('financial_research.iter_results') as research:
            result = self.app.populate_company_financials({'portfolio_id': portfolio['id']})
        self.assertEqual(result['financial_run']['job_type'], 'financials')
        self.assertEqual(result['financial_run']['phase'], 'queued')
        self.assertEqual(len(result['financial_run']['company_ids']), 3)
        start.assert_called_once()
        research.assert_not_called()

    def test_editing_link_only_source_requires_a_verified_document_url(self):
        source = self.app.all_records('sources')[0]
        source.update(storage_mode='url_only', link_status='verified')
        self.app.put('sources', source)
        with patch('source_discovery.verify_document_link', side_effect=ValueError('Not a document link')) as verify:
            with self.assertRaisesRegex(ValueError, 'Not a document link'):
                self.app.source_update({'id': source['id'], 'url': 'https://example.com/'})
        verify.assert_called_once()

    def test_general_ai_uses_gemini_38_without_grounding(self):
        output = json.dumps({'choices': [{'message': {'content': '{}'}}]}).encode()
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'gemini-test-key', 'LLM_API_KEY': 'billed-key', 'LLM_MODEL': ''}), \
                patch.object(self.app, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(output)) as send:
            for task in ('taxonomy', 'node breakdown', 'extraction', 'classification'):
                self.app.llm(task, {}, {'usage': []})
        self.assertEqual(send.call_count, 4)
        for call in send.call_args_list:
            request = call.args[0]
            body = json.loads(request.data)
            self.assertEqual(body['model'], 'gemini-3.8-flash')
            self.assertNotIn('tools', body)
            self.assertEqual(request.get_header('Authorization'), 'Bearer gemini-test-key')

    def test_exposure_analysis_uses_claude_sonnet_5(self):
        output = json.dumps({'content': [{'type': 'text', 'text': 'Result:\n```json\n{"result":"ok"}\n```'}],
                             'usage': {'input_tokens': 10, 'output_tokens': 4}}).encode()
        env = {'EXPOSURE_LLM_PROVIDER': 'claude', 'CHECKER_LLM_API_KEY': 'claude-test-key',
               'CHECKER_LLM_ENDPOINT': 'https://api.anthropic.com/v1/messages',
               'CHECKER_LLM_MODEL': 'claude-sonnet-5'}
        with patch.dict(os.environ, env, clear=False), \
                patch.object(self.app, 'urlopen', return_value=io.BytesIO(output)) as send:
            result = self.app.llm('exposure calculation', {'company': 'Example'}, {'usage': []},
                                  provider_kind='exposure')
        self.assertEqual(result, {'result': 'ok'})
        request = send.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body['model'], 'claude-sonnet-5')
        self.assertEqual(body['max_tokens'], 32768)
        self.assertEqual(body['messages'][0]['role'], 'user')
        self.assertNotIn('response_format', body)
        self.assertEqual(request.get_header('X-api-key'), 'claude-test-key')
        self.assertEqual(request.get_header('Anthropic-version'), '2023-06-01')

    def test_claude_exposure_retries_once_after_truncated_json(self):
        bad = json.dumps({'content': [{'type': 'text', 'text': '{"result":'}],
                          'stop_reason': 'max_tokens'}).encode()
        good = json.dumps({'content': [{'type': 'text', 'text': '{"result":"ok"}'}]}).encode()
        env = {'EXPOSURE_LLM_PROVIDER': 'claude', 'CHECKER_LLM_API_KEY': 'claude-test-key',
               'CHECKER_LLM_ENDPOINT': 'https://api.anthropic.com/v1/messages',
               'CHECKER_LLM_MODEL': 'claude-sonnet-5'}
        with patch.dict(os.environ, env, clear=False), \
                patch.object(self.app, 'urlopen', side_effect=[io.BytesIO(bad), io.BytesIO(good)]) as send:
            result = self.app.llm('exposure retry', {}, {'usage': []}, provider_kind='exposure')
        self.assertEqual(result, {'result': 'ok'})
        self.assertEqual(send.call_count, 2)

    def test_source_search_prefers_redis_link_cache(self):
        source = self.app.all_records('sources')[0]
        company_id = source['company_id']
        with patch.object(self.app.source_cache, 'load_or_warm', return_value=[source]) as cache:
            results = self.app.source_search({'company_id': company_id, 'source_types': [source['source_type']]})
        cache.assert_called_once()
        self.assertEqual(results['results'][0]['url'], source['url'])

    def test_opening_the_app_warms_redis_with_supabase_source_records(self):
        with patch('source_discovery.recover_stale_runs'), \
                patch.object(self.app.source_cache, 'is_hydrated', return_value=False), \
                patch.object(self.app.source_cache, 'warm_sources') as warm:
            self.app.state()
        warm.assert_called_once()
        self.assertTrue(all('evidence' in source for source in warm.call_args.args[0]))

    def test_worker_saves_refresh_this_process_cache(self):
        import source_jobs
        from types import SimpleNamespace
        record = {'id': 'new-source', 'company_id': 'company'}
        self.app._source_update_cursor = 0
        queue = SimpleNamespace(source_updates_after=lambda cursor: [(1, 'new-source')] if cursor == 0 else [])
        with patch.object(source_jobs, 'Queue', return_value=queue), \
                patch.object(self.app.store, 'find_many', return_value=[record]), \
                patch.object(self.app.source_cache, 'save_sources_to_redis', return_value=True) as save:
            self.app.refresh_worker_sources()
            self.app.refresh_worker_sources()
        save.assert_called_once_with([record], mark_pending=False)

    def test_state_includes_sources_saved_as_a_search_finishes(self):
        run = {'id': 'search', 'status': 'completed'}
        summaries = []
        summary = {'company_id': 'company', 'count': 1, 'types': {'annual_report': 1}}

        def read_records(kind):
            if kind == 'search_runs':
                summaries.append(summary)
                return [run]
            return []

        with patch('source_discovery.recover_stale_runs'), \
                patch.object(self.app, 'all_records', side_effect=read_records), \
                patch.object(self.app, 'refresh_worker_sources'), \
                patch.object(self.app.source_cache, 'is_hydrated', return_value=True), \
                patch.object(self.app.source_cache, 'get_source_summaries', side_effect=lambda: list(summaries)):
            state = self.app.state()
        self.assertEqual(state['search_runs'], [run])
        self.assertEqual(state['source_summaries'], [summary])

    def test_verification_uses_claude_sonnet_5_and_its_own_key(self):
        case = self.app.all_records('cases')[0]
        output = json.dumps({'content': [{'type': 'text', 'text': '{"verdict":"VERIFIED"}'}]}).encode()
        with patch.dict(os.environ, {'CHECKER_LLM_API_KEY': 'claude-test-key', 'CHECKER_LLM_MODEL': '',
                                    'CHECKER_LLM_ENDPOINT': 'https://api.anthropic.com/v1/messages'}), \
                patch.object(self.app, 'urlopen', return_value=io.BytesIO(output)) as send:
            result = self.app.check_case({'case_id': case['id']})
        send.assert_called_once()
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data)['model'], 'claude-sonnet-5')
        self.assertEqual(request.get_header('X-api-key'), 'claude-test-key')
        self.assertEqual(case['checker_result']['checker_model'], 'claude-sonnet-5')

    def test_verification_does_not_silently_fallback_to_another_model(self):
        case = self.app.all_records('cases')[0]
        with patch.dict(os.environ, {'CHECKER_LLM_API_KEY': 'claude-test-key',
                                    'CHECKER_LLM_MODEL': 'claude-sonnet-5'}), \
                patch.object(self.app, 'urlopen', side_effect=HTTPError('https://provider', 404, 'Model unavailable', {}, None)) as send:
            with self.assertRaisesRegex(ValueError, 'Claude Checker API error: HTTP 404'):
                self.app.check_case({'case_id': case['id']})
        send.assert_called_once()

    def test_future_holdings_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Holdings date cannot be later'):
            self.app.portfolio_import({'name': 'Future', 'holdings_date': '2999-01-01', 'rows': []})

    def test_portfolio_name_is_required(self):
        for name in (None, '', '   ', 123):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Portfolio name is required'):
                self.app.portfolio_import({'name': name, 'rows': []})

    def setUp(self):
        budget_dir = tempfile.TemporaryDirectory()
        self.addCleanup(budget_dir.cleanup)
        budget_env = patch.dict(os.environ, {'GEMINI_BUDGET_DB': str(Path(budget_dir.name) / 'gemini-budget.sqlite')})
        budget_env.start()
        self.addCleanup(budget_env.stop)
        # Add prototype to sys.path so app is imported from prototype
        import sys
        proto_dir = str(Path(__file__).resolve().parent)
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import app
        self.app = importlib.reload(app)
        blocked = patch.dict(self.app.gemini_fallback._blocked, {}, clear=True)
        blocked.start()
        self.addCleanup(blocked.stop)
        self.app.store = MemoryStore()
        source_cache_connection = patch.object(self.app.source_cache, '_connection', return_value=FakeRedis())
        source_cache_connection.start()
        self.addCleanup(source_cache_connection.stop)
        source_confidence_call = patch.object(self.app.source_confidence, 'claude_assess',
            return_value=({'light': 'yellow', 'basis': 'One cited document is not corroborated by three sources.',
                           'agreeing_source_ids': [], 'divergent_source_ids': []}, 'claude-test'))
        source_confidence_call.start()
        self.addCleanup(source_confidence_call.stop)
        self.source_upload_mock = patch.object(self.app.supabase_store, 'upload_source')
        self.source_upload_mock.start()
        self.addCleanup(self.source_upload_mock.stop)
        self.portfolio_upload_mock = patch.object(self.app.supabase_store, 'upload_portfolio_file')
        self.portfolio_upload_mock.start()
        self.addCleanup(self.portfolio_upload_mock.stop)
        self.theme = demo(self.app)

    def test_demo_reproducible_and_export(self):
        d = self.app.dashboard(self.theme['id'])
        self.assertAlmostEqual(d['summary']['portfolio']['point'], 0.18)
        self.assertAlmostEqual(d['summary']['benchmark']['point'], 0.14)
        self.assertAlmostEqual(d['summary']['active']['point'], 0.04)
        self.assertEqual(demo(self.app)['id'], self.theme['id'])
        data = self.app.export(self.theme['id'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('evidence.csv', z.namelist())
            self.assertIn('exposure_results.csv', z.namelist())
            self.assertIn('run_manifest.json', z.namelist())
            self.assertIn('company_cases.json', z.namelist())
            self.assertIn('model_outputs.json', z.namelist())
            manifest = json.loads(z.read('run_manifest.json'))
            self.assertEqual(manifest['theme']['id'], self.theme['id'])
            self.assertEqual(len(manifest['reviews']), 3)

    def test_dashboard_uses_pending_calculation_without_promoting_upper_bound(self):
        case = self.app.all_records('cases')[0]
        case['review_status'] = 'needs_review'
        case['estimate']['point'] = None
        case['estimate']['upper'] = 0.6
        case['estimate']['calculation_version'] = '1.2.2'
        self.app.put('cases', case)

        selected = self.app.selected_estimates(self.theme['id'])
        self.assertEqual(selected[case['company_id']]['point'], 0.5)
        self.assertEqual(selected[case['company_id']]['point_basis'], 'conservative_evidence')
        self.assertEqual(selected[case['company_id']]['review_status'], 'needs_review')
        self.assertTrue(selected[case['company_id']]['calculation_stale'])

        dashboard = self.app.dashboard(self.theme['id'])
        row = next(row for row in dashboard['companies'] if row['company_id'] == case['company_id'])
        self.assertTrue(row['assessed'])
        self.assertEqual(row['review_status'], 'needs_review')
        self.assertEqual(row['exposure']['point'], 0.5)

    def test_evaluate_endpoint_is_removed(self):
        with self.assertRaisesRegex(ValueError, 'Unknown API endpoint'):
            self.app.dispatch('/api/evaluate', {})

    def test_review_immutable_and_reconciliation_gate(self):
        c = self.app.all_records('cases')[0]
        # Re-reviewing decided case is rejected
        with self.assertRaises(ValueError):
            self.app.review({'case_id': c['id'], 'decision': 'rejected',
                             'reviewer': 'test', 'reason': 'test'})
        # Case with reconciliation issues cannot be approved
        body = {
            'company_id': c['company_id'], 'theme_id': self.theme['id'],
            'case': c['case'], 'classification_confidence': 'high',
            'exposure_quality': 'high', 'confidence_basis': 'Clear source disclosure'
        }
        body['case']['units'] = body['case']['units'][:1]
        new = self.app.save_case(body)
        with self.assertRaises(ValueError):
            self.app.review({'case_id': new['id'], 'decision': 'approved',
                             'reviewer': 'test', 'reason': 'test'})

    def test_cutoff_preservation_and_evidence_filtering(self):
        # Create theme with explicit cutoff
        t = self.app.theme_create({
            'theme_name': 'Dated Theme',
            'theme_description': 'Theme with explicit cutoff',
            'included_activities': 'Hardware',
            'cutoff': '2026-06-01'
        })
        self.assertEqual(t.get('cutoff'), '2026-06-01')

        # Text with >= 80 characters to form a valid evidence chunk
        content_text = ('Valid evidence text for thematic disclosure that exceeds '
                        'eighty characters in length to ensure passage extraction.')
        past = self.app.source_ingest({
            'company_id': 'DEMO_A', 'filename': 'past.txt',
            'content': base64.b64encode(content_text.encode()).decode(),
            'publication_date': '2026-05-01', 'period': 'FY2025', 'url': ''
        })
        future = self.app.source_ingest({
            'company_id': 'DEMO_A', 'filename': 'future.txt',
            'content': base64.b64encode(content_text.encode()).decode(),
            'publication_date': '2026-07-01', 'period': 'FY2025', 'url': ''
        })
        sources, valid_ids = self.app.evidence_for('DEMO_A', t['id'])
        self.assertIn(past['evidence'][0]['evidence_id'], valid_ids)
        self.assertNotIn(future['evidence'][0]['evidence_id'], valid_ids)

    def test_theme_edit_in_place_and_distinct_create(self):
        # Saving with an existing id edits that theme in place (stable id, no new record).
        count_before = len(self.app.all_records('themes'))
        edited = self.app.theme_create({**self.theme, 'id': self.theme['id'],
                                        'theme_description': 'Revised scope'})
        self.assertEqual(edited['id'], self.theme['id'])
        self.assertEqual(edited['theme_description'], 'Revised scope')
        self.assertEqual(self.app.get('themes', self.theme['id'])['theme_description'], 'Revised scope')
        self.assertEqual(len(self.app.all_records('themes')), count_before)
        # No versioning metadata remains.
        self.assertNotIn('version', edited)
        self.assertNotIn('theme_id', edited)

        # Saving without an id creates a new, independent theme.
        fresh = self.app.theme_create({
            'theme_name': 'Second Theme',
            'theme_description': 'A distinct theme',
            'included_activities': 'Something qualifying',
        })
        self.assertNotEqual(fresh['id'], self.theme['id'])
        self.assertEqual(len(self.app.all_records('themes')), count_before + 1)

    def test_theme_taxonomy_is_persisted_as_nested_json(self):
        saved = self.app.theme_create({
            'theme_name': 'Nested', 'theme_description': 'Nested taxonomy',
            'nodes': [
                {'node_id': 'parent', 'parent_id': None, 'name': 'Parent', 'definition': 'Parent', 'examples': []},
                {'node_id': 'child', 'parent_id': 'parent', 'name': 'Child', 'definition': 'Child', 'examples': []},
            ],
        })
        self.assertNotIn('parent_id', saved['nodes'][0])
        self.assertEqual(saved['nodes'][0]['children'][0]['node_id'], 'child')

    def test_theme_definition_inputs_without_values_are_stored_as_null(self):
        theme = self.app.theme_create({
            'theme_name': 'Sparse Theme',
        })
        for field in ('theme_description', 'included_activities', 'excluded_activities',
                      'negative_examples', 'additional_information', 'cutoff',
                      'geographic_scope', 'exposure_basis', 'reporting_period'):
            self.assertIsNone(theme[field], field)
        self.assertIsNone(theme['positive_examples'])

    def test_theme_delete_removes_from_list(self):
        fresh = self.app.theme_create({
            'theme_name': 'Disposable', 'theme_description': 'Temp',
            'included_activities': 'Temp activity',
        })
        self.app.dispatch('/api/theme/delete', {'id': fresh['id']})
        remaining = [t['id'] for t in self.app.all_records('themes')]
        self.assertNotIn(fresh['id'], remaining)
        with self.assertRaises(ValueError):
            self.app.dispatch('/api/theme/delete', {'id': 'theme_does_not_exist'})

    def test_portfolio_delete_removes_import_and_unshared_snapshot(self):
        imported = self.app.dispatch('/api/portfolio/import', {
            'name': 'Disposable portfolio',
            'rows': [{
                'company_name': 'TSMC', 'bloomberg_ticker': '2330 TT',
                'portfolio_wt': 1, 'reference_wt': 1,
            }],
        })
        portfolio = imported['import']
        snapshot_id = imported['snapshot']['id']
        with patch.object(self.app.supabase_store, 'delete_portfolio_file') as delete_file:
            result = self.app.dispatch('/api/portfolio/delete', {'id': portfolio['id']})
        self.assertEqual(result, {'deleted': portfolio['id'], 'warning': None})
        self.assertEqual(self.app.all_records('portfolio_imports'), [])
        self.assertNotIn(snapshot_id, [snapshot['id'] for snapshot in self.app.all_records('snapshots')])
        delete_file.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Portfolio not found'):
            self.app.dispatch('/api/portfolio/delete', {'id': portfolio['id']})

    def test_portfolio_delete_removes_uploaded_file_and_preserves_shared_snapshot(self):
        imported = self.app.dispatch('/api/portfolio/import', {
            'name': 'Excel portfolio',
            'rows': [{
                'company_name': 'TSMC', 'bloomberg_ticker': '2330 TT',
                'portfolio_wt': 1, 'reference_wt': 1,
            }],
            'file_name': 'holdings.xlsx',
            'file_content': base64.b64encode(b'workbook bytes').decode('ascii'),
        })
        portfolio = imported['import']
        self.app.put('portfolio_imports', {
            **portfolio, 'id': 'another-import', 'name': 'Another portfolio',
        })
        with patch.object(self.app.supabase_store, 'delete_portfolio_file') as delete_file:
            result = self.app.dispatch('/api/portfolio/delete', {'id': portfolio['id']})
        self.assertIsNone(result['warning'])
        delete_file.assert_called_once_with(portfolio['file_storage_path'])
        self.assertEqual(self.app.get('snapshots', imported['snapshot']['id'])['id'],
                         imported['snapshot']['id'])
        self.assertEqual([p['id'] for p in self.app.all_records('portfolio_imports')],
                         ['another-import'])

    def test_company_creation_and_id_preservation(self):
        # Explicit ID preserved
        c1 = self.app.company_create({
            'id': 'TSMC', 'name': 'Taiwan Semiconductor',
            'bloomberg_ticker': '2330 TT', 'sedol': '6889105'
        })
        self.assertEqual(c1['id'], 'TSMC')
        # Deduplication by ticker
        c2 = self.app.company_create({
            'name': 'Taiwan Semi Duplicate',
            'bloomberg_ticker': '2330 TT', 'sedol': ''
        })
        self.assertEqual(c2['id'], 'TSMC')

    def test_scanned_pdf_and_low_text_warns_and_skips_passage(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        stream = io.BytesIO()
        writer.write(stream)
        source = self.app.source_ingest({
            'company_id': 'DEMO_A', 'filename': 'scan.pdf',
            'content': base64.b64encode(stream.getvalue()).decode(),
            'publication_date': '2026-01-01', 'period': 'FY2025', 'url': ''
        })
        self.assertTrue(source['warnings'])
        self.assertFalse(source['evidence'])

        # Low text txt (< 80 chars)
        short_txt = self.app.source_ingest({
            'company_id': 'DEMO_A', 'filename': 'short.txt',
            'content': base64.b64encode(b'Too short to extract').decode(),
            'publication_date': '2026-01-01', 'period': 'FY2025', 'url': ''
        })
        self.assertTrue(short_txt['warnings'])
        self.assertFalse(short_txt['evidence'])

    def test_source_ingest_stores_company_metadata_and_validates_cutoff(self):
        source = self.app.source_ingest({
            'company_id': 'DEMO_A', 'filename': 'metadata.txt',
            'content': base64.b64encode(
                b'Annual report evidence with enough text to create a source passage and validate stored document metadata.'
            ).decode(),
            'publication_date': '2026-01-02', 'period': 'FY2025',
            'cutoff': '2026-10-01', 'url': 'https://example.test/annual-report',
            'source_type': 'annual_report',
        })
        self.assertEqual(source['company_name'], 'Company A (synthetic)')
        self.assertEqual(source['bloomberg_ticker'], 'DEMO_A')
        self.assertEqual(source['sedol'], '')
        self.assertEqual(source['cutoff'], '2026-10-01')
        same_document = {
            'company_id': 'DEMO_A', 'filename': 'metadata.txt',
            'content': base64.b64encode(
                b'Annual report evidence with enough text to create a source passage and validate stored document metadata.'
            ).decode(),
            'publication_date': '2026-01-02', 'period': 'FY2025',
            'cutoff': '2026-10-01', 'url': 'https://example.test/annual-report',
            'source_type': 'regulatory_filing',
        }
        filing = self.app.source_ingest(same_document)
        self.assertEqual(filing['source_type'], 'regulatory_filing')
        self.assertNotEqual(filing['id'], source['id'])
        self.assertEqual(self.app.source_ingest(same_document)['id'], filing['id'])
        with self.assertRaisesRegex(ValueError, 'isoformat|format'):
            self.app.source_ingest({
                'company_id': 'DEMO_A', 'filename': 'invalid-date.txt',
                'content': base64.b64encode(b'Valid enough source document content').decode(),
                'publication_date': '2026-01-03', 'period': 'FY2025',
                'cutoff': 'not-a-date', 'url': '',
            })

    def test_source_search(self):
        res = self.app.dispatch('/api/search', {
            'company_ids': ['DEMO_A'],
            'source_types': ['synthetic_example', 'annual_report'],
            'cutoff': '2026-01-02'
        })
        self.assertIn('search_run', res)
        self.assertIn('results', res)
        self.assertGreaterEqual(len(res['results']), 1)
        self.assertTrue(res['results'][0]['eligible'])

    def test_no_synthetic_runtime_entrypoints(self):
        with self.assertRaises(ValueError):
            self.app.dispatch('/api/demo', {})
        before = len(self.app.all_records('sources'))
        with self.assertRaises(ValueError):
            self.app.dispatch('/api/source/ingest_candidates', {'candidates': [{'company_id': 'DEMO_A'}]})
        self.assertEqual(len(self.app.all_records('sources')), before)

    def test_search_does_not_invent_documents(self):
        comp = self.app.company_create({'name': 'No documents', 'bloomberg_ticker': 'REAL'})
        before = len(self.app.all_records('sources'))
        result = self.app.source_search({'company_ids': [comp['id']], 'auto_ingest': True})
        self.assertEqual(result['results'], [])
        self.assertEqual(result['ingested_count'], 0)
        self.assertEqual(len(self.app.all_records('sources')), before)

    def test_latest_earnings_only_respects_cutoff_and_overrides_types(self):
        original = self.app.all_records('sources')[0]
        for sid, kind, published in [('old_call', 'earnings_call', '2025-09-01'),
                                     ('latest_call', 'earnings_call', '2025-12-01'),
                                     ('future_call', 'earnings_call', '2027-01-01'),
                                     ('annual', 'annual_report', '2025-12-31')]:
            self.app.put('sources', {**original, 'id': sid, 'company_id': 'DEMO_A',
                                    'title': sid, 'source_type': kind, 'publication_date': published})
        res = self.app.source_search({'company_ids': ['DEMO_A'],
            'source_types': ['annual_report'], 'stick_to_earnings_call': True,
            'cutoff': '2026-01-01'})
        self.assertEqual(len(res['results']), 1)
        self.assertEqual(res['results'][0]['title'], 'latest_call')
        self.assertEqual(res['results'][0]['source_type'], 'earnings_call')

    def test_batch_processes_ready_companies_and_reports_failures(self):
        template = self.app.all_records('sources')[0]
        for cid in ('READY_A', 'READY_B', 'TOO_LONG', 'NO_EVIDENCE', 'FUTURE'):
            self.app.company_create({'id': cid, 'name': cid, 'bloomberg_ticker': cid})
            self.app.put('sources', {**template, 'id': 'source_' + cid, 'company_id': cid,
                'publication_date': '2099-01-01' if cid == 'FUTURE' else '2025-01-01',
                'evidence': [] if cid == 'NO_EVIDENCE' else [{
                    'evidence_id': 'e_' + cid,
                    'passage': 'x' * (60001 if cid == 'TOO_LONG' else 100)}]})
        def analyze(payload):
            if payload['company_id'] == 'READY_B':
                raise ValueError('Provider unavailable')
            return {'id': 'case_ready'}
        with patch.object(self.app, 'analyze', side_effect=analyze) as mocked:
            res = self.app.analyze_all({'theme_id': self.theme['id']})
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(res['calculated_count'], 1)
        self.assertEqual(res['reused_count'], 3)
        self.assertEqual({e['company_id'] for e in res['errors']}, {'READY_B', 'TOO_LONG', 'NO_EVIDENCE', 'FUTURE'})

        self.app.put('portfolio_imports', {'id': 'scoped', 'theme_id': 'unrelated-theme',
                                          'rows': [{'company_id': 'READY_A'},
                                                   {'company_id': 'READY_B', 'excluded': True}]})
        with patch.object(self.app, 'analyze', return_value={'id': 'case_ready'}) as mocked:
            scoped = self.app.analyze_all({'theme_id': self.theme['id'], 'portfolio_id': 'scoped'})
        self.assertEqual([item['company_id'] for item in scoped['results']], ['READY_A'])
        self.assertEqual(scoped['errors'], [])
        mocked.assert_called_once()

    def test_generate_handles_counts_gracefully(self):
        # Non-int or 'auto'
        themes_before = self.app.all_records('themes')
        fake_nodes = [
            {'node_id': 't1', 'parent_id': None, 'name': 'T1', 'definition': 'D1', 'examples': []},
            {'node_id': 't2', 'parent_id': 't1', 'name': 'T2', 'definition': 'D2', 'examples': []}
        ]
        with patch.object(self.app, 'llm', return_value={'nodes': fake_nodes}):
            res = self.app.generate({'tier1_count': 'auto', 'tier2_count': 'auto',
                                     'theme_name': 'Test', 'included_activities': 'Act'})
            self.assertEqual(len(res['nodes']), 2)
        self.assertEqual(self.app.all_records('themes'), themes_before)

    def test_failed_model_does_not_create_zero_case(self):
        evidence = self.app.all_records('sources')[0]['evidence'][0]['evidence_id']
        before = len(self.app.all_records('cases'))
        with patch.dict(os.environ, {'EXPOSURE_LLM_PROVIDER': 'claude', 'EXPOSURE_LLM_API_KEY': '',
                                     'CHECKER_LLM_API_KEY': '', 'CLAUDE_API_KEY': '',
                                     'ANTHROPIC_API_KEY': '', 'FINANCIALS_LLM_API_KEY': '',
                                     'LLM_MODEL': ''}, clear=False):
            with self.assertRaises(ValueError):
                self.app.analyze({'theme_id': self.theme['id'],
                                  'company_id': 'DEMO_A', 'evidence_ids': [evidence]})
        self.assertEqual(before, len(self.app.all_records('cases')))
        self.assertEqual(self.app.all_records('runs')[-1]['status'], 'failed')

    def test_provider_ignores_legacy_call_cap(self):
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test', 'LLM_MODEL': 'test', 'LLM_MAX_CALLS': '0'}):
            with patch.object(self.app, 'urlopen') as send:
                send.return_value.__enter__.return_value = io.BytesIO(
                    b'{"choices":[{"message":{"content":"{\\"nodes\\":[]}"}}]}')
                self.assertEqual(self.app.llm('test', {}, {'usage': []}), {'nodes': []})
                send.assert_called_once()

    def test_security_company_mapping(self):
        p = [dict(company_id='DEMO_A', security_id='one', weight=1, snapshot_id='p', as_of_date='2026-10-03')]
        b = [dict(company_id='DEMO_B', security_id='one', weight=1, snapshot_id='b', as_of_date='2026-10-03')]
        with self.assertRaises(ValueError):
            self.app.dispatch('/api/snapshot', {'portfolio': p, 'benchmark': b})

    def test_ai_pipeline_preserves_unknown_allocation(self):
        source = self.app.all_records('sources')[0]
        evidence = source['evidence'][0]['evidence_id']
        facts = {
            'total_revenue': 100, 'denominator_evidence_ids': [evidence],
            'period': 'FY2025', 'currency': 'USD', 'scale': 'millions',
            'units': [{
                'unit_id': 'u1', 'activity': 'AI hardware', 'revenue': 100,
                'period': 'FY2025', 'currency': 'USD', 'scale': 'millions',
                'external': True, 'evidence_ids': [evidence], 'overlap_group': None
            }],
            'unresolved_questions': []
        }
        classification = {
            'classifications': [{
                'unit_id': 'u1', 'node_ids': ['compute_fab'], 'relationship_type': 'direct',
                'rationale': 'Source describes AI hardware', 'scope_check': '',
                'evidence_ids': [evidence], 'unresolved_questions': []
            }],
            'confidence_band': 'high', 'confidence_basis': 'Clear activity evidence'
        }
        with patch.object(self.app, 'llm', side_effect=[facts, classification]):
            c = self.app.analyze({'theme_id': self.theme['id'],
                                  'company_id': 'DEMO_A', 'evidence_ids': [evidence]})
        self.assertEqual(c['estimate']['point'], 0)
        self.assertEqual(c['review_status'], 'needs_review')
        self.assertEqual(c['classification_score'], 3)
        self.assertEqual(self.app.get('runs', c['run_id'])['status'], 'needs_review')
        self.assertEqual(len(self.app.all_records('model_outputs')), 3)

    def test_indirect_leaf_match_with_unknown_share_does_not_become_zero(self):
        source = next(s for s in self.app.all_records('sources') if s['company_id'] == 'DEMO_A')
        eid = source['evidence'][0]['evidence_id']
        facts = {'total_revenue': 100, 'denominator_evidence_ids': [eid], 'period': 'FY2025',
                 'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [{
                    'unit_id': 'mixed', 'activity': 'Mixed computing', 'revenue': 100, 'period': 'FY2025',
                    'currency': 'USD', 'scale': 'millions', 'external': True, 'evidence_ids': [eid]}]}
        classified = {'classifications': [{'unit_id': 'mixed', 'node_ids': ['compute_fab'],
            'relationship_type': 'indirect', 'whole_unit_supported': False, 'rationale': 'AI share undisclosed',
            'scope_check': 'Mixed AI/non-AI fabrication', 'evidence_ids': [eid],
            'unresolved_questions': ['Exact AI share of mixed computing revenue is undisclosed.']}],
            'confidence_band': 'medium', 'confidence_basis': 'Allocation not disclosed'}
        with patch.object(self.app, 'llm', side_effect=[facts, classified]) as model:
            saved = self.app.analyze({'theme_id': self.theme['id'], 'company_id': 'DEMO_A', 'evidence_ids': [eid]})
        self.assertEqual(model.call_count, 2)
        self.assertEqual(saved['estimate']['point'], 0)
        self.assertEqual(saved['estimate']['upper'], 1)
        self.assertEqual(saved['case']['units'][0]['relationship_type'], 'insufficient_evidence')
        self.assertEqual(saved['case']['units'][0]['original_relationship_type'], 'indirect')
        self.assertIn('Exact AI share', saved['estimate']['issues'][0])
        self.assertTrue(saved['estimate']['issues'])

    def test_excluded_with_open_scope_question_preserves_unknown_range(self):
        record = {'unit_id': 'u1', 'node_ids': [], 'relationship_type': 'excluded',
                  'rationale': 'Unclear product scope', 'scope_check': 'May be outside theme',
                  'evidence_ids': ['e1'], 'unresolved_questions': ['Does this product qualify?']}
        facts = {'units': [{'unit_id': 'u1', 'activity': 'Mixed product'}]}
        result = self.app._validate_classification_response(
            {'classifications': [record], 'confidence_band': 'low', 'confidence_basis': 'Open scope question'},
            facts, set(), {'e1'}, [{'evidence_id': 'e1', 'passage': 'Mixed product'}])
        self.assertEqual(result['classifications'][0]['relationship_type'], 'insufficient_evidence')
        self.assertEqual(result['classifications'][0]['original_relationship_type'], 'excluded')
        self.assertEqual(record['relationship_type'], 'excluded')

    def test_mixed_segment_uses_fractional_attribution_and_leaf_split(self):
        passages = [{'evidence_id': 'e1', 'passage':
            'HPC revenue was USD 600 million and growth was driven by AI accelerators.'}]
        facts = {'units': [
            {'unit_id': 'hpc', 'activity': 'High performance computing'},
            {'unit_id': 'other', 'activity': 'Other revenue'},
        ]}
        records = [
            {'unit_id': 'hpc', 'node_ids': ['foundry', 'packaging'], 'relationship_type': 'direct',
             'rationale': 'Only the evidenced AI-related portion qualifies', 'scope_check': 'AI infrastructure revenue',
             'evidence_ids': ['e1'], 'unresolved_questions': [], 'whole_unit_supported': False,
             'allocation': {'lower': .35, 'upper': .5, 'point': 5/12, 'method': 'estimated',
                            'assumption': 'AI foundry and packaging are 25% of total company revenue.'},
             'leaf_allocations': {'foundry': .8, 'packaging': .2},
             'revenue_attribution_quote': 'HPC revenue was USD 600 million and growth was driven by AI accelerators.'},
            {'unit_id': 'other', 'node_ids': [], 'relationship_type': 'excluded',
             'rationale': 'Outside theme', 'scope_check': 'Not AI infrastructure', 'evidence_ids': ['e1'],
             'unresolved_questions': [], 'whole_unit_supported': False, 'revenue_attribution_quote': ''},
        ]
        classified = self.app._validate_classification_response(
            {'classifications': records, 'confidence_band': 'medium', 'confidence_basis': 'Estimated from cited mix'},
            facts, {'foundry', 'packaging'}, {'e1'}, passages)
        by_id = {record['unit_id']: record for record in classified['classifications']}
        lo, hi, point, method = self.app._classification_allocation(by_id['hpc'], passages)
        case = {'total_revenue': 1000, 'denominator_evidence_ids': ['e1'], 'period': 'FY2026',
                'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [
            {'unit_id': 'hpc', 'activity': 'High performance computing', 'revenue': 600,
             'period': 'FY2026', 'currency': 'USD', 'scale': 'millions', 'external': True,
             'evidence_ids': ['e1'], 'classification_evidence_ids': ['e1'], 'overlap_group': None,
             'node_ids': ['foundry', 'packaging'], 'relationship_type': 'direct', 'rationale': 'Supported',
             'scope_check': 'AI infrastructure', 'lower': lo, 'upper': hi, 'point': point, 'method': method,
             'assumption': records[0]['allocation']['assumption'],
             'allocations': records[0]['leaf_allocations']},
            {'unit_id': 'other', 'activity': 'Other revenue', 'revenue': 400,
             'period': 'FY2026', 'currency': 'USD', 'scale': 'millions', 'external': True,
             'evidence_ids': ['e1'], 'classification_evidence_ids': ['e1'], 'overlap_group': None,
             'node_ids': [], 'relationship_type': 'excluded', 'rationale': 'Outside theme',
             'scope_check': 'Not AI infrastructure', 'lower': 0, 'upper': 0, 'point': 0,
             'method': 'derived', 'assumption': '', 'allocations': {}},
        ]}
        nodes = [
            {'node_id': 'theme', 'parent_id': None, 'name': 'AI Infrastructure',
             'definition': 'AI infrastructure', 'examples': []},
            {'node_id': 'foundry', 'parent_id': 'theme', 'name': 'Wafers & Foundry',
             'definition': 'AI chip foundry', 'examples': []},
            {'node_id': 'packaging', 'parent_id': 'theme', 'name': 'Advanced Packaging',
             'definition': 'AI chip packaging', 'examples': []},
        ]
        estimate = self.app.engine.estimate(case, nodes, {'e1'})
        self.assertAlmostEqual(estimate['point'], .25)
        self.assertAlmostEqual(estimate['node_exposures']['foundry']['point'], .20)
        self.assertAlmostEqual(estimate['node_exposures']['packaging']['point'], .05)

    def test_batch_calculates_example_exposure_and_persists_cited_rollup(self):
        import engine
        node = lambda nid, name, children: {'node_id': nid, 'name': name, 'definition': name,
                                           'examples': [], 'children': children}
        theme = self.app.theme_create({'theme_name': 'Clean Energy', 'nodes': [
            node('clean_energy', 'Clean Energy', [
                node('renewable', 'Renewable Generation', [node('solar', 'Solar', []), node('wind', 'Wind', [])]),
                node('storage', 'Energy Storage', []),
            ]),
        ]})
        self.app.company_create({'id': 'ABC', 'name': 'ABC', 'bloomberg_ticker': 'ABC SP'})
        text = ('Total external revenue USD 1000 million. Solar panels revenue USD 200 million. '
                'Wind turbines revenue USD 100 million. Integrated solar-and-battery systems revenue USD 50 million. '
                'Standalone batteries revenue USD 150 million. Unrelated services revenue USD 500 million.')
        source = self.app.source_ingest({'company_id': 'ABC', 'publication_date': '2026-01-01', 'period': 'FY2025',
            'filename': 'ABC.txt', 'source_type': 'annual_report', 'content': base64.b64encode(text.encode()).decode()})
        eid = source['evidence'][0]['evidence_id']
        facts = {'total_revenue': 1000, 'denominator_evidence_ids': [eid], 'period': 'FY2025',
                 'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': []}
        classifications = []
        segments = [
            ('R1', 'Solar panels', 200, ['solar']), ('R2', 'Wind turbines', 100, ['wind']),
            ('R3', 'Integrated solar-and-battery systems', 50, ['solar', 'storage']),
            ('R4', 'Standalone batteries', 150, ['storage']), ('R5', 'Unrelated services', 500, []),
        ]
        for uid, activity, revenue, leaves in segments:
            facts['units'].append({'unit_id': uid, 'activity': activity, 'revenue': revenue, 'period': 'FY2025',
                'currency': 'USD', 'scale': 'millions', 'external': True, 'evidence_ids': [eid], 'overlap_group': None})
            classifications.append({'unit_id': uid, 'node_ids': leaves, 'relationship_type': 'direct' if leaves else 'excluded',
                'rationale': 'Supported product revenue', 'scope_check': '' if leaves else 'Services outside Clean Energy',
                'evidence_ids': [eid], 'unresolved_questions': [], 'whole_unit_supported': bool(leaves),
                'revenue_attribution_quote': f'{activity} revenue USD {revenue} million.'})
        self.app.put('portfolio_imports', {'id': 'abc_portfolio', 'theme_id': theme['id'], 'rows': [{'company_id': 'ABC'}]})
        response = {'classifications': classifications, 'confidence_band': 'high', 'confidence_percent': 87,
                    'confidence_basis': 'Disclosed product revenue'}
        with patch.object(self.app, 'llm', side_effect=[facts, response]) as model:
            batch = self.app.analyze_all({'theme_id': theme['id'], 'portfolio_id': 'abc_portfolio', 'company_ids': ['ABC']})
        self.assertEqual(batch['errors'], [])
        case = batch['results'][0]['case']
        self.assertEqual(case['review_status'], 'needs_review')
        self.assertEqual(case['estimate']['calculation_status'], 'complete')
        self.assertAlmostEqual(case['estimate']['point'], .5)
        self.assertEqual(case['estimate']['main_theme_revenue'], 500)
        for nid, expected in [('solar', .25), ('wind', .1), ('renewable', .35), ('storage', .2), ('clean_energy', .5)]:
            self.assertAlmostEqual(case['estimate']['node_exposures'][nid]['point'], expected)
        self.assertEqual(case['estimate']['node_exposures']['clean_energy']['revenue_unit_ids'], ['R1', 'R2', 'R3', 'R4'])
        self.assertEqual(self.app.get('cases', case['id'])['estimate']['point'], .5)
        self.assertEqual(self.app.get('cases', case['id'])['confidence_percent'], 87)
        self.assertEqual(self.app.get('cases', case['id'])['exposure_percent'], 50)
        self.assertEqual(case['estimate']['calculation_version'], engine.VERSION)
        self.assertIn('children', model.call_args_list[1].args[1]['theme']['nodes'][0])
        with self.assertRaisesRegex(ValueError, 'selected portfolio'):
            self.app.analyze_all({'theme_id': theme['id'], 'portfolio_id': 'abc_portfolio', 'company_ids': ['DEMO_A']})
        # The second portfolio receives the same persisted answer without a
        # source fetch or AI call, even after the backend has been reloaded.
        self.app.put('portfolio_imports', {'id': 'second_portfolio', 'theme_id': theme['id'],
                                         'rows': [{'company_id': 'ABC'}]})
        saved_store = self.app.store
        self.app = importlib.reload(self.app)
        self.app.store = saved_store
        with patch.object(self.app, 'llm') as model, patch('exposure_sources.prepare_sources') as prepare:
            reused = self.app.analyze_all({'theme_id': theme['id'], 'portfolio_id': 'second_portfolio', 'company_ids': ['ABC']})
        model.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(reused['reused_count'], 1)
        self.assertEqual(reused['calculated_count'], 0)
        self.assertEqual(reused['results'][0]['case']['id'], case['id'])
        self.assertEqual(reused['results'][0]['case']['exposure_percent'], 50)
        self.assertEqual(reused['results'][0]['case']['confidence_percent'], 87)

    def test_batch_force_recalculate_bypasses_saved_company_result(self):
        self.app.put('portfolio_imports', {'id': 'force-rerun', 'theme_id': self.theme['id'],
                                           'rows': [{'company_id': 'DEMO_A'}]})
        new_case = {'id': 'fresh-case', 'estimate': {'point': 0.25}}
        with patch.object(self.app.analysis_library, 'current_results',
                          side_effect=AssertionError('Forced rerun must not reuse a saved case')), \
                patch('exposure_sources.prepare_sources', return_value=([{'id': 'source'}], ['evidence'], [])) as prepare, \
                patch('exposure_sources.select_evidence', return_value=(['evidence'], ['Automatic selection'])) as select, \
                patch.object(self.app, 'analyze', return_value=new_case) as analyze, \
                patch.object(self.app, '_search_unresolved_case',
                             return_value=(new_case, {'status': 'not_needed', 'message': ''}, False, False)):
            result = self.app.analyze_all({'theme_id': self.theme['id'], 'portfolio_id': 'force-rerun',
                                           'company_ids': ['DEMO_A'], 'force_recalculate': True})
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['calculated_count'], 1)
        self.assertEqual(result['reused_count'], 0)
        self.assertEqual(result['results'][0]['case']['id'], 'fresh-case')
        prepare.assert_called_once()
        select.assert_called_once()
        analyze.assert_called_once_with({
            'theme_id': self.theme['id'], 'company_id': 'DEMO_A', 'evidence_ids': ['evidence'],
            'evidence_selection_notes': ['Automatic selection'], 'source_read_errors': []
        })

    def test_batch_reads_link_only_sources_with_missing_or_null_evidence_before_calculating(self):
        self.app.company_create({'id': 'LINKED', 'name': 'Linked company', 'bloomberg_ticker': 'LINKED SP'})
        source = {'id': 'linked_report', 'company_id': 'LINKED', 'publication_date': '2026-01-01',
                  'period': 'FY2025', 'title': 'Annual report', 'url': 'https://issuer.test/report.pdf',
                  'link_status': 'verified', 'storage_mode': 'url_only'}
        # Real source-library metadata may omit evidence/sha256 altogether,
        # while older saved records may explicitly contain evidence: null.
        self.app.put('sources', source)
        self.app.put('sources', {**source, 'id': 'unreadable_report',
                                'url': 'https://issuer.test/unreadable.pdf', 'evidence': None})
        self.app.put('sources', {**source, 'id': 'future_report',
                                'url': 'https://issuer.test/future.pdf', 'publication_date': '2099-01-01'})
        self.app.put('portfolio_imports', {'id': 'linked_portfolio', 'theme_id': self.theme['id'],
                                         'rows': [{'company_id': 'LINKED'}]})
        quote = 'Dedicated AI chips revenue USD 50 million.'
        text = 'Page 12\nConsolidated external revenue USD 100 million. ' + quote + ' Unrelated services revenue USD 50 million.'

        def read_document(url, timeout):
            if 'unreadable' in url:
                raise ValueError('Document site disallows automated access')
            self.assertEqual(url, source['url'])
            return text

        def llm(task, payload, run, **kwargs):
            if 'total_revenue' not in payload.get('facts', {}):
                evidence_id = next(passage['evidence_id'] for passage in payload['passages']
                                   if passage['source_id'] == 'linked_report')
                return {'total_revenue': 100, 'denominator_evidence_ids': [evidence_id],
                        'period': 'FY2025', 'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [],
                        'units': [{'unit_id': uid, 'activity': activity, 'revenue': 50, 'period': 'FY2025',
                                   'currency': 'USD', 'scale': 'millions', 'external': True,
                                   'evidence_ids': [evidence_id], 'overlap_group': None}
                                  for uid, activity in [('chips', 'Dedicated AI chips'), ('other', 'Unrelated services')]]}
            evidence_id = payload['facts']['denominator_evidence_ids'][0]
            return {'confidence_band': 'high', 'confidence_percent': 87,
                    'confidence_basis': 'Cited product revenue and unrelated services',
                    'classifications': [
                        {'unit_id': 'chips', 'node_ids': ['compute_fab'], 'relationship_type': 'direct',
                         'rationale': 'Source identifies dedicated AI chip revenue', 'scope_check': '',
                         'evidence_ids': [evidence_id], 'unresolved_questions': [],
                         'whole_unit_supported': True, 'revenue_attribution_quote': quote},
                        {'unit_id': 'other', 'node_ids': [], 'relationship_type': 'excluded',
                         'rationale': 'Services are unrelated to AI infrastructure', 'scope_check': 'Unrelated services outside theme',
                         'evidence_ids': [evidence_id], 'unresolved_questions': [], 'whole_unit_supported': False},
                    ]}

        with patch('financial_research._read_document', side_effect=read_document) as read, \
                patch.object(self.app, 'llm', side_effect=llm) as model:
            batch = self.app.analyze_all({'theme_id': self.theme['id'], 'portfolio_id': 'linked_portfolio',
                                         'company_ids': ['LINKED']})
        self.assertEqual(batch['errors'], [])
        self.assertEqual(read.call_count, 2)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(batch['calculated_count'], 1)
        result = batch['results'][0]
        self.assertEqual(result['company_id'], 'LINKED')
        self.assertEqual(result['case']['exposure_percent'], 50)
        self.assertEqual(result['case']['confidence_percent'], 87)
        self.assertEqual(self.app.get('cases', result['case_id'])['estimate']['point'], .5)
        self.assertTrue(self.app.get('sources', 'linked_report')['evidence'])
        self.assertEqual(result['source_read_errors'][0]['source_id'], 'unreadable_report')
        self.assertEqual(len([case for case in self.app.all_records('cases') if case['company_id'] == 'LINKED']), 1)

    def test_evidence_lookup_accepts_missing_and_null_passages_and_keeps_cutoff(self):
        self.app.company_create({'id': 'NO_TEXT', 'name': 'No text company', 'bloomberg_ticker': 'NO_TEXT SP'})
        source = {'id': 'missing_evidence', 'company_id': 'NO_TEXT', 'publication_date': '2026-01-01'}
        self.app.put('sources', source)
        self.app.put('sources', {**source, 'id': 'null_evidence', 'evidence': None})
        self.app.put('sources', {**source, 'id': 'future_evidence', 'publication_date': '2099-01-01'})
        sources, valid = self.app.evidence_for('NO_TEXT', self.theme['id'])
        self.assertEqual({row['id'] for row in sources}, {'missing_evidence', 'null_evidence'})
        self.assertEqual(valid, set())

    def test_full_match_with_an_invented_quote_is_rejected(self):
        classification = {'relationship_type': 'direct', 'whole_unit_supported': True,
                          'evidence_ids': ['e1'], 'revenue_attribution_quote': 'Invented revenue quote'}
        with self.assertRaisesRegex(ValueError, 'supporting quote'):
            self.app._classification_allocation(classification, [{'evidence_id': 'e1', 'passage': 'Actual disclosure'}])

    def test_analysis_corrects_a_reformatted_quote_once_before_saving(self):
        source = next(s for s in self.app.all_records('sources') if s['company_id'] == 'DEMO_A')
        evidence = source['evidence'][0]['evidence_id']
        facts = {'total_revenue': 100, 'denominator_evidence_ids': [evidence], 'period': 'FY2025',
                 'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [
            {'unit_id': uid, 'activity': uid, 'revenue': 50, 'period': 'FY2025', 'currency': 'USD',
             'scale': 'millions', 'external': True, 'evidence_ids': [evidence], 'overlap_group': None}
            for uid in ('hardware', 'unrelated')]}
        bad = {'confidence_band': 'high', 'confidence_percent': 90, 'confidence_basis': 'Disclosed revenue', 'classifications': [
            {'unit_id': 'hardware', 'node_ids': ['compute_fab'], 'relationship_type': 'direct', 'rationale': 'AI hardware',
             'scope_check': '', 'evidence_ids': [evidence], 'unresolved_questions': [], 'whole_unit_supported': True,
             'revenue_attribution_quote': 'Reformatted revenue sentence not in the source'},
            {'unit_id': 'unrelated', 'node_ids': [], 'relationship_type': 'excluded', 'rationale': 'Consumer services',
             'scope_check': 'Unrelated consumer services are outside the theme', 'evidence_ids': [evidence],
             'unresolved_questions': [], 'whole_unit_supported': False, 'revenue_attribution_quote': ''},
        ]}
        corrected = json.loads(json.dumps(bad))
        corrected['classifications'][0]['revenue_attribution_quote'] = source['evidence'][0]['passage']
        with patch.object(self.app, 'llm', side_effect=[facts, bad, corrected]) as model:
            result = self.app.analyze({'theme_id': self.theme['id'], 'company_id': 'DEMO_A', 'evidence_ids': [evidence]})
        self.assertEqual(model.call_count, 3)
        self.assertAlmostEqual(result['estimate']['point'], .5)
        self.assertTrue(any(row['stage'] == 'classification_repair' for row in self.app.all_records('model_outputs')))

    def test_malformed_extraction_repair_retries_fresh_extraction(self):
        source = next(s for s in self.app.all_records('sources') if s['company_id'] == 'DEMO_A')
        evidence = source['evidence'][0]['evidence_id']
        rejected = {'total_revenue': 100, 'denominator_evidence_ids': [evidence], 'period': 'FY2025',
                    'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [
            {'unit_id': 'invalid', 'activity': 'Invalid total', 'revenue': 120, 'period': 'FY2025',
             'currency': 'USD', 'scale': 'millions', 'external': True, 'evidence_ids': [evidence],
             'overlap_group': None}]}
        malformed_repair = {'total_revenue': 100, 'period': 'FY2025'}
        retried = {'total_revenue': 100, 'denominator_evidence_ids': [evidence], 'period': 'FY2025',
                   'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [
            {'unit_id': 'supported', 'activity': 'Supported AI hardware', 'revenue': 50, 'period': 'FY2025',
             'currency': 'USD', 'scale': 'millions', 'external': True, 'evidence_ids': [evidence],
             'overlap_group': None}]}
        classification = {'classifications': [
            {'unit_id': 'supported', 'node_ids': ['compute_fab'], 'relationship_type': 'direct',
             'rationale': 'Supported hardware', 'scope_check': 'Within the approved theme',
             'evidence_ids': [evidence], 'unresolved_questions': [], 'whole_unit_supported': True,
             'revenue_attribution_quote': source['evidence'][0]['passage']}],
            'confidence_band': 'medium', 'confidence_percent': None,
            'confidence_basis': 'One cited document supports the revenue unit.'}
        with patch.object(self.app, 'llm', side_effect=[rejected, malformed_repair, retried, classification]) as model:
            result = self.app.analyze({'theme_id': self.theme['id'], 'company_id': 'DEMO_A',
                                       'evidence_ids': [evidence]})
        self.assertEqual(model.call_count, 4)
        self.assertAlmostEqual(result['estimate']['point'], .5)
        self.assertEqual(result['estimate']['calculation_status'], 'partial')
        self.assertTrue(any(row['stage'] == 'extraction_retry' for row in self.app.all_records('model_outputs')))

    def test_extraction_validation_rejects_internal_sales_before_classification(self):
        source = next(s for s in self.app.all_records('sources') if s['company_id'] == 'DEMO_A')
        evidence = source['evidence'][0]['evidence_id']
        facts = {'total_revenue': 100, 'denominator_evidence_ids': [evidence], 'period': 'FY2025',
                 'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [
            {'unit_id': 'sales', 'activity': 'sales', 'revenue': 100, 'period': 'FY2025',
             'currency': 'USD', 'scale': 'millions', 'external': False, 'evidence_ids': [evidence]}]}
        with self.assertRaisesRegex(ValueError, 'intersegment'):
            self.app._validate_extracted_revenue(facts, self.theme['nodes'], {evidence})
        self.assertNotIn('relationship_type', facts['units'][0])
        facts['units'][0]['external'] = True
        self.app._validate_extracted_revenue(facts, self.theme['nodes'], {evidence})

    def test_portfolio_import_and_snapshot_creation(self):
        rows = [
            {'company_name': 'TSMC', 'bloomberg_ticker': '2330 TT', 'sedol': '6889106',
             'country': 'TW', 'sector': 'Technology', 'portfolio_wt': '20%', 'reference_wt': '10%'},
            {'company_name': 'Samsung Electronics', 'bloomberg_ticker': '005930 KS', 'sedol': '6771720',
             'country': 'KR', 'sector': 'Technology', 'portfolio_wt': '80%', 'reference_wt': '90%'}
        ]
        res = self.app.dispatch('/api/portfolio/import', {
            'name': 'Schroders Test Portfolio',
            'holdings_date': '2026-10-03',
            'benchmark_name': 'MSCI Asia ex Japan',
            'rows': rows
        })
        self.assertIn('import', res)
        self.assertIn('snapshot', res)
        self.assertNotIn('theme_id', res['import'])
        self.assertEqual(res['companies_matched'], 2)
        # Verify snapshot exists and feeds dashboard
        snapshot_id = res['snapshot']['id']
        dash = self.app.dashboard(self.theme['id'], snapshot_id)
        self.assertIsNotNone(dash)
        self.assertIn('summary', dash)
        self.assertIn('portfolio', dash['summary'])
        self.assertEqual(len(self.app.all_records('portfolio_imports')), 1)

    def test_portfolio_import_batches_company_lookup_and_writes(self):
        rows = [
            {'company_name': f'Company {index}', 'bloomberg_ticker': f'{index:04d} TT',
             'sedol': f'SEDOL{index:05d}', 'country': 'Taiwan',
             'portfolio_wt': 1 / 80, 'reference_wt': 1 / 80}
            for index in range(80)
        ]
        original_all_records = self.app.all_records
        company_reads = 0

        def count_company_reads(kind):
            nonlocal company_reads
            if kind == 'companies':
                company_reads += 1
            return original_all_records(kind)

        with patch.object(self.app, 'all_records', side_effect=count_company_reads), \
                patch.object(self.app, 'put_many', wraps=self.app.put_many) as put_many:
            result = self.app.portfolio_import({
                'name': 'Large portfolio', 'holdings_date': '2026-10-03', 'rows': rows
            })

        self.assertEqual(company_reads, 1)
        put_many.assert_called_once()
        self.assertEqual(len(put_many.call_args.args[1]), 80)
        self.assertEqual(len(result['companies']), 80)

    def test_portfolio_import_saves_uploaded_excel_to_storage_and_record(self):
        rows = [{
            'company_name': 'TSMC', 'bloomberg_ticker': '2330 TT',
            'portfolio_wt': 1, 'reference_wt': 1,
        }]
        file_bytes = b'workbook bytes'
        with patch.object(self.app.supabase_store, 'upload_portfolio_file') as upload:
            result = self.app.dispatch('/api/portfolio/import', {
                'name': 'Excel Portfolio',
                'holdings_date': '2026-10-03',
                'rows': rows,
                'file_name': 'holdings.xlsx',
                'file_content': base64.b64encode(file_bytes).decode('ascii'),
            })
        record = result['import']
        upload.assert_called_once_with(
            record['file_storage_path'], file_bytes,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertRegex(record['file_storage_path'], r'^portfolios/import_[0-9a-f]+\.xlsx$')
        self.assertEqual(record['file_name'], 'holdings.xlsx')
        self.assertEqual(record['file_size'], len(file_bytes))
        self.assertEqual(record['file_sha256'], hashlib.sha256(file_bytes).hexdigest())
        self.assertEqual(len(self.app.all_records('portfolio_imports')), 1)

    def test_taxonomy_approval_requires_leaves(self):
        # Tier-1 node without any Tier-2 children fails approval
        orphan_node = [{'node_id': 'alone', 'parent_id': None, 'name': 'Alone',
                        'definition': 'No children', 'examples': []}]
        import engine
        with self.assertRaisesRegex(ValueError, 'Each Tier-1 node must have at least one Tier-2 subtheme'):
            engine.taxonomy(orphan_node, require_leaves=True)

if __name__ == '__main__':
    unittest.main()
