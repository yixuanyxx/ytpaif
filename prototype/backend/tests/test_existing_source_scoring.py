import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import score_existing_sources as scoring


class ExistingSourceScoringTest(unittest.TestCase):
    def setUp(self):
        budget_dir = tempfile.TemporaryDirectory()
        self.addCleanup(budget_dir.cleanup)
        budget_env = patch.dict(os.environ, {
            'GEMINI_BUDGET_DB': str(Path(budget_dir.name) / 'gemini-budget.sqlite3'),
        })
        budget_env.start()
        self.addCleanup(budget_env.stop)

    def test_demo_name_filter_includes_underscored_test_portfolios(self):
        self.assertTrue(scoring.DEMO_PORTFOLIO_NAME.search('Demo_Portfolio_84_Stocks_Test_A'))
        self.assertTrue(scoring.DEMO_PORTFOLIO_NAME.search('Example of 80 stock portfolio'))
        self.assertFalse(scoring.DEMO_PORTFOLIO_NAME.search('iShares MSCI All Country Asia ex Japan ETF'))

    def test_preserves_actual_low_scores_and_marks_metadata_only(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({'scores': [
            {'index': 0, 'score': 48, 'reason': 'Insufficient supporting detail'},
            {'index': 1, 'score': 83, 'reason': 'Document passage matches'},
        ]})}]}]}
        records = [{'title': 'Annual report', 'company_name': 'Example'},
                   {'title': 'Report', 'evidence': [{'passage': 'Report excerpt'}]}]
        with patch.dict(scoring.os.environ, {'FINANCIALS_LLM_API_KEY': 'test'}), \
             patch.object(scoring, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())):
            result = scoring.assess(records)
        self.assertEqual(result[0]['ai_confidence'], 48)
        self.assertEqual(result[0]['ai_confidence_basis'], 'metadata only')
        self.assertEqual(result[1]['ai_confidence_basis'], 'document excerpts and metadata')
