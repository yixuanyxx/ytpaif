"""Persistent safety budget for outbound Gemini requests.

Each reservation represents one HTTP attempt, including model and credential
fallbacks. SQLite's immediate transaction keeps concurrent app and worker
processes on the same installation within one rolling 24-hour budget.
"""

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


DEFAULT_DAILY_CAP = 60
WINDOW_SECONDS = 24 * 60 * 60


class BudgetExceeded(ValueError):
    """The configured Gemini request budget has been consumed."""


def call_cap():
    value = os.environ.get('GEMINI_DAILY_CALL_CAP', str(DEFAULT_DAILY_CAP)).strip()
    try:
        cap = int(value)
    except ValueError:
        raise ValueError('GEMINI_DAILY_CALL_CAP must be a non-negative integer') from None
    if cap < 0:
        raise ValueError('GEMINI_DAILY_CALL_CAP must be a non-negative integer')
    return cap


def database_path():
    configured = os.environ.get('GEMINI_BUDGET_DB', '').strip()
    return Path(configured) if configured else Path(__file__).resolve().parent / 'data' / 'gemini-budget.sqlite3'


def _connection():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.execute('PRAGMA busy_timeout=10000')
    connection.execute('CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, requested_at REAL NOT NULL)')
    connection.execute('CREATE INDEX IF NOT EXISTS attempts_requested_at ON attempts(requested_at)')
    return connection


def status(at=None):
    """Return the rolling-window budget shown in the app."""
    current = time.time() if at is None else at
    cap = call_cap()
    with closing(_connection()) as connection:
        used = connection.execute('SELECT COUNT(*) FROM attempts WHERE requested_at > ?',
                                  (current - WINDOW_SECONDS,)).fetchone()[0]
    return {'call_cap': cap, 'calls_used_24h': used,
            'calls_remaining_24h': max(0, cap - used)}


def reserve(at=None):
    """Atomically reserve one attempt before opening the provider connection."""
    current = time.time() if at is None else at
    cap = call_cap()
    connection = _connection()
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DELETE FROM attempts WHERE requested_at <= ?',
                           (current - WINDOW_SECONDS,))
        used = connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        if used >= cap:
            connection.execute('ROLLBACK')
            raise BudgetExceeded(
                f'Gemini safety budget reached: {cap} requests in the last 24 hours. '
                'Check your Google AI Studio limits before changing GEMINI_DAILY_CALL_CAP.')
        connection.execute('INSERT INTO attempts (requested_at) VALUES (?)', (current,))
        connection.execute('COMMIT')
        return {'call_cap': cap, 'calls_used_24h': used + 1,
                'calls_remaining_24h': cap - used - 1}
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise ValueError('Could not check the Gemini safety budget; request was not sent') from error
    finally:
        connection.close()
