"""Send background-search completion summaries using configured recipients."""
import base64
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def recipients(channel):
    default_path = Path(__file__).with_name('search-notification-recipients.json')
    path = Path(os.environ.get('SEARCH_NOTIFICATION_RECIPIENTS_FILE', str(default_path)))
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return []
    values = data.get('emails' if channel == 'email' else 'phones', [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Notification recipients must be lists of strings')
    pattern = r'[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+' if channel == 'email' else r'\+[1-9]\d{7,14}'
    values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if any(not re.fullmatch(pattern, value) for value in values):
        raise ValueError('Invalid notification recipient')
    return values


def send(channel, run):
    addresses = recipients(channel)
    if not addresses:
        return 'not_configured'
    mode = run['search_mode'].capitalize()
    status = run['status']
    count = run.get('results_count', 0)
    subject = f'{mode} AI Search {status}'
    summary = f'{subject}. {count} document(s) saved. Open Source Documents to review.'
    if channel == 'email':
        host, username, password, sender = [
            os.environ.get(f'SEARCH_SMTP_{field}', '').strip()
            for field in ('HOST', 'USERNAME', 'PASSWORD', 'FROM')
        ]
        if not all((host, username, password, sender)):
            return 'not_configured'
        message = EmailMessage()
        message['Subject'] = subject
        message['From'] = sender
        message.set_content(summary)
        port = int(os.environ.get('SEARCH_SMTP_PORT', '465'))
        context = ssl.create_default_context()
        smtp = (smtplib.SMTP_SSL(host, port, timeout=10, context=context) if port == 465
                else smtplib.SMTP(host, port, timeout=10))
        with smtp:
            if port != 465:
                smtp.starttls(context=context)
            smtp.login(username, password)
            failures = 0
            for recipient in addresses:
                if 'To' in message:
                    message.replace_header('To', recipient)
                else:
                    message['To'] = recipient
                try:
                    smtp.send_message(message, to_addrs=[recipient])
                except (OSError, smtplib.SMTPException):
                    failures += 1
    else:
        account, key, secret, sender = [
            os.environ.get(f'SEARCH_SMS_{field}', '').strip()
            for field in ('ACCOUNT_SID', 'API_KEY', 'API_SECRET', 'FROM')
        ]
        if not all((account, key, secret, sender)):
            return 'not_configured'
        if not re.fullmatch(r'AC[0-9a-fA-F]{32}', account):
            raise ValueError('Invalid SMS account configuration')
        credentials = base64.b64encode(f'{key}:{secret}'.encode()).decode()
        failures = 0
        for recipient in addresses:
            request = Request(f'https://api.twilio.com/2010-04-01/Accounts/{account}/Messages.json',
                              data=urlencode({'From': sender, 'To': recipient, 'Body': summary}).encode(),
                              headers={'Authorization': f'Basic {credentials}',
                                       'Content-Type': 'application/x-www-form-urlencoded'})
            try:
                with urlopen(request, timeout=10):
                    pass
            except OSError:
                failures += 1
    if failures:
        return 'failed' if failures == len(addresses) else 'partially_submitted'
    return 'submitted'
