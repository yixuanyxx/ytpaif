import base64
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import zipfile
from urllib.error import HTTPError
from test_fixtures import MemoryStore, demo

class AppFlowTest(unittest.TestCase):
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
        self.assertEqual(res['analysed_count'], 1)
        self.assertEqual({e['company_id'] for e in res['errors']}, {'READY_B', 'TOO_LONG'})

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
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': '', 'LLM_MODEL': ''}):
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
        self.assertIsNone(c['estimate']['point'])
        self.assertEqual(c['review_status'], 'needs_review')
        self.assertEqual(c['classification_score'], 3)
        self.assertEqual(self.app.get('runs', c['run_id'])['status'], 'needs_review')
        self.assertEqual(len(self.app.all_records('model_outputs')), 2)

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
