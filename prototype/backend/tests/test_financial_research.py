import io
import json
import os
import unittest
from email.message import Message
from types import SimpleNamespace
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
            'market_cap': [], 'net_revenue': [{'source_id': 'report', 'value': 'USD 20 million', 'year': '2026',
                                             'quote': 'Consolidated revenue USD 20 million in 2026.'}],
        })}]}]}
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test', 'FINANCIALS_LLM_MODEL': 'gemini-3.8-flash'}), \
                patch.object(financial_research, 'urlopen', side_effect=[
                    HTTPError('https://provider', 429, 'Quota', {}, None), io.BytesIO(json.dumps(output).encode())]) as send:
            result = financial_research.research_company({'name': 'Example'}, [{'id': 'report', 'evidence': [
                {'page': 1, 'passage': 'Consolidated revenue USD 20 million in 2026.'}]}])
        self.assertEqual(result['model'], 'gemini-3.7-flash')
        self.assertEqual(result['net_revenue']['value'], 'USD 20 million')
        self.assertEqual(send.call_count, 2)

    def test_legacy_chat_endpoint_uses_document_text_without_search_tools(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({
            'market_cap': [],
            'net_revenue': [{'source_id': 'report', 'value': 'USD 724.8 million', 'year': '2026',
                             'quote': 'Consolidated revenue USD 724.8 million in 2026.'}],
        })}]}]}
        with patch.dict(os.environ, {
            'FINANCIALS_LLM_API_KEY': 'test-key',
            'FINANCIALS_LLM_ENDPOINT': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
        }), patch.object(financial_research, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
            result = financial_research.research_company(
                {'id': 'kpit', 'name': 'KPIT TECHNOLOGIES LTD'},
                [{'id': 'report', 'url': 'https://www.kpit.com/report', 'evidence': [
                    {'page': 1, 'passage': 'Consolidated revenue USD 724.8 million in 2026.'}]}],
            )
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, financial_research.DEFAULT_ENDPOINT)
        self.assertEqual(request.get_header('X-goog-api-key'), 'test-key')
        self.assertNotIn('tools', json.loads(request.data))
        self.assertEqual(result['net_revenue']['value'], 'USD 724.8 million')
        self.assertIsNone(result['market_cap'])

    def test_linked_report_is_read_before_ai_and_unsupported_quote_is_rejected(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({
            'market_cap': [{'source_id': 'report', 'value': 'USD 50 million', 'year': '2026', 'quote': 'Invented quotation'}],
            'net_revenue': [{'source_id': 'report', 'value': 'USD 20 million', 'year': '2026', 'quote': 'Revenue USD 20 million.'}],
        })}]}]}
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test'}), \
                patch.object(financial_research, '_read_document', return_value='Revenue USD 20 million.') as read, \
                patch.object(financial_research, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
            result = financial_research.research_company({'name': 'Example'}, [{'id': 'report', 'url': 'https://issuer.test/report.pdf'}])
        read.assert_called_once()
        self.assertEqual(result['net_revenue']['value'], 'USD 20 million')
        self.assertIsNone(result['market_cap'])
        self.assertNotIn('tools', json.loads(send.call_args.args[0].data))

    def test_unreadable_reports_skip_ai_request(self):
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'test'}), \
                patch.object(financial_research, '_read_document', side_effect=ValueError('Unreadable report')), \
                patch.object(financial_research, 'urlopen') as send:
            result = financial_research.research_company({'name': 'Example'}, [{'id': 'report', 'url': 'https://issuer.test/report.pdf'}])
        self.assertIn('No readable text', result['error'])
        send.assert_not_called()

    def test_pdf_viewer_reads_financial_pages_without_sending_ai_credentials(self):
        viewer = io.BytesIO(b'<html><iframe src="/report.pdf"></iframe></html>')
        viewer.headers = Message()
        report = io.BytesIO(b'%PDF-mocked-report')
        report.headers = Message()
        pages = [SimpleNamespace(extract_text=lambda: 'Example Industries Annual Report FY2026'),
                 SimpleNamespace(extract_text=lambda: 'Consolidated statement of income. Revenue USD 20 million.')]
        with patch('source_discovery.public_url', side_effect=lambda url: url), \
                patch('approved_collector.robots_permits', return_value=True), \
                patch('pypdf.PdfReader', return_value=SimpleNamespace(pages=pages)), \
                patch.object(financial_research, 'build_opener') as build:
            build.return_value.open.side_effect = [viewer, report]
            text = financial_research._read_document('https://issuer.test/annual-report', 10)
        self.assertIn('Revenue USD 20 million.', text)
        self.assertIn('Page 2', text)
        requests = build.return_value.open.call_args_list
        self.assertEqual(requests[1].args[0].full_url, 'https://issuer.test/report.pdf')
        for call in requests:
            self.assertIsNone(call.args[0].get_header('X-goog-api-key'))
            self.assertIsNone(call.args[0].get_header('Authorization'))

    def test_private_document_url_is_rejected_before_reading(self):
        with patch('source_discovery.public_url', side_effect=ValueError('Private address')), \
                patch.object(financial_research, 'build_opener') as build:
            with self.assertRaisesRegex(ValueError, 'Private address'):
                financial_research._read_document('https://127.0.0.1/report.pdf', 10)
        build.assert_not_called()

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
