import importlib
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_fixtures import MemoryStore
from test_source_cache import FakeRedis


class UnresolvedFallbackTest(unittest.TestCase):
    def setUp(self):
        budget_dir = tempfile.TemporaryDirectory()
        self.addCleanup(budget_dir.cleanup)
        budget_env = patch.dict(os.environ, {
            'GEMINI_BUDGET_DB': str(Path(budget_dir.name) / 'gemini-budget.sqlite'),
            'GEMINI_DAILY_CALL_CAP': '60',
        })
        budget_env.start()
        self.addCleanup(budget_env.stop)
        import app
        self.app = importlib.reload(app)
        self.app.store = MemoryStore()
        cache = patch.object(self.app.source_cache, '_connection', return_value=FakeRedis())
        cache.start()
        self.addCleanup(cache.stop)
        confidence = patch.object(self.app.source_confidence, 'claude_assess',
            return_value=({'light': 'yellow', 'basis': 'One credible source',
                           'agreeing_source_ids': [], 'divergent_source_ids': []}, 'claude-test'))
        confidence.start()
        self.addCleanup(confidence.stop)
        self.theme = self.app.theme_create({
            'theme_name': 'AI Infrastructure', 'reporting_period': 'FY2025', 'cutoff': '2026-11-08',
            'nodes': [{'node_id': 'compute', 'name': 'AI Computing', 'definition': 'Dedicated AI hardware',
                       'examples': [], 'children': [{'node_id': 'chips', 'name': 'AI Chips',
                           'definition': 'Dedicated AI chips', 'examples': ['AI accelerators'], 'children': []}]}],
        })
        self.company = self.app.company_create({'id': 'ACME', 'name': 'Acme Semiconductor',
                                                'bloomberg_ticker': 'ACME SP Equity'})
        self.base_passage = ('Consolidated external FY2025 revenue USD 100 million. '
                             'Mixed computing segment revenue USD 100 million. The AI share is undisclosed.')
        self.app.put('sources', {'id': 'base', 'company_id': 'ACME', 'source_type': 'annual_report',
                                 'publication_date': '2026-01-01', 'period': 'FY2025', 'sha256': 'base-hash',
                                 'evidence': [{'evidence_id': 'base-evidence', 'source_id': 'base',
                                               'page': 1, 'passage': self.base_passage}]})
        self.app.put('portfolio_imports', {'id': 'portfolio-one', 'theme_id': self.theme['id'],
                                            'rows': [{'company_id': 'ACME'}]})

    def _initial_outputs(self):
        facts = {'total_revenue': 100, 'denominator_evidence_ids': ['base-evidence'], 'period': 'FY2025',
                 'currency': 'USD', 'scale': 'millions', 'unresolved_questions': [], 'units': [
                     {'unit_id': 'mixed', 'activity': 'Mixed computing', 'revenue': 100,
                      'period': 'FY2025', 'currency': 'USD', 'scale': 'millions', 'external': True,
                      'evidence_ids': ['base-evidence'], 'overlap_group': None}]}
        classification = {'classifications': [{'unit_id': 'mixed', 'node_ids': ['chips'],
            'relationship_type': 'indirect', 'whole_unit_supported': False,
            'rationale': 'AI share is undisclosed', 'scope_check': 'Mixed AI and non-AI computing',
            'evidence_ids': ['base-evidence'], 'unresolved_questions': ['Exact AI revenue share is undisclosed']}],
            'confidence_band': 'medium', 'confidence_basis': 'Mixed segment'}
        return facts, classification

    def _request(self, portfolio='portfolio-one', **extra):
        return {'theme_id': self.theme['id'], 'portfolio_id': portfolio,
                'company_ids': ['ACME'], **extra}

    def test_row_analysis_searches_once_and_reuses_unresolved_case_without_more_gemini(self):
        import unresolved_web_search
        facts, classification = self._initial_outputs()
        with patch.object(self.app, 'llm', side_effect=[facts, classification]) as model, \
                patch.object(unresolved_web_search, 'discover', return_value={
                    'sources': [], 'queries': ['Acme Semiconductor ACME 2025 AI chips revenue'],
                    'diagnostics': {'provider': 'tavily_web_index', 'gemini_search_calls': 0}}) as discover:
            first = self.app.analyze_all(self._request())
        self.assertEqual(first['errors'], [])
        self.assertEqual(model.call_count, 2)
        discover.assert_called_once()
        row = first['results'][0]
        self.assertEqual(row['fallback_status'], 'not_found')
        self.assertTrue(row['fallback_search_attempted'])
        self.assertIsNone(row['case']['estimate']['point'])
        self.assertEqual(row['case']['fallback_web_search']['diagnostics']['gemini_search_calls'], 0)
        self.assertEqual(len([c for c in self.app.all_records('cases') if c['company_id'] == 'ACME']), 1)

        self.app.put('portfolio_imports', {'id': 'portfolio-two', 'theme_id': self.theme['id'],
                                            'rows': [{'company_id': 'ACME'}]})
        with patch.object(self.app, 'llm') as model, patch.object(unresolved_web_search, 'discover') as discover:
            second = self.app.analyze_all(self._request('portfolio-two'))
        model.assert_not_called()
        discover.assert_not_called()
        self.assertEqual(second['results'][0]['fallback_status'], 'already_searched')
        self.assertFalse(second['results'][0]['fallback_search_attempted'])
        self.assertEqual(second['results'][0]['case']['id'], row['case']['id'])

    def test_new_numeric_source_is_reanalysed_with_citations_and_point_estimate(self):
        import unresolved_web_search
        facts, classification = self._initial_outputs()
        with patch.object(self.app, 'llm', side_effect=[facts, classification]):
            initial = self.app.analyze_all(self._request(allow_unresolved_web_search=False))
        self.assertEqual(initial['errors'], [])
        self.assertEqual(initial['results'][0]['fallback_status'], 'deferred')
        self.assertIsNone(initial['results'][0]['case']['estimate']['point'])

        quote = 'Dedicated AI chip revenue USD 18 million.'
        web_source = {'id': 'web-numeric', 'company_id': 'ACME', 'source_type': 'earnings_call',
                      'title': 'Acme FY2025 earnings transcript', 'url': 'https://acme.example/transcript-2025',
                      'publication_date': '2026-02-01', 'period': 'FY2025', 'sha256': 'web-hash',
                      'link_status': 'verified', 'theme_numeric_evidence': True,
                      'search_provider': 'tavily_web_index',
                      'evidence': [{'evidence_id': 'web-evidence', 'source_id': 'web-numeric', 'page': None,
                                    'passage': f'FY2025 Acme revenue details. {quote} Unrelated product revenue USD 82 million.'}]}
        revised_facts = {'total_revenue': 100, 'denominator_evidence_ids': ['base-evidence'],
                         'period': 'FY2025', 'currency': 'USD', 'scale': 'millions',
                         'unresolved_questions': [], 'units': [
                             {'unit_id': 'ai', 'activity': 'Dedicated AI chips', 'revenue': 18,
                              'period': 'FY2025', 'currency': 'USD', 'scale': 'millions', 'external': True,
                              'evidence_ids': ['web-evidence'], 'overlap_group': None},
                             {'unit_id': 'other', 'activity': 'Unrelated products', 'revenue': 82,
                              'period': 'FY2025', 'currency': 'USD', 'scale': 'millions', 'external': True,
                              'evidence_ids': ['web-evidence'], 'overlap_group': None}]}
        revised_classification = {'classifications': [
            {'unit_id': 'ai', 'node_ids': ['chips'], 'relationship_type': 'direct',
             'rationale': 'Dedicated AI chip revenue is disclosed', 'scope_check': '',
             'evidence_ids': ['web-evidence'], 'unresolved_questions': [],
             'whole_unit_supported': True, 'revenue_attribution_quote': quote},
            {'unit_id': 'other', 'node_ids': [], 'relationship_type': 'excluded',
             'rationale': 'General products are outside AI infrastructure',
             'scope_check': 'Unrelated products are outside the AI chip scope',
             'evidence_ids': ['web-evidence'], 'unresolved_questions': [], 'whole_unit_supported': False},
        ], 'confidence_band': 'high', 'confidence_basis': 'Disclosed dedicated revenue'}
        with patch.object(unresolved_web_search, 'discover', return_value={
                'sources': [web_source], 'queries': ['Acme FY2025 AI chips revenue'],
                'diagnostics': {'provider': 'tavily_web_index', 'gemini_search_calls': 0}}) as discover, \
                patch.object(self.app, 'llm', side_effect=[revised_facts, revised_classification]) as model:
            second = self.app.analyze_all(self._request())
        self.assertEqual(second['errors'], [])
        self.assertEqual(model.call_count, 2)
        discover.assert_called_once()
        self.assertEqual(second['results'][0]['fallback_status'], 'resolved')
        self.assertTrue(second['results'][0]['fallback_search_attempted'])
        self.assertAlmostEqual(second['results'][0]['case']['estimate']['point'], .18)
        self.assertEqual(second['results'][0]['case']['exposure_percent'], 18)
        self.assertIn('web-evidence', [row['evidence_id'] for row in model.call_args_list[0].args[1]['passages']])
        self.assertEqual(self.app.get('sources', 'web-numeric')['search_provider'], 'tavily_web_index')

    def test_resolved_company_reuses_saved_result_without_web_search(self):
        import unresolved_web_search
        case = {'id': 'resolved', 'company_id': 'ACME', 'theme_id': self.theme['id'],
                'analysis_scope_key': self.app.analysis_library.scope_key(self.theme),
                'source_signature': self.app.analysis_library.source_signature(self.app.company_source_records('ACME')),
                'review_status': 'needs_review', 'created_at': self.app.now(),
                'case': {'period': 'FY2025'},
                'estimate': {'point': .18, 'calculation_version': self.app.engine.VERSION},
                'confidence_percent': 80}
        self.app.put('cases', case)
        with patch.object(self.app, 'llm') as model, patch.object(unresolved_web_search, 'discover') as discover:
            result = self.app.analyze_all(self._request())
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['results'][0]['fallback_status'], 'not_needed')
        self.assertFalse(result['results'][0]['fallback_search_attempted'])
        model.assert_not_called()
        discover.assert_not_called()

    def test_approximate_share_is_saved_without_another_gemini_call_or_exact_point(self):
        import unresolved_web_search
        facts, classification = self._initial_outputs()
        with patch.object(self.app, 'llm', side_effect=[facts, classification]):
            self.app.analyze_all(self._request(allow_unresolved_web_search=False))
        approximate = {'id': 'web-approx', 'company_id': 'ACME', 'source_type': 'earnings_call',
                       'title': 'FY2025 call', 'url': 'https://acme.example/approx',
                       'publication_date': '2026-02-01', 'period': 'FY2025',
                       'link_status': 'verified', 'theme_numeric_evidence': True,
                       'theme_numeric_evidence_kind': 'approximate_share',
                       'evidence': [{'evidence_id': 'approx-evidence', 'source_id': 'web-approx',
                                     'passage': 'AI accelerators were a high teens percent of total FY2025 revenue.'}]}
        with patch.object(unresolved_web_search, 'discover', return_value={
                'sources': [approximate], 'queries': ['Acme FY2025 AI revenue'],
                'diagnostics': {'provider': 'tavily_web_index', 'gemini_search_calls': 0}}), \
                patch.object(self.app, 'llm') as model:
            result = self.app.analyze_all(self._request())
        model.assert_not_called()
        self.assertEqual(result['results'][0]['fallback_status'], 'still_unresolved')
        self.assertIsNone(result['results'][0]['case']['estimate']['point'])
        self.assertEqual(self.app.get('sources', 'web-approx')['theme_numeric_evidence_kind'], 'approximate_share')

    def test_saved_web_evidence_waits_for_budget_then_analyses_without_searching_again(self):
        import unresolved_web_search
        facts, classification = self._initial_outputs()
        with patch.object(self.app, 'llm', side_effect=[facts, classification]):
            self.app.analyze_all(self._request(allow_unresolved_web_search=False))
        numeric = {'id': 'web-saved', 'company_id': 'ACME', 'source_type': 'earnings_call',
                   'title': 'FY2025 call', 'url': 'https://acme.example/2025',
                   'publication_date': '2026-02-01', 'period': 'FY2025',
                   'link_status': 'verified', 'theme_numeric_evidence': True,
                   'theme_numeric_evidence_kind': 'reported_share',
                   'evidence': [{'evidence_id': 'web-saved-evidence', 'source_id': 'web-saved',
                                 'passage': 'Dedicated AI chip revenue was 18 percent of FY2025 total revenue.'}]}
        with patch.object(unresolved_web_search, 'discover', return_value={
                'sources': [numeric], 'queries': ['Acme FY2025 AI revenue'], 'diagnostics': {}}) as discover, \
                patch.object(self.app, '_fallback_analysis_budget_available', return_value=False), \
                patch.object(self.app, 'llm') as model:
            first = self.app.analyze_all(self._request())
        discover.assert_called_once()
        model.assert_not_called()
        self.assertEqual(first['results'][0]['fallback_status'], 'analysis_deferred')
        self.assertEqual(self.app.get('sources', 'web-saved')['id'], 'web-saved')

        saved_case = first['results'][0]['case']
        resolved = {**saved_case, 'id': 'resolved-after-budget',
                    'estimate': {**saved_case['estimate'], 'point': .18},
                    'exposure_percent': 18}
        metadata = {**saved_case['fallback_web_search'], 'status': 'resolved',
                    'message': 'New cited evidence produced an exposure estimate.'}
        with patch.object(self.app, '_fallback_analysis_budget_available', return_value=True), \
                patch.object(self.app, '_analyse_found_web_sources', return_value=(resolved, metadata)) as analyse, \
                patch.object(unresolved_web_search, 'discover') as discover:
            second = self.app.analyze_all(self._request())
        analyse.assert_called_once()
        discover.assert_not_called()
        self.assertEqual(second['results'][0]['fallback_status'], 'resolved')
        self.assertFalse(second['results'][0]['fallback_search_attempted'])
        self.assertAlmostEqual(second['results'][0]['case']['estimate']['point'], .18)

    def test_direct_batch_request_limits_web_searches_and_defers_other_unresolved_companies(self):
        import unresolved_web_search
        facts, classification = self._initial_outputs()
        with patch.object(self.app, 'llm', side_effect=[facts, classification]):
            initial = self.app.analyze_all(self._request(allow_unresolved_web_search=False))
        original = initial['results'][0]['case']
        for company_id in ('BETA', 'GAMMA'):
            self.app.company_create({'id': company_id, 'name': company_id + ' Semiconductor',
                                     'bloomberg_ticker': company_id + ' SP Equity'})
            source = {**self.app.get('sources', 'base'), 'id': 'base-' + company_id,
                      'company_id': company_id, 'evidence': [{
                          'evidence_id': 'base-evidence-' + company_id,
                          'source_id': 'base-' + company_id, 'page': 1, 'passage': self.base_passage}]}
            self.app.put('sources', source)
            clone = copy.deepcopy(original)
            clone.update(id='case-' + company_id, company_id=company_id,
                         source_signature=self.app.analysis_library.source_signature([source]),
                         created_at=self.app.now())
            clone.pop('fallback_web_search', None)
            self.app.put('cases', clone)
        portfolio = self.app.get('portfolio_imports', 'portfolio-one')
        portfolio['rows'].extend([{'company_id': 'BETA'}, {'company_id': 'GAMMA'}])
        self.app.put('portfolio_imports', portfolio)
        with patch.dict(os.environ, {'UNRESOLVED_SEARCH_BATCH_LIMIT': '2'}), \
                patch.object(unresolved_web_search, 'discover', return_value={
                    'sources': [], 'queries': [], 'diagnostics': {}}) as discover, \
                patch.object(self.app, 'llm') as model:
            batch = self.app.analyze_all({'theme_id': self.theme['id'], 'portfolio_id': 'portfolio-one'})
        model.assert_not_called()
        self.assertEqual(batch['errors'], [])
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(sorted(row['fallback_status'] for row in batch['results']),
                         ['deferred', 'not_found', 'not_found'])


if __name__ == '__main__':
    unittest.main()
