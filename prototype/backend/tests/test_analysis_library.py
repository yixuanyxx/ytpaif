import copy
import unittest

import analysis_library
import engine


class AnalysisLibraryTest(unittest.TestCase):
    def setUp(self):
        self.theme = {'id': 'theme', 'theme_name': 'Energy', 'reporting_period': 'FY2025',
                      'updated_at': '2026-01-01', 'nodes': [{'node_id': 'solar', 'name': 'Solar',
                      'definition': 'Solar', 'examples': [], 'children': []}]}
        self.case = {'id': 'saved', 'company_id': 'company', 'theme_id': 'theme',
                     'analysis_scope_key': analysis_library.scope_key(self.theme),
                     'review_status': 'needs_review', 'created_at': '2026-01-01',
                     'classification_confidence': 'high', 'confidence_percent': 88,
                     'case': {'period': 'FY2025'},
                     'estimate': {'point': .5, 'calculation_version': engine.VERSION}}

    def test_company_result_is_shared_but_scope_period_and_version_must_match(self):
        self.assertTrue(analysis_library.is_current(self.case, self.theme))
        changed = {**self.theme, 'updated_at': '2026-02-01'}
        self.assertTrue(analysis_library.is_current(self.case, changed))
        for field, value in [('reporting_period', 'FY2026'), ('theme_name', 'Different scope'), ('id', 'other_theme')]:
            with self.subTest(field=field):
                self.assertFalse(analysis_library.is_current(self.case, {**self.theme, field: value}))
        old = copy.deepcopy(self.case)
        old['estimate']['calculation_version'] = 'old'
        self.assertFalse(analysis_library.is_current(old, self.theme))

    def test_stale_version_remains_displayable_but_is_not_reusable(self):
        old = copy.deepcopy(self.case)
        old['estimate']['calculation_version'] = '1.2.2'
        displayed = analysis_library.display_results([old], [self.theme])
        self.assertEqual(len(displayed), 1)
        self.assertEqual(displayed[0]['exposure_percent'], 50)
        self.assertTrue(displayed[0]['calculation_stale'])
        self.assertFalse(displayed[0]['analysis_reusable'])
        self.assertEqual(analysis_library.current_results([old], [self.theme]), [])

    def test_latest_rejected_case_does_not_restore_an_old_answer(self):
        rejected = {**self.case, 'id': 'newer', 'review_status': 'rejected', 'created_at': '2026-02-01'}
        self.assertEqual(analysis_library.current_results([rejected, self.case], [self.theme]), [])

    def test_supported_zero_and_unknown_are_saved_distinctly(self):
        self.assertEqual(analysis_library.result_fields({'point': 0}, 0),
                         {'exposure_percent': 0, 'confidence_percent': 0})
        self.assertEqual(analysis_library.result_fields({'point': None}),
                         {'exposure_percent': None, 'confidence_percent': None})
        with self.assertRaises(ValueError):
            analysis_library.result_fields({'point': .5}, 101)

    def test_upper_bound_is_never_presented_as_a_comparable_exposure(self):
        self.assertEqual(analysis_library.result_fields({'point': None, 'upper': .59}),
                         {'exposure_percent': None, 'confidence_percent': None})
        self.assertEqual(analysis_library.result_fields(
            {'point': 0, 'upper': .59, 'calculation_status': 'undetermined'}),
            {'exposure_percent': None, 'confidence_percent': None})
        self.assertEqual(analysis_library.result_fields(
            {'point': 0, 'upper': .59, 'calculation_status': 'complete'}),
            {'exposure_percent': 0, 'confidence_percent': None})

    def test_new_sources_invalidate_saved_result_but_loading_text_does_not(self):
        source = {'id': 'report', 'company_id': 'company', 'period': 'FY2025',
                  'publication_date': '2026-01-01', 'url': 'https://issuer.test/report.pdf'}
        saved = {**self.case, 'source_signature': analysis_library.source_signature([source])}
        self.assertTrue(analysis_library.is_current(saved, self.theme, [{**source, 'evidence': ['loaded text']}]))
        added = {**source, 'id': 'new_report', 'period': 'FY2026'}
        self.assertFalse(analysis_library.is_current(saved, self.theme, [source, added]))
        self.assertTrue(analysis_library.is_current(saved, self.theme, []))

    def test_metadata_only_source_signature_survives_evidence_normalization(self):
        metadata = {'id': 'linked', 'reporting_period': 'FY2025', 'publication_date': '2026-01-01'}
        normalized = {**metadata, 'period': 'FY2025', 'sha256': None, 'evidence': []}
        self.assertEqual(analysis_library.source_signature([metadata]), analysis_library.source_signature([normalized]))


if __name__ == '__main__':
    unittest.main()
