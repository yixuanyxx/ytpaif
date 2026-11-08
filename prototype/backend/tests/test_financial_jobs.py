import copy
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import financial_jobs
import source_jobs
import source_notifications
from test_fixtures import MemoryStore


class FinancialJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = source_jobs.Queue(Path(self.temp.name) / 'jobs.sqlite3')
        self.store = MemoryStore()
        self.companies = [{'id': 'a', 'name': 'Current company name'}, {'id': 'b', 'name': 'Company B'}]
        self.store.put_many('companies', copy.deepcopy(self.companies))
        self.run = {'id': 'financial-test', 'job_type': 'financials', 'portfolio_id': 'p', 'portfolio_name': 'Portfolio',
                    'status': 'running', 'company_ids': ['a', 'b'], 'companies_completed': [],
                    'results': {}, 'outcomes': {}, 'errors': []}
        self.queue.enqueue(self.run, self.companies)

        def put(kind, record):
            if kind == 'search_runs':
                self.queue.save(copy.deepcopy(record))
            return self.store.put(kind, record)

        self.app = SimpleNamespace(store=self.store, get=self.store.get, put=put,
                                   source_link_records=lambda: [], now=lambda: '2026-11-05T00:00:00Z')
        self.fact = {'market_cap': None, 'net_revenue': {
            'value': 'USD 20 million', 'year': '2026', 'supporting_sources': 1, 'evaluated_sources': 1}}

    def test_each_company_is_saved_and_terminal_job_queues_telegram_once(self):
        with patch.object(financial_jobs.financial_research, 'iter_results', return_value=iter([
                ('a', self.fact), ('b', {'error': 'AI quota exceeded'})])):
            financial_jobs.worker(self.app, self.run, self.companies)
        saved, _ = self.queue.job(self.run['id'])
        self.assertEqual(saved['status'], 'incomplete')
        self.assertEqual((saved['updated'], saved['failed'], saved['unresolved']), (1, 1, 0))
        self.assertEqual(self.store.get('companies', 'a')['net_revenue'], 'USD 20 million')
        with patch('source_notifications.send', return_value='submitted') as send:
            self.queue.deliver_next_notification()
            self.assertFalse(self.queue.deliver_next_notification())
        send.assert_called_once_with('telegram', saved)

    def test_crash_after_company_save_replays_cached_result_without_ai(self):
        class WorkerCrash(BaseException):
            pass

        original_put = self.app.put

        def crash_after_company_save(kind, record):
            original_put(kind, record)
            if kind == 'companies':
                raise WorkerCrash()

        self.app.put = crash_after_company_save
        with patch.object(financial_jobs.financial_research, 'iter_results', return_value=iter([('a', self.fact)])):
            with self.assertRaises(WorkerCrash):
                financial_jobs.worker(self.app, self.run, [self.companies[0]])
        recovered, _ = self.queue.job(self.run['id'])
        self.assertEqual(recovered['status'], 'running')
        self.assertIn('a', recovered['results'])
        self.assertNotIn('a', recovered['outcomes'])
        self.app.put = original_put
        with patch.object(financial_jobs.financial_research, 'iter_results') as research:
            financial_jobs.worker(self.app, recovered, [self.companies[0]])
        research.assert_not_called()
        self.assertEqual(self.queue.job(self.run['id'])[0]['status'], 'completed')
        self.assertEqual(self.store.get('companies', 'a')['name'], 'Current company name')

    def test_unresolved_company_keeps_its_existing_financial_values(self):
        company = self.store.get('companies', 'a')
        company['net_revenue'] = 'USD 10 million'
        with patch.object(financial_jobs.financial_research, 'iter_results', return_value=iter([
                ('a', {'market_cap': None, 'net_revenue': None})])):
            financial_jobs.worker(self.app, self.run, [self.companies[0]])
        self.assertEqual(self.store.get('companies', 'a')['net_revenue'], 'USD 10 million')
        self.assertEqual(self.run['unresolved'], 1)

    def test_duplicate_submissions_reuse_active_portfolio_job(self):
        other = {**self.run, 'id': 'duplicate'}
        self.assertEqual(self.queue.enqueue(other, self.companies)['id'], self.run['id'])
        self.assertEqual(len(self.queue.records()), 1)

    def test_cancel_marks_financial_lookup_terminal(self):
        self.app.put = lambda *args: self.fail('Remote store must not delay the durable cancel response')
        with patch.object(financial_jobs.source_jobs, 'Queue', return_value=self.queue), \
                patch.object(financial_jobs.source_jobs, 'ensure_worker'):
            result = financial_jobs.cancel(self.app, {'id': self.run['id']})
        saved, _ = self.queue.job(self.run['id'])
        self.assertEqual(result['financial_run']['status'], 'cancelled')
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['cancel_reason'], 'Financial lookup cancelled by user.')

    def test_worker_stops_when_lookup_is_cancelled(self):
        calls = []
        self.app.is_cancelled = lambda run_id: len(calls) >= 1

        def results(companies, sources, should_cancel=None):
            calls.append('started')
            yield 'a', self.fact
            self.fail('No further company should be processed after cancellation')

        with patch.object(financial_jobs.financial_research, 'iter_results', side_effect=results):
            financial_jobs.worker(self.app, self.run, self.companies)
        self.assertEqual(self.queue.job(self.run['id'])[0]['status'], 'running')

    def test_cancel_during_result_checkpoint_does_not_write_company_financials(self):
        original_put = self.app.put
        self.app.is_cancelled = lambda run_id: self.queue.job(run_id)[0]['status'] == 'cancelled'

        def cancel_at_checkpoint(kind, record):
            original_put(kind, record)
            if kind == 'search_runs' and record.get('results'):
                self.queue.cancel(self.run['id'])

        self.app.put = cancel_at_checkpoint
        with patch.object(financial_jobs.financial_research, 'iter_results', return_value=iter([('a', self.fact)])):
            financial_jobs.worker(self.app, self.run, self.companies)
        self.assertNotIn('net_revenue', self.store.get('companies', 'a'))
        self.assertEqual(self.queue.job(self.run['id'])[0]['status'], 'cancelled')

    def test_execute_dispatches_financial_job_to_financial_worker(self):
        def complete(app, run, companies):
            self.queue.save({**run, 'status': 'completed'})

        with patch.object(source_jobs, 'database_path', return_value=self.queue.path), \
                patch.object(financial_jobs, 'worker', side_effect=complete) as worker, \
                patch('source_notifications.send', return_value='submitted') as send:
            source_jobs.execute(self.run['id'])
        self.assertEqual(worker.call_args.args[1]['job_type'], 'financials')
        self.assertEqual(worker.call_args.args[2], self.companies)
        send.assert_called_once()

    def test_telegram_message_describes_financial_results(self):
        run = {**self.run, 'status': 'completed', 'companies_completed': ['a', 'b'],
               'updated': 2, 'failed': 0, 'unresolved': 0}
        with patch.dict(source_notifications.os.environ, {'SEARCH_TELEGRAM_BOT_TOKEN': '123:test_token'}), \
                patch.object(source_notifications, 'chat_ids', return_value=['123']), \
                patch.object(source_notifications, 'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(b'{"ok":true}')
            self.assertEqual(source_notifications.send('telegram', run), 'submitted')
        payload = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertIn('Market Cap & Revenue lookup completed', payload['text'])
        self.assertIn('2 of 2 companies processed', payload['text'])

    def test_detached_financial_job_finishes_after_parent_exits(self):
        child = '''import sys,time
time.sleep(.5)
import app,financial_research,source_jobs,source_notifications
from urllib.request import OpenerDirector
def offline(*args,**kwargs):
    raise AssertionError('Live HTTP disabled in worker test')
OpenerDirector.open=offline
from test_fixtures import MemoryStore
app.store=MemoryStore()
app.store.put('companies', {'id':'a','name':'Company A'})
app.store.put('companies', {'id':'b','name':'Company B'})
app.source_link_records=lambda: []
financial_research.iter_results=lambda companies,sources,should_cancel=None: iter((company['id'],{'market_cap':None,'net_revenue':None}) for company in companies)
source_notifications.send=lambda channel,run: 'submitted'
source_jobs.database_path=lambda: __import__('pathlib').Path(sys.argv[1])
source_jobs.execute('financial-test')
'''
        parent = '''import subprocess,sys
from source_jobs import launch_arguments
subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]],stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**launch_arguments())
'''
        tests_directory = str(Path(__file__).resolve().parent)
        python_path = os.pathsep.join((str(source_jobs.ROOT), tests_directory))
        with patch.dict(os.environ, {'PYTHONPATH': python_path}):
            subprocess.run([sys.executable, '-c', parent, str(self.queue.path), child], check=True, timeout=5)
        until = time.monotonic() + 10
        while self.queue.job(self.run['id'])[0]['status'] == 'running' and time.monotonic() < until:
            time.sleep(.05)
        saved, _ = self.queue.job(self.run['id'])
        self.assertEqual(saved['status'], 'incomplete')
        self.assertEqual(saved['unresolved'], 2)
        with self.queue.connect() as db:
            state = db.execute('SELECT state FROM completion_notifications WHERE job_id=?', (self.run['id'],)).fetchone()[0]
        self.assertEqual(state, 'submitted')
