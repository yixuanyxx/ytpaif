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
from concurrent.futures import CancelledError

ROOT = Path(__file__).resolve().parent


def database_path():
    return Path(os.environ.get('SOURCE_JOB_DB', str(ROOT / 'data' / 'source-jobs.sqlite3'))).resolve()


class Queue:
    def __init__(self, path=None, timeout=10, ensure_schema=True):
        self.path = Path(path or database_path())
        self.timeout = timeout
        if not ensure_schema:
            return
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
            db.execute('CREATE TABLE IF NOT EXISTS source_updates (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS completion_notifications
                (job_id TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL,
                 PRIMARY KEY (job_id, channel))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=self.timeout)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, run, companies):
        with self.connect() as db:
            if run.get('job_type') in ('financials', 'analysis'):
                db.execute('BEGIN IMMEDIATE')
                for row in db.execute("SELECT payload FROM jobs WHERE state IN ('queued','running')"):
                    existing = json.loads(row[0])
                    if (run.get('job_type') == 'financials'
                            and existing.get('job_type') == 'financials'
                            and existing.get('portfolio_id') == run['portfolio_id']):
                        return existing
                    if (existing.get('job_type') == 'analysis'
                            and existing.get('request_key')
                            and existing.get('request_key') == run.get('request_key')):
                        return existing
            if run.get('search_mode') in ('short', 'long'):
                run['awaiting_worker'] = True
            db.execute('INSERT INTO jobs (id, payload, companies, state) VALUES (?, ?, ?, ?)',
                       (run['id'], json.dumps(run), json.dumps(companies), 'queued'))
        return run

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
        if not row:
            raise ValueError('Job not found')
        return json.loads(row[0]), json.loads(row[1])

    def save(self, run):
        # A late network response must never resurrect a terminal job.
        with self.connect() as db:
            updated = db.execute("UPDATE jobs SET payload=?, state=? WHERE id=? AND state IN ('queued','running')",
                                 (json.dumps(run), run['status'], run['id']))
            if updated.rowcount and run['status'] in ('completed', 'incomplete', 'failed', 'cancelled'):
                self.queue_notifications(db, run)

    @staticmethod
    def queue_notifications(db, run):
        if run.get('job_type') == 'financials' or run.get('search_mode') in ('short', 'long'):
            db.execute("INSERT OR IGNORE INTO completion_notifications VALUES (?, 'telegram', 'pending')",
                       (run['id'],))

    def deliver_next_notification(self):
        from source_notifications import send

        with self.connect() as db:
            row = db.execute("""SELECT n.job_id, n.channel, j.payload FROM completion_notifications n
                                JOIN jobs j ON j.id=n.job_id WHERE n.state='pending' AND n.channel='telegram'
                                ORDER BY n.rowid LIMIT 1""").fetchone()
            if not row:
                return False
            job_id, channel, payload = row
            # Claim before sending. An interrupted provider call may have succeeded,
            # so automatically repeating it could send the same notice twice.
            db.execute("UPDATE completion_notifications SET state='sending' WHERE job_id=? AND channel=?",
                       (job_id, channel))
        try:
            state = send(channel, json.loads(payload))
        except Exception as error:
            state = 'failed'
            print(f'Source search notification {job_id} ({channel}) failed: {type(error).__name__}', file=sys.stderr)
        with self.connect() as db:
            db.execute('UPDATE completion_notifications SET state=? WHERE job_id=? AND channel=?',
                       (state, job_id, channel))
        return True

    def cancel(self, job_id, reason='Search cancelled by user.'):
        """Durably stop a queued/running job without letting a late worker revive it."""
        with self.connect() as db:
            # Read and transition under the same write lock. Otherwise a worker
            # could finish between SELECT and UPDATE and cancel() would return
            # a cancelled payload even though the stored job is completed.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Source-search run not found')
            run = json.loads(row[0])
            if run.get('status') in ('completed', 'incomplete', 'failed', 'cancelled'):
                return run
            run['status'] = 'cancelled'
            run['cancel_reason'] = reason
            updated = db.execute('UPDATE jobs SET payload=?, state=? WHERE id=? AND state IN (\'queued\',\'running\')',
                                 (json.dumps(run), 'cancelled', job_id))
            if updated.rowcount:
                self.queue_notifications(db, run)
            return run

    def cancel_company(self, job_id, company_id):
        """Mark one company cancelled without terminating its shared batch job."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Analysis run not found')
            run = json.loads(row[0])
            if company_id not in run.get('company_ids', []):
                raise ValueError('Company is not part of this analysis run')
            if run.get('status') in ('completed', 'incomplete', 'failed', 'cancelled'):
                return run
            if company_id in run.get('companies_completed', []):
                return run
            run['cancelled_company_ids'] = list(dict.fromkeys([
                *run.get('cancelled_company_ids', []), company_id,
            ]))
            if run.get('inflight_company_id') == company_id:
                run['interrupt_company_id'] = company_id
            remaining = [candidate for candidate in run.get('company_ids', [])
                         if candidate not in run.get('companies_completed', [])
                         and candidate not in run['cancelled_company_ids']]
            state = 'running'
            if not remaining:
                run['status'] = 'cancelled'
                run['phase'] = 'finished'
                run['cancel_reason'] = 'All remaining company analyses were cancelled by user.'
                state = 'cancelled'
            db.execute("UPDATE jobs SET payload=?, state=? WHERE id=? AND state IN ('queued','running')",
                       (json.dumps(run), state, job_id))
            return run

    def claim_company_interrupt(self, job_id):
        """Consume a one-shot request to interrupt the current analysis child."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return None
            run = json.loads(row[0])
            company_id = run.pop('interrupt_company_id', None)
            if company_id:
                db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(run), job_id))
            return company_id

    def merge(self, remote):
        rows = {record['id']: record for record in remote}
        rows.update({r['id']: r for r in self.records()})
        return list(rows.values())

    def stage_source(self, job_id, record):
        with self.connect() as db:
            # Retain existing journals on cancellation, but never add a late
            # network result after the job has become terminal.
            inserted = db.execute('''INSERT OR REPLACE INTO pending_sources
                SELECT ?, ?, ? WHERE EXISTS
                (SELECT 1 FROM jobs WHERE id=? AND state IN ('queued','running'))''',
                (job_id, record['id'], json.dumps(record), job_id))
            return bool(inserted.rowcount)

    def pending_sources(self, job_id):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM pending_sources WHERE job_id=?', (job_id,))]

    def acknowledge_sources(self, job_id, records):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row[0] not in ('queued', 'running'):
                return
            db.executemany('INSERT INTO source_updates (id) VALUES (?)', [(record['id'],) for record in records])
            db.executemany('DELETE FROM pending_sources WHERE job_id=? AND id=?',
                           [(job_id, record['id']) for record in records])

    def source_updates_after(self, cursor):
        with self.connect() as db:
            return list(db.execute('SELECT seq, id FROM source_updates WHERE seq > ? ORDER BY seq', (cursor,)))

    def latest_source_update(self):
        with self.connect() as db:
            return (db.execute('SELECT COALESCE(MAX(seq), 0) FROM source_updates').fetchone() or [0])[0]

    def record_source_updates(self, records):
        with self.connect() as db:
            db.executemany('INSERT INTO source_updates (id) VALUES (?)', [(record['id'],) for record in records])


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
            if queue.deliver_next_notification():
                idle_since = time.monotonic()
                continue
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
            while child.poll() is None:
                current, _ = queue.job(job_id)
                cancellable = current.get('job_type') in ('financials', 'analysis') or current.get('search_mode') in ('short', 'long')
                if cancellable and current.get('status') == 'cancelled':
                    # This is the exact process launched for this job. Killing
                    # it also closes discovery/lookup threads blocked in HTTP;
                    # a cancelled job must not occupy the queue until timeout.
                    child.terminate()
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)
                    break
                if (current.get('job_type') == 'analysis'
                        and current.get('interrupt_company_id')
                        and queue.claim_company_interrupt(job_id)):
                    child.terminate()
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)
                    break
                try:
                    child.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
            # A crash leaves the checkpoint running: resume without a time limit.
            time.sleep(.2)


def persist_discovered_sources(app, records):
    """Persist each staged batch before acknowledging it, without a browser."""
    # Supabase is authoritative. Stable source IDs make retries safe if the
    # process exits after the upsert but before the queue is acknowledged.
    saved = app.store.put_many('sources', records)
    # A shared Redis instance makes the new links visible immediately. A local
    # in-process fallback is only an optional cache, never the durable copy.
    app.source_cache.save_sources_to_redis(records)
    return saved


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
                if kind == 'companies' and self.is_cancelled(job_id):
                    return record
                if kind == 'search_runs':
                    current, _ = queue.job(job_id)
                    if current.get('status') == 'cancelled' and record.get('status') != 'cancelled':
                        return current
                    record['cancelled_company_ids'] = list(dict.fromkeys([
                        *record.get('cancelled_company_ids', []),
                        *current.get('cancelled_company_ids', []),
                    ]))
                    queue.save(record)  # Durable before the remote mirror is attempted.
                    if record['status'] == 'running' and time.monotonic() - self.last_mirror < 10:
                        return record
                    self.last_mirror = time.monotonic()
                    # The local queue is the durable source of truth for job
                    # progress. A transient Supabase mirror failure must not
                    # throw away a completed local checkpoint or restart work.
                    try:
                        return app.put(kind, record)
                    except Exception:
                        return record
                return app.put(kind, record)

            def is_cancelled(self, run_id):
                current, _ = queue.job(run_id)
                return current.get('status') == 'cancelled'

            def is_company_cancelled(self, run_id, company_id):
                current, _ = queue.job(run_id)
                return company_id in current.get('cancelled_company_ids', [])

            def put_many(self, kind, records):
                import app
                if kind == 'sources':
                    if self.is_cancelled(job_id):
                        raise CancelledError()
                    return persist_discovered_sources(app, records)
                return app.put_many(kind, records)

            def stage_source(self, record):
                if not queue.stage_source(job_id, record):
                    raise CancelledError()

            def pending_sources(self):
                return queue.pending_sources(job_id)

            def acknowledge_sources(self, records):
                if self.is_cancelled(job_id):
                    raise CancelledError()
                queue.acknowledge_sources(job_id, records)

        if run.get('job_type') == 'financials':
            import financial_jobs
            try:
                financial_jobs.worker(WorkerApp(), run, companies)
            finally:
                # Use the current formatter even if an older supervisor survived deployment.
                while queue.deliver_next_notification():
                    pass
        elif run.get('job_type') == 'analysis':
            import analysis_jobs
            analysis_jobs.worker(WorkerApp(), run, companies)
        else:
            import source_discovery
            if run.pop('awaiting_worker', False):
                run['deadline_unix'] = time.time() + run.get('time_limit_seconds', source_discovery.SEARCH_TIME_LIMIT_SECONDS)
                queue.save(run)
            source_discovery.worker(WorkerApp(), run, companies)


if __name__ == '__main__':
    if sys.argv[1] == 'supervise':
        supervise(Queue())
    else:
        execute(sys.argv[2])
