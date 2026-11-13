"""The object the runner expects, over the store functions that exist.

``services/scheduling/scheduler.py`` was written against a ``ScheduleStore``
class with ``list_enabled``/``get``/``get_user``/``disable``. The store module
is a set of functions taking a connection, and no class was ever written - so
``ScheduleRunner`` raised ``AttributeError`` on its first call and no schedule
has ever fired.

This is that class: a thin adapter, no logic of its own. The SQL stays in
``store.py``; the authorization stays in ``model.py``; the runner keeps the
policy (re-decide the owner at every fire). Every method opens a pooled
connection and closes it, because a scheduler thread must not hold one between
fires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from services.scheduling import store


@dataclass(frozen=True)
class Schedule:
    """One row of ``job_schedules``, as the runner reads it."""

    id: int
    schedule_type: str
    name: str
    owner_id: int
    payload: Dict[str, Any]
    interval_minutes: int
    enabled: bool

    @property
    def disabled(self) -> bool:
        return not self.enabled

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Schedule":
        return cls(
            id=int(row["id"]),
            schedule_type=row["schedule_type"],
            name=row.get("name") or "",
            owner_id=int(row["owner_user_id"]),
            payload=dict(row.get("payload") or {}),
            interval_minutes=int(row["interval_minutes"]),
            enabled=bool(row["enabled"]),
        )


class ScheduleStoreAdapter:
    """``list_enabled``/``get``/``get_user``/``disable`` over ``store.py``."""

    def __init__(self, connection_factory: Optional[Callable[[], Any]] = None):
        self._connection_factory = connection_factory

    # -- plumbing --------------------------------------------------------
    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        from Api.utils.utils import get_connection

        return get_connection()

    # -- the runner's four calls ----------------------------------------
    def list_enabled(self) -> List[Schedule]:
        conn = self._connect()
        try:
            rows = store.list_schedules(conn, all_users=True)
        finally:
            conn.close()
        return [Schedule.from_row(row) for row in rows if row.get("enabled")]

    def get(self, schedule_id: int) -> Optional[Schedule]:
        conn = self._connect()
        try:
            row = store.get_schedule(conn, schedule_id)
        finally:
            conn.close()
        return Schedule.from_row(row) if row else None

    def get_user(self, owner_id: int) -> Optional[Dict[str, Any]]:
        """The owner as the database sees them *now*.

        The runner re-decides authorization at every fire, so this is a fresh
        read, not a cached one: whoever lost the role since the schedule was
        created must not be able to run it.
        """
        conn = self._connect()
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, role, is_active FROM users WHERE id = %s",
                    (owner_id,))
                row = cur.fetchone()
            conn.rollback()
        finally:
            conn.close()
        if not row:
            return None
        return {"id": row[0], "username": row[1], "role": row[2],
                "is_active": row[3]}

    # -- one scheduler per database ---------------------------------------
    #: Advisory lock key. Any constant works as long as it is stable: two
    #: processes that ask for the same key cannot both be the leader, and a
    #: process that dies releases it with its connection.
    LEADER_LOCK_KEY = 0x5C4E_1D01

    def claim_leadership(self):
        """Hold the scheduler's advisory lock, or return ``None``.

        A session-level advisory lock, taken on a connection this object keeps
        open for as long as it is the leader: the connection *is* the lock, so a
        crashed process releases it and a second process cannot fire the same
        schedule twice. Nothing is written to the database, so this needs no
        migration and no schema.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)",
                            (self.LEADER_LOCK_KEY,))
                acquired = bool(cur.fetchone()[0])
            conn.commit()
        except Exception:
            conn.close()
            raise
        if not acquired:
            conn.close()
            return None
        return conn

    def release_leadership(self, conn) -> None:
        """Give the lock back. Called from ``ScheduleRunner.stop``."""
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)",
                            (self.LEADER_LOCK_KEY,))
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def disable(self, schedule_id: int, reason: str) -> None:
        conn = self._connect()
        try:
            store.update_schedule(conn, schedule_id, enabled=False,
                                  disabled_reason=reason)
            conn.commit()
        finally:
            conn.close()
