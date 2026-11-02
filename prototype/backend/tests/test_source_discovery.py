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
    def test_both_grounded_search_models_share_one_api_key(self):
        output = {'steps': [{'type': 'google_search_call'}, {'type': 'model_output', 'content': [{
            'text': '{"sources":[]}', 'annotations': [{'type': 'url_citation', 'url': 'https://example.com'}]}]}]}
        for broad, expected in ((False, 'gemini-3.5-flash-lite'), (True, 'gemini-3.8-flash')):
            with self.subTest(broad=broad), patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'shared-test-key'}), \
                    patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as send:
                discovery.grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', broad, 5)
            request = send.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(body['model'], expected)
            self.assertEqual(body['tools'], [{'type': 'google_search', 'search_types': ['web_search']}])
            self.assertNotIn('generation_config', body)
            self.assertEqual(request.get_header('X-goog-api-key'), 'shared-test-key')

    def test_submission_uses_selected_types_and_all_portfolio_stocks(self):
        portfolio = {'id': 'portfolio', 'rows': [{'company_id': 'a'}, {'company_id': 'b'},
                                                 {'company_id': 'a'}, {'company_id': 'CASH'}]}
        app = SimpleNamespace(get=lambda kind, key: portfolio if kind == 'portfolio_imports' else {'id': key, 'name': key},
                              all_records=lambda kind: [{'id': cid, 'name': cid} for cid in ('a', 'b')],
                              ident=lambda kind: 'job', now=lambda: 'now', parse_date=lambda value: value,
                              put=lambda kind, record: record)
        with patch('source_jobs.Queue') as queue, patch('source_jobs.ensure_worker'):
            result = discovery.start(app, {'portfolio_id': 'portfolio', 'company_ids': ['a'],
                'source_types': ['annual_report', 'annual_report'], 'period': '2025',
                'cutoff': '2026-11-02'})['search_run']
        self.assertEqual(result['company_ids'], ['a', 'b'])
        self.assertEqual(result['source_types'], ['annual_report'])
        self.assertEqual(result['documents_per_type'], 2)
        self.assertEqual(result['search_models'], {'first_pass': 'gemini-3.5-flash-lite',
                                                 'missing_results': 'gemini-3.8-flash'})
        self.assertEqual(result['storage_mode'], 'url_only')
        self.assertEqual(result['period'], '2025')
        self.assertEqual(result['time_limit_seconds'], 600)
        self.assertGreater(result['deadline_unix'], discovery.time.time())
        self.assertEqual(len(result['missing']), 2)
        queue.return_value.enqueue.assert_called_once()

    def test_empty_and_unknown_type_selection_rejected_before_queueing(self):
        with patch('source_jobs.Queue') as queue:
            for types in ([], None, ['made_up']):
                with self.assertRaisesRegex(ValueError, 'source type'):
                    discovery.start(None, {'source_types': types})
            queue.assert_not_called()

    def test_reporting_year_is_required_even_when_using_latest_documents(self):
        with self.assertRaisesRegex(ValueError, 'four-digit year'):
            discovery.start(None, {'latest_documents': True, 'period': '', 'source_types': ['annual_report']})

    def test_more_than_two_source_types_are_rejected_before_queueing(self):
        with self.assertRaisesRegex(ValueError, 'at most 2 source types'):
            discovery.start(None, {'period': '2025', 'source_types': [
                'annual_report', 'regulatory_filing', 'earnings_call'
            ]})

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
            result = discovery.grounded_search({'name': 'TSMC'}, ['annual_report'], '', '2026-11-02', False, 5,
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
        run = {'id': 'job', 'source_types': selected, 'period': None, 'cutoff': '2026-11-02',
               'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        sources, snapshots, calls = [], [], []

        def search(company, types, period, cutoff, broad, timeout, known, excluded_sites):
            calls.append((company['id'], list(types), broad, timeout, known, excluded_sites))
            number = 2 if broad else 1
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

    def test_unavailable_documents_finish_incomplete_after_both_search_phases(self):
        run = {'source_types': ['annual_report'], 'period': None, 'cutoff': '2026-11-02',
               'latest_documents': True, 'results': [], 'errors': [], 'status': 'running'}
        app = worker_app([], [])
        with patch.object(discovery, 'grounded_search', side_effect=[[], []]) as search, \
                patch.object(discovery.time, 'sleep') as sleep:
            discovery.worker(app, run, [{'id': 'a', 'name': 'a'}])
        self.assertEqual(run['status'], 'incomplete')
        self.assertEqual(run['missing'][0]['found'], 0)
        self.assertEqual(search.call_count, 2)
        sleep.assert_not_called()

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
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(len(called), 3)
        self.assertEqual(len(sources), 8)

    def test_only_verified_url_and_metadata_saved_without_file_storage(self):
        app = SimpleNamespace(now=lambda: 'now', source_ingest=lambda *_: self.fail('Text ingestion must not run'))
        row = {'url': 'https://example.com/report', 'source_type': 'annual_report',
               'publication_date': '2026-01-01', 'period': '2025'}
        with patch.object(discovery, 'verify_document_link', return_value=('https://example.com/report.pdf', 'application/pdf')), \
                patch('supabase_store.upload_source') as upload, \
                patch('pypdf.PdfReader', side_effect=AssertionError('No PDF extraction')):
            record = discovery.save_document(app, {'cutoff': '2026-11-02'}, {'id': 'a', 'name': 'A'}, row)
        upload.assert_not_called()
        self.assertEqual(record['url'], 'https://example.com/report.pdf')
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
                discovery.grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', False, 1)
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
                    discovery.grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', broad, 5)

    def test_only_cited_urls_are_accepted(self):
        cited = 'https://example.com/report'
        output = {'steps': [{'type': 'google_search_call', 'arguments': {'queries': ['example annual report']}},
                            {'type': 'model_output', 'content': [{'text': 'annual_report | 2026-01-01 | 2025 | Example annual report',
                  'annotations': [{'type': 'url_citation', 'url': cited, 'start_index': 0, 'end_index': 20}]}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as request:
            result = discovery.grounded_search({'name': 'Example'}, ['annual_report'], 'FY2025', '2026-11-01', False, 5)
        self.assertEqual([row['url'] for row in result], [cited])
        self.assertEqual(result[0]['source_type'], 'annual_report')
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['tools'], [{'type': 'google_search', 'search_types': ['web_search']}])
        self.assertNotIn('generation_config', body)

    def test_uncited_or_ungrounded_candidates_are_diagnosed_and_rejected(self):
        output = {'steps': [{'type': 'model_output', 'content': [{'text': 'annual_report | 2026-01-01 | 2025 | Example annual report'}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())) as request:
            result = discovery.grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', False, 5)
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
            result = discovery.grounded_search({'name': 'Example'}, ['annual_report'], '', '2026-11-02', False, 5)
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
