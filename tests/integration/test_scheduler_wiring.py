"""A schedule can actually fire, and fire the job it names.

Until this round the scheduling subsystem could not run at all: the app imported
a class name the module did not define, the runner expected a store object that
was never written, and every fire called a ``JobManager`` method that did not
exist. Three failures that no test caught, because no test ever let a fire
happen.

These tests let it happen: a real schedule row in a real database, the real
adapter, the real runner, and the real job manager. Nothing here asserts that
APScheduler's clock is right - that is its own business - but everything else in
the path from "the interval elapsed" to "a job exists" is exercised.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from _seed import connect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.jobs import job_state  # noqa: E402
from services.jobs.manager import JobManager  # noqa: E402
from services.scheduling import model, store  # noqa: E402
from services.scheduling.scheduler import ScheduleRunner, Scheduler  # noqa: E402
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
    """An administrator to own the schedules this module creates."""
    username = f"sched_{uuid.uuid4().hex[:8]}"
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


def _make(conn, admin_id, *, schedule_type="retention", payload=None,
          interval=60, enabled=True):
    row = store.create_schedule(
        conn, schedule_type=schedule_type, name=f"t_{uuid.uuid4().hex[:6]}",
        owner_user_id=admin_id, payload=payload or {}, interval_minutes=interval)
    conn.commit()
    if not enabled:
        store.update_schedule(conn, row["id"], enabled=False)
        conn.commit()
        row = store.get_schedule(conn, row["id"])
    return row


def test_the_app_can_import_the_name_it_asks_for():
    """`app.py` imports `Scheduler`; the module defines `ScheduleRunner`."""
    assert Scheduler is ScheduleRunner


def test_the_adapter_lists_only_enabled_schedules_owned_by_a_real_user(conn, admin_id,
                                                                     adapter):
    on = _make(conn, admin_id)
    off = _make(conn, admin_id, enabled=False)

    enabled = {s.id for s in adapter.list_enabled()}
    assert on["id"] in enabled
    assert off["id"] not in enabled

    fetched = adapter.get(on["id"])
    assert fetched.schedule_type == "retention"
    assert fetched.payload == {}
    assert fetched.disabled is False


def test_the_adapter_reads_the_owner_as_the_database_sees_them(conn, admin_id, adapter):
    owner = adapter.get_user(admin_id)
    assert owner["role"] == "admin" and owner["is_active"] is True
    assert adapter.get_user(-1) is None


def test_the_adapter_disables_with_a_stated_reason(conn, admin_id, adapter):
    row = _make(conn, admin_id)
    adapter.disable(row["id"], "owner_role")
    refreshed = store.get_schedule(conn, row["id"])
    assert refreshed["enabled"] is False
    assert refreshed["disabled_reason"] == "owner_role"


def test_fire_now_enqueues_the_job_for_an_authorised_owner(conn, admin_id, adapter):
    """The path ``POST /api/schedules/<id>/run_now`` takes.

    A retention schedule is used because the schedule vocabulary is closed by a
    CHECK constraint (m0001): retention is the type that exists and whose job
    is harmless to run.
    """
    row = _make(conn, admin_id, schedule_type="retention")
    manager = JobManager(synchronous=True)
    runner = ScheduleRunner(job_manager=manager, store=adapter)

    outcome = runner.fire_now(row)
    assert outcome["status"] == "queued", outcome
    stored = manager.get(outcome["job_id"])
    assert stored["job_type"] == "retention"
    assert stored["status"] in (job_state.COMPLETED, job_state.QUEUED,
                                job_state.COMPLETED_WITH_WARNINGS)


def test_fire_now_refuses_and_disables_when_the_owner_lost_the_role(conn, admin_id,
                                                                   adapter):
    row = _make(conn, admin_id)
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET role = 'analyst' WHERE id = %s", (admin_id,))
    conn.commit()

    manager = JobManager(synchronous=True)
    runner = ScheduleRunner(job_manager=manager, store=adapter)
    outcome = runner.fire_now(row)

    assert outcome["status"] == "disabled"
    assert outcome["reason"] == "owner_role"
    refreshed = store.get_schedule(conn, row["id"])
    assert refreshed["enabled"] is False
    assert refreshed["disabled_reason"] == "owner_role"


def test_a_fire_through_the_registered_trigger_creates_a_job(conn, admin_id, adapter):
    """The path APScheduler takes: the trigger's function, with the schedule id.

    The clock is not simulated - the interval is left alone - but the function
    APScheduler would call is the function called here, which is the part that
    was broken.
    """
    row = _make(conn, admin_id)
    manager = JobManager(synchronous=True)
    runner = ScheduleRunner(job_manager=manager, store=adapter)

    runner._schedule_one(adapter.get(row["id"]))
    apscheduler_job = runner._scheduler.get_job(f"schedule-{row['id']}")
    assert apscheduler_job is not None, "the schedule was never registered"

    apscheduler_job.func(row["id"])

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM jobs WHERE job_type = 'retention'"
                    " AND created_at > NOW() - INTERVAL '2 minutes'")
        assert cur.fetchone()[0] >= 1, "the registered trigger produced no job"

    runner._scheduler.remove_all_jobs()


def test_run_now_answers_200_now_that_a_runner_exists(admin_client, conn, admin_id):
    row = _make(conn, admin_id, schedule_type="retention")
    resp = admin_client.post(f"/api/schedules/{row['id']}/run_now")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    assert resp.get_json()["result"]["status"] == "queued"


def test_the_database_and_the_vocabulary_agree_on_what_may_be_scheduled(conn):
    """The CHECK constraint and ``SCHEDULE_TYPES`` must not drift apart.

    This is what stopped a third type being scheduled without a migration: the
    table refuses it. Reading the constraint from the catalogue means the next
    person to add a type learns that here rather than from a 500.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'job_schedules_schedule_type_check'")
        definition = cur.fetchone()[0]
    # The definition reads CHECK (((schedule_type)::text = ANY ((ARRAY['a'::...,
    # 'b'::...])::text[]))); the names are the quoted literals inside it.
    import re

    allowed = set(re.findall(r"'([a-z_]+)'", definition.split("ARRAY[", 1)[1]))
    assert allowed == set(model.SCHEDULE_TYPES)
