"""Exercise the real HTTP handler using the headers sent by production nginx."""
import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app
from test_fixtures import MemoryStore, demo


class ProductionHttpTest(unittest.TestCase):
    def setUp(self):
        self.store_patch = patch.object(app, 'store', MemoryStore())
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        with patch.object(app.supabase_store, 'upload_source'):
            self.theme = demo(app)
        # Require the pipeline to provide the same explicit origin as production.
        self.assertIn('https://is4002.com', os.environ.get('TRUSTED_ORIGINS', '').split(','))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request_dashboard(self, origin):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request('POST', '/api/dashboard', json.dumps({'theme_id': self.theme['id']}), {
                'Host': '127.0.0.1',
                'Origin': origin,
                'X-Forwarded-Host': 'is4002.com',
                'X-Forwarded-Proto': 'https',
                'Content-Type': 'application/json',
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_dashboard_works_through_production_proxy(self):
        status, body = self.request_dashboard('https://is4002.com')
        self.assertEqual(status, 200, body)
        self.assertIn('summary', body)
        self.assertIn('companies', body)

    def test_proxy_headers_do_not_allow_an_untrusted_origin(self):
        status, _ = self.request_dashboard('https://attacker.example')
        self.assertEqual(status, 403)
