"""Detection coverage and run history, for administrators (Detection page).

Reads ``content_signal_runs`` (m0017), the one record of which detector
version analysed which content, when and with what outcome. Nothing here
detects or writes; re-detection is the existing ``signal_redetection`` job
(``services/detection/redetection.py``, queued by ``POST
/api/signals/redetect``).

* ``detection_status`` - per registered detector: the current version, the
  run counts by (version, status), how many contents were never analysed
  and how many the ``stale`` scope would process. The stale count uses the
  same SQL the job uses (``redetection.HAS_TEXT`` and ``_stale_clause``),
  so the number shown is the set a "re-detect stale" job would select.
  All counts come from one ``REPEATABLE READ, READ ONLY`` snapshot and are
  exact; "never analysed" is reported as such, never as zero signals.
* ``list_runs`` - the run rows, newest first, filterable by detector,
  status and version (``current`` / ``older`` / an exact version), with one
  file of the content for navigation. Paged with ``limit + 1`` and
  ``has_more``; the offset is bounded and a larger one is refused, not
  clipped.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import psycopg2
import psycopg2.errors
import psycopg2.extras

from services.detection import detectors as registry
from services.detection import redetection

STATUSES = ("complete", "truncated", "no_text", "failed")
TRIGGERS = ("ingestion", "redetection")
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_OFFSET = 100_000
STATEMENT_TIMEOUT_MS = 15_000


class DetectionAdminError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _snapshot(conn):
    conn.rollback()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
    return cur


def _versions(conn, names):
    """Current detector versions, read in the caller's transaction (the
    detectors' version() expects a plain tuple cursor)."""
    with conn.cursor() as plain:
        return {name: registry.get(name).version(plain) for name in names}


def _timeout():
    return DetectionAdminError(
        "QUERY_TIMEOUT", f"the detection query took longer than {STATEMENT_TIMEOUT_MS // 1000} s", 503)


def _coverage_counts(cur, versions):
    """Every coverage count in ONE pass over the analysable contents.

    Per detector d with current version v, a content is
    * never analysed  <=> it has no run for d;
    * stale           <=> it has no run for d, or that run's version is not
                          v, or that run failed.
    That is the job's ``redetection._stale_clause`` (NOT EXISTS a run for d
    at v that did not fail) rewritten as a LEFT JOIN: equivalent because
    ``content_signal_runs`` has PRIMARY KEY (hash_id, detector), so there is
    at most one run per (content, detector). The rewrite matters: the six
    separate NOT EXISTS counts re-scanned every content six times (4.0 s at
    500,000 contents; one correlated pass 3.3 s; this 1.0 s, see
    docs/implementation/SIGNAL_DETECTION_ADMIN.md). A v of None (no
    gazetteer) matches no run, so everything is stale, as in the job.
    ``tests/integration/test_detection_admin.py`` checks each count against
    the job's own selection SQL on every run state.
    """
    select = ["count(*) AS analysable"]
    joins, any_stale = [], []
    select_params, join_params, any_params = [], [], []
    for i, (name, version) in enumerate(versions.items()):
        alias = f"r{i}"
        joins.append(f"LEFT JOIN content_signal_runs {alias}"
                     f" ON {alias}.hash_id = h.id AND {alias}.detector = %s")
        join_params.append(name)
        stale = (f"({alias}.hash_id IS NULL OR {alias}.detector_ver IS DISTINCT FROM %s"
                 f" OR {alias}.status = 'failed')")
        select.append(f"count(*) FILTER (WHERE {alias}.hash_id IS NULL) AS \"never_{name}\"")
        select.append(f"count(*) FILTER (WHERE {stale}) AS \"stale_{name}\"")
        select_params.append(version or "")
        any_stale.append(stale)
        any_params.append(version or "")
    select.insert(1, "count(*) FILTER (WHERE " + " OR ".join(any_stale) + ") AS stale_any")
    # Parameter order follows the SQL text: SELECT list, then the JOINs.
    cur.execute("SELECT " + ", ".join(select) + " FROM hashs h " + " ".join(joins)  # nosec B608 # aliases generated in code, detector names from the code registry, filter columns from a fixed tuple; values are bound parameters
                + " WHERE " + redetection.HAS_TEXT,
                any_params + select_params + join_params)
    return dict(cur.fetchone())


def detection_status(conn) -> Dict[str, Any]:
    names = registry.validate(None)
    try:
        cur = _snapshot(conn)
        try:
            versions = _versions(conn, names)
            counts = _coverage_counts(cur, versions)
            analysable, stale_any = counts["analysable"], counts["stale_any"]
            cur.execute("SELECT txid_current_snapshot()::text AS snap, NOW() AS at")
            snap = cur.fetchone()
            out = []
            for name in names:
                current = versions[name]
                cur.execute("SELECT detector_ver, status, count(*) AS n, max(ran_at) AS last"
                            " FROM content_signal_runs WHERE detector = %s"
                            " GROUP BY detector_ver, status ORDER BY detector_ver, status", (name,))
                groups = [dict(r) for r in cur.fetchall()]
                stale, never = counts[f"stale_{name}"], counts[f"never_{name}"]
                at_current = {s: 0 for s in STATUSES}
                older = {s: 0 for s in STATUSES}
                for g in groups:
                    bucket = at_current if (current and g["detector_ver"] == current) else older
                    bucket[g["status"]] += g["n"]
                out.append({
                    "detector": name,
                    "current_version": current,
                    "current_version_available": current is not None,
                    "base_version": registry.get(name).base_version,
                    "at_current_version": at_current,
                    "at_older_versions": older,
                    "by_version": [{"version": g["detector_ver"], "status": g["status"],
                                    "runs": g["n"],
                                    "last_ran_at": g["last"].isoformat() if g["last"] else None}
                                   for g in groups],
                    "never_analysed": never,
                    "stale": stale,
                })
        finally:
            cur.close()
            conn.rollback()
    except psycopg2.errors.QueryCanceled:
        conn.rollback()
        raise _timeout() from None
    return {"analysable_contents": analysable, "stale_any_detector": stale_any,
            "detectors": out, "snapshot": snap["snap"],
            "measured_at": snap["at"].isoformat(), "exact": True}


def _positive(name, raw, default=None, maximum=None, allow_zero=False):
    if raw in (None, ""):
        return default
    if isinstance(raw, int) and not isinstance(raw, bool):
        value = raw
    elif isinstance(raw, str) and raw.isdigit() and len(raw) <= 18:
        value = int(raw)
    else:
        value = -1
    if value < (0 if allow_zero else 1):
        kind = "non-negative" if allow_zero else "positive"
        raise DetectionAdminError("VALIDATION_FAILED", f"{name} must be a {kind} whole number")
    if maximum is not None and value > maximum:
        raise DetectionAdminError("VALIDATION_FAILED", f"{name} must be at most {maximum}")
    return value


def parse_run_filters(args: Dict[str, Any]) -> Dict[str, Any]:
    known = {"detector", "status", "version", "trigger", "hash_id", "limit", "offset"}
    unknown = sorted(set(args) - known)
    if unknown:
        raise DetectionAdminError("VALIDATION_FAILED", f"unknown filter(s): {', '.join(unknown)}")
    detector = args.get("detector") or None
    if detector is not None and detector not in registry.NAMES:
        raise DetectionAdminError("VALIDATION_FAILED",
                                  f"detector must be one of: {', '.join(registry.NAMES)}")
    status = args.get("status") or None
    if status is not None and status not in STATUSES:
        raise DetectionAdminError("VALIDATION_FAILED", f"status must be one of: {', '.join(STATUSES)}")
    trigger = args.get("trigger") or None
    if trigger is not None and trigger not in TRIGGERS:
        raise DetectionAdminError("VALIDATION_FAILED", f"trigger must be one of: {', '.join(TRIGGERS)}")
    version = args.get("version") or None
    if version is not None and (not isinstance(version, str) or len(version) > 40):
        raise DetectionAdminError("VALIDATION_FAILED", "version must be 'current', 'older' or a version")
    if version in ("current", "older") and detector is None:
        raise DetectionAdminError("VALIDATION_FAILED",
                                  f"version '{version}' needs a detector (versions differ per detector)")
    return {"detector": detector, "status": status, "trigger": trigger, "version": version,
            "hash_id": _positive("hash_id", args.get("hash_id")),
            "limit": _positive("limit", args.get("limit"), DEFAULT_LIMIT, MAX_LIMIT),
            "offset": _positive("offset", args.get("offset"), 0, MAX_OFFSET, allow_zero=True)}


def list_runs(conn, filters: Dict[str, Any]) -> Dict[str, Any]:
    where, params = [], []
    for column in ("detector", "status", "trigger", "hash_id"):
        if filters.get(column) is not None:
            where.append(f"r.{column} = %s")
            params.append(filters[column])
    try:
        cur = _snapshot(conn)
        try:
            version = filters.get("version")
            current = None
            if version in ("current", "older"):
                current = _versions(conn, [filters["detector"]])[filters["detector"]]
                if current is None:
                    raise DetectionAdminError(
                        "VERSION_UNAVAILABLE",
                        f"the current {filters['detector']} version cannot be determined "
                        "(for places: no gazetteer loaded); filter by an exact version", 409)
                where.append("r.detector_ver = %s" if version == "current" else "r.detector_ver <> %s")
                params.append(current)
            elif version is not None:
                where.append("r.detector_ver = %s")
                params.append(version)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            cur.execute(
                # Page first, then look up one file per paged run: the file
                # lookup must not run for every matching run.
                "SELECT r.hash_id, r.detector, r.detector_ver, r.status, r.anchor_date,"  # nosec B608 # aliases generated in code, detector names from the code registry, filter columns from a fixed tuple; values are bound parameters
                " r.chars_total, r.chars_scanned, r.signal_count, r.trigger, r.job_id, r.error,"
                " r.ran_at, p.path_id, p.file_name, p.path_count"
                " FROM (SELECT * FROM content_signal_runs r" + clause
                + " ORDER BY r.ran_at DESC, r.hash_id DESC, r.detector LIMIT %s OFFSET %s) r"
                " LEFT JOIN LATERAL (SELECT min(pa.id) AS path_id, count(*) AS path_count,"
                "   (array_agg(pa.file_name ORDER BY pa.id))[1] AS file_name"
                "   FROM paths pa JOIN hash_contexts hc ON hc.id = pa.context_id"
                "   WHERE hc.hash_id = r.hash_id) p ON TRUE"
                " ORDER BY r.ran_at DESC, r.hash_id DESC, r.detector",
                params + [filters["limit"] + 1, filters["offset"]])
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.rollback()
    except psycopg2.errors.QueryCanceled:
        conn.rollback()
        raise _timeout() from None
    has_more = len(rows) > filters["limit"]
    rows = rows[:filters["limit"]]
    for r in rows:
        r["ran_at"] = r["ran_at"].isoformat() if r["ran_at"] else None
        r["anchor_date"] = r["anchor_date"].isoformat() if r["anchor_date"] else None
        r["is_current_version"] = (r["detector_ver"] == current) if current else None
    return {"items": rows, "limit": filters["limit"], "offset": filters["offset"],
            "has_more": has_more, "total": None,
            "total_reason": "not counted per page; the Coverage table has exact counts"}
