"""Bounded Gemini model and authentication fallback with the original capabilities."""
import hashlib
import json
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request


DEFAULT_MODELS = (
    'gemini-3.8-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-3.5-flash',
    'gemini-3.5-flash-lite',
    'gemini-3.1-pro-preview',
    'gemini-3.1-flash-lite',
)
RETRYABLE_STATUSES = (404, 429, 500, 502, 503, 504)
_blocked = {}
_auth_blocked = {}
_lock = threading.Lock()


def models(primary):
    """Start at the configured model and move down the ordered list once."""
    configured = os.environ.get('GEMINI_MODEL_FALLBACKS')
    chain = list(DEFAULT_MODELS) if configured is None else [name.strip() for name in configured.split(',') if name.strip()]
    chain = list(dict.fromkeys(chain))
    if primary in chain:
        return chain[chain.index(primary):]
    return [primary, *chain]


class ModelsExhausted(HTTPError):
    def __init__(self, url, attempts):
        self.attempts = attempts
        status = 429 if any(row['http_status'] == 429 for row in attempts) else attempts[-1]['http_status']
        summary = ', '.join(f"{row['model']} (HTTP {row['http_status']})" for row in attempts)
        message = ('All configured Gemini models are currently unavailable or quota-limited. '
                   f'Model results: {summary}. Check project quota and billing or try again later.')
        super().__init__(url, status, message, {}, None)

    def __str__(self):
        return self.reason


def request_json(request, timeout, *, open_request):
    """Keep the selected key unless authentication fails, then use another configured key."""
    if urlparse(request.full_url).hostname != 'generativelanguage.googleapis.com':
        return _request_models(request, timeout, open_request=open_request)
    primary = (request.get_header('X-goog-api-key') or request.get_header('Authorization') or '').removeprefix('Bearer ')
    configured = [os.environ.get(name, '').strip()
                  for name in ('FINANCIALS_LLM_API_KEY', 'GEMINI_API_KEY', 'LLM_API_KEY')]
    credentials = list(dict.fromkeys(key for key in [primary, *configured] if key))
    deadline = time.monotonic() + timeout
    for credential in credentials:
        key_id = hashlib.sha256(credential.encode()).hexdigest()
        with _lock:
            rejected_until = _auth_blocked.get(key_id, 0)
        if rejected_until > time.monotonic():
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Gemini request deadline reached while checking configured credentials')
        headers = dict(request.header_items())
        headers.pop('Authorization', None)
        headers.pop('X-goog-api-key', None)
        if request.get_header('Authorization') and not request.get_header('X-goog-api-key'):
            headers['Authorization'] = 'Bearer ' + credential
        else:
            headers['X-goog-api-key'] = credential
        candidate = Request(request.full_url, data=request.data, headers=headers, method=request.get_method())
        try:
            output, info = _request_models(candidate, remaining, open_request=open_request)
            return output, {**info, 'key_fallback_used': credential != primary}
        except HTTPError as error:
            if error.code != 401:
                raise
            with _lock:
                _auth_blocked[key_id] = time.monotonic() + 3600
            error.close()
    raise HTTPError(request.full_url, 401, 'All configured Gemini API keys failed authentication. Configure a valid key.', {}, None)


def _request_models(request, timeout, *, open_request):
    """Try each eligible model within one deadline, preserving request content."""
    body = json.loads(request.data)
    primary = body['model']
    if urlparse(request.full_url).hostname != 'generativelanguage.googleapis.com':
        with open_request(request, timeout=timeout) as response:
            return json.load(response), {'model': primary, 'models_attempted': [primary], 'fallback_used': False}

    chain = models(primary)
    credential = request.get_header('X-goog-api-key') or request.get_header('Authorization') or ''
    key_id = hashlib.sha256(credential.removeprefix('Bearer ').encode()).hexdigest()
    # Grounding can be blocked while ordinary text generation still works.
    capability = bool(body.get('tools'))
    deadline = time.monotonic() + timeout
    attempts = []
    for model in chain:
        cache_key = (key_id, model, capability)
        with _lock:
            blocked_until, status = _blocked.get(cache_key, (0, 0))
        if blocked_until > time.monotonic():
            attempts.append({'model': model, 'http_status': status, 'cooldown': True})
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Gemini request deadline reached while trying available models')
        candidate = Request(request.full_url, data=json.dumps({**body, 'model': model}).encode(),
                            headers=dict(request.header_items()), method=request.get_method())
        try:
            # Reserve time for another model if an overloaded model stops responding.
            with open_request(candidate, timeout=min(20, remaining)) as response:
                output = json.load(response)
            return output, {'model': model, 'models_attempted': [row['model'] for row in attempts] + [model],
                            'fallback_used': model != primary}
        except HTTPError as error:
            if error.code not in RETRYABLE_STATUSES or len(chain) == 1:
                raise
            status = error.code
            retry_after = (error.headers or {}).get('Retry-After', '')
            error.close()
        except (TimeoutError, URLError) as error:
            if len(chain) == 1 or isinstance(error, URLError) and not isinstance(error.reason, TimeoutError):
                raise
            status, retry_after = 504, ''
        attempts.append({'model': model, 'http_status': status})
        delay = {429: 60, 404: 600}.get(status, 15)
        if str(retry_after).isdigit():
            delay = max(delay, int(retry_after))
        with _lock:
            _blocked[cache_key] = (time.monotonic() + delay, status)
    raise ModelsExhausted(request.full_url, attempts)
