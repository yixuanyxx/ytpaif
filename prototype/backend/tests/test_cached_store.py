import unittest
from unittest.mock import patch

import source_cache
from cached_store import CachedStore
from supabase_store import SupabaseError, SupabaseStore
from test_source_cache import FakeRedis


class CachedStoreTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.connection = patch.object(source_cache, '_connection', return_value=self.redis)
        self.connection.start()
        self.addCleanup(self.connection.stop)
        self.store = CachedStore()
        self.rows = [{'id': 'a', 'company_id': 'c', 'name': 'Original'}]
        self.outage = SupabaseError('HTTP 402', retryable=True)

    def warm(self, rows=None):
        with patch.object(SupabaseStore, 'all_records', return_value=self.rows if rows is None else rows):
            return self.store.all_records('companies')

    def test_fresh_cache_avoids_repeated_supabase_requests(self):
        self.warm()
        with patch.object(SupabaseStore, 'all_records') as load:
            self.assertEqual(self.store.all_records('companies'), self.rows)
        load.assert_not_called()

    def test_outage_uses_complete_cache_across_store_instances(self):
        self.warm()
        other = CachedStore()
        with patch.object(other, 'FRESH_SECONDS', -1), \
                patch.object(SupabaseStore, 'all_records', side_effect=self.outage):
            self.assertEqual(other.all_records('companies'), self.rows)
        self.assertTrue(other.status()['cached_fallback'])

    def test_uncached_table_is_not_misrepresented_as_empty(self):
        with patch.object(SupabaseStore, 'all_records', side_effect=self.outage):
            with self.assertRaisesRegex(SupabaseError, 'no complete Redis copy'):
                self.store.all_records('companies')

    def test_successful_empty_snapshot_is_valid_fallback(self):
        self.warm([])
        with patch.object(self.store, 'FRESH_SECONDS', -1), \
                patch.object(SupabaseStore, 'all_records', side_effect=self.outage):
            self.assertEqual(self.store.all_records('companies'), [])

    def test_complete_snapshot_satisfies_targeted_outage_reads(self):
        self.warm()
        with patch.object(SupabaseStore, 'find', side_effect=self.outage), \
                patch.object(SupabaseStore, 'find_many', side_effect=self.outage), \
                patch.object(SupabaseStore, 'company_records', side_effect=self.outage):
            self.assertEqual(self.store.find('companies', 'a'), self.rows[0])
            self.assertIsNone(self.store.find('companies', 'missing'))
            self.assertEqual(self.store.find_many('companies', ['a']), self.rows)
            self.assertEqual(self.store.company_records('companies', 'c'), self.rows)

    def test_permission_error_does_not_use_stale_cache(self):
        self.warm()
        with patch.object(self.store, 'FRESH_SECONDS', -1), \
                patch.object(SupabaseStore, 'all_records', side_effect=SupabaseError('HTTP 403')):
            with self.assertRaisesRegex(SupabaseError, '403'):
                self.store.all_records('companies')

    def test_failed_write_is_not_acknowledged_or_cached(self):
        self.warm()
        with patch.object(SupabaseStore, 'put', side_effect=self.outage):
            with self.assertRaises(SupabaseError):
                self.store.put('companies', {'id': 'a', 'name': 'Unsaved'})
        self.assertEqual(self.store.all_records('companies'), self.rows)

    def test_successful_write_and_delete_update_complete_snapshot(self):
        self.warm()
        updated = {'id': 'a', 'name': 'Saved'}
        with patch.object(SupabaseStore, 'put', return_value=updated):
            self.store.put('companies', updated)
        self.assertEqual(self.store.all_records('companies'), [updated])
        with patch.object(SupabaseStore, 'delete'):
            self.store.delete('companies', 'a')
        self.assertEqual(self.store.all_records('companies'), [])

    def test_recovery_refreshes_cache_and_clears_notice(self):
        self.warm()
        with patch.object(self.store, 'FRESH_SECONDS', -1):
            with patch.object(SupabaseStore, 'all_records', side_effect=self.outage):
                self.store.all_records('companies')
            with patch.object(SupabaseStore, 'all_records', return_value=[]):
                self.assertEqual(self.store.all_records('companies'), [])
        self.assertFalse(self.store.status()['cached_fallback'])

    def test_redis_failure_does_not_block_supabase(self):
        with patch.object(source_cache, '_connection', side_effect=RuntimeError('Redis down')), \
                patch.object(SupabaseStore, 'all_records', return_value=self.rows):
            self.assertEqual(self.store.all_records('companies'), self.rows)
