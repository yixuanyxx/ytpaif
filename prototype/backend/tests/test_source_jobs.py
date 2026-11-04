"""Queue recovery tests use real processes and temporary databases, no providers."""
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import source_jobs


class SourceJobsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'jobs.sqlite3'
        self.queue = source_jobs.Queue(self.path)
        self.run = {'id': 'test-job', 'status': 'running', 'results': [], 'results_count': 0,
                    'missing': [{'company_id': 'company', 'source_type': 'annual_report'}]}

    def tearDown(self):
        try:
            self.temp.cleanup()
        except Exception:
            pass

    def test_queue_survives_reopening_without_deadline(self):
        self.queue.enqueue(self.run, [{'id': 'company'}])
        partial = {**self.run, 'results_count': 1, 'results': [{'source_id': 'saved'}]}
        self.queue.save(partial)
        reopened = source_jobs.Queue(self.path)
        self.assertEqual(reopened.next_job()[1], partial)
        self.assertEqual(len(reopened.next_job()), 3)
        with reopened.connect() as db:
            self.assertNotIn('deadline', [column[1] for column in db.execute('PRAGMA table_info(jobs)')])

    def test_uploaded_source_metadata_survives_restart_until_acknowledged(self):
        self.queue.enqueue(self.run, [])
        record = {'id': 'source-a', 'storage_path': 'sources/source-a.pdf', 'url': 'https://example.com/a'}
        self.queue.stage_source('test-job', record)
        reopened = source_jobs.Queue(self.path)
        self.assertEqual(reopened.pending_sources('test-job'), [record])
        reopened.stage_source('test-job', record)
        self.assertEqual(len(reopened.pending_sources('test-job')), 1)
        reopened.acknowledge_sources('test-job', [record])
        self.assertEqual(reopened.pending_sources('test-job'), [])
        self.assertEqual([row[1] for row in reopened.source_updates_after(0)], ['source-a'])

    def test_discovered_sources_are_saved_to_supabase_without_browser_sync(self):
        record = {'id': 'source-a', 'company_id': 'company'}
        store = SimpleNamespace(put_many=Mock(return_value=[record]))
        cache = SimpleNamespace(save_sources_to_redis=Mock(return_value=False))
        app = SimpleNamespace(store=store, source_cache=cache)
        self.assertEqual(source_jobs.persist_discovered_sources(app, [record]), [record])
        store.put_many.assert_called_once_with('sources', [record])
        cache.save_sources_to_redis.assert_called_once_with([record])

    def test_failed_supabase_save_leaves_staged_sources_unacknowledged(self):
        record = {'id': 'source-a'}
        self.queue.enqueue(self.run, [])
        self.queue.stage_source('test-job', record)
        store = SimpleNamespace(put_many=Mock(side_effect=RuntimeError('storage unavailable')))
        cache = SimpleNamespace(save_sources_to_redis=Mock())
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            source_jobs.persist_discovered_sources(SimpleNamespace(store=store, source_cache=cache), [record])
        self.assertEqual(self.queue.pending_sources('test-job'), [record])
        cache.save_sources_to_redis.assert_not_called()

    def test_real_crashed_worker_resumes_from_checkpoint(self):
        self.queue.enqueue(self.run, [])
        script = '''import sys
from source_jobs import Queue
q=Queue(sys.argv[1])
r,_=q.job('test-job')
if not r.get('results_count'):
    r['results_count']=1
    r['results']=[{'source_id':'already-saved'}]
    q.save(r)
    sys.exit(1)
assert r['results'][0]['source_id']=='already-saved'
r['status']='completed'
q.save(r)
'''
        source_jobs.supervise(self.queue, [sys.executable, '-c', script, str(self.path)], idle_seconds=.1)
        result = self.queue.records()[0]
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['results_count'], 1)

    def test_completed_job_cannot_be_resurrected(self):
        self.queue.enqueue(self.run, [])
        self.queue.save({**self.run, 'status': 'completed', 'results_count': 1})
        self.queue.save(self.run)
        self.assertEqual(self.queue.records()[0]['status'], 'completed')
        self.assertEqual(self.queue.records()[0]['results_count'], 1)
        self.assertIsNone(self.queue.next_job())

    def test_terminal_search_queues_email_and_sms_once_across_restart(self):
        run = {**self.run, 'search_mode': 'long'}
        self.queue.enqueue(run, [])
        completed = {**run, 'status': 'completed', 'results_count': 2}
        self.queue.save(completed)
        self.queue.save(completed)
        reopened = source_jobs.Queue(self.path)
        with patch('source_notifications.send', return_value='submitted') as send:
            self.assertTrue(reopened.deliver_next_notification())
            self.assertTrue(reopened.deliver_next_notification())
            self.assertFalse(reopened.deliver_next_notification())
        self.assertEqual([call.args for call in send.call_args_list], [('email', completed), ('sms', completed)])

    def test_cancelled_job_is_terminal_and_does_not_run_again(self):
        self.queue.enqueue(self.run, [])
        cancelled = self.queue.cancel('test-job')
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['cancel_reason'], 'Search cancelled by user.')
        self.assertIsNone(self.queue.next_job())
        self.assertEqual(self.queue.cancel('test-job')['status'], 'cancelled')

    def test_only_one_supervisor_can_own_queue(self):
        with source_jobs.process_lock(str(self.path)+'.worker.lock') as acquired:
            self.assertTrue(acquired)
            with source_jobs.process_lock(str(self.path)+'.worker.lock') as second:
                self.assertFalse(second)

    def test_browser_or_backend_exit_does_not_kill_detached_worker(self):
        # The parent stands in for the HTTP server. Its worker must outlive it.
        script = '''import subprocess,sys
from source_jobs import launch_arguments
child="from source_jobs import Queue; import sys,time; time.sleep(.5); q=Queue(sys.argv[1]); r,_=q.job('test-job'); r['status']='completed'; q.save(r)"
subprocess.Popen([sys.executable,'-c',child,sys.argv[1]], stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **launch_arguments())
'''
        self.queue.enqueue(self.run, [])
        subprocess.run([sys.executable, '-c', script, str(self.path)], check=True, timeout=5)
        until = time.monotonic()+5
        while time.monotonic() < until and self.queue.records()[0]['status'] == 'running':
            time.sleep(.05)
        self.assertEqual(self.queue.records()[0]['status'], 'completed')

    def test_local_terminal_state_overrides_stale_remote_running(self):
        self.queue.enqueue(self.run, [])
        self.queue.save({**self.run, 'status': 'completed'})
        self.assertEqual(self.queue.merge([self.run])[0]['status'], 'completed')

    def test_old_running_run_is_not_stopped_by_elapsed_time(self):
        remote = {**self.run, 'created_at': '2020-01-01T00:00:00+00:00'}
        self.assertEqual(self.queue.merge([remote])[0]['status'], 'running')
        self.assertEqual(remote['status'], 'running')

    def test_migrate_old_queue_preserves_jobs_and_removes_time_limit(self):
        path = Path(self.temp.name) / 'old.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, companies TEXT NOT NULL, deadline REAL NOT NULL, state TEXT NOT NULL)')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', ('test-job', json.dumps(self.run), '[]', 1, 'running'))
        db.close()
        migrated = source_jobs.Queue(path)
        self.assertEqual(migrated.next_job()[1], self.run)
        with migrated.connect() as db:
            self.assertNotIn('deadline', [column[1] for column in db.execute('PRAGMA table_info(jobs)')])

    def test_supervisor_restart_does_not_duplicate_surviving_job(self):
        self.queue.enqueue(self.run, [])
        child = '''import sys,time
from source_jobs import Queue,process_lock
q=Queue(sys.argv[1])
with process_lock(str(q.path)+'.job.lock') as acquired:
    if acquired:
        r,_=q.job('test-job')
        r['attempts']=r.get('attempts',0)+1
        q.save(r)
        time.sleep(1)
        r['status']='completed'
        q.save(r)
'''
        supervisor = '''import sys
from source_jobs import Queue,supervise
supervise(Queue(sys.argv[1]), [sys.executable,'-c',sys.argv[2],sys.argv[1]], idle_seconds=.1)
'''
        process = subprocess.Popen([sys.executable, '-c', supervisor, str(self.path), child])
        try:
            until = time.monotonic()+5
            while not self.queue.records()[0].get('attempts') and time.monotonic() < until:
                time.sleep(.02)
            self.assertEqual(self.queue.records()[0].get('attempts'), 1)
            process.kill()
            process.wait(timeout=3)
            time.sleep(1.2)
            source_jobs.supervise(self.queue, [sys.executable, '-c', child, str(self.path)], idle_seconds=.1)
            self.assertEqual(self.queue.records()[0]['status'], 'completed')
            self.assertEqual(self.queue.records()[0]['attempts'], 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
