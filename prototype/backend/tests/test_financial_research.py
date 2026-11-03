import unittest

import financial_research


class FinancialConsensusTest(unittest.TestCase):
    def test_latest_year_then_majority_value_wins(self):
        rows = [
            {'source_id': 'a', 'value': 'USD 50 million', 'year': '2026'},
            {'source_id': 'b', 'value': 'USD 50 million', 'year': '2026'},
            {'source_id': 'c', 'value': 'USD 20 million', 'year': '2026'},
            {'source_id': 'older', 'value': 'USD 99 million', 'year': '2025'},
        ]
        self.assertEqual(financial_research.consensus(rows), {
            'value': 'USD 50 million', 'year': '2026',
            'supporting_sources': 2, 'evaluated_sources': 3,
        })

    def test_no_candidates_has_no_consensus(self):
        self.assertIsNone(financial_research.consensus([]))
