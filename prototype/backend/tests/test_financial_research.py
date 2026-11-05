import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import financial_research


class FinancialConsensusTest(unittest.TestCase):
    def setUp(self):
        blocked = patch.dict(financial_research.gemini_fallback._blocked, {}, clear=True)
        blocked.start()
        self.addCleanup(blocked.stop)

    def test_financial_quota_falls_back_and_returns_supported_figures(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({
            'market_cap': [], 'net_revenue': [{'source_id': 'report', 'value': 'USD 20 million', 'year': '2026'}],
        })}]}]}
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test', 'FINANCIALS_LLM_MODEL': 'gemini-3.8-flash'}), \
                patch.object(financial_research, 'urlopen', side_effect=[
                    HTTPError('https://provider', 429, 'Quota', {}, None), io.BytesIO(json.dumps(output).encode())]) as send:
            result = financial_research.research_company({'name': 'Example'}, [{'id': 'report'}])
        self.assertEqual(result['model'], 'gemini-3.7-flash')
        self.assertEqual(result['net_revenue']['value'], 'USD 20 million')
        self.assertEqual(send.call_count, 2)

    def test_legacy_chat_endpoint_uses_grounded_interactions(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({
            'market_cap': [],
            'net_revenue': [{'source_id': 'report', 'value': 'USD 724.8 million', 'year': '2026'}],
        })}]}]}
        with patch.dict(os.environ, {
            'FINANCIALS_LLM_API_KEY': 'test-key',
            'FINANCIALS_LLM_ENDPOINT': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
        }), patch.object(financial_research, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
            result = financial_research.research_company(
                {'id': 'kpit', 'name': 'KPIT TECHNOLOGIES LTD'},
                [{'id': 'report', 'url': 'https://www.kpit.com/report'}],
            )
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, financial_research.DEFAULT_ENDPOINT)
        self.assertEqual(request.get_header('X-goog-api-key'), 'test-key')
        self.assertEqual(json.loads(request.data)['tools'][0]['type'], 'google_search')
        self.assertEqual(result['net_revenue']['value'], 'USD 724.8 million')
        self.assertIsNone(result['market_cap'])

    def test_lookup_reports_provider_status_without_exposing_response_body(self):
        for status in (400, 401, 403, 429, 503):
            with self.subTest(status=status), patch.object(financial_research, 'research_company', side_effect=HTTPError(
                    'https://provider.example', status, 'private provider details', {}, None)):
                result = financial_research.populate([{'id': 'kpit'}], {})['kpit']
            self.assertEqual(result['http_status'], status)
            self.assertIn(f'HTTP {status}', result['error'])
            self.assertNotIn('private provider details', result['error'])
            if status == 429:
                self.assertIn('quota', result['error'])

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
