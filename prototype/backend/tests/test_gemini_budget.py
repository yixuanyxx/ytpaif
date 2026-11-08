import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request

import gemini_budget
import gemini_fallback


class GeminiBudgetTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {
            'GEMINI_BUDGET_DB': str(Path(temporary.name) / 'budget.sqlite3'),
            'GEMINI_DAILY_CALL_CAP': '2',
            'GEMINI_MODEL_FALLBACKS': '',
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_reservations_are_atomic_and_expire_after_24_hours(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            attempts = list(pool.map(self._try_reserve, range(4)))
        self.assertEqual(attempts.count(True), 2)
        self.assertEqual(attempts.count(False), 2)
        self.assertEqual(gemini_budget.status()['calls_remaining_24h'], 0)
        future = gemini_budget.time.time() + gemini_budget.WINDOW_SECONDS + 1
        self.assertEqual(gemini_budget.status(at=future)['calls_used_24h'], 0)
        self.assertEqual(gemini_budget.reserve(at=future)['calls_remaining_24h'], 1)

    @staticmethod
    def _try_reserve(_):
        try:
            gemini_budget.reserve()
            return True
        except gemini_budget.BudgetExceeded:
            return False

    def test_provider_does_not_send_an_attempt_past_the_cap(self):
        request = Request('https://generativelanguage.googleapis.com/v1beta/interactions',
                          data=json.dumps({'model': 'gemini-3.8-flash', 'input': 'x'}).encode(),
                          headers={'x-goog-api-key': 'test'})
        send = Mock(side_effect=lambda *args, **kwargs: io.BytesIO(b'{"steps":[]}'))
        for _ in range(2):
            gemini_fallback.request_json(request, 5, open_request=send)
        with self.assertRaisesRegex(gemini_budget.BudgetExceeded, 'safety budget reached'):
            gemini_fallback.request_json(request, 5, open_request=send)
        self.assertEqual(send.call_count, 2)

    def test_zero_cap_blocks_all_requests(self):
        with patch.dict(os.environ, {'GEMINI_DAILY_CALL_CAP': '0'}):
            with self.assertRaises(gemini_budget.BudgetExceeded):
                gemini_budget.reserve()


if __name__ == '__main__':
    unittest.main()
