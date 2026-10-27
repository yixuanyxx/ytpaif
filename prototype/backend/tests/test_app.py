import base64
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from test_fixtures import demo

class AppFlowTest(unittest.TestCase):
    def setUp(self):
        self.audit_sync_mock = patch('audit_store.sync_once', return_value={'configured': True, 'sent': 3})
        self.audit_sync_mock.start()
        self.addCleanup(self.audit_sync_mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        os.environ['THEME_DATA_DIR'] = self.temp.name
        # Add prototype to sys.path so app is imported from prototype
        import sys
        proto_dir = str(Path(__file__).resolve().parent)
        if proto_dir not in sys.path:
            sys.path.insert(0, proto_dir)
        import app
        self.app = importlib.reload(app)
        self.theme = demo(self.app)

    def tearDown(self):
        self.temp.cleanup()

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

    def test_generate_handles_counts_gracefully(self):
        # Non-int or 'auto'
        fake_nodes = [
            {'node_id': 't1', 'parent_id': None, 'name': 'T1', 'definition': 'D1', 'examples': []},
            {'node_id': 't2', 'parent_id': 't1', 'name': 'T2', 'definition': 'D2', 'examples': []}
        ]
        with patch.object(self.app, 'llm', return_value={'nodes': fake_nodes}):
            res = self.app.generate({'tier1_count': 'auto', 'tier2_count': 'auto',
                                     'theme_name': 'Test', 'included_activities': 'Act'})
            self.assertEqual(len(res['nodes']), 2)

    def test_failed_model_does_not_create_zero_case(self):
        evidence = self.app.all_records('sources')[0]['evidence'][0]['evidence_id']
        before = len(self.app.all_records('cases'))
        with patch.dict(os.environ, {'LLM_API_KEY': '', 'LLM_MODEL': ''}):
            with self.assertRaises(ValueError):
                self.app.analyze({'theme_id': self.theme['id'],
                                  'company_id': 'DEMO_A', 'evidence_ids': [evidence]})
        self.assertEqual(before, len(self.app.all_records('cases')))
        self.assertEqual(self.app.all_records('runs')[-1]['status'], 'failed')

    def test_provider_call_cap(self):
        with patch.dict(os.environ, {'LLM_API_KEY': 'test', 'LLM_MODEL': 'test', 'LLM_MAX_CALLS': '0'}):
            with self.assertRaisesRegex(ValueError, 'cap'):
                self.app.llm('test', {}, {'usage': []})

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

    def test_evaluate_endpoint(self):
        sample_rows = [{
            'actual': 'hardware', 'predicted': 'hardware',
            'confidence_band': 'high', 'point': 0.5, 'reference_point': 0.5,
            'lower': 0.4, 'upper': 0.6, 'reference_lower': 0.4, 'reference_upper': 0.6
        }]
        res = self.app.dispatch('/api/evaluate', {'examples': sample_rows})
        self.assertIn('answered_accuracy', res)
        self.assertIn('exposure_mean_absolute_error_pp', res)
        self.assertEqual(res['answered_accuracy'], 1.0)
        self.assertEqual(res['exposure_mean_absolute_error_pp'], 0.0)
        # Empty set raises clear error
        with self.assertRaises(ValueError):
            self.app.dispatch('/api/evaluate', {'examples': []})

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
        self.assertEqual(res['companies_matched'], 2)
        # Verify snapshot exists and feeds dashboard
        snapshot_id = res['snapshot']['id']
        dash = self.app.dashboard(self.theme['id'], snapshot_id)
        self.assertIsNotNone(dash)
        self.assertIn('summary', dash)
        self.assertIn('portfolio', dash['summary'])
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
