import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

import gemini_fallback


class GeminiFallbackTest(unittest.TestCase):
    def setUp(self):
        self.blocked_patch = patch.dict(gemini_fallback._blocked, {}, clear=True)
        self.blocked_patch.start()
        self.addCleanup(self.blocked_patch.stop)
        rejected = patch.dict(gemini_fallback._auth_blocked, {}, clear=True)
        rejected.start()
        self.addCleanup(rejected.stop)
        self.env_patch = patch.dict(os.environ)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        os.environ.pop('GEMINI_MODEL_FALLBACKS', None)
        for name in ('FINANCIALS_LLM_API_KEY', 'GEMINI_API_KEY', 'LLM_API_KEY'):
            os.environ[name] = ''

    def test_legacy_daily_cap_setting_does_not_block_provider_requests(self):
        request = self.request()
        send = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(b'{}'))
        with patch.dict(os.environ, {'GEMINI_DAILY_CALL_CAP': '0', 'GEMINI_MODEL_FALLBACKS': ''}):
            for _ in range(61):
                gemini_fallback.request_json(request, 5, open_request=send)
        self.assertEqual(send.call_count, 61)

    def request(self, key='key-a', grounded=True):
        body = {'model': 'gemini-3.8-flash', 'input': 'Read only the supplied evidence.', 'store': False}
        if grounded:
            body['tools'] = [{'type': 'google_search', 'search_types': ['web_search']}]
        return Request('https://generativelanguage.googleapis.com/v1beta/interactions',
                       data=json.dumps(body).encode(), headers={'x-goog-api-key': key})

    def test_quota_and_busy_models_fall_back_preserving_key_prompt_and_search(self):
        request = self.request()
        send = Mock(side_effect=[
            HTTPError(request.full_url, 429, 'Quota', {}, None),
            HTTPError(request.full_url, 503, 'Busy', {}, None),
            io.BytesIO(b'{"steps":[]}'),
        ])
        output, info = gemini_fallback.request_json(request, 30, open_request=send)
        self.assertEqual(output, {'steps': []})
        self.assertEqual(info['model'], 'gemini-3.6-flash')
        self.assertTrue(info['fallback_used'])
        self.assertEqual(info['models_attempted'], list(gemini_fallback.DEFAULT_MODELS[:3]))
        original = json.loads(request.data)
        for call in send.call_args_list:
            candidate = call.args[0]
            body = json.loads(candidate.data)
            self.assertEqual(candidate.get_header('X-goog-api-key'), 'key-a')
            self.assertEqual({k: v for k, v in body.items() if k != 'model'},
                             {k: v for k, v in original.items() if k != 'model'})

    def test_exhausted_models_are_tried_once_then_skipped_during_cooldown(self):
        request = self.request()
        send = Mock(side_effect=HTTPError(request.full_url, 429, 'Quota', {}, None))
        for _ in range(2):
            with self.assertRaises(gemini_fallback.ModelsExhausted) as raised:
                gemini_fallback.request_json(request, 30, open_request=send)
            self.assertEqual(len(raised.exception.attempts), 7)
            self.assertEqual(raised.exception.code, 429)
            self.assertNotIn('key-a', str(raised.exception))
        self.assertEqual(send.call_count, 7)
        self.assertTrue(all(row.get('cooldown') for row in raised.exception.attempts))

    def test_cooldown_is_scoped_to_key_and_search_capability(self):
        request = self.request()
        send = Mock(side_effect=[HTTPError(request.full_url, 429, 'Quota', {}, None), io.BytesIO(b'{}')])
        gemini_fallback.request_json(request, 30, open_request=send)
        for key, grounded, expected in (
                ('key-a', True, 'gemini-3.7-flash'),
                ('key-b', True, 'gemini-3.8-flash'),
                ('key-a', False, 'gemini-3.8-flash')):
            send = Mock(return_value=io.BytesIO(b'{}'))
            _, info = gemini_fallback.request_json(self.request(key, grounded), 30, open_request=send)
            self.assertEqual(info['model'], expected)
            self.assertEqual(send.call_count, 1)

    def test_authentication_and_bad_requests_stop_without_model_switching(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                send = Mock(side_effect=HTTPError('https://provider', status, 'Rejected', {}, None))
                with self.assertRaises(HTTPError) as raised:
                    gemini_fallback.request_json(self.request(key=f'key-{status}'), 30, open_request=send)
                self.assertEqual(raised.exception.code, status)
                self.assertEqual(send.call_count, 1)

    def test_rejected_key_uses_other_configured_key_and_remembers_rejection(self):
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'working-key', 'LLM_API_KEY': 'rejected-key'}):
            request = self.request(key='rejected-key')
            send = Mock(side_effect=[HTTPError(request.full_url, 401, 'Rejected', {}, None), io.BytesIO(b'{}')])
            _, info = gemini_fallback.request_json(request, 30, open_request=send)
            self.assertTrue(info['key_fallback_used'])
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args_list[0].args[0].get_header('X-goog-api-key'), 'rejected-key')
            self.assertEqual(send.call_args_list[1].args[0].get_header('X-goog-api-key'), 'working-key')
            send = Mock(return_value=io.BytesIO(b'{}'))
            gemini_fallback.request_json(request, 30, open_request=send)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0].get_header('X-goog-api-key'), 'working-key')

    def test_authentication_fallback_replaces_the_bearer_key(self):
        request = self.request(key='rejected-key')
        request.remove_header('X-goog-api-key')
        request.add_header('Authorization', 'Bearer rejected-key')
        with patch.dict(os.environ, {'FINANCIALS_LLM_API_KEY': 'working-key'}):
            send = Mock(side_effect=[HTTPError(request.full_url, 401, 'Rejected', {}, None), io.BytesIO(b'{}')])
            _, info = gemini_fallback.request_json(request, 30, open_request=send)
        self.assertTrue(info['key_fallback_used'])
        self.assertEqual(send.call_args.args[0].get_header('Authorization'), 'Bearer working-key')
        self.assertIsNone(send.call_args.args[0].get_header('X-goog-api-key'))

    def test_unavailable_model_falls_back_and_success_stops_the_chain(self):
        send = Mock(side_effect=[HTTPError('https://provider', 404, 'Unavailable', {}, None), io.BytesIO(b'{}')])
        _, info = gemini_fallback.request_json(self.request(), 30, open_request=send)
        self.assertEqual(info['model'], 'gemini-3.7-flash')
        self.assertEqual(send.call_count, 2)

    def test_all_attempts_share_the_original_deadline(self):
        elapsed = [0]
        timeouts = []

        def send(request, timeout):
            timeouts.append(timeout)
            if len(timeouts) == 1:
                elapsed[0] += 10
                raise HTTPError(request.full_url, 503, 'Busy', {}, None)
            elapsed[0] += 1
            return io.BytesIO(b'{}')

        with patch.object(gemini_fallback.time, 'monotonic', side_effect=lambda: elapsed[0]):
            gemini_fallback.request_json(self.request(), 15, open_request=send)
        self.assertEqual(timeouts, [15, 5])

    def test_unresponsive_model_times_out_then_tries_another_model(self):
        send = Mock(side_effect=[TimeoutError('No response'), io.BytesIO(b'{}')])
        _, info = gemini_fallback.request_json(self.request(), 90, open_request=send)
        self.assertEqual(info['model'], 'gemini-3.7-flash')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].kwargs['timeout'], 20)

    def test_configured_model_order_moves_down_and_can_disable_fallback(self):
        self.assertEqual(gemini_fallback.models('gemini-3.5-flash-lite'), list(gemini_fallback.DEFAULT_MODELS[4:]))
        with patch.dict(os.environ, {'GEMINI_MODEL_FALLBACKS': ''}):
            self.assertEqual(gemini_fallback.models('gemini-3.8-flash'), ['gemini-3.8-flash'])

    def test_chat_compatibility_endpoint_preserves_bearer_authentication(self):
        request = Request('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
                          data=json.dumps({'model': 'gemini-3.8-flash', 'messages': [{'role': 'user', 'content': 'Reply JSON'}],
                                           'response_format': {'type': 'json_object'}}).encode(),
                          headers={'Authorization': 'Bearer key-b'})
        send = Mock(side_effect=[HTTPError(request.full_url, 503, 'Busy', {}, None), io.BytesIO(b'{"choices":[]}')])
        _, info = gemini_fallback.request_json(request, 30, open_request=send)
        self.assertEqual(info['model'], 'gemini-3.7-flash')
        for call in send.call_args_list:
            self.assertEqual(call.args[0].get_header('Authorization'), 'Bearer key-b')
            self.assertEqual(json.loads(call.args[0].data)['response_format'], {'type': 'json_object'})
