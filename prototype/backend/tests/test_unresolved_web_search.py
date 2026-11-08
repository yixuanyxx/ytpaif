import io
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import unresolved_web_search as search
import web_source_index


class UnresolvedWebSearchTest(unittest.TestCase):
    def setUp(self):
        self.company = {'id': 'TSMC', 'name': 'Taiwan Semiconductor Manufacturing',
                        'bloomberg_ticker': '2330 TT Equity',
                        'website': 'https://investor.tsmc.com'}
        self.theme = {'id': 'ai', 'theme_name': 'AI Infrastructure',
                      'reporting_period': 'FY2025', 'cutoff': '2026-11-08',
                      'nodes': [{'node_id': 'compute', 'name': 'AI computing',
                                 'definition': 'Computing for AI', 'examples': [],
                                 'children': [{'node_id': 'accelerators', 'name': 'AI accelerators',
                                               'definition': 'Specialized AI chips',
                                               'keywords': ['GPU'], 'examples': ['AI chips'],
                                               'children': []}]}]}
        self.url = 'https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2025-earnings-transcript.html'
        self.hit = {'url': self.url, 'title': 'TSMC FY2025 earnings transcript',
                    'published_date': '2026-01-15', 'score': .9}
        self.document = ('TSMC FY2025 earnings transcript. January 15, 2026. '
                         'AI accelerator revenue represented 18 percent of our total revenue in 2025.')
        self.records = {}

        def put(kind, record):
            self.records[(kind, record['id'])] = record
            return record

        self.app = SimpleNamespace(find=lambda kind, key: self.records.get((kind, key)), put=put,
                                   now=lambda: datetime.now(timezone.utc).isoformat())

    def _patch_sources(self, hits=None, document=None):
        return (patch.object(search.source_discovery, 'resolve_issuer_website', return_value=(self.company['website'], 'saved')),
                patch.object(search.source_discovery, 'approved_domains', return_value=['investor.tsmc.com']),
                patch.object(search, 'exchange_domains', return_value=[]),
                patch.object(search.web_source_index, 'search', return_value=hits if hits is not None else [self.hit]),
                patch.object(search.source_discovery, 'specific_document_url', return_value=True),
                patch.object(search.source_discovery, 'verify_document_link',
                             side_effect=lambda row, **kwargs: (row['url'], 'text/html')),
                patch.object(search.financial_research, '_read_document',
                             return_value=self.document if document is None else document))

    def test_queries_use_company_ticker_reporting_year_and_nested_leaf_without_gemini(self):
        patches = self._patch_sources()
        with patches[0], patches[1], patches[2], patches[3] as index, patches[4], patches[5], patches[6] as read, \
                patch.object(search.source_discovery, 'grounded_search') as gemini_grounding:
            result = search.discover(self.app, self.company, self.theme)
            cached = search.discover(self.app, self.company, self.theme)
        gemini_grounding.assert_not_called()
        self.assertEqual(index.call_count, 2, 'Only two bounded ordinary web queries should run')
        self.assertEqual(read.call_count, 1, 'Duplicate URLs should be read only once')
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['diagnostics']['gemini_search_calls'], 0)
        self.assertEqual(result['sources'][0]['search_provider'], 'tavily_web_index')
        self.assertTrue(result['sources'][0]['theme_numeric_evidence'])
        self.assertIn('18 percent', result['sources'][0]['evidence'][0]['passage'])
        self.assertEqual(result['sources'][0]['period'], '2025')
        self.assertEqual(result['sources'][0]['publication_date'], '2026-01-15')
        self.assertTrue(cached['diagnostics']['cache_hit'])
        self.assertEqual(cached['sources'][0]['id'], result['sources'][0]['id'])
        queries = ' '.join(result['queries'])
        for fragment in ('Taiwan Semiconductor Manufacturing', '2330', '2025', 'AI accelerators'):
            self.assertIn(fragment, queries)
        self.assertTrue(all(call.kwargs['max_results'] <= 5 for call in index.call_args_list))

    def test_growth_only_or_missing_year_is_not_revenue_evidence(self):
        for document, hits in (
            ('TSMC FY2025 transcript. January 15, 2026. AI accelerator revenue grew 18 percent year over year.',
             [self.hit]),
            ('TSMC earnings transcript. January 15, 2026. AI accelerator revenue represented 18 percent of our total revenue.',
             [{**self.hit, 'title': 'TSMC earnings transcript'}]),
        ):
            with self.subTest(document=document):
                self.records.clear()
                patches = self._patch_sources(hits=hits, document=document)
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                    result = search.discover(self.app, self.company, self.theme)
                self.assertEqual(result['sources'], [])
                self.assertEqual(result['diagnostics']['qualified_count'], 0)

    def test_unapproved_search_hit_is_not_read_or_saved(self):
        malicious = {**self.hit, 'url': 'https://investor.tsmc.com.evil.test/report-2025.html'}
        patches = self._patch_sources(hits=[malicious])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as verify, patches[6] as read:
            result = search.discover(self.app, self.company, self.theme)
        self.assertEqual(result['sources'], [])
        verify.assert_not_called()
        read.assert_not_called()

    def test_blocked_official_documents_do_not_hide_approved_news_evidence(self):
        official_hits = [
            {**self.hit, 'url': f'https://investor.tsmc.com/reports/blocked-{index}.pdf', 'score': 1-index*.1}
            for index in (1, 2)
        ]
        reuters_url = 'https://www.reuters.com/business/technology/tsmc-2025-ai-revenue-2026-01-15/'
        reuters = {**self.hit, 'url': reuters_url,
                   'title': 'Taiwan Semiconductor Manufacturing FY2025 AI revenue', 'score': .6}

        def verify(row, **kwargs):
            if 'investor.tsmc.com' in row['url']:
                raise ValueError('robots blocked')
            return row['url'], 'text/html'

        with patch.object(search.source_discovery, 'resolve_issuer_website',
                          return_value=(self.company['website'], 'saved')), \
                patch.object(search.source_discovery, 'approved_domains',
                             return_value=['investor.tsmc.com', 'reuters.com']), \
                patch.object(search.source_discovery, 'approved_source_url', return_value=True), \
                patch.object(search, 'exchange_domains', return_value=[]), \
                patch.object(search.web_source_index, 'search', return_value=[*official_hits, reuters]), \
                patch.object(search.source_discovery, 'specific_document_url', return_value=True), \
                patch.object(search.source_discovery, 'verify_document_link', side_effect=verify) as verifier, \
                patch.object(search.financial_research, '_read_document', return_value=
                             'Taiwan Semiconductor Manufacturing FY2025 results. January 15, 2026. '
                             'AI accelerator revenue represented 18 percent of total revenue.') as reader:
            result = search.discover(self.app, self.company, self.theme)
        self.assertEqual(result['sources'][0]['url'], reuters_url)
        self.assertEqual(verifier.call_count, 3)
        reader.assert_called_once()

    def test_english_q4_transcript_beats_chinese_mirror_and_older_reports(self):
        base = 'https://investor.tsmc.com/'
        hits = [
            {'url': base + 'chinese/reports/2026-01/TSMC%204Q25%20Transcript.pdf',
             'title': 'TSMC 4Q25 Transcript', 'score': .99},
            {'url': base + 'english/annual-report/2025/index.html',
             'title': 'TSMC 2025 Annual Report', 'score': .95},
            {'url': base + 'english/reports/2025-04/TSMC%201Q25%20Transcript.pdf',
             'title': 'TSMC 1Q25 Transcript', 'score': .90},
            {'url': base + 'english/reports/2025-10/TSMC%203Q25%20Transcript.pdf',
             'title': 'TSMC 3Q25 Transcript', 'score': .85},
            {'url': base + 'english/reports/2026-01/TSMC%204Q25%20Transcript.pdf',
             'title': 'TSMC 4Q25 Transcript', 'score': .20},
        ]
        english_q4 = hits[-1]['url']
        read_text = ('TSMC Q4 2025 earnings call. January 15, 2026. '
                     'Revenue from AI accelerators accounted for high-teens percent '
                     'of our total revenue in 2025.')
        with patch.object(search.source_discovery, 'resolve_issuer_website',
                          return_value=(self.company['website'], 'saved')), \
                patch.object(search.source_discovery, 'approved_domains',
                             return_value=['investor.tsmc.com']), \
                patch.object(search, 'exchange_domains', return_value=[]), \
                patch.object(search.web_source_index, 'search', return_value=hits), \
                patch.object(search.source_discovery, 'specific_document_url', return_value=True), \
                patch.object(search.source_discovery, 'verify_document_link',
                             side_effect=lambda row, **kwargs: (row['url'], 'application/pdf')) as verify, \
                patch.object(search.financial_research, '_read_document', return_value=read_text):
            result = search.discover(self.app, self.company, self.theme)
        self.assertEqual(verify.call_args_list[0].args[0]['url'], english_q4)
        self.assertEqual(result['sources'][0]['url'], english_q4)
        self.assertEqual(result['sources'][0]['theme_numeric_evidence_kind'], 'approximate_share')

    def test_approximate_ai_share_is_distinguished_from_exact_share_and_broad_hpc(self):
        approximate = 'AI accelerator revenue represented a high teens percent of total FY2025 revenue.'
        exact = 'AI accelerator revenue represented 18 percent of total FY2025 revenue.'
        broad = 'High performance computing represented 58 percent of total FY2025 revenue.'
        self.assertEqual(search._evidence_kind(search._revenue_excerpt(approximate, self.theme), self.theme),
                         'approximate_share')
        self.assertEqual(search._evidence_kind(search._revenue_excerpt(exact, self.theme), self.theme),
                         'reported_share')
        self.assertEqual(search._revenue_excerpt(broad, self.theme), '')

    def test_ai_demand_and_capex_guidance_is_not_ai_revenue(self):
        excerpt = (
            'o Ltd Earnings Call (Chinese, English) Page 14 Jeff Su - Taiwan Semiconductor '
            'Manufacturing Co Ltd - Director of Investor Relations Okay. So Sunny\'s first '
            'question is around CapEx. She notes, of course, that we raised our 2025 '
            'revenue guidance this year, and we certainly still see a very robust demand '
            'from AI, yet we kept our CapEx guidance in the same range of $38 to $42 '
            '(billion). So she wants to understand why. Is it because of macro uncertainty? '
            'Is it because of constraints in the construction? And her other part of this '
            'question is what is the CapEx outl'
        )
        self.assertEqual(search._revenue_excerpt(excerpt, self.theme), '')
        supported = ('Revenue from AI accelerators was US$21 billion in 2025, '
                     'representing 18 percent of total revenue.')
        self.assertEqual(search._evidence_kind(search._revenue_excerpt(supported, self.theme), self.theme),
                         'reported_share')

    def test_forecast_ai_share_is_not_reported_exposure(self):
        forecast = 'AI revenue guidance represents 18 percent of forecast total revenue for 2025.'
        self.assertEqual(search._revenue_excerpt(forecast, self.theme), '')
        reported = 'AI accelerator revenue represented 18 percent of total revenue in 2025.'
        self.assertEqual(search._evidence_kind(search._revenue_excerpt(reported, self.theme), self.theme),
                         'reported_share')

    def test_html_publication_date_uses_approved_bounded_header(self):
        class Response(io.BytesIO):
            def geturl(self):
                return self_url

        self_url = self.url
        html = b'<html><head><meta property="article:published_time" content="2026-01-15"></head></html>'
        opener = SimpleNamespace(open=lambda request, timeout: Response(html))
        with patch.object(search, 'robots_permits', return_value=True), \
                patch.object(search.source_discovery, 'approved_domains', return_value=['investor.tsmc.com']), \
                patch.object(search.source_discovery, 'approved_source_url', return_value=True), \
                patch.object(search.source_discovery, 'public_url'), \
                patch.object(search, 'build_opener', return_value=opener):
            published = search._html_publication_date(self.url, self.company, 5)
        self.assertEqual(published, '2026-01-15')


class TavilyIndexTest(unittest.TestCase):
    def test_basic_search_disables_ai_answer_and_filters_unapproved_hits(self):
        sent = []
        payload = {'results': [
            {'url': 'https://investor.tsmc.com/earnings/2025', 'title': 'Official transcript', 'score': .9},
            {'url': 'https://investor.tsmc.com/earnings/2025#section', 'title': 'Duplicate'},
            {'url': 'https://investor.tsmc.com.evil.test/earnings/2025', 'title': 'Lookalike'},
            {'url': 'http://investor.tsmc.com/earnings/2025', 'title': 'Insecure'},
        ]}

        def open_request(request, timeout):
            sent.append(request)
            return io.BytesIO(json.dumps(payload).encode())

        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test-tavily-key'}), \
                patch.object(web_source_index, 'build_opener',
                             return_value=SimpleNamespace(open=open_request)):
            rows = web_source_index.search('TSMC 2025 AI revenue', ['investor.tsmc.com'], 10, max_results=99)
        self.assertEqual(len(sent), 1)
        body = json.loads(sent[0].data)
        self.assertEqual(body['search_depth'], 'basic')
        self.assertEqual(body['include_domains'], ['investor.tsmc.com'])
        self.assertEqual(body['include_domains_mode'], 'restrict')
        self.assertFalse(body['include_answer'])
        self.assertFalse(body['include_raw_content'])
        self.assertEqual(body['max_results'], 5)
        self.assertEqual([row['url'] for row in rows], ['https://investor.tsmc.com/earnings/2025'])


if __name__ == '__main__':
    unittest.main()
