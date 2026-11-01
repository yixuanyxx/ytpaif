import io
import json
import unittest
from unittest.mock import patch
import source_discovery as discovery


class DiscoveryTest(unittest.TestCase):
    def test_only_cited_urls_are_accepted(self):
        rows = [{'url': 'https://example.com/report', 'source_type': 'annual_report'},
                {'url': 'https://invented.example/report', 'source_type': 'annual_report'}]
        output = {'steps': [{'type': 'model_output', 'content': [{'text': json.dumps({'sources': rows}),
                  'annotations': [{'type': 'url_citation', 'url': rows[0]['url']}]}]}]}
        with patch.dict(discovery.os.environ, {'GEMINI_API_KEY': 'test'}), \
                patch.object(discovery, 'urlopen', return_value=io.BytesIO(json.dumps(output).encode())):
            result = discovery.grounded_search({'name': 'Example'}, ['annual_report'], 'FY2025', '2026-11-01', False, 5)
        self.assertEqual(result, rows[:1])

    def test_private_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            discovery.public_url('http://127.0.0.1/secrets')
        with patch.object(discovery.socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                discovery.public_url('https://example.com/private')
