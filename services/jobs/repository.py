"""Persistence layer for the job system.

Uses the single established database layer (pooled connections from
``database.database``). Additive only: reads/writes the ``jobs`` and
``job_events`` tables created by migration 0006.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2.extras

from Api.utils.utils import get_connection, return_connection


def _now():
    return datetime.now(timezone.utc)


def _dump(value) -> str:
    return json.dumps(value or {}, default=str)


#: The values ``job_events.event_type`` actually admits (the frozen baseline's
#: CHECK). The job system speaks a richer vocabulary - per-file, per-phase and
#: warning/error events (``services/jobs/job_state.EVENT_TYPES``) - so a write
#: is translated here, in the one place that touches the table:
#:
#: * the lifecycle events and ``PROGRESS``/``STATUS_CHANGE`` are stored as-is;
#: * the per-file / per-phase kinds are progress detail of a running job and
#:   are stored as ``PROGRESS``;
#: * ``WARNING``/``ERROR`` are stored as ``STATUS_CHANGE``;
#: * the terminal ``FAILED`` is stored as ``JOB_FAILED``.
#:
#: In every translated case the original name is kept as ``event_kind`` in the
#: payload, so a reader (and the SSE stream, which publishes before persisting)
#: loses nothing. Without this translation PostgreSQL rejects the row outright
#: - the cancellation request's WARNING event and the crash-recovery FAILED
#: event were both being dropped with a CheckViolation.
STORED_EVENT_TYPES = frozenset({
    "JOB_CREATED", "JOB_STARTED", "JOB_PROGRESS", "JOB_PAUSED", "JOB_RESUMED",
    "JOB_COMPLETED", "JOB_COMPLETED_WITH_WARNINGS", "JOB_FAILED",
    "JOB_CANCELLED", "PROGRESS", "STATUS_CHANGE",
})

_KIND_TO_STORED = {
    "JOB_CREATED": "JOB_CREATED", "JOB_STARTED": "JOB_STARTED",
    "JOB_PAUSED": "JOB_PAUSED", "JOB_RESUMED": "JOB_RESUMED",
    "JOB_COMPLETED": "JOB_COMPLETED",
    "JOB_COMPLETED_WITH_WARNINGS": "JOB_COMPLETED_WITH_WARNINGS",
    "JOB_CANCELLED": "JOB_CANCELLED", "JOB_FAILED": "JOB_FAILED",
    "PROGRESS": "PROGRESS", "STATUS_CHANGE": "STATUS_CHANGE",
    "WARNING": "STATUS_CHANGE", "ERROR": "STATUS_CHANGE",
    "FAILED": "JOB_FAILED",
    "PHASE_STARTED": "PROGRESS", "PHASE_COMPLETED": "PROGRESS",
    "FILE_DISCOVERED": "PROGRESS", "FILE_PROCESSING": "PROGRESS",
    "FILE_COMPLETED": "PROGRESS", "FILE_SKIPPED": "PROGRESS",
    "FILE_FAILED": "PROGRESS", "ARCHIVE_EXTRACTED": "PROGRESS",
    "TEXT_EXTRACTED": "PROGRESS", "OCR_COMPLETED": "PROGRESS",
    "DUPLICATE_DETECTED": "PROGRESS", "DATABASE_WRITE": "PROGRESS",
}


def stored_event(event_type: str, payload: Optional[Dict[str, Any]] = None):
    """``(stored_event_type, payload)`` for one event the job system emits."""
    stored = _KIND_TO_STORED.get(event_type, "STATUS_CHANGE")
    body = dict(payload or {})
    if stored != event_type:
        body.setdefault("event_kind", event_type)
    return stored, body


class JobRepository:
    """CRUD + queries for jobs and job events."""

    COLUMNS = (
        "job_id, job_type, status, progress, current_phase, current_item,"
        " source, options, stats, errors, warnings, result_summary,"
        " created_by, created_at, started_at, completed_at,"
        " cancellation_requested, pause_requested, updated_at"
    )

    def _conn(self):
        # Single established connection path (API-04 dual-mode wrapper).
        return get_connection()

    def create(self, record: Dict[str, Any]) -> Dict[str, Any]:
        sql = (
            "INSERT INTO jobs (job_id, job_type, status, progress, current_phase,"
            " source, options, stats, errors, warnings, created_by,"
            " cancellation_requested, pause_requested)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING "
            + COLUMNS_PLACEHOLDER
        )
        params = (
            record["job_id"], record["job_type"], record.get("status", "QUEUED"),
            int(record.get("progress", 0)), record.get("current_phase"),
            record.get("source"), _dump(record.get("options")),
            _dump(record.get("stats")), _dump(record.get("errors", [])),
            _dump(record.get("warnings", [])), record.get("created_by"),
            bool(record.get("cancellation_requested", False)),
            bool(record.get("pause_requested", False)),
        )
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            return_connection(conn)
        if row is None:
            raise RuntimeError("job insert failed")
        return self._decode(dict(row))

    def update_fields(self, job_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        sets, params = [], []
        json_fields = {"options", "stats", "errors", "warnings", "result_summary"}
        for key, value in fields.items():
            if key not in ALLOWED_UPDATE_FIELDS:
                raise ValueError(f"Unknown job field: {key}")
            sets.append(f"{key} = %s")
            params.append(_dump(value) if key in json_fields else value)
        sets.append("updated_at = %s")
        params.append(_now())
        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = %s"
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            return_connection(conn)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        sql = f"SELECT {self.COLUMNS} FROM jobs WHERE job_id = %s"
        return self._decode_or_none(self._fetchone_dict(sql, (job_id,)))

    def list_jobs(
        self,
        job_type: Optional[str] = None,
        status: Optional[str] = None,
        created_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        where, params = [], []
        if job_type:
            where.append("job_type = %s")
            params.append(job_type)
        if status:
            where.append("status = %s")
            params.append(status)
        if created_by:
            where.append("created_by = %s")
            params.append(created_by)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        sql = (
            f"SELECT {self.COLUMNS} FROM jobs{clause}"
            f" ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
        )
        return [self._decode(r) for r in self._fetchall_dicts(sql, params)]

    def count_by_status(self) -> Dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        rows = self._fetchall_dicts(sql, ())
        return {r["status"]: int(r["n"]) for r in rows}

    def stale_running(self, older_than_seconds: int) -> List[Dict[str, Any]]:
        """Jobs marked RUNNING but not updated recently (crash candidates)."""
        sql = (
            f"SELECT {self.COLUMNS} FROM jobs WHERE status = 'RUNNING'"
            " AND updated_at < now() - (%s || ' seconds')::interval"
            " ORDER BY created_at"
        )
        return [self._decode(r) for r in
                self._fetchall_dicts(sql, (int(older_than_seconds),))]

    # -- events ------------------------------------------------------------
    def add_event(self, job_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        event_type, payload = stored_event(event_type, payload)
        sql = ("INSERT INTO job_events (job_id, event_type, payload)"
               " VALUES (%s, %s, %s)")
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (job_id, event_type, _dump(payload)))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            return_connection(conn)

    def add_events(self, job_id: str, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        rows = [(job_id, stored, _dump(body))
                for stored, body in (stored_event(e["event_type"], e.get("payload"))
                                     for e in events)]
        sql = ("INSERT INTO job_events (job_id, event_type, payload)"
               " VALUES (%s, %s, %s)")
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, sql, rows)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            return_connection(conn)

    def get_events(self, job_id: str, limit: int = 500,
                   after_id: int = 0) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        sql = (
            "SELECT event_id, event_type, payload, created_at"
            " FROM job_events WHERE job_id = %s AND event_id > %s"
            " ORDER BY event_id LIMIT " + str(limit)
        )
        rows = self._fetchall_dicts(sql, (job_id, int(after_id)))
        out = []
        for r in rows:
            out.append({
                "event_id": int(r["event_id"]),
                "event_type": r["event_type"],
                "payload": r["payload"] if isinstance(r["payload"], dict)
                else json.loads(r["payload"] or "{}"),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })
        return out

    def delete(self, job_id: str) -> bool:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE job_id = %s", (job_id,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            return_connection(conn)

    # -- helpers -----------------------------------------------------------
    def _fetchone_dict(self, sql, params) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            return_connection(conn)

    def _fetchall_dicts(self, sql, params) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            return_connection(conn)

    @staticmethod
    def _decode_or_none(row):
        return JobRepository._decode(row) if row else None

    @staticmethod
    def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(row)
        for key in ("options", "stats", "result_summary"):
            if key in row and row[key] is not None and not isinstance(row[key], dict):
                try:
                    row[key] = json.loads(row[key] or "{}")
                except (TypeError, ValueError):
                    row[key] = {}
            elif key in row and row[key] is None:
                row[key] = {} if key != "result_summary" else None
        for key in ("errors", "warnings"):
            if key in row and row[key] is not None and not isinstance(row[key], list):
                try:
                    row[key] = json.loads(row[key] or "[]")
                except (TypeError, ValueError):
                    row[key] = []
            elif key in row and row[key] is None:
                row[key] = []
        for key in ("created_at", "started_at", "completed_at", "updated_at"):
            if row.get(key) is not None:
                row[key] = row[key].isoformat()
        for key in ("cancellation_requested", "pause_requested"):
            row[key] = bool(row.get(key))
        return row


ALLOWED_UPDATE_FIELDS = {
    "status", "progress", "current_phase", "current_item", "stats",
    "errors", "warnings", "result_summary", "started_at", "completed_at",
    "cancellation_requested", "pause_requested", "source",
}

COLUMNS_PLACEHOLDER = (
    "job_id, job_type, status, progress, current_phase, current_item,"
    " source, options, stats, errors, warnings, result_summary,"
    " created_by, created_at, started_at, completed_at,"
    " cancellation_requested, pause_requested, updated_at"
)
