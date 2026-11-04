"""Send background-search completion summaries to configured Telegram chats."""
import json
import os
import re
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def chat_ids():
    default_path = Path(__file__).with_name('search-notification-recipients.json')
    path = Path(os.environ.get('SEARCH_NOTIFICATION_RECIPIENTS_FILE', str(default_path)))
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return []
    values = data.get('telegram_chat_ids', []) if isinstance(data, dict) else None
    if not isinstance(values, list) or any(type(value) not in (str, int) for value in values):
        raise ValueError('Telegram chat IDs must be a list of strings or integers')
    ids = list(dict.fromkeys(str(value).strip() for value in values))
    if any(not re.fullmatch(r'-?[1-9]\d{0,19}', value) for value in ids):
        raise ValueError('Invalid Telegram chat ID')
    return ids


def send(channel, run):
    if channel != 'telegram':
        return 'not_configured'
    token = os.environ.get('SEARCH_TELEGRAM_BOT_TOKEN', '').strip()
    recipients = chat_ids()
    if not token or not recipients:
        return 'not_configured'
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise ValueError('Invalid Telegram bot token format')

    mode = run['search_mode'].capitalize()
    status = run['status']
    count = run.get('results_count', 0)
    summary = f'{mode} AI Search {status}. {count} document(s) saved. Open Source Documents to review.'
    opener = build_opener(NoRedirect())
    failures = 0
    for recipient in recipients:
        request = Request(f'https://api.telegram.org/bot{token}/sendMessage',
                          data=json.dumps({'chat_id': int(recipient), 'text': summary}).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=10) as response:
                if json.load(response).get('ok') is not True:
                    failures += 1
        except (OSError, ValueError, AttributeError):
            failures += 1
    if failures:
        return 'failed' if failures == len(recipients) else 'partially_submitted'
    return 'submitted'
