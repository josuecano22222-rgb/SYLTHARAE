"""Integration: what the scheduler does on the first boot of a real database.

The scheduler is revived code, so the questions are all about a cold start:
with an empty ``job_schedules`` table does anything fire; with one enabled row,
what does the first fire do and can it be seen before it deletes anything; does
stopping the process stop the timer; and do two processes over one database both
fire it.

APScheduler's clock is not simulated - the interval is left alone - but every
function it would call is called here, the advisory lock is taken for real, and
the job rows are read back from PostgreSQL.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from _seed import connect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.jobs.manager import JobManager  # noqa: E402
from services.scheduling import model, store  # noqa: E402
from services.scheduling.scheduler import ScheduleRunner  # noqa: E402
from services.scheduling.store_adapter import ScheduleStoreAdapter  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def conn(pg_db):
    connection = connect(pg_db)
    connection.autocommit = False
    yield connection
    connection.close()


@pytest.fixture()
def admin_id(conn):
    username = f"boot_{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active)"
            " VALUES (%s, 'x', 'admin', TRUE) RETURNING id", (username,))
        user_id = cur.fetchone()[0]
    conn.commit()
    yield user_id
    with conn.cursor() as cur:
        cur.execute("DELETE FROM job_schedules WHERE owner_user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()


@pytest.fixture()
def adapter(pg_db):
    return ScheduleStoreAdapter(connection_factory=lambda: connect(pg_db))


@pytest.fixture()
def clean_schedules(conn):
    """Start from no enabled schedules, so "the default is quiet" is measurable."""
    with conn.cursor() as cur:
        cur.execute("UPDATE job_schedules SET enabled = FALSE WHERE enabled")
    conn.commit()
    yield
    with conn.cursor() as cur:
        cur.execute("UPDATE job_schedules SET enabled = FALSE WHERE enabled")
    conn.commit()


def _make(conn, admin_id, *, schedule_type="retention", payload=None, interval=60):
    row = store.create_schedule(
        conn, schedule_type=schedule_type, name=f"boot_{uuid.uuid4().hex[:6]}",
        owner_user_id=admin_id, payload=payload or {}, interval_minutes=interval)
    conn.commit()
    return row


def _default_first_boot_matrix():
    """The table under test, as data, so the test and the report cannot drift."""
    return [
        # (type, default state, what a first fire does)
        ("retention", "disabled",
         "prunes only the areas whose setting is non-zero; every area defaults to "
         "keep-forever except finished jobs (90 days)"),
        ("scenario_evaluation", "disabled",
         "evaluates the active scenarios and records outcomes; writes no deletions"),
    ]


def test_the_first_boot_matrix_covers_every_schedulable_type():
    assert {row[0] for row in _default_first_boot_matrix()} == set(model.SCHEDULE_TYPES)


def test_an_empty_schedule_table_starts_and_registers_nothing(clean_schedules, adapter):
    """No rows -> no timers. The default state of a new database is quiet."""
    runner = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    assert runner.start() is True
    try:
        assert runner._scheduler.get_jobs() == []
        # A one-shot fire of nothing must not happen either: the scheduler has
        # no jobs, so there is nothing to fire.
        assert runner._job_ids == {}
    finally:
        runner.stop()


def test_a_retention_schedule_is_registered_with_its_interval(conn, admin_id,
                                                              adapter):
    row = _make(conn, admin_id, interval=37)
    runner = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    assert runner.start() is True
    try:
        job = runner._scheduler.get_job(f"schedule-{row['id']}")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 37 * 60
    finally:
        runner.stop()


def test_the_first_fire_is_observable_before_it_deletes_anything(conn, admin_id,
                                                                 adapter):
    """A fire leaves a job row and an audit row; deletion is what the job reports.

    The schedule created here carries a policy for an area whose setting is
    keep-forever, so the honest expectation of a first fire on this database is
    "a job ran and deleted nothing", which is exactly what an operator wants to
    see before trusting the timer.
    """
    row = _make(conn, admin_id, payload={"policy": "audit_log"})
    manager = JobManager(synchronous=True)
    runner = ScheduleRunner(job_manager=manager, store=adapter)
    assert runner.start() is True
    try:
        job = runner._scheduler.get_job(f"schedule-{row['id']}")
        # What APScheduler calls when the interval elapses.
        job.func(row["id"])
    finally:
        runner.stop()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT job_id, status, source, options FROM jobs"
            " WHERE source = %s ORDER BY created_at DESC LIMIT 1",
            (f"schedule:{row['id']}",))
        job_row = cur.fetchone()
    assert job_row is not None, "the fire produced no job, so nothing is observable"
    job_id, status, source, options = job_row
    assert source == f"schedule:{row['id']}"
    assert options.get("areas") == ["audit_log"], options
    assert "schedule_id" not in options, "the option no job reads came back"

    # The observable half of "it ran": the job row says which schedule fired it,
    # and the job's own result says what it deleted. A keep-forever area is the
    # safe first fire: it must report that it kept everything.
    assert status in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), status
    with conn.cursor() as cur:
        cur.execute("SELECT result_summary FROM jobs WHERE job_id = %s", (job_id,))
        summary = cur.fetchone()[0]
    areas = (summary or {}).get("areas") or {}
    assert "audit_log" in areas, summary
    assert areas["audit_log"]["kept_forever"] is True, areas
    assert areas["audit_log"]["deleted"] == 0, areas

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_log WHERE action = 'retention.applied'"
            " AND resource = 'retention:audit_log'"
            " AND created_at > NOW() - INTERVAL '2 minutes'")
        assert cur.fetchone()[0] == 0, (
            "a keep-forever area wrote a retention.applied row although it "
            "deleted nothing")


def test_stop_stops_the_timer_and_releases_the_lock(conn, admin_id, adapter):
    row = _make(conn, admin_id)
    runner = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    assert runner.start() is True
    assert runner._scheduler.running is True
    assert runner._leader_conn is not None

    runner.stop()

    assert runner._scheduler.running is False
    assert runner._leader_conn is None
    # The lock is free again: another runner can take it immediately.
    second = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    assert second.start() is True
    assert second._scheduler.get_job(f"schedule-{row['id']}") is not None
    second.stop()


def test_two_instances_do_not_both_fire(conn, admin_id, adapter):
    """Two web processes over one database: one leader, one quiet follower."""
    _make(conn, admin_id)
    first = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    second = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    assert first.start() is True
    try:
        assert second.start() is False, "two processes both became the scheduler"
        assert second._scheduler.running is False
        assert second._scheduler.get_jobs() == []
    finally:
        first.stop()


def test_the_second_instance_can_take_over_once_the_first_stops(conn, admin_id,
                                                                adapter):
    """The lock is not a lease: a graceful stop hands the timers over."""
    _make(conn, admin_id)
    first = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    second = ScheduleRunner(job_manager=JobManager(synchronous=True), store=adapter)
    first.start()
    assert second.start() is False
    first.stop()
    assert second.start() is True
    second.stop()


def test_a_payload_area_reaches_the_job_and_an_unknown_one_does_not(conn, admin_id,
                                                                    adapter):
    manager = JobManager(synchronous=True)
    runner = ScheduleRunner(job_manager=manager, store=adapter)

    good = _make(conn, admin_id, payload={"policy": "jobs"})
    outcome = runner.fire_now(good)
    assert outcome["status"] == "queued"
    stored = manager.get(outcome["job_id"])
    assert stored["options"].get("areas") == ["jobs"]

    bad = _make(conn, admin_id, payload={"policy": "no_such_area"})
    outcome = runner.fire_now(bad)
    assert outcome["status"] == "queued"
    stored = manager.get(outcome["job_id"])
    assert "areas" not in stored["options"], (
        "an area that names no policy was handed to the job anyway")


def test_exiting_the_process_stops_the_scheduler_and_frees_the_lock(pg_db):
    """The web app has no shutdown hook of its own except process exit.

    A child process starts a runner, prints how many timers it registered and
    exits. Afterwards the parent must be able to take the lock, which is only
    true if the dead process released it - the property that keeps a crashed
    deployment from blocking the next one.
    """
    import json
    import os
    import subprocess

    script = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from _seed import connect\n"
        "from services.jobs.manager import JobManager\n"
        "from services.scheduling.scheduler import ScheduleRunner\n"
        "from services.scheduling.store_adapter import ScheduleStoreAdapter\n"
        "settings = json.loads(%r)\n"
        "adapter = ScheduleStoreAdapter(connection_factory=lambda: connect(settings))\n"
        "runner = ScheduleRunner(JobManager(synchronous=True), adapter)\n"
        "started = runner.start()\n"
        "print(json.dumps({'started': started, 'jobs': len(runner._scheduler.get_jobs())}))\n"
        % (str(ROOT / "tests" / "integration"), json.dumps(pg_db))
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, timeout=180,
                               env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert completed.returncode == 0, completed.stderr[-2000:]
    reported = json.loads(completed.stdout.strip().splitlines()[-1])
    assert reported["started"] is True

    adapter = ScheduleStoreAdapter(connection_factory=lambda: connect(pg_db))
    survivor = ScheduleRunner(JobManager(synchronous=True), adapter)
    assert survivor.start() is True, (
        "the exited process still holds the scheduler lock")
    survivor.stop()


def test_a_sigkilled_leader_releases_the_lock_and_the_next_one_leads(pg_db):
    """The lock is the connection, so ``kill -9`` has to free it.

    The graceful path is covered above. This one kills the leader the way an
    OOM killer or a container stop does - no ``stop()``, no ``finally`` - and
    asks the two questions that decide whether a crashed deployment can be
    restarted: while the leader is alive a second process must decline, and
    once it is killed the next process must be able to lead.
    """
    import json
    import os
    import signal
    import subprocess
    import time

    script = (
        "import json, sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from _seed import connect\n"
        "from services.jobs.manager import JobManager\n"
        "from services.scheduling.scheduler import ScheduleRunner\n"
        "from services.scheduling.store_adapter import ScheduleStoreAdapter\n"
        "settings = json.loads(%r)\n"
        "adapter = ScheduleStoreAdapter(connection_factory=lambda: connect(settings))\n"
        "runner = ScheduleRunner(JobManager(synchronous=True), adapter)\n"
        "print(json.dumps({'started': runner.start()}), flush=True)\n"
        "time.sleep(120)\n"
        % (str(ROOT / "tests" / "integration"), json.dumps(pg_db))
    )
    leader = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT)})
    survivor = ScheduleRunner(
        JobManager(synchronous=True),
        ScheduleStoreAdapter(connection_factory=lambda: connect(pg_db)))
    try:
        reported = json.loads(leader.stdout.readline())
        assert reported["started"] is True, "the child never took the lock"

        # A second process, while the leader is alive: the lock is held.
        assert survivor.start() is False, (
            "two leaders were started against one database")

        leader.send_signal(signal.SIGKILL)
        assert leader.wait(timeout=30) == -signal.SIGKILL

        # The backend notices the dead socket on its own schedule; the retry is
        # the test waiting for Postgres, not the lock being weaker than claimed.
        deadline = time.time() + 15
        while time.time() < deadline:
            if survivor.start() is True:
                break
            time.sleep(0.5)
        else:
            pytest.fail("the SIGKILLed leader still holds the scheduler lock")
    finally:
        try:
            survivor.stop()
        except Exception:
            pass
        if leader.poll() is None:
            leader.kill()
            leader.wait(timeout=30)
