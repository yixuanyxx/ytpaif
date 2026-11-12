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
        self.background = patch.object(CachedStore, 'request_backup')
        self.background.start()
        self.addCleanup(self.background.stop)
        self.store = CachedStore()
        self.rows = [{'id': 'a', 'company_id': 'c', 'name': 'Original'}]
        self.outage = SupabaseError('HTTP 402', retryable=True)

    def warm(self):
        with patch.object(SupabaseStore, 'all_records', return_value=self.rows):
            self.assertEqual(self.store.all_records('companies'), self.rows)

    def test_primary_reads_never_refresh_from_backup(self):
        self.warm()
        with patch.object(SupabaseStore, 'all_records', side_effect=AssertionError('backup read')):
            self.assertEqual(CachedStore().all_records('companies'), self.rows)
            self.assertEqual(self.store.find('companies', 'a'), self.rows[0])
            self.assertEqual(self.store.company_records('companies', 'c'), self.rows)

    def test_outage_accepts_writes_across_instances_and_retries_backup(self):
        self.warm()
        updated = {'id': 'a', 'name': 'Changed'}
        with patch.object(SupabaseStore, 'put', side_effect=self.outage):
            self.assertEqual(self.store.put('companies', updated), updated)
            self.assertEqual(self.store.sync_backup(), 0)
        other = CachedStore()
        self.assertEqual(other.find('companies', 'a'), updated)
        self.assertEqual(other.status()['pending'], 1)
        with patch.object(SupabaseStore, 'put', return_value=updated) as backup:
            self.assertEqual(other.sync_backup(), 1)
        backup.assert_called_once_with('companies', updated)
        self.assertEqual(other.status()['pending'], 0)

    def test_cold_outage_retains_new_records_and_marks_missing_history(self):
        new = {'id': 'new', 'name': 'New'}
        self.store.put('companies', new)
        with patch.object(SupabaseStore, 'all_records', side_effect=self.outage):
            self.assertEqual(CachedStore().all_records('companies'), [new])
            self.assertEqual(self.store.all_records('search_runs'), [])
        self.assertIn('search_runs', self.store.status()['stale_kinds'])

    def test_restoration_merges_pending_edits_and_deletes_over_backup(self):
        self.store.put('companies', {'id': 'b', 'name': 'Local'})
        self.store.delete('companies', 'a')
        with patch.object(SupabaseStore, 'all_records', return_value=self.rows):
            self.assertEqual(self.store.all_records('companies'), [{'id': 'b', 'name': 'Local'}])
        with patch.object(SupabaseStore, 'put'), patch.object(SupabaseStore, 'delete') as delete:
            self.store.sync_backup()
        delete.assert_called_once_with('companies', 'a')
        self.assertEqual(self.store.status()['pending'], 0)
        self.assertEqual(self.store.all_records('companies'), [{'id': 'b', 'name': 'Local'}])

    def test_repeated_edit_backs_up_latest_value(self):
        self.warm()
        self.store.put('companies', {'id': 'a', 'name': 'First'})
        self.store.put('companies', {'id': 'a', 'name': 'Latest'})
        with patch.object(SupabaseStore, 'put') as backup:
            self.store.sync_backup()
        backup.assert_called_once_with('companies', {'id': 'a', 'name': 'Latest'})

    def test_permission_error_on_restore_is_not_hidden(self):
        with patch.object(SupabaseStore, 'all_records', side_effect=SupabaseError('403')):
            with self.assertRaises(SupabaseError):
                self.store.all_records('companies')

    def test_failed_redis_write_cannot_be_acknowledged(self):
        with patch.object(source_cache, '_connection', return_value=None):
            with self.assertRaisesRegex(SupabaseError, 'Redis primary'):
                self.store.put('companies', {'id': 'a'})

    def test_edit_during_backup_remains_pending(self):
        self.warm()
        self.store.put('companies', {'id': 'a', 'name': 'First'})
        def edit_during_backup(kind, record):
            self.store.put(kind, {'id': 'a', 'name': 'Concurrent'})
        with patch.object(SupabaseStore, 'put', side_effect=edit_during_backup):
            self.store.sync_backup()
        self.assertEqual(self.store.status()['pending'], 1)
        self.assertEqual(self.store.find('companies', 'a')['name'], 'Concurrent')

    def test_bulk_write_and_delete_survive_backup_outage(self):
        self.warm()
        self.store.put_many('companies', [{'id': 'b'}, {'id': 'c'}])
        self.store.delete('companies', 'a')
        with patch.object(SupabaseStore, 'put', side_effect=self.outage), \
                patch.object(SupabaseStore, 'delete', side_effect=self.outage):
            self.store.sync_backup()
        self.assertEqual({r['id'] for r in CachedStore().all_records('companies')}, {'b', 'c'})
        self.assertEqual(self.store.status()['pending'], 3)
