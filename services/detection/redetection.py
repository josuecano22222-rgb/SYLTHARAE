"""Re-detection: apply the current detectors to already-stored content.

Runs as the existing JobManager job type ``signal_redetection`` - not a
second job framework. Each (content, detector) pair is its own transaction:
one bad document or one failing detector is recorded as a ``failed`` run
with its error and the job continues; the job finishes
``completed_with_warnings`` (never a silent success) when anything failed.

Selection (``scope``):

* ``stale``   - content never analysed by a requested detector, analysed by
  an older version (for ``places`` that includes an older *gazetteer*), or
  whose last run failed (the default). Only the stale detectors run;
* ``all``     - every content with stored text, every requested detector;
* ``hash_ids``- an explicit list, every requested detector.

``detectors`` restricts which detectors run (default: all registered).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.detection import temporal_intel
from services.detection import detectors as registry
from services.detection import signal_store

logger = logging.getLogger(__name__)

SCOPES = ("stale", "all", "hash_ids")

#: Content the detectors can analyse: stored text in either table. Shared by
#: the ``stale`` selection and by ``detection_status`` so that the count the
#: interface shows is the set the job would process.
HAS_TEXT = ("(EXISTS (SELECT 1 FROM contents_raw r WHERE r.hash_id = h.id)"
            " OR EXISTS (SELECT 1 FROM contents c WHERE c.hash_id = h.id))")
BATCH = 200


@dataclass
class RedetectionResult:
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False
    versions: Dict[str, Optional[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"result_summary": {
            "detector_ver": self.versions.get(temporal_intel.DETECTOR_NAME),
            "detector_versions": dict(self.versions), **self.stats,
            "warnings": len(self.warnings), "cancelled": self.cancelled}}


def _stale_clause(versions: Dict[str, Optional[str]]) -> Tuple[str, Tuple]:
    parts, params = [], []
    for name, version in versions.items():
        parts.append("NOT EXISTS (SELECT 1 FROM content_signal_runs r WHERE r.hash_id = h.id"
                     " AND r.detector = %s AND r.detector_ver = %s AND r.status <> 'failed')")
        # A detector that cannot report a version (no gazetteer) is always stale.
        params.extend([name, version or ""])
    return "(" + " OR ".join(parts) + ")", tuple(params)


def _select_sql(scope: str, versions: Dict[str, Optional[str]]):
    if scope == "all":
        return ("SELECT DISTINCT hash_id FROM contents_raw WHERE hash_id > %s"
                " ORDER BY hash_id LIMIT %s", ())
    if scope == "stale":
        clause, params = _stale_clause(versions)
        return ("SELECT h.id FROM hashs h WHERE h.id > %s AND " + HAS_TEXT + " AND " + clause +  # nosec B608 # HAS_TEXT constant and a stale clause of fixed fragments; detector names and versions are bound parameters
                " ORDER BY h.id LIMIT %s", params)
    raise ValueError(f"scope must be one of {SCOPES}")


def _current_versions(connection_factory, names) -> Dict[str, Optional[str]]:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            versions = {name: registry.get(name).version(cur) for name in names}
        conn.rollback()
        return versions
    finally:
        _release(conn)


def run_redetection(connection_factory: Callable, *, scope: str = "stale",
                    hash_ids: Optional[Sequence[int]] = None, job_id: Optional[str] = None,
                    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
                    cancel_cb: Optional[Callable[[], bool]] = None,
                    detectors: Optional[Sequence[str]] = None) -> RedetectionResult:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    names = registry.validate(list(detectors) if detectors else None)
    if scope == "hash_ids":
        ids = sorted({int(h) for h in (hash_ids or [])})
        if not ids:
            raise ValueError("scope 'hash_ids' requires a non-empty hash_ids list")
    versions = _current_versions(connection_factory, names)
    result = RedetectionResult(
        stats={"scope": scope, "detectors": list(names), "processed": 0, "complete": 0,
               "truncated": 0, "no_text": 0, "failed": 0, "signals": 0,
               "by_detector": {n: {"runs": 0, "complete": 0, "truncated": 0, "no_text": 0,
                                   "failed": 0, "skipped_current": 0, "signals": 0,
                                   "with_signals": 0} for n in names}},
        versions=versions)

    def batches():
        if scope == "hash_ids":
            for i in range(0, len(ids), BATCH):
                yield ids[i:i + BATCH]
            return
        sql, extra = _select_sql(scope, versions)
        last = 0
        while True:
            conn = connection_factory()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, (last, *extra, BATCH))
                    chunk = [r[0] for r in cur.fetchall()]
                conn.rollback()
            finally:
                _release(conn)
            if not chunk:
                return
            yield chunk
            last = chunk[-1]

    for chunk in batches():
        for hash_id in chunk:
            if cancel_cb and cancel_cb():
                result.cancelled = True
                return result
            outcome = redetect_one(connection_factory, hash_id, job_id, detectors=names,
                                   skip_current=versions if scope == "stale" else None)
            result.stats["processed"] += 1
            result.stats[outcome["status"]] = result.stats.get(outcome["status"], 0) + 1
            result.stats["signals"] += outcome.get("signals", 0)
            for name, one in outcome.get("detectors", {}).items():
                bucket = result.stats["by_detector"][name]
                if one["status"] == "current":
                    bucket["skipped_current"] += 1
                    continue
                bucket["runs"] += 1
                bucket[one["status"]] = bucket.get(one["status"], 0) + 1
                bucket["signals"] += one.get("signals", 0)
                bucket["with_signals"] += 1 if one.get("signals") else 0
                if one["status"] == "failed":
                    result.warnings.append(f"hash {hash_id} [{name}]: {one['error']}")
                elif one["status"] == "truncated":
                    result.warnings.append(f"hash {hash_id} [{name}]: text longer than "
                                           f"{temporal_intel.MAX_SCAN_CHARS} characters; "
                                           "scanned up to the limit")
            if outcome["status"] == "failed" and not outcome.get("detectors"):
                result.warnings.append(f"hash {hash_id}: {outcome['error']}")
        if progress_cb:
            progress_cb({"current_phase": "Re-detecting signals",
                         "files_done": result.stats["processed"],
                         "stats": dict(result.stats)})
    return result


def _release(conn) -> None:
    for name in ("release", "close"):
        fn = getattr(conn, name, None)
        if fn:
            try:
                fn()
            except Exception:
                pass
            return


def _overall(statuses: List[str]) -> str:
    ran = [s for s in statuses if s != "current"]
    if not ran:
        return "complete"
    for status in ("failed", "truncated"):
        if status in ran:
            return status
    return "no_text" if all(s == "no_text" for s in ran) else "complete"


def redetect_one(connection_factory: Callable, hash_id: int,
                 job_id: Optional[str] = None, *, detectors: Optional[Sequence[str]] = None,
                 skip_current: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    """Detect and store signals for one content, one transaction per detector.

    Never raises for a detection/database failure: the failure is recorded
    as a ``failed`` run (and logged) and reported in the outcome.
    ``skip_current`` maps detector -> current version; a detector whose
    successful run already has that version is left untouched.
    """
    names = registry.validate(list(detectors) if detectors else None)
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM hashs WHERE id = %s", (hash_id,))
            exists = cur.fetchone() is not None
            current = {}
            if exists and skip_current:
                cur.execute("SELECT detector FROM content_signal_runs WHERE hash_id = %s"
                            " AND status <> 'failed' AND (detector, detector_ver) IN"
                            " (SELECT * FROM unnest(%s::text[], %s::text[]))",
                            (hash_id, list(skip_current), [v or "" for v in skip_current.values()]))
                current = {r[0] for r in cur.fetchall()}
        conn.rollback()
        if not exists:
            return {"status": "failed", "error": "content does not exist"}
        per: Dict[str, Dict[str, Any]] = {}
        for name in names:
            if name in current:
                per[name] = {"status": "current"}
                continue
            per[name] = _run_one(conn, hash_id, name, job_id)
        statuses = [o["status"] for o in per.values()]
        outcome = {"status": _overall(statuses), "detectors": per,
                   "signals": sum(o.get("signals", 0) for o in per.values())}
        errors = [f"[{n}] {o['error']}" for n, o in per.items() if o["status"] == "failed"]
        if errors:
            outcome["error"] = "; ".join(errors)
        return outcome
    finally:
        _release(conn)


def _run_one(conn, hash_id: int, detector: str, job_id: Optional[str]) -> Dict[str, Any]:
    try:
        with conn.cursor() as cur:
            anchor, _reason = signal_store.anchor_for(cur, hash_id)
            text = signal_store.stored_text(cur, hash_id)
            outcome = signal_store.detect_and_store(
                cur, hash_id, text, anchor, trigger=signal_store.TRIGGER_REDETECTION,
                job_id=job_id, detector=detector)
        conn.commit()
        return outcome
    except Exception as exc:
        conn.rollback()
        text = str(exc).strip()
        message = f"{exc.__class__.__name__}: {text.splitlines()[0] if text else ''}"
        logger.error("Re-detection [%s] failed for hash %s: %s", detector, hash_id, message)
        try:
            with conn.cursor() as cur:
                signal_store.record_run(cur, hash_id, status="failed",
                                        trigger=signal_store.TRIGGER_REDETECTION,
                                        error=message, job_id=job_id, detector=detector)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Could not record the failed [%s] run for hash %s", detector,
                             hash_id)
        return {"status": "failed", "error": message}
