"""Durable thematic-exposure analysis job tests."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import analysis_jobs
import app
import source_jobs
from test_fixtures import MemoryStore


class AnalysisJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = source_jobs.Queue(Path(self.temp.name) / 'jobs.sqlite3')
        self.store = MemoryStore()
        self.store.put('themes', {'id': 'theme', 'theme_name': 'AI'})
        self.store.put('companies', {'id': 'a', 'name': 'Company A'})
        self.store.put('companies', {'id': 'b', 'name': 'Company B'})
        self.store.put('portfolio_imports', {
            'id': 'portfolio', 'name': 'Portfolio',
            'rows': [{'company_id': 'a'}, {'company_id': 'b'}, {'company_id': 'CASH'},
                     {'company_id': 'b', 'excluded': True}],
        })
        for company_id in ('a', 'b'):
            self.store.put('sources', {
                'id': 'source-' + company_id, 'company_id': company_id,
                'url': 'https://example.test/' + company_id,
                'publication_date': '2026-01-01', 'period': 'FY2025',
            })
        self.identifiers = iter(('analysis-one', 'analysis-two', 'analysis-three'))

        def put(kind, record):
            if kind == 'search_runs':
                self.queue.save(copy.deepcopy(record))
            return self.store.put(kind, copy.deepcopy(record))

        self.app = SimpleNamespace(
            get=self.store.get,
            all_records=self.store.all_records,
            put=put,
            ident=lambda prefix: next(self.identifiers),
            now=lambda: '2026-11-08T00:00:00Z',
        )

    @staticmethod
    def _configured_provider():
        # Gemini test mode intentionally uses the same key as extraction.
        return patch.dict(os.environ, {
            'FINANCIALS_LLM_API_KEY': 'test-key',
            'EXPOSURE_CONFIDENCE_PROVIDER': 'gemini',
            'GEMINI_API_KEY': 'test-key',
        }, clear=False)

    def _run(self, *, force=False):
        return {
            'id': 'analysis-test', 'job_type': 'analysis', 'status': 'running', 'phase': 'queued',
            'theme_id': 'theme', 'portfolio_id': 'portfolio', 'company_ids': ['a', 'b'],
            'companies_completed': [], 'outcomes': {}, 'results': {}, 'errors': [],
            'deferred_company_ids': [], 'force_recalculate': force,
            'allow_unresolved_web_search': False,
        }

    @staticmethod
    def _success(company_id, reused=False):
        return {
            'results': [{
                'company_id': company_id,
                'company_name': 'Company ' + company_id.upper(),
                'case_id': 'case-' + company_id,
                'status': 'reused' if reused else 'success',
                'reused': reused,
                'case': {'id': 'case-' + company_id},
                'fallback_status': 'not_needed',
                'fallback_message': '',
            }],
            'errors': [],
        }

    def test_start_returns_promptly_and_deduplicates_equivalent_active_request(self):
        data = {'theme_id': 'theme', 'portfolio_id': 'portfolio'}
        with self._configured_provider(), \
                patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue), \
                patch.object(analysis_jobs.source_jobs, 'ensure_worker') as ensure:
            first = analysis_jobs.start(self.app, data)['analysis_run']
            duplicate = analysis_jobs.start(self.app, data)['analysis_run']
        self.assertEqual(first['job_type'], 'analysis')
        self.assertEqual(first['phase'], 'queued')
        self.assertEqual(first['company_ids'], ['a', 'b'])
        self.assertEqual(duplicate['id'], first['id'])
        self.assertEqual(len(self.queue.records()), 1)
        self.assertEqual(ensure.call_count, 2)

    def test_start_validates_per_company_scope_and_carries_force_rerun(self):
        with self._configured_provider(), \
                patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue), \
                patch.object(analysis_jobs.source_jobs, 'ensure_worker'):
            run = analysis_jobs.start(self.app, {
                'theme_id': 'theme', 'portfolio_id': 'portfolio', 'company_ids': ['b'],
                'force_recalculate': True, 'allow_unresolved_web_search': False,
            })['analysis_run']
        self.assertEqual(run['company_ids'], ['b'])
        self.assertTrue(run['force_recalculate'])
        with self._configured_provider(), self.assertRaisesRegex(ValueError, 'selected portfolio'):
            analysis_jobs.start(self.app, {
                'theme_id': 'theme', 'portfolio_id': 'portfolio', 'company_ids': ['missing'],
            })

    def test_analysis_is_not_deduplicated_against_an_active_financial_job(self):
        financial = {'id': 'financial-running', 'job_type': 'financials', 'status': 'running',
                     'portfolio_id': 'portfolio'}
        self.queue.enqueue(financial, [])
        with self._configured_provider(), \
                patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue), \
                patch.object(analysis_jobs.source_jobs, 'ensure_worker'):
            run = analysis_jobs.start(self.app, {
                'theme_id': 'theme', 'portfolio_id': 'portfolio',
            })['analysis_run']
        self.assertEqual(run['job_type'], 'analysis')
        self.assertNotEqual(run['id'], financial['id'])
        self.assertEqual(len(self.queue.records()), 2)

    def test_omitted_company_ids_include_source_less_holdings_for_web_discovery(self):
        self.store.put('companies', {'id': 'no-source', 'name': 'No source'})
        portfolio = self.store.get('portfolio_imports', 'portfolio')
        portfolio['rows'].append({'company_id': 'no-source'})
        with self._configured_provider(), \
                patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue), \
                patch.object(analysis_jobs.source_jobs, 'ensure_worker'):
            run = analysis_jobs.start(self.app, {
                'theme_id': 'theme', 'portfolio_id': 'portfolio',
            })['analysis_run']
        self.assertEqual(run['company_ids'], ['a', 'b', 'no-source'])

        disabled_queue = source_jobs.Queue(Path(self.temp.name) / 'disabled.sqlite3')
        with self._configured_provider(), \
                patch.object(analysis_jobs.source_jobs, 'Queue', return_value=disabled_queue), \
                patch.object(analysis_jobs.source_jobs, 'ensure_worker'):
            disabled = analysis_jobs.start(self.app, {
                'theme_id': 'theme', 'portfolio_id': 'portfolio',
                'allow_unresolved_web_search': False,
            })['analysis_run']
        self.assertEqual(disabled['company_ids'], ['a', 'b'])

    def test_start_rejects_missing_analysis_provider_configuration_before_queueing(self):
        with patch.dict(os.environ, {
                'FINANCIALS_LLM_API_KEY': '', 'CHECKER_LLM_API_KEY': '',
                'CLAUDE_API_KEY': '', 'ANTHROPIC_API_KEY': '',
                'EXPOSURE_CONFIDENCE_PROVIDER': 'claude'}, clear=False), \
                patch.object(analysis_jobs.source_jobs, 'ensure_worker') as ensure:
            with self.assertRaisesRegex(ValueError, 'FINANCIALS_LLM_API_KEY'):
                analysis_jobs.start(self.app, {'theme_id': 'theme', 'portfolio_id': 'portfolio'})
        ensure.assert_not_called()

    def test_worker_checkpoints_each_company_and_forwards_force_rerun(self):
        run = self._run(force=True)
        companies = [{'id': 'a', 'name': 'Company A'}, {'id': 'b', 'name': 'Company B'}]
        self.queue.enqueue(run, companies)
        self.app.analyze_all = Mock(side_effect=[self._success('a'), self._success('b', reused=True)])
        analysis_jobs.worker(self.app, run, companies)
        saved, _ = self.queue.job('analysis-test')
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['companies_completed'], ['a', 'b'])
        self.assertEqual({company_id: outcome['status'] for company_id, outcome in saved['outcomes'].items()},
                         {'a': 'success', 'b': 'reused'})
        self.assertEqual((saved['analysed_count'], saved['calculated_count'], saved['reused_count'], saved['failed_count']),
                         (2, 1, 1, 0))
        self.assertEqual(saved['results']['a']['case_id'], 'case-a')
        self.assertNotIn('case', saved['results']['a'])
        self.assertEqual(self.app.analyze_all.call_args_list[0].args[0], {
            'theme_id': 'theme', 'portfolio_id': 'portfolio', 'company_ids': ['a'],
            'force_recalculate': True, 'allow_unresolved_web_search': False,
            'analysis_job_id': 'analysis-test',
        })

    def test_quota_failure_stops_remaining_companies_without_repeating_failures(self):
        run = self._run()
        companies = [{'id': 'a', 'name': 'Company A'}, {'id': 'b', 'name': 'Company B'}]
        self.queue.enqueue(run, companies)
        self.app.analyze_all = Mock(return_value={
            'results': [],
            'errors': [{'company_id': 'a', 'error': 'Provider HTTP 429'}],
        })
        analysis_jobs.worker(self.app, run, companies)
        saved, _ = self.queue.job('analysis-test')
        self.assertEqual(self.app.analyze_all.call_count, 1)
        self.assertEqual(saved['status'], 'incomplete')
        self.assertEqual(saved['completion_reason'], 'provider_quota')
        self.assertEqual(saved['deferred_company_ids'], ['b'])
        self.assertEqual(saved['outcomes']['a']['status'], 'failed')
        self.assertIn('HTTP 429', saved['outcomes']['a']['error'])

    def test_fallback_provider_quota_defers_current_and_remaining_companies(self):
        run = self._run()
        run['allow_unresolved_web_search'] = True
        companies = [{'id': 'a', 'name': 'Company A'}, {'id': 'b', 'name': 'Company B'}]
        self.queue.enqueue(run, companies)
        first = self._success('a')
        first['results'][0].update(
            fallback_status='analysis_deferred',
            fallback_message='Follow-up analysis is waiting for Gemini provider quota or rate limit: Provider HTTP 429',
            fallback_search_attempted=True,
        )
        self.app.analyze_all = Mock(return_value=first)
        analysis_jobs.worker(self.app, run, companies)
        saved, _ = self.queue.job('analysis-test')
        self.app.analyze_all.assert_called_once()
        self.assertEqual(saved['status'], 'incomplete')
        self.assertEqual(saved['completion_reason'], 'provider_quota')
        self.assertEqual(saved['deferred_company_ids'], ['a', 'b'])
        self.assertEqual(saved['outcomes']['a']['status'], 'deferred')
        self.assertIn('HTTP 429', saved['outcomes']['a']['error'])

    def test_worker_resumes_after_a_checkpoint_without_repeating_completed_company(self):
        run = self._run()
        companies = [{'id': 'a', 'name': 'Company A'}, {'id': 'b', 'name': 'Company B'}]
        already_saved = self._success('a')['results'][0]
        run.update(
            results={'a': already_saved},
            outcomes={'a': already_saved},
            companies_completed=['a'],
        )
        self.queue.enqueue(run, companies)
        self.app.analyze_all = Mock(return_value=self._success('b'))
        analysis_jobs.worker(self.app, run, companies)
        self.app.analyze_all.assert_called_once_with({
            'theme_id': 'theme', 'portfolio_id': 'portfolio', 'company_ids': ['b'],
            'force_recalculate': False, 'allow_unresolved_web_search': False,
            'analysis_job_id': 'analysis-test',
        })
        saved, _ = self.queue.job('analysis-test')
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['companies_completed'], ['a', 'b'])

    def test_worker_recovers_a_case_saved_before_its_checkpoint(self):
        run = self._run(force=True)
        run['inflight_company_id'] = 'a'
        companies = [{'id': 'a', 'name': 'Company A'}, {'id': 'b', 'name': 'Company B'}]
        self.store.put('cases', {
            'id': 'case-recovered', 'company_id': 'a', 'theme_id': 'theme',
            'analysis_job_id': 'analysis-test', 'created_at': '2026-11-08T00:00:01Z',
        })
        self.queue.enqueue(run, companies)
        self.app.analyze_all = Mock(return_value=self._success('b'))
        analysis_jobs.worker(self.app, run, companies)
        self.app.analyze_all.assert_called_once_with({
            'theme_id': 'theme', 'portfolio_id': 'portfolio', 'company_ids': ['b'],
            'force_recalculate': True, 'allow_unresolved_web_search': False,
            'analysis_job_id': 'analysis-test',
        })
        saved, _ = self.queue.job('analysis-test')
        self.assertEqual(saved['results']['a']['case_id'], 'case-recovered')
        self.assertNotIn('inflight_company_id', saved)

    def test_worker_enforces_one_fallback_search_budget_across_company_calls(self):
        run = self._run()
        run['allow_unresolved_web_search'] = True
        companies = [{'id': 'a', 'name': 'Company A'}, {'id': 'b', 'name': 'Company B'}]
        self.queue.enqueue(run, companies)
        first = self._success('a')
        first['results'][0]['fallback_search_attempted'] = True
        second = self._success('b')
        # The mock advertises a search, but the worker must not have granted
        # one to the second call after the run-level budget was consumed.
        second['results'][0]['fallback_search_attempted'] = True
        self.app.unresolved_search_batch_limit = lambda: 1
        self.app.analyze_all = Mock(side_effect=[first, second])
        analysis_jobs.worker(self.app, run, companies)
        calls = [call.args[0] for call in self.app.analyze_all.call_args_list]
        self.assertTrue(calls[0]['allow_unresolved_web_search'])
        self.assertFalse(calls[1]['allow_unresolved_web_search'])
        saved, _ = self.queue.job('analysis-test')
        self.assertEqual(saved['fallback_searches'], 1)
        self.assertEqual(saved['fallback_search_limit'], 1)

    def test_status_returns_only_analysis_queue_records(self):
        run = self._run()
        self.queue.enqueue(run, [])
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            self.assertEqual(analysis_jobs.status({'id': 'analysis-test'})['analysis_run']['id'], 'analysis-test')
        self.queue.enqueue({'id': 'not-analysis', 'job_type': 'financials', 'status': 'running', 'portfolio_id': 'other'}, [])
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            with self.assertRaisesRegex(ValueError, 'not found'):
                analysis_jobs.status({'id': 'not-analysis'})

    def test_cancel_marks_active_analysis_terminal_in_the_durable_queue(self):
        run = self._run()
        run.update(
            results={'a': {'status': 'success', 'case_id': 'case-a'}},
            outcomes={'a': {'status': 'success', 'case_id': 'case-a'}},
            companies_completed=['a'], analysed_count=1, calculated_count=1,
            reused_count=0, failed_count=0,
        )
        self.queue.enqueue(run, [])
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            response = analysis_jobs.cancel(self.app, {'id': '  analysis-test  '})
        saved, _ = self.queue.job('analysis-test')
        self.assertEqual(response['analysis_run']['status'], 'cancelled')
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['cancel_reason'], 'Analysis cancelled by user.')
        self.assertEqual(saved['results'], run['results'])
        self.assertEqual(saved['outcomes'], run['outcomes'])
        self.assertEqual(saved['companies_completed'], ['a'])
        self.assertEqual((saved['analysed_count'], saved['calculated_count'],
                          saved['reused_count'], saved['failed_count']), (1, 1, 0, 0))
        self.assertEqual(self.store.get('search_runs', run['id'])['status'], 'cancelled')
        self.assertIsNone(self.queue.next_job())

    def test_cancel_cannot_target_another_durable_job_type(self):
        financial = {
            'id': 'financial-running', 'job_type': 'financials',
            'status': 'running', 'portfolio_id': 'portfolio',
        }
        self.queue.enqueue(financial, [])
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            with self.assertRaisesRegex(ValueError, '^Analysis run not found$'):
                analysis_jobs.cancel(self.app, {'id': financial['id']})
        saved, _ = self.queue.job(financial['id'])
        self.assertEqual(saved['status'], 'running')
        self.assertIsNotNone(self.queue.next_job())

    def test_cancel_validates_missing_and_unknown_run_ids(self):
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            with self.assertRaisesRegex(ValueError, '^Analysis run ID is required$'):
                analysis_jobs.cancel(self.app, {'id': '  '})
            with self.assertRaisesRegex(ValueError, '^Analysis run not found$'):
                analysis_jobs.cancel(self.app, {'id': 'missing'})

    def test_cancel_is_idempotent_after_an_analysis_has_finished(self):
        run = self._run()
        self.queue.enqueue(run, [])
        self.queue.save({**run, 'status': 'completed', 'phase': 'finished'})
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            response = analysis_jobs.cancel(self.app, {'id': run['id']})
        saved, _ = self.queue.job(run['id'])
        self.assertEqual(response['analysis_run']['status'], 'completed')
        self.assertEqual(saved['status'], 'completed')
        self.assertNotIn('cancel_reason', saved)

    def test_cancel_succeeds_when_the_remote_mirror_is_unavailable(self):
        run = self._run()
        self.queue.enqueue(run, [])
        unavailable = SimpleNamespace(put=Mock(side_effect=RuntimeError('offline')))
        with patch.object(analysis_jobs.source_jobs, 'Queue', return_value=self.queue):
            response = analysis_jobs.cancel(unavailable, {'id': run['id']})
        self.assertEqual(response['analysis_run']['status'], 'cancelled')
        self.assertEqual(self.queue.job(run['id'])[0]['status'], 'cancelled')

    def test_remote_mirror_cannot_regress_a_cancelled_run_to_running(self):
        run = self._run()
        self.queue.enqueue(run, [])
        cancelled = self.queue.cancel(run['id'], 'Analysis cancelled by user.')
        stale = {**run, 'phase': 'analysing', 'analysed_count': 1}
        with patch.object(app, 'store', self.store), \
                patch.object(source_jobs, 'Queue', return_value=self.queue):
            app.put('search_runs', stale)
        self.assertEqual(self.store.get('search_runs', run['id']), cancelled)

    def test_queue_dispatches_analysis_worker(self):
        run = self._run()
        self.queue.enqueue(run, [{'id': 'a', 'name': 'Company A'}])

        def complete(worker_app, queued_run, companies):
            queued_run['status'] = 'completed'
            worker_app.put('search_runs', queued_run)

        with patch.object(source_jobs, 'database_path', return_value=self.queue.path), \
                patch.object(analysis_jobs, 'worker', side_effect=complete) as worker:
            source_jobs.execute('analysis-test')
        self.assertEqual(worker.call_args.args[1]['job_type'], 'analysis')
        self.assertEqual(worker.call_args.args[2], [{'id': 'a', 'name': 'Company A'}])
        self.assertEqual(self.queue.job('analysis-test')[0]['status'], 'completed')


class AnalysisStatusHttpTest(unittest.TestCase):
    def _handler(self, path):
        handler = object.__new__(app.Handler)
        handler.path = path
        handler.headers = {'Host': '127.0.0.1', 'Cookie': 'session'}
        replies = []
        handler.respond = lambda body, status=200, **_: replies.append((body, status))
        return handler, replies

    def test_get_status_reads_query_id_after_authentication(self):
        handler, replies = self._handler('/api/analysis/run?id=analysis%20one')
        with patch.object(app.app_auth, 'valid', return_value=True), \
                patch.object(app, 'analysis_run', return_value={'analysis_run': {'id': 'analysis one'}}) as status:
            handler.do_GET()
        status.assert_called_once_with({'id': 'analysis one'})
        self.assertEqual(replies, [({'analysis_run': {'id': 'analysis one'}}, 200)])

    def test_get_status_requires_authenticated_session(self):
        handler, replies = self._handler('/api/analysis/run?id=analysis-one')
        with patch.object(app.app_auth, 'valid', return_value=False), \
                patch.object(app, 'analysis_run') as status:
            handler.do_GET()
        status.assert_not_called()
        self.assertEqual(replies, [({'error': 'Login required'}, 401)])

    def _post_handler(self, body):
        raw = json.dumps(body).encode()
        handler = object.__new__(app.Handler)
        handler.path = '/api/analysis/cancel'
        handler.headers = {
            'Host': '127.0.0.1',
            'Origin': 'http://127.0.0.1',
            'Cookie': 'session',
            'Content-Length': str(len(raw)),
        }
        handler.rfile = io.BytesIO(raw)
        replies = []
        handler.respond = lambda response, status=200, **_: replies.append((response, status))
        return handler, replies

    def test_post_cancel_dispatches_after_authentication(self):
        handler, replies = self._post_handler({'id': 'analysis-one'})
        response = {'analysis_run': {'id': 'analysis-one', 'status': 'cancelled'}}
        with patch.object(app.app_auth, 'valid', return_value=True), \
                patch.object(app, 'analysis_cancel', return_value=response) as cancel:
            handler.do_POST()
        cancel.assert_called_once_with({'id': 'analysis-one'})
        self.assertEqual(replies, [(response, 200)])

    def test_post_cancel_requires_authenticated_session(self):
        handler, replies = self._post_handler({'id': 'analysis-one'})
        with patch.object(app.app_auth, 'valid', return_value=False), \
                patch.object(app, 'analysis_cancel') as cancel:
            handler.do_POST()
        cancel.assert_not_called()
        self.assertEqual(replies, [({'error': 'Login required'}, 401)])


if __name__ == '__main__':
    unittest.main()
