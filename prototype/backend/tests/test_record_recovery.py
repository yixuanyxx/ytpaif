import base64
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cached_store import CachedStore
import record_recovery
import source_cache
from supabase_store import SupabaseStore
from test_source_cache import FakeRedis


def export(kind, records, total=None):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['kind', 'record_count', 'records_base64', 'total_count'])
    writer.writerow([kind, len(records), base64.b64encode(json.dumps(records).encode()).decode(),
                     len(records) if total is None else total])
    return out.getvalue()


class RecordRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.redis = FakeRedis()
        for patcher in (patch.dict(os.environ, RECORD_RECOVERY_DIR=self.folder.name),
                        patch.object(source_cache, '_connection', return_value=self.redis),
                        patch.object(CachedStore, 'request_backup')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = CachedStore()

    def test_restore_retains_primary_edits_and_tombstones(self):
        self.store.put('companies', {'id': 'a', 'name': 'Current'})
        self.store.delete('companies', 'b')
        result = record_recovery.restore(self.store, export('companies', [
            {'id': 'a', 'name': 'Old'}, {'id': 'b'}, {'id': 'c'}]))
        self.assertEqual(result['counts'], {'companies': 2})
        self.assertEqual(self.store.all_records('companies'), [{'id': 'a', 'name': 'Current'}, {'id': 'c'}])
        self.assertTrue(list(Path(self.folder.name).glob('export-*.json')))

    def test_cold_redis_recovers_from_disk_without_supabase(self):
        record_recovery.restore(self.store, export('themes', [{'id': 'theme'}]))
        self.redis.values.clear()
        with patch.object(SupabaseStore, 'all_records', side_effect=AssertionError('No backup request')):
            self.assertEqual(CachedStore().all_records('themes'), [{'id': 'theme'}])

    def test_partial_batches_do_not_claim_complete_snapshot(self):
        first = export('sources', [{'id': 'a'}], 2)
        result = record_recovery.restore(self.store, first)
        self.assertEqual(result['staged']['sources']['received'], 1)
        self.assertNotIn('sources', result['counts'])
        self.assertIsNone(self.store._cached('sources', ['all']))
        record_recovery.restore(self.store, first)  # Safe retry does not duplicate rows.
        result = record_recovery.restore(self.store, export('sources', [{'id': 'b'}], 2))
        self.assertEqual(result['counts'], {'sources': 2})
        self.assertTrue(result['staged']['sources']['committed'])
        self.assertEqual({r['id'] for r in self.store.all_records('sources')}, {'a', 'b'})

    def test_completed_batches_are_safe_to_replay_without_recommitting(self):
        first = export('sources', [{'id': 'a'}], 2)
        second = export('sources', [{'id': 'b'}], 2)
        record_recovery.restore(self.store, first)
        record_recovery.restore(self.store, second)
        with patch.object(self.store, '_save', side_effect=AssertionError('must not recommit')):
            replay = record_recovery.restore(self.store, first)
        self.assertEqual(replay['counts'], {})
        self.assertTrue(replay['staged']['sources']['committed'])
        self.assertEqual({r['id'] for r in self.store.all_records('sources')}, {'a', 'b'})

    def test_invalid_exports_cannot_change_redis(self):
        for text in (export('../outside', [{'id': 'a'}]),
                     export('companies', [{'id': 'a'}, {'id': 'a'}]),
                     export('companies', [{}]),
                     export('companies', [{'id': 'a'}]).replace(',1,', ',2,', 1)):
            with self.assertRaises((ValueError, KeyError)):
                record_recovery.restore(self.store, text)
        self.assertEqual(self.redis.values, {})

    def test_disk_failure_stops_recovery_before_redis_is_changed(self):
        with patch.object(record_recovery, 'save_file', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                record_recovery.restore(self.store, export('themes', [{'id': 'a'}]))
        self.assertEqual(self.redis.values, {})

    def test_persistence_configuration_is_verified(self):
        config = {'appendonly': 'no'}
        self.redis.config_get = lambda name: {name: config.get(name)}
        self.redis.config_set = lambda name, value: config.update({name: value})
        self.redis.config_rewrite = lambda: None
        result = record_recovery.restore(self.store, export('themes', []))
        self.assertTrue(result['persistence']['aof_enabled'])
        self.assertTrue(result['persistence']['configuration_saved'])
        self.assertEqual(config['maxmemory-policy'], 'noeviction')

    def test_restore_endpoint_requires_authentication_and_same_origin(self):
        import app
        raw = json.dumps({'csv': export('themes', [])}).encode()
        for origin, authenticated, expected in (
                ('http://127.0.0.1', False, 401), ('https://untrusted.example', True, 403)):
            handler = object.__new__(app.Handler)
            handler.path = '/api/records/restore'
            handler.headers = {'Host': '127.0.0.1', 'Origin': origin,
                               'Cookie': 'session', 'Content-Length': str(len(raw))}
            handler.rfile = io.BytesIO(raw)
            replies = []
            handler.respond = lambda body, status=200, **kwargs: replies.append(status)
            with patch.object(app.app_auth, 'valid', return_value=authenticated), \
                    patch.object(record_recovery, 'restore') as restore:
                handler.do_POST()
            restore.assert_not_called()
            self.assertEqual(replies, [expected])
