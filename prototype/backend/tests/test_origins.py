import os
import unittest
from unittest.mock import patch
from app import Handler


class OriginTest(unittest.TestCase):
    def check_request(self, origin, trusted=''):
        handler = object.__new__(Handler)
        handler.headers = {'Host': '127.0.0.1', 'Origin': origin, 'Content-Length': '0'}
        replies = []
        handler.respond = lambda body, status=200: replies.append((body, status))
        with patch.dict(os.environ, {'TRUSTED_ORIGINS': trusted}):
            handler.do_POST()
        return replies[0][1]

    def test_production_origin_passes_origin_validation(self):
        self.assertEqual(self.check_request('https://is4002.com', 'https://is4002.com'), 400)

    def test_unknown_origin_is_rejected(self):
        self.assertEqual(self.check_request('https://attacker.example', 'https://is4002.com'), 403)

    def test_missing_origin_is_rejected(self):
        self.assertEqual(self.check_request('', 'https://is4002.com'), 403)

    def test_local_origin_still_works(self):
        self.assertEqual(self.check_request('http://127.0.0.1'), 400)
