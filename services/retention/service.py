"""Applying retention: counted, batched, audited deletion.

Set-based, bounded work only:

* each area deletes in batches of ``batch`` rows picked by ``ctid`` (no
  primary-key assumption on any table), looping until the batch comes back
  short, the cancellation fires or the progress budget is exhausted;
* the status guard keeps live rows out (a running job, an unfinished run);
* every applied area writes one ``retention.applied`` audit row with the
  actor, the days and the deleted count - deletion is never silent;
* one transaction per batch: a stopped run leaves the earlier batches
  applied and the audit row states exactly how far it got.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

BATCH = 5000


def _settings_get(key: str, default: Any) -> Any:
    try:
        from settings.settings_manager import get_settings_manager

        return get_settings_manager().get(key, default)
    except Exception:
        return default


def configured_days(area: str) -> int:
    """The effective policy for ``area``: the stored setting, re-validated
    by the model (a hand-edited settings file cannot smuggle a policy)."""
    from services.retention.model import effective_days, setting_key

    return effective_days(area, _settings_get(setting_key(area), None))


def count(conn, area: str, days: Optional[int] = None) -> int:
    """How many rows the policy would remove right now (one COUNT)."""
    from services.retention.model import policy_for

    policy = policy_for(area)
    days = configured_days(area) if days is None else days
    if days == 0:
        return 0
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        where = [f"{policy['age_column']} < NOW() - make_interval(days => %s)"]
        args: List[Any] = [days]
        if policy["status_guard"]:
            where.append(f"({policy['status_guard']})")
        cur.execute(f"SELECT count(*) FROM {policy['table']} WHERE "  # nosec B608 # table/column/guard are fixed literals from POLICIES; days is a bound parameter
                    + " AND ".join(where), args)
        n = cur.fetchone()[0]
    conn.rollback()
    return n


def oldest(conn, area: str):
    """The oldest row's age timestamp, or None when the table is empty."""
    from services.retention.model import policy_for

    policy = policy_for(area)
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute(f"SELECT min({policy['age_column']}) FROM "  # nosec B608 # fixed literal from POLICIES
                    + policy["table"])
        row = cur.fetchone()
    conn.rollback()
    return row[0].isoformat() if row and row[0] else None


def total_rows(conn, area: str) -> int:
    from services.retention.model import policy_for

    policy = policy_for(area)
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute(f"SELECT count(*) FROM {policy['table']}")  # nosec B608 # fixed literal from POLICIES
        n = cur.fetchone()[0]
    conn.rollback()
    return n


def overview(conn) -> List[Dict[str, Any]]:
    """Everything the page needs: per area, the effective days, the rows it
    would remove now, the table's size and its oldest row."""
    from services.retention.model import AREAS

    out = []
    for area in AREAS:
        days = configured_days(area)
        out.append({
            "area": area, "days": days,
            "description": _description(area),
            "deletes": _deletes(area),
            "eligible": count(conn, area, days) if days else 0,
            "total": total_rows(conn, area),
            "oldest": oldest(conn, area),
        })
    return out


def _description(area: str) -> str:
    from services.retention.model import POLICIES

    return POLICIES[area]["description"]


def _deletes(area: str) -> str:
    from services.retention.model import POLICIES

    return POLICIES[area]["deletes"]


def apply_area(conn, area: str, *, actor: str = "system",
               batch: int = BATCH,
               progress_cb: Optional[Callable] = None,
               cancel_cb: Optional[Callable[[], bool]] = None,
               days: Optional[int] = None) -> Dict[str, Any]:
    """Prune one area now. Batched, one transaction per batch; the audit
    row states the final count. Returns ``{deleted, batches, days}``."""
    from services.retention.model import policy_for, validate_days

    policy = policy_for(area)
    days = configured_days(area) if days is None else validate_days(
        area, days)
    if days == 0:
        return {"area": area, "deleted": 0, "batches": 0, "days": 0,
                "kept_forever": True}

    where = [f"{policy['age_column']} < NOW() - make_interval(days => %s)"]
    args: List[Any] = [days]
    if policy["status_guard"]:
        where.append(f"({policy['status_guard']})")
    criteria = " AND ".join(where)  # nosec B108

    deleted, batches = 0, 0
    started = time.monotonic()
    while True:
        if cancel_cb and cancel_cb():
            break
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute(
                f"DELETE FROM {policy['table']} WHERE ctid IN ("  # nosec B608 # table/columns/guard are fixed literals from POLICIES; days and the batch size are bound parameters
                f"  SELECT ctid FROM {policy['table']} WHERE {criteria}"
                f"  LIMIT %s)", args + [batch])
            removed = cur.rowcount
        conn.commit()
        deleted += removed
        batches += 1
        if progress_cb:
            progress_cb({"percent": 0, "current_phase": f"retention: {area}",
                         "files_processed": deleted, "files_total": 0})
        if removed < batch:
            break
    _audit(area, actor, days, deleted, batches)
    logger.info("retention %s: %d rows in %d batches (%.1fs)",
                area, deleted, batches, time.monotonic() - started)
    return {"area": area, "deleted": deleted, "batches": batches, "days": days}


def _audit(area: str, actor: str, days: int, deleted: int, batches: int) -> None:
    try:
        from core.security.service import get_auth_service

        get_auth_service().audit(
            "retention.applied", username=actor or "system",
            resource=f"retention:{area}",
            detail={"days": days, "deleted": deleted, "batches": batches})
    except Exception:
        logger.exception("retention audit write failed for %s", area)


def run_retention(get_connection: Callable, *, areas: Optional[List[str]] = None,
                  actor: str = "system", job_id: Optional[str] = None,
                  progress_cb: Optional[Callable] = None,
                  cancel_cb: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """The ``retention`` job body: every enabled area (or the named subset),
    each in its own batched transactions. Returns the per-area counts."""
    from services.retention.model import AREAS

    targets = areas if areas else list(AREAS)
    results: Dict[str, Any] = {}
    with get_connection() as conn:
        for i, area in enumerate(targets):
            if cancel_cb and cancel_cb():
                break
            results[area] = apply_area(conn, area, actor=actor,
                                       progress_cb=progress_cb,
                                       cancel_cb=cancel_cb)
            if progress_cb:
                progress_cb({"percent": int((i + 1) * 100 / max(len(targets), 1)),
                             "current_phase": f"retention: {area}",
                             "files_processed": results[area]["deleted"],
                             "files_total": 0})
    return {"areas": results,
            "deleted": sum(r["deleted"] for r in results.values()),
            "cancelled": bool(cancel_cb and cancel_cb())}
