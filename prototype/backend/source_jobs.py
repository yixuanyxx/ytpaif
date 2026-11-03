"""Disk-backed source queue. A detached supervisor owns the worker lifecycle.

The database belongs to this installation, never to the shared Supabase project.
Keep SOURCE_JOB_DB on persistent disk when running on a server.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent


def database_path():
    return Path(os.environ.get('SOURCE_JOB_DB', str(ROOT / 'data' / 'source-jobs.sqlite3'))).resolve()


class Queue:
    def __init__(self, path=None):
        self.path = Path(path or database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs
                (id TEXT PRIMARY KEY, payload TEXT NOT NULL, companies TEXT NOT NULL,
                 state TEXT NOT NULL)''')
            if any(column[1] == 'deadline' for column in db.execute('PRAGMA table_info(jobs)')):
                db.execute('ALTER TABLE jobs DROP COLUMN deadline')
            db.execute('''CREATE TABLE IF NOT EXISTS pending_sources
                (job_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                 PRIMARY KEY (job_id, id))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, run, companies):
        with self.connect() as db:
            db.execute('INSERT INTO jobs (id, payload, companies, state) VALUES (?, ?, ?, ?)',
                       (run['id'], json.dumps(run), json.dumps(companies), 'queued'))

    def records(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs ORDER BY rowid')]

    def next_job(self):
        with self.connect() as db:
            row = db.execute("SELECT id, payload, companies FROM jobs WHERE state IN ('queued','running') ORDER BY rowid LIMIT 1").fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2])) if row else None

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT payload, companies FROM jobs WHERE id=?', (job_id,)).fetchone()
        return json.loads(row[0]), json.loads(row[1])

    def save(self, run):
        # A late network response must never resurrect a terminal job.
        with self.connect() as db:
            db.execute("UPDATE jobs SET payload=?, state=? WHERE id=? AND state IN ('queued','running')",
                       (json.dumps(run), 'running' if run['status'] == 'running' else run['status'], run['id']))

    def cancel(self, job_id, reason='Search cancelled by user.'):
        """Durably stop a queued/running job without letting a late worker revive it."""
        with self.connect() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Source-search run not found')
            run = json.loads(row[0])
            if run.get('status') in ('completed', 'incomplete', 'failed', 'cancelled'):
                return run
            run['status'] = 'cancelled'
            run['cancel_reason'] = reason
            db.execute('UPDATE jobs SET payload=?, state=? WHERE id=? AND state IN (\'queued\',\'running\')',
                       (json.dumps(run), 'cancelled', job_id))
            return run

    def merge(self, remote):
        rows = {record['id']: record for record in remote}
        rows.update({r['id']: r for r in self.records()})
        return list(rows.values())

    def stage_source(self, job_id, record):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO pending_sources VALUES (?, ?, ?)',
                       (job_id, record['id'], json.dumps(record)))

    def pending_sources(self, job_id):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM pending_sources WHERE job_id=?', (job_id,))]

    def acknowledge_sources(self, job_id, records):
        with self.connect() as db:
            db.executemany('DELETE FROM pending_sources WHERE job_id=? AND id=?',
                           [(job_id, record['id']) for record in records])


@contextmanager
def process_lock(path):
    """OS releases this lock on process death; safe across backend restarts."""
    stream = open(path, 'a+b')
    stream.seek(0)
    stream.write(b'0')
    stream.flush()
    stream.seek(0)
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except OSError:
        pass
    try:
        yield acquired
    finally:
        stream.close()


def launch_arguments():
    if os.name == 'nt':
        return {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}


def ensure_worker():
    queue = Queue()
    # Taking the lock briefly avoids spawning processes on every search/status call.
    with process_lock(str(queue.path) + '.worker.lock') as available:
        if not available:
            return
    with open(queue.path.parent / 'source-worker.log', 'ab') as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'supervise'],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         cwd=str(ROOT), **launch_arguments())


def supervise(queue, command=None, idle_seconds=None):
    with process_lock(str(queue.path) + '.worker.lock') as acquired:
        if not acquired:
            return
        idle_since = time.monotonic()
        while True:
            job = queue.next_job()
            if not job:
                if idle_seconds is not None and time.monotonic() - idle_since >= idle_seconds:
                    return
                time.sleep(.2)
                continue
            idle_since = time.monotonic()
            job_id, _, _ = job
            args = command or [sys.executable, str(Path(__file__).resolve()), 'execute', job_id]
            with open(queue.path.parent / 'source-worker.log', 'ab') as log:
                child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            child.wait()
            # A crash leaves the checkpoint running: resume without a time limit.
            time.sleep(.2)


def execute(job_id):
    queue = Queue()
    # If a supervisor crashed, its surviving child still owns this job.
    with process_lock(str(queue.path) + '.' + job_id + '.lock') as acquired:
        if not acquired:
            return
        run, companies = queue.job(job_id)
        if run['status'] != 'running':
            return

        class WorkerApp:
            last_mirror = 0
            def __getattr__(self, name):
                import app
                return getattr(app, name)

            def put(self, kind, record):
                import app
                if kind == 'search_runs':
                    current, _ = queue.job(job_id)
                    if current.get('status') == 'cancelled' and record.get('status') == 'running':
                        return current
                    queue.save(record)  # Durable before the remote mirror is attempted.
                    if record['status'] == 'running' and time.monotonic() - self.last_mirror < 10:
                        return record
                    self.last_mirror = time.monotonic()
                return app.put(kind, record)

            def put_many(self, kind, records):
                # Discovery results stay in the session's Redis cache.  The
                # browser logout/tab-close sync promotes its pending links to
                # Supabase; other source workflows keep their existing
                # immediate-persistence behaviour.
                if kind == 'sources':
                    if not app.source_cache.save_sources_to_redis(records):
                        raise RuntimeError('Redis source cache is unavailable; source links were not persisted')
                    return records
                return app.put_many(kind, records)

            def stage_source(self, record):
                queue.stage_source(job_id, record)

            def pending_sources(self):
                return queue.pending_sources(job_id)

            def acknowledge_sources(self, records):
                queue.acknowledge_sources(job_id, records)

        import source_discovery
        source_discovery.worker(WorkerApp(), run, companies)


if __name__ == '__main__':
    if sys.argv[1] == 'supervise':
        supervise(Queue())
    else:
        execute(sys.argv[2])
