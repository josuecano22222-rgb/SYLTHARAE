"""Database operations for ``job_schedules`` (migration m0031).

All SQL is parameterized. The claim is the heart of the at-most-once
guarantee: ``claim_due`` advances ``next_run_at`` for the rows it returns
inside one ``FOR UPDATE SKIP LOCKED`` statement, so two ticks (or two
processes) can never take the same fire, and a schedule that was due
through a long downtime fires once, not once per missed interval.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from services.scheduling.model import (
    DISABLE_REASONS, SCHEDULE_TYPES, ScheduleError, validate_interval, validate_payload,
)

_SCHEDULE_COLUMNS = (
    "id, schedule_type, name, owner_user_id, payload, interval_minutes, enabled,"
    " disabled_reason, next_run_at, last_run_at, last_job_id, last_status,"
    " consecutive_failures, created_at, updated_at")


def _dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _to_api(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in ("next_run_at", "last_run_at", "created_at", "updated_at"):
        out[key] = row[key].isoformat() if row.get(key) else None
    return out


def create_schedule(conn, *, schedule_type: str, name: str, owner_user_id: int,
                    payload: Any, interval_minutes: Any,
                    next_run_at: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Validate and insert one schedule. Commits. Returns the API shape."""
    if schedule_type not in SCHEDULE_TYPES:
        raise ScheduleError(
            f"schedule_type must be one of: {', '.join(SCHEDULE_TYPES)}")
    minutes = validate_interval(interval_minutes)
    stored_payload = validate_payload(schedule_type, payload)
    if not isinstance(name, str) or not name.strip():
        raise ScheduleError("name is required")
    when = next_run_at
    try:
        with conn.cursor() as cur:
            if when is None:
                # The first fire is one interval from now - never immediately.
                cur.execute(
                    "INSERT INTO job_schedules (schedule_type, name, owner_user_id,"
                    " payload, interval_minutes, next_run_at)"
                    " VALUES (%s, %s, %s, %s, %s,"
                    " NOW() + make_interval(mins => %s)) RETURNING id",
                    (schedule_type, name.strip(), owner_user_id,
                     psycopg2.extras.Json(stored_payload), minutes, minutes))
            else:
                cur.execute(
                    "INSERT INTO job_schedules (schedule_type, name, owner_user_id,"
                    " payload, interval_minutes, next_run_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (schedule_type, name.strip(), owner_user_id,
                     psycopg2.extras.Json(stored_payload), minutes, when))
            new_id = cur.fetchone()[0]
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise ScheduleError("you already have a schedule with this name") from None
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + _SCHEDULE_COLUMNS +
                    " FROM job_schedules WHERE id = %s", (new_id,))
        row = cur.fetchone()
    conn.rollback()
    return _to_api(row)


def get_schedule(conn, schedule_id: int) -> Optional[Dict[str, Any]]:
    conn.rollback()   # a pooled connection may arrive mid-transaction
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + _SCHEDULE_COLUMNS +
                    " FROM job_schedules WHERE id = %s", (schedule_id,))
        row = cur.fetchone()
    conn.rollback()
    return _to_api(row) if row else None


def list_schedules(conn, *, owner_user_id: Optional[int] = None,
                   all_users: bool = False) -> List[Dict[str, Any]]:
    where = "" if all_users else " WHERE owner_user_id = %s"
    with _dict_cur(conn) as cur:
        cur.execute("SELECT " + _SCHEDULE_COLUMNS + " FROM job_schedules" + where +
                    " ORDER BY enabled DESC, next_run_at NULLS LAST, id",
                    () if all_users else (owner_user_id,))
        rows = cur.fetchall()
    conn.rollback()
    return [_to_api(r) for r in rows]


def update_schedule(conn, schedule_id: int, *, name: Optional[str] = None,
                    payload: Any = None, interval_minutes: Any = None,
                    enabled: Optional[bool] = None,
                    disabled_reason: Optional[str] = None) -> Dict[str, Any]:
    """Apply the supplied fields (others untouched). Resumes clear the fire
    time forward from now, so a long pause never fires a burst on resume.
    Commits. Returns the fresh row; ``None`` if the schedule is gone."""
    current = get_schedule(conn, schedule_id)
    if current is None:
        return None
    sets, args = [], []
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ScheduleError("name is required")
        sets.append("name = %s")
        args.append(name.strip())
    if payload is not None:
        sets.append("payload = %s")
        args.append(psycopg2.extras.Json(
            validate_payload(current["schedule_type"], payload)))
    if interval_minutes is not None:
        sets.append("interval_minutes = %s")
        args.append(validate_interval(interval_minutes))
    if enabled is not None:
        sets.append("enabled = %s")
        args.append(bool(enabled))
        if enabled:
            sets.append("disabled_reason = NULL")
            sets.append("next_run_at = NOW() + make_interval(mins => interval_minutes)")
            sets.append("consecutive_failures = 0")
        else:
            sets.append("disabled_reason = %s")
            args.append(disabled_reason or DISABLE_REASONS["paused"])
    if not sets:
        return current
    args.append(schedule_id)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE job_schedules SET " + ", ".join(sets) +
                        ", updated_at = NOW() WHERE id = %s", args)
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise ScheduleError("you already have a schedule with this name") from None
    conn.rollback()
    fresh = get_schedule(conn, schedule_id)
    return fresh


def delete_schedule(conn, schedule_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM job_schedules WHERE id = %s", (schedule_id,))
        deleted = cur.rowcount > 0
    conn.commit()
    return deleted


def set_fire_outcome(conn, schedule_id: int, *, job_id: Optional[str],
                     status: Optional[str], disable_reason: Optional[str] = None,
                     enqueue_failed: bool = False) -> None:
    """Record what a fire did: the job it created, or why nothing was created.

    ``disable_reason`` disables the schedule (a permanent condition - the
    owner lost the role, or the payload no longer validates). A transient
    enqueue failure leaves the schedule enabled and counts one failure.
    """
    conn.rollback()   # a pooled connection may arrive mid-transaction
    with conn.cursor() as cur:
        if disable_reason:
            cur.execute(
                "UPDATE job_schedules SET enabled = FALSE,"
                " disabled_reason = %s, last_status = %s, updated_at = NOW()"
                " WHERE id = %s",
                (DISABLE_REASONS[disable_reason],
                 f"disabled: {disable_reason}", schedule_id))
        elif enqueue_failed:
            cur.execute(
                "UPDATE job_schedules SET last_status = 'enqueue_failed',"
                " consecutive_failures = consecutive_failures + 1,"
                " updated_at = NOW() WHERE id = %s", (schedule_id,))
        else:
            cur.execute(
                "UPDATE job_schedules SET last_job_id = %s, last_status = %s,"
                " updated_at = NOW() WHERE id = %s", (job_id, status, schedule_id))
    conn.commit()


def claim_due(conn, *, limit: int = 5,
              now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Take up to ``limit`` due schedules and advance them (at-most-once).

    One statement: select due enabled rows, lock them, move ``next_run_at``
    forward by each row's own interval, return the claimed rows. A crash
    between the claim and the enqueue costs one fire (the next interval
    still comes); it can never double-fire.
    """
    conn.rollback()   # a pooled connection may arrive mid-transaction
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    with _dict_cur(conn) as cur:
        cur.execute(
            "WITH due AS ("
            "  SELECT id FROM job_schedules"
            "  WHERE enabled AND next_run_at <= %s"
            "  ORDER BY next_run_at LIMIT %s FOR UPDATE SKIP LOCKED"
            ") UPDATE job_schedules s SET next_run_at = %s +"
            " make_interval(mins => s.interval_minutes), last_run_at = %s,"
            " updated_at = %s FROM due WHERE s.id = due.id RETURNING s.*",
            (moment, limit, moment, moment, moment))
        rows = cur.fetchall()
    conn.commit()   # the claim is durable: at-most-once, never a burst
    return [_to_api(r) for r in rows]


def refresh_job_statuses(conn) -> int:
    """Copy each schedule's last job status onto the schedule (one set-based
    statement). A job that reached FAILED counts one consecutive failure;
    any other status resets the count. Returns the rows updated."""
    conn.rollback()   # a pooled connection may arrive mid-transaction
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE job_schedules s SET last_status = j.status,"
            " consecutive_failures = CASE WHEN j.status = 'FAILED'"
            "  THEN s.consecutive_failures + 1 ELSE 0 END,"
            " updated_at = NOW()"
            " FROM jobs j"
            " WHERE s.last_job_id = j.job_id"
            "   AND s.last_status IS DISTINCT FROM j.status")
        updated = cur.rowcount
    conn.commit()
    return updated
    # (refresh_job_statuses opens on the caller's fresh transaction)


def owner_of(conn, schedule_id: int) -> Optional[Dict[str, Any]]:
    """The schedule owner as the database sees them right now."""
    conn.rollback()   # never read authorization through an old snapshot
    with _dict_cur(conn) as cur:
        cur.execute(
            "SELECT u.id, u.username, u.role, u.is_active, s.schedule_type,"
            " s.payload, s.name"
            " FROM job_schedules s JOIN users u ON u.id = s.owner_user_id"
            " WHERE s.id = %s", (schedule_id,))
        row = cur.fetchone()
    conn.rollback()
    return dict(row) if row else None
