import io
import json
import copy
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch
import source_discovery as discovery


def worker_app(sources, snapshots):
    staged = {}

    def save_many(kind, batch):
        known = {source['id'] for source in sources}
        sources.extend(record for record in batch if record['id'] not in known)

    return SimpleNamespace(all_records=lambda kind: list(sources), put_many=save_many,
        stage_source=lambda record: staged.update({record['id']: record}),
        pending_sources=lambda: list(staged.values()),
        acknowledge_sources=lambda batch: [staged.pop(record['id'], None) for record in batch],
        parse_date=lambda value: value, now=lambda: 'now',
        put=lambda kind, record: snapshots.append(copy.deepcopy(record)))


def saved_document(app, run, company, row, timeout=20):
    return {**row, 'id': row['url'], 'company_id': company['id'], 'company_name': company['name'],
            'link_status': 'verified', 'storage_mode': 'url_only'}


class DiscoveryTest(unittest.TestCase):
    def test_transient_gemini_error_retries_without_grounding(self):
        import approved_collector
        candidates = [{'url': 'https://reuters.com/2025', 'title': 'Example annual report 2025',
                       'context': 'Example annual report 2025 published 2026-04-01'}]
        output = {'steps': [{'type': 'model_output', 'content': [{'text': '{"sources":[]}'}]}]}
        error = HTTPError('https://provider', 503, 'Unavailable', {}, None)
        with patch.dict(discovery.os.environ, {'FINANCIALS_LLM_API_KEY': 'test'}), \
             patch.object(approved_collector, 'collect', return_value=(candidates, True)), \
             patch.object(discovery.time, 'sleep'), \
             patch.object(discovery, 'urlopen', side_effect=[error, io.BytesIO(json.dumps(output).encode())]) as send:
            discovery.grounded_search({'name': 'Example'}, ['annual_report'], '2025', '2026-11-03', False, 20)
        self.assertEqual(send.call_count, 2)

    def test_coforge_official_site_is_approved(self):
        self.assertTrue(discovery.approved_source_url('https://investors.coforge.com/annual-report', {'name': 'COFORGE LTD'}))
        self.assertFalse(discovery.approved_source_url('https://investors.coforge.com.evil.test/x', {'name': 'COFORGE LTD'}))

    def test_coforge_uses_the_matching_disclosure_date_without_gemini(self):
        import approved_collector
        report = {'url': 'https://investors.coforge.com/hubfs/Annual-Report-2025.pdf',
                  'title': 'FY 2024-25 pdf'}
        notice = 'https://investors.coforge.com/hubfs/Intimation-of-Annual-Report-FY2025.pdf'
        company = {'name': 'COFORGE LTD'}
        candidates = list(approved_collector.official_annual_reports(company, [report], [
            (notice, 'Intimation of Annual Report FY2025 | August 28, 2025'),
            (notice, 'Intimation of Annual Report for FY2024 | August 01, 2024'),
        ]))
        with patch.dict(discovery.os.environ, {'FINANCIALS_LLM_API_KEY': ''}), \
                patch.object(approved_collector, 'collect', return_value=(candidates, True)), \
                patch.object(discovery, 'urlopen') as send:
            rows = discovery.grounded_search(company, ['annual_report'], '2025', '2026-11-03', False, 20)
            early = discovery.grounded_search(company, ['annual_report'], '2025', '2025-08-27', False, 20)
        send.assert_not_called()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], report['url'])
        self.assertEqual(rows[0]['publication_date'], '2025-08-28')
        self.assertEqual(rows[0]['publication_date_source_url'], notice)
        self.assertEqual(early, [])

    def test_issuer_disclosure_matching_handles_other_issuers_and_conflicts(self):
        from approved_collector import official_annual_reports
        company = {'name': 'Example Industries'}
        report = {'title': 'FY 2024–25', 'url': 'https://ir.example.test/files/annual-report-2025.pdf'}
        notice = ('https://ir.example.test/disclosure/123',
                  'Submission of Annual Report for FY 2024–25 | April 2, 2026')
        self.assertEqual(list(official_annual_reports(company, [report], [notice]))[0]
                         ['official_annual_report']['publication_date'], '2026-04-02')
        conflicting = (notice[0] + '4', notice[1].replace('April 2', 'April 3'))
        self.assertEqual(list(official_annual_reports(company, [report], [notice, conflicting])), [])

    def test_scraped_links_require_their_own_document_type_and_date_evidence(self):
        import approved_collector
        candidates = [
            {'url': 'https://reuters.com/example/2025', 'title': 'Example market news 2025',
             'context': 'Nearby investor presentation dated 2025-06-01', 'publication_dates': ['2025-06-01']},
            {'url': 'https://reuters.com/example/investor-presentation-2025',
             'title': 'Example investor presentation 2025', 'context': 'Nearby filing dated 2025-06-01',
             'publication_dates': []},
            {'url': 'https://reuters.com/example/transcript-intimation-2025',
             'title': 'Example earnings call transcript intimation 2025', 'context': '2025-06-01',
             'publication_dates': ['2025-06-01']},
            {'url': 'https://reuters.com/example/investor-presentation-fy26',
             'title': 'Example investor presentation Q1 FY26, published July 23, 2025',
             'publication_dates': ['2025-07-23']},
        ]
        with patch.object(approved_collector, 'collect', return_value=(candidates, True)), \
                patch.object(discovery, 'urlopen') as send:
            rows = discovery.grounded_search({'name': 'Example'}, ['investor_presentation', 'earnings_call'],
                                             '2025', '2026-11-03', False, 20)
        send.assert_not_called()
        self.assertEqual(rows, [])

    def test_company_matching_ignores_generic_company_suffixes(self):
        import approved_collector
        company = {'name': 'VENTURE CORP LTD'}
        self.assertFalse(approved_collector.company_matches('Marathon Petroleum Corporation earnings', company))
        self.assertTrue(approved_collector.company_matches('Venture Corporation annual report', company))

    def test_link_parser_uses_anchor_title_without_unrelated_dates(self):
        import approved_collector
        from scrapy import Selector
        page = Selector(text='<p>Annual Report 2025 published 02 Apr 2026 '
                             '<a title="Annual Report 2025" href="/annual.pdf">Download</a></p>')
        _, title, context = next(approved_collector.page_links(page))
        self.assertEqual(title, 'Annual Report 2025')
        self.assertEqual(approved_collector.explicit_dates(context), [])

    def test_link_parser_separates_adjacent_table_rows(self):
        import approved_collector
        from scrapy import Selector
        page = Selector(text='<table><tr><td>Annual Report 2024</td><td>02 Apr 2025</td>'
                             '<td><a href="/annual.pdf">Annual Report</a></td></tr>'
                             '<tr><td>Sustainability Report 2025</td><td>02 Apr 2026</td>'
                             '<td><a href="/sustain.pdf">Sustainability Report</a></td></tr></table>')
        _, _, context = next(approved_collector.page_links(page))
        self.assertIn('Annual Report 2024', context)
        self.assertNotIn('Sustainability Report', context)

    def test_scrapy_batches_keep_redirects_within_approved_and_permitted_urls(self):
        import approved_collector
        base = 'https://issuer.test'
        company = {'name': 'Example Industries', 'website': base}
        domains = [*discovery.APPROVED_DOMAINS, 'issuer.test']
        batches = []

        def download(batch, timeout, limit, agent):
            batches.extend(batch)
            return {
                base: (302, '/restricted/annual-report', ''),
                base + '/investors': (302, 'https://unapproved.test/report', ''),
                base + '/sitemap.xml': (200, '', '<sitemapindex><sitemap><loc>http://issuer.test/investor.xml</loc></sitemap></sitemapindex>'),
                base + '/investor.xml': (200, '', f'<urlset><url><loc>{base}/annual-reports</loc></url></urlset>'),
                base + '/annual-reports': (200, '', '<a href="/annual-2025.pdf">Annual Report 2025 | April 2, 2026</a>'),
            }

        with patch.object(approved_collector, 'build_opener') as opener, \
                patch('scrapy_pages.download', side_effect=download):
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = (
                b'User-agent: *\nDisallow: /restricted/\nSitemap: https://issuer.test/sitemap.xml')
            rows, _ = approved_collector.collect(company, domains, lambda url: url, 5)
        self.assertNotIn(base + '/restricted/annual-report', batches)
        self.assertFalse(any('unapproved.test' in url for url in batches))
        self.assertIn(base + '/annual-reports', batches)
        self.assertEqual(rows[0]['publication_dates'], ['2026-04-02'])

    def test_collected_links_use_free_key_and_no_search_grounding(self):
        import approved_collector
        candidates = [{'url': 'https://reuters.com/doc/123', 'title': 'Example annual report 2025',
                       'context': 'Example annual report FY2025 published 2026-04-01'}]
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({'sources': [
            {'index': 0, 'source_type': 'annual_report', 'publication_date': '2026-04-01', 'period': '2025', 'confidence_score': 85, 'reason': 'matching report'},
            {'index': 1, 'source_type': 'annual_report', 'publication_date': '2026-04-01', 'period': '2025', 'confidence_score': 85}]})}]}]}
        with patch.dict(discovery.os.environ, {'FINANCIALS_LLM_API_KEY': 'free-test-key', 'LLM_API_KEY': 'billed-key'}), \
                patch.object(approved_collector, 'collect', return_value=(candidates, True)), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
            rows = discovery.grounded_search({'name': 'Example'}, ['annual_report'], '2025', '2026-11-03', False, 20)
        request = send.call_args.args[0]
        body = json.loads(request.data)
        self.assertNotIn('tools', body)
        self.assertEqual(body['model'], 'gemini-3.5-flash-lite')
        self.assertEqual(request.get_header('X-goog-api-key'), 'free-test-key')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], candidates[0]['url'])
        self.assertEqual(rows[0]['ai_confidence'], 85)
        self.assertTrue(rows.diagnostics['collection_incomplete'])

    def test_low_confidence_candidate_is_available_as_fallback(self):
        import approved_collector
        candidates = [{'url': 'https://reuters.com/doc/123', 'title': 'Example annual report 2025',
                       'context': 'Example annual report FY2025 published 2026-04-01'}]
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({'sources': [
            {'index': 0, 'source_type': 'annual_report', 'publication_date': '2026-04-01',
             'period': '2025', 'confidence_score': 70}]})}]}]}
        with patch.dict(discovery.os.environ, {'FINANCIALS_LLM_API_KEY': 'free-test-key'}), \
                patch.object(approved_collector, 'collect', return_value=(candidates, True)), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())):
            rows = discovery.grounded_search({'name': 'Example'}, ['annual_report'], '2025', '2026-11-03', False, 20)
            self.assertEqual(rows[0]['ai_confidence'], 70)

    def test_gemini_cannot_relabel_annual_report_as_sustainability_report(self):
        import approved_collector
        candidates = [{'url': 'https://reuters.com/report/2024.pdf', 'title': 'Annual Report 2024 - PDF Version',
                       'context': 'Venture Annual Report 2024, published 2025-04-02'}]
        response = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({'sources': [
            {'index': 0, 'source_type': 'sustainability_report', 'publication_date': '2025-04-02',
             'period': '2024', 'confidence_score': 95}]})}]}]}
        with patch.dict(discovery.os.environ, {'FINANCIALS_LLM_API_KEY': 'test'}), \
                patch.object(approved_collector, 'collect', return_value=(candidates, True)), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            rows = discovery.grounded_search({'name': 'Venture'}, ['sustainability_report'], '2024', '2026-11-03', False, 20)
        self.assertEqual(rows, [])

    def test_fallback_fills_only_gaps_with_closest_scores_per_stock_and_type(self):
        run = {'source_types': ['annual_report', 'earnings_call'], 'search_periods': ['2026', '2025', '2024'],
               'cutoff': '2026-11-03', 'storage_mode': 'url_only'}
        companies = [{'id': 'a'}, {'id': 'b'}]
        def source(cid, kind, score, url):
            return {'company_id': cid, 'source_type': kind, 'url': url, 'period': '2025',
                    'publication_date': '2026-04-01', 'link_status': 'verified', 'ai_confidence': score}
        existing = [source('a', 'annual_report', 90, 'https://reuters.com/a')]
        candidates = [source('a', 'annual_report', 60, 'https://reuters.com/60'),
                      source('a', 'annual_report', 70, 'https://reuters.com/70'),
                      source('a', 'annual_report', 69, 'https://reuters.com/69'),
                      source('b', 'earnings_call', 68, 'https://reuters.com/b1'),
                      source('b', 'earnings_call', 50, 'https://reuters.com/b2'),
                      source('b', 'earnings_call', 10, 'https://reuters.com/b3')]
        selected = discovery.select_fallback_sources(run, companies, existing, candidates)
        self.assertEqual([s['ai_confidence'] for s in selected], [70, 68, 50])
        self.assertTrue(all(s['ai_confidence_fallback'] for s in selected))

    def test_worker_saves_best_fallback_after_higher_confidence_sources(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': '2026',
               'search_periods': ['2026'], 'cutoff': '2026-11-03',
               'results': [], 'errors': [], 'status': 'running'}
        sources = []
        rows = [{'url': f'https://reuters.com/report/{score}', 'source_type': 'annual_report',
                 'publication_date': '2026-04-01', 'period': '2026', 'ai_confidence': score}
                for score in (90, 70, 50)]
        with patch.object(discovery, 'grounded_search', return_value=rows), \
                patch.object(discovery, 'save_document', side_effect=saved_document):
            discovery.worker(worker_app(sources, []), run, [{'id': 'a', 'name': 'Example'}])
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(sorted(s['ai_confidence'] for s in sources), [70, 90])
        fallback = next(s for s in sources if s['ai_confidence'] == 70)
        self.assertTrue(fallback['ai_confidence_fallback'])

    def test_collector_blocks_unapproved_redirects(self):
        import approved_collector
        redirect = approved_collector.ApprovedRedirect(['reuters.com'], lambda url: url)
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            redirect.redirect_request(None, None, 302, '', {}, 'https://reuters.com.attacker.test/page')

    def test_approved_source_domains_and_company_sites(self):
        company = {'website': 'https://example.com'}
        for url in ('https://www.reuters.com/article/1', 'https://seekingalpha.com/article/1',
                    'https://investors.example.com/report.pdf'):
            self.assertTrue(discovery.approved_source_url(url, company))
        for url in ('https://reuters.com.attacker.test/report', 'https://unapproved.test/report'):
            self.assertFalse(discovery.approved_source_url(url, company))

    def test_gemini_prompt_requires_approved_domain_only_queries(self):
        output = {'steps': []}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
            discovery.legacy_grounded_search({'name': 'Example', 'website': 'example.com'},
                                      ['annual_report'], '2026', '2026-11-03', False, 5)
        prompt = json.loads(send.call_args.args[0].data)['input']
        for phrase in ('HARD SOURCE RESTRICTION', 'site:', 'seekingalpha.com', 'example.com',
                       'Never search, cite, use, or return information from any other domain'):
            self.assertIn(phrase, prompt)

    def test_both_grounded_search_models_share_one_api_key(self):
        output = {'steps': [{'type': 'google_search_call'}, {'type': 'model_output', 'content': [{
            'text': '{"sources":[]}', 'annotations': [{'type': 'url_citation', 'url': 'https://example.com'}]}]}]}
        for broad, expected in ((False, 'gemini-3.5-flash-lite'), (True, 'gemini-3.8-flash')):
            with self.subTest(broad=broad), patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'shared-test-key'}), \
                    patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
                discovery.legacy_grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', broad, 5)
            request = send.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(body['model'], expected)
            self.assertEqual(body['tools'], [{'type': 'google_search', 'search_types': ['web_search']}])
            self.assertNotIn('generation_config', body)
            self.assertEqual(request.get_header('X-goog-api-key'), 'shared-test-key')

    def test_submission_uses_selected_types_and_selected_portfolio_stocks(self):
        portfolio = {'id': 'portfolio', 'rows': [{'company_id': 'a'}, {'company_id': 'b'},
                                                 {'company_id': 'a'}, {'company_id': 'CASH'}]}
        app = SimpleNamespace(get=lambda kind, key: portfolio if kind == 'portfolio_imports' else {'id': key, 'name': key},
                              all_records=lambda kind: [{'id': cid, 'name': cid} for cid in ('a', 'b')],
                              ident=lambda kind: 'job', now=lambda: 'now', parse_date=lambda value: value,
                              put=lambda kind, record: record)
        with patch('source_jobs.Queue') as queue, patch('source_jobs.ensure_worker'), \
                patch.object(discovery, 'datetime', wraps=discovery.datetime) as clock:
            clock.now.return_value = discovery.datetime(2026, 1, 1)
            result = discovery.start(app, {'portfolio_id': 'portfolio', 'company_ids': ['a'],
                'source_types': ['annual_report', 'annual_report'], 'period': '2025',
                'cutoff': '2026-11-02'})['search_run']
            queue.return_value.enqueue.assert_called_once()
            long_result = discovery.start(app, {'portfolio_id': 'portfolio', 'company_ids': ['a', 'b', 'a'],
                'source_types': ['annual_report'], 'search_mode': 'long'})['search_run']
        self.assertEqual(result['company_ids'], ['a'])
        self.assertEqual(result['source_types'], ['annual_report'])
        self.assertEqual(result['documents_per_type'], 2)
        self.assertEqual(result['search_models'], {'first_pass': 'gemini-3.5-flash-lite',
                                                 'missing_results': 'gemini-3.5-flash-lite'})
        self.assertEqual(result['storage_mode'], 'url_only')
        self.assertEqual(result['period'], '2026')
        self.assertEqual(result['search_periods'], ['2026', '2025', '2024'])
        self.assertEqual(result['search_mode'], 'short')
        self.assertEqual(result['time_limit_seconds'], 480)
        self.assertNotIn('deadline_unix', result)
        self.assertEqual(len(result['missing']), 1)
        self.assertEqual(long_result['company_ids'], ['a', 'b'])
        self.assertEqual(long_result['time_limit_seconds'], 12 * 60)

    def test_empty_and_unknown_type_selection_rejected_before_queueing(self):
        with patch('source_jobs.Queue') as queue:
            for types in ([], None, ['made_up']):
                with self.assertRaisesRegex(ValueError, 'source type'):
                    discovery.start(None, {'source_types': types})
            queue.assert_not_called()

    def test_search_rejects_more_than_two_source_types(self):
        with patch('source_jobs.Queue') as queue:
            with self.assertRaisesRegex(ValueError, 'Select at most 2 source types'):
                discovery.start(None, {'source_types': list(discovery.TYPES[:3])})
            queue.assert_not_called()

    def test_coverage_counts_two_unique_eligible_urls_only_for_selected_types(self):
        run = {'source_types': ['annual_report'], 'period': '2025', 'cutoff': '2026-11-02'}
        source = {'company_id': 'a', 'source_type': 'annual_report', 'period': '2025',
                  'publication_date': '2026-01-01', 'url': 'https://example.com/a'}
        sources = [source, {**source, 'url': source['url']+'#page=1'},
                   {**source, 'url': 'https://example.com/future', 'publication_date': '2027-01-01'},
                   {**source, 'url': 'https://example.com/old', 'period': '2024'},
                   {**source, 'url': 'https://example.com/earnings', 'source_type': 'earnings_call'}]
        gaps = discovery.coverage_gaps(run, [{'id': 'a'}, {'id': 'b'}], sources)
        self.assertEqual([g['found'] for g in gaps], [1, 0])
        self.assertTrue(all(g['required'] == 2 for g in gaps))
        sources.append({**source, 'url': 'https://example.com/b'})
        self.assertEqual(discovery.coverage_gaps(run, [{'id': 'a'}], sources), [])

    def test_url_only_coverage_requires_verified_direct_links(self):
        run = {'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02', 'storage_mode': 'url_only'}
        source = {'company_id': 'a', 'source_type': 'annual_report', 'period': '2025',
                  'publication_date': '2026-01-01', 'url': 'https://example.com/report.pdf'}
        sources = [source, {**source, 'url': '', 'sha256': 'file-only'},
                   {**source, 'url': 'https://example.com/verified.pdf', 'link_status': 'verified'}]
        self.assertEqual(discovery.coverage_gaps(run, [{'id': 'a'}], sources)[0]['found'], 1)

    def test_existing_website_is_excluded_only_for_its_company_and_source_type(self):
        sources = [
            {'company_id': 'tsmc', 'source_type': 'annual_report', 'link_status': 'verified',
             'discovery_url': 'https://www.capitaliq.com/report/tsmc-2025',
             'url': 'https://files.capitaliq.com/tsmc-2025.pdf'},
            {'company_id': 'tsmc', 'source_type': 'regulatory_filing', 'link_status': 'verified',
             'url': 'https://www.capitaliq.com/filing/tsmc'},
            {'company_id': 'other', 'source_type': 'annual_report', 'link_status': 'verified',
             'url': 'https://capitaliq.com/report/other'},
        ]
        self.assertEqual(discovery.excluded_websites(sources, 'tsmc', ['annual_report', 'regulatory_filing']), {
            'annual_report': ['capitaliq.com', 'files.capitaliq.com'],
            'regulatory_filing': ['capitaliq.com'],
        })

    def test_grounded_search_rejects_an_already_searched_website_for_that_type(self):
        urls = ['https://capitaliq.com/report/tsmc', 'https://reuters.com/article/tsmc-report']
        output = {'steps': [
            {'type': 'google_search_call'},
            {'type': 'model_output', 'content': [{'text': '\n'.join(
                f'annual_report | 2026-01-01 | 2025 | TSMC annual report {index}' for index in range(2)),
                'annotations': [
                    {'type': 'url_citation', 'url': url, 'start_index': index * 60, 'end_index': index * 60 + 10}
                    for index, url in enumerate(urls)]}]},
        ]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as request:
            result = discovery.legacy_grounded_search({'name': 'TSMC'}, ['annual_report'], '', '2026-11-02', False, 5,
                                                excluded_sites_by_type={'annual_report': ['capitaliq.com']})
        self.assertEqual([row['url'] for row in result], urls[1:])
        self.assertIn('capitaliq.com', request.call_args.args[0].data.decode())

    def test_expired_search_flushes_already_found_documents_without_new_provider_calls(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'results': [], 'errors': [], 'status': 'running', 'deadline_unix': discovery.time.time() - 1}
        sources, snapshots = [], []
        app = worker_app(sources, snapshots)
        app.stage_source({'id': 'saved', 'company_id': 'a', 'source_type': 'annual_report',
            'url': 'https://example.com/report.pdf', 'publication_date': '2026-01-01', 'period': '2025',
            'link_status': 'verified'})
        with patch.object(discovery, 'grounded_search') as search:
            discovery.worker(app, run, [{'id': 'a', 'name': 'A'}])
        search.assert_not_called()
        self.assertEqual(run['status'], 'incomplete')
        self.assertEqual(run['completion_reason'], 'time_limit_reached')
        self.assertEqual(len(sources), 1)

    def test_subsequent_search_skips_a_company_type_already_covered_by_two_verified_links(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'results': [], 'errors': [], 'status': 'running'}
        sources = [
            {'id': f'source-{n}', 'company_id': 'tsmc', 'source_type': 'annual_report',
             'url': f'https://capitaliq.com/tsmc/report-{n}', 'publication_date': '2026-01-01',
             'period': '2025', 'link_status': 'verified', 'storage_mode': 'url_only'}
            for n in range(2)
        ]
        app = worker_app(sources, [])
        with patch.object(discovery, 'grounded_search') as search:
            discovery.worker(app, run, [{'id': 'tsmc', 'name': 'TSMC'}])
        search.assert_not_called()
        self.assertEqual(run['status'], 'completed')

    def test_worker_keeps_searching_until_two_per_type_for_every_stock(self):
        companies = [{'id': cid, 'name': cid} for cid in ('a', 'b')]
        selected = ['annual_report', 'regulatory_filing']
        run = {'id': 'job', 'source_types': selected, 'period': '2026', 'search_periods': ['2026', '2025', '2024'], 'cutoff': '2026-11-02',
               'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        sources, snapshots, calls = [], [], []

        def search(company, types, period, cutoff, broad, timeout, known, excluded_sites):
            calls.append((company['id'], list(types), broad, timeout, known, excluded_sites))
            number = 1 if period == '2026' else 2
            # Even a provider that returns an unticked category cannot save it.
            return [{'url': f'https://example.com/{company["id"]}/{t}/{number}',
                     'source_type': t, 'publication_date': '2026-01-01', 'period': '2025'}
                    for t in [*types, 'earnings_call']]

        app = worker_app(sources, snapshots)
        with patch.object(discovery, 'grounded_search', side_effect=search), \
                patch.object(discovery, 'save_document', side_effect=saved_document), \
                patch.object(discovery.time, 'sleep') as sleep:
            discovery.worker(app, run, companies)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['missing'], [])
        self.assertEqual(len(sources), 8)
        self.assertEqual({s['source_type'] for s in sources}, set(selected))
        self.assertEqual(sorted(c[0] for c in calls[:2]), ['a', 'b'])
        self.assertEqual(sorted(c[0] for c in calls[2:]), ['a', 'b'])
        self.assertTrue(all(c[3] == 90 for c in calls))
        self.assertTrue(all(c[4] for c in calls[2:]))
        self.assertTrue(any(len(s['missing']) > 0 for s in snapshots[:-1]))
        sleep.assert_not_called()

    def test_unavailable_documents_finish_incomplete_after_approved_search(self):
        run = {'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        app = worker_app([], [])
        with patch.object(discovery, 'grounded_search', side_effect=[[], []]) as search, \
                patch.object(discovery.time, 'sleep') as sleep:
            discovery.worker(app, run, [{'id': 'a', 'name': 'a'}])
        self.assertEqual(run['status'], 'incomplete')
        self.assertEqual(run['missing'][0]['found'], 0)
        self.assertEqual(search.call_count, 1)
        sleep.assert_not_called()

    def test_exhausted_three_year_window_marks_remaining_gaps(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': '2026',
               'search_periods': ['2026', '2025', '2024'], 'cutoff': '2026-11-02',
               'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        app = worker_app([], [])
        searched_periods = []

        def no_results(company, source_types, period, cutoff, broad, timeout, known, excluded_sites):
            searched_periods.append(period)
            return []

        with patch.object(discovery, 'grounded_search', side_effect=no_results):
            discovery.worker(app, run, [{'id': 'a', 'name': 'A'}])
        self.assertEqual(run['status'], 'incomplete')
        self.assertEqual(run['completion_reason'], 'coverage_window_exhausted')
        self.assertEqual(searched_periods, ['2026', '2025', '2024'])

    def test_first_pass_searches_and_downloads_are_concurrent_within_the_time_limit(self):
        companies = [{'id': str(i), 'name': str(i)} for i in range(4)]
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'latest_documents': False, 'results': [], 'errors': [], 'status': 'running'}
        sources, snapshots = [], []
        # Existing coverage must not skip the first pass, even outside latest mode.
        for company in companies:
            for n in range(2):
                sources.append({'id': f'old-{company["id"]}-{n}', 'company_id': company['id'],
                    'source_type': 'annual_report', 'period': '2025', 'publication_date': '2026-01-01',
                    'url': f'https://example.com/old/{company["id"]}/{n}'})
        search_gate = threading.Barrier(4)
        download_gate = threading.Barrier(2)
        lock = threading.Lock()
        download_calls = []

        def search(company, types, period, cutoff, broad, timeout, known, excluded_sites):
            self.assertFalse(broad)
            search_gate.wait(timeout=5)
            return [{'url': f'https://example.com/{company["id"]}/{n}', 'source_type': 'annual_report',
                     'publication_date': '2026-01-01', 'period': '2025'} for n in range(3)]

        def save(app, run, company, row, timeout=20):
            with lock:
                download_calls.append(row['url'])
                first_two = len(download_calls) <= 2
            if first_two:
                download_gate.wait(timeout=5)
            return saved_document(app, run, company, row)

        app = worker_app(sources, snapshots)
        original_reads, original_put_many = app.all_records, app.put_many
        with patch.object(discovery, 'grounded_search', side_effect=search) as searches, \
                patch.object(discovery, 'save_document', side_effect=save), \
                patch.object(app, 'all_records', wraps=original_reads) as reads, \
                patch.object(app, 'put_many', wraps=original_put_many) as batches:
            discovery.worker(app, run, companies)
        self.assertEqual(run['status'], 'completed')
        self.assertTrue(run['first_pass_completed'])
        self.assertEqual(run['current_search_model'], 'gemini-3.5-flash-lite')
        self.assertEqual(searches.call_count, 4)
        self.assertEqual(len(download_calls), 12)  # No first-pass cap of two.
        self.assertEqual(run['results_count'], 12)
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(batches.call_count, 1)

    def test_followups_only_search_gaps_after_every_first_pass_save(self):
        companies = [{'id': cid, 'name': cid} for cid in ('a', 'b')]
        run = {'id': 'job', 'source_types': ['annual_report', 'regulatory_filing'], 'period': None,
               'cutoff': '2026-11-02', 'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        sources, snapshots, called = [], [], []
        app = worker_app(sources, snapshots)

        def search(company, types, period, cutoff, broad, timeout, known, excluded_sites):
            called.append((company['id'], broad, types))
            if broad:
                self.assertTrue(run['first_pass_completed'])
                self.assertEqual(run['current_search_model'], 'gemini-3.8-flash')
                # Both stocks' first-pass documents must already be committed.
                self.assertEqual(len(sources), 6)
                self.assertEqual(company['id'], 'b')
                self.assertEqual(types, ['regulatory_filing'])
            categories = ['annual_report', 'regulatory_filing'] if company['id'] == 'a' else ['annual_report']
            if broad: categories = types
            return [{'url': f'https://example.com/{company["id"]}/{t}/{n}', 'source_type': t,
                     'publication_date': '2026-01-01', 'period': '2025'} for t in categories for n in range(2)]

        with patch.object(discovery, 'grounded_search', side_effect=search), \
                patch.object(discovery, 'save_document', side_effect=saved_document), \
                patch.object(discovery.time, 'sleep'):
            discovery.worker(app, run, companies)
        self.assertEqual(run['status'], 'incomplete')
        self.assertEqual(len(called), 2)
        self.assertEqual(len(sources), 6)

    def test_only_verified_url_and_metadata_saved_without_file_storage(self):
        app = SimpleNamespace(now=lambda: 'now', source_ingest=lambda *_: self.fail('Text ingestion must not run'))
        row = {'url': 'https://example.com/report', 'source_type': 'annual_report',
               'publication_date': '2026-01-01', 'period': '2025'}
        with patch.object(discovery, 'verify_document_link', return_value=('https://reuters.com/report.pdf', 'application/pdf')), \
                patch('supabase_store.upload_source') as upload, \
                patch('pypdf.PdfReader', side_effect=AssertionError('No PDF extraction')):
            record = discovery.save_document(app, {'cutoff': '2026-11-02'}, {'id': 'a', 'name': 'A'}, row)
        upload.assert_not_called()
        self.assertEqual(record['url'], 'https://reuters.com/report.pdf')
        self.assertEqual(record['discovery_url'], row['url'])
        self.assertEqual(record['evidence'], [])
        self.assertEqual(record['extraction_status'], 'not_requested')
        self.assertIsNone(record['storage_path'])
        self.assertIsNone(record['sha256'])
        self.assertEqual(record['storage_mode'], 'url_only')
        self.assertEqual(record['link_status'], 'verified')

    def test_homepages_indexes_and_login_urls_rejected(self):
        for url in ('https://example.com/', 'https://example.com/en', 'https://example.com/investor-relations',
                    'https://example.com/reports/annual-reports', 'https://example.com/login/doc/2025',
                    'https://finance.yahoo.com/quote/TSM/'):
            with self.subTest(url=url), patch.object(discovery, 'public_url', side_effect=lambda u: u), \
                    patch.object(discovery, 'build_opener') as opener:
                with self.assertRaisesRegex(ValueError, 'landing page'):
                    discovery.verify_document_link({'url': url, 'source_type': 'annual_report'})
                opener.assert_not_called()

    def test_direct_pdf_link_checked_with_head_without_reading_pdf_bytes(self):
        response = io.BytesIO(b'%PDF-1.7')
        response.headers = {'Content-Type': 'application/pdf'}
        response.geturl = lambda: 'https://example.com/reports/annual-2025.pdf'
        with patch.object(discovery, 'public_url', side_effect=lambda u: u), \
                patch.object(discovery, 'build_opener') as opener, patch.object(response, 'read') as read:
            opener.return_value.open.return_value = response
            url, mime = discovery.verify_document_link({'url': response.geturl(), 'source_type': 'annual_report'})
        read.assert_not_called()
        self.assertEqual(mime, 'application/pdf')
        self.assertTrue(url.endswith('.pdf'))
        self.assertEqual(opener.return_value.open.call_args.args[0].method, 'HEAD')

    def test_redirect_to_homepage_does_not_count_as_document(self):
        response = io.BytesIO(b'')
        response.headers = {'Content-Type': 'text/html'}
        response.geturl = lambda: 'https://example.com/'
        with patch.object(discovery, 'public_url', side_effect=lambda u: u), \
                patch.object(discovery, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, 'redirects'):
                discovery.verify_document_link({'url': 'https://example.com/report-2025', 'source_type': 'annual_report'})

    def test_specific_article_verified_by_title_and_metadata(self):
        url = 'https://example.com/news/2026/tsmc-earnings-call'
        head = io.BytesIO(b'')
        page = io.BytesIO(b'<title>TSMC Q3 2026 Earnings Call Transcript</title><meta property="og:type" content="article">')
        for response in (head, page):
            response.headers = {'Content-Type': 'text/html'}
            response.geturl = lambda: url
        with patch.object(discovery, 'public_url', side_effect=lambda u: u), \
                patch.object(discovery, 'build_opener') as opener:
            opener.return_value.open.side_effect = [head, page]
            result, mime = discovery.verify_document_link({'url': url, 'source_type': 'earnings_call',
                                                         'title': 'TSMC Q3 2026 Earnings Call Transcript'})
        self.assertEqual(result, url)
        self.assertEqual(mime, 'text/html')

    def test_specific_annual_report_viewer_is_not_confused_with_a_generic_index(self):
        self.assertTrue(discovery.specific_document_url('https://example.com/annualReports/2025/english/index.html'))
        self.assertFalse(discovery.specific_document_url('https://example.com/investors/index.html'))

    def test_nonmatching_html_page_is_rejected(self):
        url = 'https://example.com/news/2026/company-results'
        responses = [io.BytesIO(b''), io.BytesIO(b'<title>Company investor information</title>')]
        for response in responses:
            response.headers = {'Content-Type': 'text/html'}
            response.geturl = lambda: url
        with patch.object(discovery, 'public_url', side_effect=lambda u: u), \
                patch.object(discovery, 'build_opener') as opener:
            opener.return_value.open.side_effect = responses
            with self.assertRaisesRegex(ValueError, 'does not match'):
                discovery.verify_document_link({'url': url, 'source_type': 'annual_report',
                                               'title': 'TSMC Annual Report 2025'})

    def test_restart_replays_staged_metadata_and_reuses_completed_first_pass(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'latest_documents': True, 'first_pass_completed': True,
               'results': [], 'errors': [], 'status': 'running'}
        sources, snapshots = [], []
        app = worker_app(sources, snapshots)
        for n in range(2):
            app.stage_source({'id': str(n), 'company_id': 'a', 'source_type': 'annual_report',
                'url': f'https://example.com/{n}', 'publication_date': '2026-01-01', 'period': '2025', 'link_status': 'verified'})
        with patch.object(discovery, 'grounded_search') as search:
            discovery.worker(app, run, [{'id': 'a', 'name': 'a'}])
        search.assert_not_called()
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['results_count'], 2)
        self.assertEqual(app.pending_sources(), [])

    def test_failed_bulk_save_keeps_uploaded_metadata_for_recovery(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'first_pass_completed': True, 'results': [], 'errors': [], 'status': 'running'}
        app = worker_app([], [])
        app.stage_source({'id': 'saved', 'company_id': 'a', 'source_type': 'annual_report',
            'url': 'https://example.com/a', 'publication_date': '2026-01-01', 'period': '2025'})
        with patch.object(app, 'put_many', side_effect=RuntimeError('Storage metadata offline')):
            discovery.worker(app, run, [{'id': 'a', 'name': 'a'}])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(app.pending_sources()), 1)

    def test_quota_error_stops_with_actionable_message(self):
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), patch.object(
                discovery, 'urlopen', side_effect=HTTPError('https://provider', 429, 'Quota', {}, None)) as request:
            with self.assertRaisesRegex(discovery.ProviderError, 'quota.*billing'):
                discovery.legacy_grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', False, 1)
        self.assertEqual(request.call_count, 1)

    def test_quota_ends_a_worker_run_as_incomplete_with_real_progress(self):
        run = {'id': 'job', 'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        app = worker_app([], [])
        with patch.object(discovery, 'grounded_search', side_effect=discovery.ProviderError('quota reached', 429)):
            discovery.worker(app, run, [{'id': 'a', 'name': 'A'}])
        self.assertEqual(run['status'], 'incomplete')
        self.assertEqual(run['completion_reason'], 'provider_quota')
        self.assertEqual(run['companies_completed'], [])
        self.assertEqual(run['errors'][-1]['reason'], 'quota reached')

    def test_unavailable_models_report_project_access_restriction(self):
        body = json.dumps({'error': {'message': 'This model is no longer available to new users.'}}).encode()
        for broad, model in ((False, 'gemini-3.5-flash-lite'), (True, 'gemini-3.8-flash')):
            with self.subTest(broad=broad), patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                    patch.object(discovery, 'urlopen', side_effect=HTTPError('https://provider', 404, 'Unavailable', {}, io.BytesIO(body))):
                with self.assertRaisesRegex(discovery.ProviderError, model + ' is no longer available'):
                    discovery.legacy_grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', broad, 5)

    def test_only_cited_urls_are_accepted(self):
        cited = 'https://example.com/report'
        output = {'steps': [{'type': 'google_search_call', 'arguments': {'queries': ['example annual report']}},
                            {'type': 'model_output', 'content': [{'text': 'annual_report | 2026-01-01 | 2025 | Example annual report',
                  'annotations': [{'type': 'url_citation', 'url': cited, 'start_index': 0, 'end_index': 20}]}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as request:
            result = discovery.legacy_grounded_search({'name': 'Example'}, ['annual_report'], 'FY2025', '2026-11-01', False, 5)
        self.assertEqual([row['url'] for row in result], [cited])
        self.assertEqual(result[0]['source_type'], 'annual_report')
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['tools'], [{'type': 'google_search', 'search_types': ['web_search']}])
        self.assertNotIn('generation_config', body)

    def test_uncited_or_ungrounded_candidates_are_diagnosed_and_rejected(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': 'annual_report | 2026-01-01 | 2025 | Example annual report'}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as request:
            result = discovery.legacy_grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', False, 5)
        self.assertEqual(result, [])
        self.assertEqual(result.diagnostics['candidate_count'], 0)
        self.assertEqual(result.diagnostics['google_search_calls'], 0)
        self.assertEqual(result.diagnostics['citation_count'], 0)
        self.assertEqual(result.diagnostics['outcome'], 'search_not_invoked')
        self.assertFalse(result.diagnostics['fallback_used'])
        self.assertEqual(request.call_count, 1)

    def test_uncited_interaction_does_not_use_an_ungrounded_fallback(self):
        interaction = {'steps': [{'type': 'google_search_call'}, {'type': 'model_output', 'content': [
            {'text': 'annual_report | 2026-01-01 | 2025 | Example annual report'}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(interaction).encode())) as request:
            result = discovery.legacy_grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', False, 5)
        self.assertEqual(result, [])
        self.assertFalse(result.diagnostics['fallback_used'])
        self.assertEqual(result.diagnostics['google_search_calls'], 1)
        self.assertEqual(result.diagnostics['citation_count'], 0)
        self.assertEqual(request.call_count, 1)

    def test_recovery_starts_durable_worker_without_a_time_limit(self):
        with patch('source_jobs.ensure_worker') as worker:
            discovery.recover_stale_runs(None)
        worker.assert_called_once()

    def test_private_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            discovery.public_url('http://127.0.0.1/secrets')
        with patch.object(discovery.socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                discovery.public_url('https://example.com/private')
