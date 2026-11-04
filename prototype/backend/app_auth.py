"""Application login using hashed credentials and Redis-backed sessions."""
import hashlib
import hmac
import os
import secrets
from http.cookies import SimpleCookie
import source_cache

COOKIE = 'themelab_session'

def client():
    connection = source_cache._connection()
    if connection is None or type(connection).__module__.startswith('fakeredis'):
        raise RuntimeError('A shared Redis server is required for login')
    return connection

def check_credentials(username, password):
    configured = os.environ.get('APP_LOGIN_USERNAME', '')
    try:
        salt, expected = os.environ.get('APP_LOGIN_PASSWORD_HASH', '').split('$', 1)
        actual = hashlib.pbkdf2_hmac('sha256', str(password).encode(), bytes.fromhex(salt), 310000).hex()
        return bool(configured) and hmac.compare_digest(str(username), configured) and hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def token_from_header(header):
    cookie = SimpleCookie()
    try:
        cookie.load(header or '')
        token = cookie[COOKIE].value if COOKIE in cookie else ''
        return token if len(token) == 64 and all(c in '0123456789abcdef' for c in token) else ''
    except Exception:
        return ''

def key(token):
    return source_cache._key('login:' + hashlib.sha256(token.encode()).hexdigest())

def valid(header):
    token = token_from_header(header)
    return bool(token and client().get(key(token)))

def create():
    token = secrets.token_hex(32)
    client().set(key(token), '1', ex=8 * 60 * 60)
    return token

def revoke(header):
    token = token_from_header(header)
    if token:
        client().delete(key(token))
