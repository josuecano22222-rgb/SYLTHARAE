"""Reading the audit log (``audit_log``, m0003) - administrators only.

The log is written by ``AuthService.audit`` / ``audit_strict`` and by the
fail-closed ``DATA_EXPORTED`` hook; nothing here writes to it. This module
answers "who did what, to what, when, from where" over it:

* filters: exact ``action``, exact ``username``, ``user_id``, a ``resource``
  prefix (``scenario_evaluation:12``, ``export:search``...), a time window
  (``since`` inclusive, ``until`` exclusive, ISO 8601);
* newest first, keyset-paged by id (``before_id``) so a page is stable while
  new entries arrive and deep pages cost the same as the first;
* no silent truncation: a page asks for ``limit + 1`` rows and says
  ``has_more``; the total is not computed (counting an unbounded log is the
  unbounded query this page must not issue) and the response says so;
* one ``REPEATABLE READ, READ ONLY`` transaction with a statement timeout per
  request; a timeout is reported as such, never as "no entries".

Parameterised SQL only; the ``resource`` prefix escapes ``%``, ``_`` and
``\\`` so it is matched literally.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.errors
import psycopg2.extras

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_ACTIONS = 500
STATEMENT_TIMEOUT_MS = 5_000
_ID = re.compile(r"^[1-9][0-9]{0,18}\Z")


class AuditQueryError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _positive_int(name: str, raw: Any) -> Optional[int]:
    if raw in (None, ""):
        return None
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    if isinstance(raw, str) and _ID.match(raw):
        return int(raw)
    raise AuditQueryError("VALIDATION_FAILED", f"{name} must be a positive whole number")


def _timestamp(name: str, raw: Any) -> Optional[datetime.datetime]:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise AuditQueryError("VALIDATION_FAILED", f"{name} must be an ISO 8601 date or time")
    try:
        value = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise AuditQueryError("VALIDATION_FAILED",
                              f"{name} must be an ISO 8601 date or time") from None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def _text(name: str, raw: Any, limit: int = 255) -> Optional[str]:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw) > limit:
        raise AuditQueryError("VALIDATION_FAILED", f"{name} must be text of at most {limit} characters")
    return raw


def parse_filters(args: Dict[str, Any]) -> Dict[str, Any]:
    """Strictly parse request arguments (unknown names refused)."""
    known = {"action", "username", "user_id", "resource", "since", "until", "before_id", "limit"}
    unknown = sorted(set(args) - known)
    if unknown:
        raise AuditQueryError("VALIDATION_FAILED", f"unknown filter(s): {', '.join(unknown)}")
    limit = _positive_int("limit", args.get("limit")) or DEFAULT_LIMIT
    if limit > MAX_LIMIT:
        raise AuditQueryError("VALIDATION_FAILED", f"limit must be at most {MAX_LIMIT}")
    filters = {
        "action": _text("action", args.get("action")),
        "username": _text("username", args.get("username"), 64),
        "user_id": _positive_int("user_id", args.get("user_id")),
        "resource": _text("resource", args.get("resource")),
        "since": _timestamp("since", args.get("since")),
        "until": _timestamp("until", args.get("until")),
        "before_id": _positive_int("before_id", args.get("before_id")),
        "limit": limit,
    }
    if filters["since"] and filters["until"] and filters["since"] >= filters["until"]:
        raise AuditQueryError("VALIDATION_FAILED", "since must be earlier than until")
    return filters


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _entry(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row["id"], "user_id": row["user_id"], "username": row["username"],
            "action": row["action"], "resource": row["resource"], "detail": row["detail"],
            "ip_address": row["ip_address"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None}


def _read(conn, sql: str, params) -> List[Dict[str, Any]]:
    conn.rollback()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    except psycopg2.errors.QueryCanceled:
        conn.rollback()
        raise AuditQueryError("QUERY_TIMEOUT",
                              f"the audit query took longer than {STATEMENT_TIMEOUT_MS // 1000} s; "
                              "narrow it (action, user, resource or time window)", 503) from None
    conn.rollback()
    return rows


def list_entries(conn, filters: Dict[str, Any]) -> Dict[str, Any]:
    where, params = [], []
    for column in ("action", "username", "user_id"):
        if filters.get(column) is not None:
            where.append(f"{column} = %s")
            params.append(filters[column])
    if filters.get("resource"):
        where.append("resource LIKE %s ESCAPE '\\'")
        params.append(_like_prefix(filters["resource"]))
    if filters.get("since"):
        where.append("created_at >= %s")
        params.append(filters["since"])
    if filters.get("until"):
        where.append("created_at < %s")
        params.append(filters["until"])
    if filters.get("before_id"):
        where.append("id < %s")
        params.append(filters["before_id"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    limit = filters["limit"]
    rows = _read(conn, "SELECT id, user_id, username, action, resource, detail, ip_address,"  # nosec B608 # filter columns from a fixed tuple; values are bound parameters
                       " created_at FROM audit_log" + clause + " ORDER BY id DESC LIMIT %s",
                 params + [limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_entry(r) for r in rows],
        "limit": limit,
        "has_more": has_more,
        "next_before_id": rows[-1]["id"] if has_more and rows else None,
        "total": None,
        "total_reason": "not counted: the log is unbounded; page with next_before_id",
    }


def get_entry(conn, entry_id: int) -> Optional[Dict[str, Any]]:
    rows = _read(conn, "SELECT id, user_id, username, action, resource, detail, ip_address,"
                       " created_at FROM audit_log WHERE id = %s", (entry_id,))
    return _entry(rows[0]) if rows else None


def actions(conn) -> Dict[str, Any]:
    """The distinct action names (for the filter menu), capped and stated."""
    # A loose index scan over idx_audit_log_action: one index probe per
    # distinct name instead of reading every entry (SELECT DISTINCT measured
    # 101 ms at 1,000,000 entries; this is proportional to the names).
    rows = _read(conn, """
        WITH RECURSIVE names(action) AS (
            SELECT min(action) FROM audit_log
            UNION ALL
            SELECT (SELECT min(a.action) FROM audit_log a WHERE a.action > names.action)
            FROM names WHERE names.action IS NOT NULL
        )
        SELECT action FROM names WHERE action IS NOT NULL LIMIT %s""", (MAX_ACTIONS + 1,))
    names = [r["action"] for r in rows]
    return {"items": names[:MAX_ACTIONS], "capped": len(names) > MAX_ACTIONS,
            "cap": MAX_ACTIONS}
