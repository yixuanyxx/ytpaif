import threading
import time
import unittest
from unittest.mock import patch

import source_cache


def wait_for_background_task(name, timeout=2):
    """Poll until the named background task has fully finished (its `fn` has
    returned or raised and `run_once_in_background` has cleared its guard),
    so a test can safely inspect state the task was responsible for."""
    deadline = time.time() + timeout
    while name in source_cache._background_tasks_running:
        if time.time() > deadline:
            raise AssertionError(f'background task {name!r} did not finish in time')
        time.sleep(0.005)


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def __getattr__(self, name):
        def queue(*args):
            self.commands.append((name, args))
            return self
        return queue

    def execute(self):
        for name, args in self.commands:
            getattr(self.client, name)(*args)
        return []


class FakeRedis:
    def __init__(self):
        self.values, self.sets = {}, {}

    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = str(value)
    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.sets.pop(key, None)
    def sadd(self, key, *members): self.sets.setdefault(key, set()).update(map(str, members))
    def srem(self, key, *members): self.sets.setdefault(key, set()).difference_update(map(str, members))
    def smembers(self, key): return set(self.sets.get(key, set()))
    def mget(self, keys): return [self.values.get(key) for key in keys]
    def scan_iter(self, match):
        prefix = match.rstrip('*')
        return [key for key in [*self.values, *self.sets] if key.startswith(prefix)]
    def pipeline(self, transaction=True): return FakePipeline(self)


class SourceCacheTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.sources = [{
            'id': 'source-a', 'company_id': 'company-a', 'company_name': 'TSMC',
            'bloomberg_ticker': '2330 TT', 'sedol': '6889106',
            'source_type': 'annual_report', 'url': 'https://example.com/annual.pdf',
            'publication_date': '2025-03-01', 'period': '2025', 'link_status': 'verified',
            'evidence': [{'passage': 'This must not be cached'}],
        }]

    def test_warm_stores_link_metadata_without_document_evidence(self):
        with patch.object(source_cache, '_connection', return_value=self.redis):
            self.assertTrue(source_cache.warm_sources(self.sources, force=True))
            cached = source_cache.cached_sources()
        self.assertEqual(cached[0]['url'], 'https://example.com/annual.pdf')
        self.assertEqual(cached[0]['sedol'], '6889106')
        self.assertNotIn('evidence', cached[0])

    def test_load_or_warm_uses_supabase_once_then_uses_redis(self):
        calls = []
        def load():
            calls.append('supabase')
            return self.sources
        with patch.object(source_cache, '_connection', return_value=self.redis):
            self.assertEqual(source_cache.load_or_warm(load)[0]['id'], 'source-a')
            self.assertEqual(source_cache.load_or_warm(load)[0]['id'], 'source-a')
        self.assertEqual(calls, ['supabase'])

    def test_source_write_updates_existing_cache(self):
        updated = {**self.sources[0], 'id': 'source-b', 'source_type': 'regulatory_filing',
                   'url': 'https://example.com/filing.pdf'}
        with patch.object(source_cache, '_connection', return_value=self.redis):
            source_cache.warm_sources(self.sources, force=True)
            self.assertTrue(source_cache.upsert_sources([updated]))
            cached = source_cache.cached_sources()
        self.assertEqual({row['id'] for row in cached}, {'source-a', 'source-b'})
        self.assertTrue(any(row['source_type'] == 'regulatory_filing' for row in cached))

    def test_delete_source_removes_session_record_and_indexes(self):
        with patch.object(source_cache, '_connection', return_value=self.redis):
            source_cache.warm_sources(self.sources, force=True)
            self.assertTrue(source_cache.delete_source('source-a'))
            self.assertIsNone(source_cache.get_source('source-a'))
            self.assertEqual(source_cache.cached_sources(), [])
            self.assertEqual(source_cache.get_sources_for_stock(company_id='company-a'), [])

    def test_background_task_runs_once_even_when_triggered_concurrently(self):
        calls = []
        started = threading.Event()
        release = threading.Event()

        def slow_task():
            calls.append(1)
            started.set()
            self.assertTrue(release.wait(timeout=2), 'background task was never released')

        self.assertTrue(source_cache.run_once_in_background('demo-task', slow_task))
        self.assertTrue(started.wait(timeout=2), 'background task never started')
        # Triggered again while the first run is still in flight: must not
        # start a second overlapping run.
        self.assertTrue(source_cache.run_once_in_background('demo-task', slow_task))
        release.set()
        self.assertEqual(len(calls), 1)

    def test_background_task_can_retry_after_a_failed_run(self):
        calls = []

        def failing_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('simulated Supabase outage')

        self.assertTrue(source_cache.run_once_in_background('retry-task', failing_once))
        wait_for_background_task('retry-task')
        self.assertNotIn('retry-task', source_cache._background_tasks_running)
        self.assertTrue(source_cache.run_once_in_background('retry-task', failing_once))
        wait_for_background_task('retry-task')
        self.assertEqual(len(calls), 2)

    def test_hydration_runs_once_even_when_triggered_concurrently(self):
        calls = []
        started = threading.Event()
        release = threading.Event()

        def load_sources():
            calls.append(1)
            started.set()
            self.assertTrue(release.wait(timeout=2), 'hydration was never released')
            return self.sources

        with patch.object(source_cache, '_connection', return_value=self.redis):
            self.assertTrue(source_cache.start_background_hydration(load_sources))
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(source_cache.start_background_hydration(load_sources))
            release.set()
        self.assertEqual(len(calls), 1)

    def test_hydration_failure_does_not_mark_the_cache_hydrated_and_can_retry(self):
        from supabase_store import SupabaseError
        calls = []

        def load_sources():
            calls.append(1)
            if len(calls) == 1:
                raise SupabaseError('Supabase outage', retryable=True)
            return self.sources

        with patch.object(source_cache, '_connection', return_value=self.redis):
            self.assertTrue(source_cache.start_background_hydration(load_sources))
            wait_for_background_task('source-hydration')
            self.assertFalse(source_cache.is_hydrated(),
                              'a failed hydration must never be mistaken for an empty-but-complete one')
            self.assertTrue(source_cache.start_background_hydration(load_sources))
            wait_for_background_task('source-hydration')
            self.assertTrue(source_cache.is_hydrated())
        self.assertEqual(len(calls), 2)
