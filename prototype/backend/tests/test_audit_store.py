import importlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import audit_store


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / 'audit.sqlite'
        self.env = patch.dict(os.environ, {'THEME_DATA_DIR': self.temp.name,
            'SUPABASE_URL': 'https://audit.example', 'SUPABASE_SECRET_KEY': 'sb_secret_test'})
        self.env.start()
        with sqlite3.connect(self.database) as conn:
            audit_store.initialize(conn)
            audit_store.enqueue(conn, 'request.input', {'input': 'test'})

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_failure_retries_with_same_event_id(self):
        failed = HTTPError('https://audit.example', 503, 'busy', {}, None)
        captured = []
        def success(request, **kwargs):
            captured.extend(json.loads(request.data))
            self.assertEqual(request.get_header('Apikey'), 'sb_secret_test')
            self.assertIsNone(request.get_header('Authorization'))
            return io.BytesIO(b'')
        with patch.object(audit_store, 'urlopen', side_effect=failed):
            self.assertEqual(audit_store.sync_once(self.database)['error'], 'HTTP 503')
        with sqlite3.connect(self.database) as conn:
            event_id, attempts, error = conn.execute('SELECT event_id,attempts,last_error FROM audit_outbox').fetchone()
            self.assertEqual((attempts, error), (1, 'HTTP 503'))
        with patch.object(audit_store, 'urlopen', side_effect=success) as send:
            self.assertEqual(audit_store.sync_once(self.database)['sent'], 1)
            self.assertEqual(audit_store.sync_once(self.database)['sent'], 0)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(captured[0]['event_id'], event_id)

    def test_disabled_sync_makes_no_network_call(self):
        with patch.dict(os.environ, {'SUPABASE_URL': ''}), patch.object(audit_store, 'urlopen') as send:
            self.assertFalse(audit_store.sync_once(self.database)['configured'])
            send.assert_not_called()

    def test_secrets_redacted_upload_omitted_model_text_kept(self):
        payload = audit_store.sanitize({'api_key': 'private', 'nested': {'password': 'private'},
            'upload': {'filename': 'test.pdf', 'content': 'base64'},
            'model': {'role': 'assistant', 'content': 'actual output'}})
        self.assertNotIn('private', json.dumps(payload))
        self.assertEqual(payload['upload']['content']['omitted'], 'file content')
        self.assertEqual(payload['model']['content'], 'actual output')

    def test_changes_are_atomic_with_audit_events(self):
        import app
        app = importlib.reload(app)
        app.put('companies', {'id': 'a', 'name': 'Before'})
        with patch.object(audit_store, 'enqueue', side_effect=RuntimeError('disk error')):
            with self.assertRaises(RuntimeError):
                app.put('companies', {'id': 'a', 'name': 'After'})
        self.assertEqual(app.get('companies', 'a')['name'], 'Before')
        app.put('companies', {'id': 'a', 'name': 'After'})
        with app.db() as conn:
            body = json.loads(conn.execute('SELECT body FROM audit_outbox ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        self.assertEqual(body['payload']['before']['name'], 'Before')
        self.assertEqual(body['payload']['after']['name'], 'After')

    def test_request_input_and_failure_share_id(self):
        import app
        app = importlib.reload(app)
        with self.assertRaises(ValueError):
            app.dispatch('/api/theme', {'theme_name': ''})
        with app.db() as conn:
            bodies = [json.loads(row[0]) for row in conn.execute('SELECT body FROM audit_outbox')]
        self.assertEqual([b['event_type'] for b in bodies], ['request.input', 'request.failed'])
        self.assertTrue(bodies[0]['request_id'])
        self.assertEqual(bodies[0]['request_id'], bodies[1]['request_id'])
        self.assertIsNone(audit_store.request_id.get())

    def test_backfill_is_idempotent_and_labelled_baseline(self):
        import app
        app = importlib.reload(app)
        import sync_audit
        sync_audit.app = app
        app.put('companies', {'id': 'a', 'name': 'Existing'})
        self.assertEqual(sync_audit.backfill(), 1)
        self.assertEqual(sync_audit.backfill(), 0)
        with app.db() as conn:
            bodies = [json.loads(row[0]) for row in conn.execute('SELECT body FROM audit_outbox')]
        self.assertEqual(bodies[-1]['event_type'], 'record.baseline')

    def test_each_view_tags_inputs_outputs_and_record_changes(self):
        import app
        app = importlib.reload(app)
        for view, expected in [('overview', 'overview'), ('theme', 'theme_definition'),
                               ('portfolio', 'portfolio_import'), ('sources', 'source_documents'),
                               ('analysis', 'analysis_review'), ('review', 'analysis_review'),
                               ('evaluate', 'evaluate')]:
            app.audited_action('/api/export', {'view': view},
                lambda: app.put('companies', {'id': view, 'name': view}), requested_view=view)
        with app.db() as conn:
            events = [json.loads(r[0]) for r in conn.execute('SELECT body FROM audit_outbox')]
        self.assertEqual(len(events), 21)
        for index, expected in enumerate(['overview','theme_definition','portfolio_import',
                                         'source_documents','analysis_review','analysis_review','evaluate']):
            group = events[index * 3:index * 3 + 3]
            self.assertEqual([e['event_type'] for e in group], ['request.input','record.created','request.output'])
            self.assertEqual({e['view_name'] for e in group}, {expected})
            self.assertEqual(len({e['request_id'] for e in group}), 1)
        self.assertIsNone(audit_store.view_name.get())

    def test_dashboard_does_not_queue_audit_events(self):
        import app
        app = importlib.reload(app)
        self.assertEqual(app.audited_action('/api/dashboard', {}, lambda: {'result': 1}), {'result': 1})
        app.audited_action('/api/export', {}, lambda: b'zip', requested_view='dashboard')
        with app.db() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM audit_outbox').fetchone()[0], 0)

    def test_fallback_view_and_context_cleanup_after_failure(self):
        import app
        app = importlib.reload(app)
        for path, view in [('/api/generate','theme_definition'),('/api/portfolio/import','portfolio_import'),
                           ('/api/source/search','source_documents'),('/api/review','analysis_review'),
                           ('/api/evaluate','evaluate')]:
            self.assertEqual(audit_store.resolve_view(path), view)
        with self.assertRaises(ValueError):
            app.audited_action('/api/generate', {}, lambda: (_ for _ in ()).throw(ValueError('failed')))
        self.assertIsNone(audit_store.view_name.get())
        with app.db() as conn:
            events = [json.loads(r[0]) for r in conn.execute('SELECT body FROM audit_outbox')]
        self.assertEqual({e['view_name'] for e in events}, {'theme_definition'})

    def test_sync_writes_directly_to_combined_view_tables(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute('DELETE FROM audit_outbox')
            audit_store.enqueue(conn, 'request.input', {'path': '/api/generate'})
            audit_store.enqueue(conn, 'request.output', {'path': '/api/generate', 'output': {}})
            audit_store.enqueue(conn, 'request.input', {'path': '/api/source/search'})
        requests = []
        def send(request, **kwargs):
            requests.append((request.full_url, json.loads(request.data)))
            return io.BytesIO(b'')
        with patch.object(audit_store, 'urlopen', side_effect=send):
            self.assertEqual(audit_store.sync_once(self.database)['sent'], 3)
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0][0].endswith('/theme_definition?on_conflict=event_id'))
        self.assertEqual([e['event_type'] for e in requests[0][1]], ['request.input','request.output'])
        self.assertTrue(requests[1][0].endswith('/source_documents?on_conflict=event_id'))
        self.assertNotIn('theme_audit_events', str(requests))

    def test_failed_table_does_not_mark_other_tables_pending(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute('DELETE FROM audit_outbox')
            audit_store.enqueue(conn, 'request.input', {'path': '/api/generate'})
            audit_store.enqueue(conn, 'request.input', {'path': '/api/source/search'})
        def send(request, **kwargs):
            if 'theme_definition?' in request.full_url:
                raise HTTPError(request.full_url, 503, 'busy', {}, None)
            return io.BytesIO(b'')
        with patch.object(audit_store, 'urlopen', side_effect=send):
            result = audit_store.sync_once(self.database)
        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['error'], 'HTTP 503')
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(audit_store.status(conn)['pending'], 1)
            self.assertEqual(audit_store.status(conn)['synced'], 1)

    def test_theme_save_and_update_sync_immediately_with_exact_request(self):
        import app
        app = importlib.reload(app)
        captured = []
        def send(request, **kwargs):
            self.assertTrue(request.full_url.endswith('/theme_definition?on_conflict=event_id'))
            events = json.loads(request.data)
            self.assertEqual(len({e['request_id'] for e in events}), 1)
            captured.extend(events)
            return io.BytesIO(b'')
        with patch.object(audit_store, 'urlopen', side_effect=send):
            saved = app.dispatch('/api/theme', {'theme_name': 'AI', 'positive_examples': 'AI chips'})
            updated = app.dispatch('/api/theme', {'id': saved['id'], 'theme_name': 'AI revised', 'positive_examples': 'AI chips'})
        self.assertEqual(saved['audit_sync']['state'], 'synced')
        self.assertEqual(updated['audit_sync']['state'], 'synced')
        self.assertEqual(saved['id'], updated['id'])
        self.assertEqual(len(captured), 6)
        self.assertEqual([e['event_type'] for e in captured],
                         ['request.input','record.created','request.output','request.input','record.updated','request.output'])
        self.assertEqual(captured[4]['payload']['before']['theme_name'], 'AI')
        self.assertEqual(captured[4]['payload']['after']['theme_name'], 'AI revised')

    def test_missing_theme_table_reports_pending_without_losing_theme(self):
        import app
        app = importlib.reload(app)
        error = HTTPError('https://audit.example', 404, 'missing table', {}, None)
        with patch.object(audit_store, 'urlopen', side_effect=error):
            saved = app.dispatch('/api/theme', {'theme_name': 'AI', 'positive_examples': 'AI chips'})
        self.assertEqual(saved['audit_sync']['state'], 'pending')
        self.assertEqual(saved['audit_sync']['error'], 'HTTP 404')
        self.assertEqual(app.get('themes', saved['id'])['theme_name'], 'AI')
        with app.db() as conn:
            self.assertEqual(audit_store.status(conn)['pending'], 3)
