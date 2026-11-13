"""Persist detector output in ``content_signals`` / ``content_signal_runs``.

One implementation used by both paths that produce signals:

* **ingestion** (step 10 of ``ContentDBService.process_document``), inside
  the document's own transaction and a savepoint, and
* **re-detection** (job type ``signal_redetection``), which re-reads stored
  text so a new detector version can be applied to existing content.

Semantics: for a given (content, detector) the stored signals are *replaced*
atomically by the latest run, so a detector upgrade never leaves two
versions' signals side by side. ``ON CONFLICT (dedup_key) DO NOTHING`` makes
concurrent runs on the same content safe. The run row is always written -
including for content with no text and for failed runs - so "not analysed"
can never be mistaken for "nothing found".
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2.extras

from core.criteria.compiler import AccessScope
from core.detection import temporal_intel

logger = logging.getLogger(__name__)

TRIGGER_INGESTION = "ingestion"
TRIGGER_REDETECTION = "redetection"

_INSERT = (
    "INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type, value, surface,"
    " char_start, char_end, language, calendar, resolution, date_from, date_to,"
    " text_orientation, anchor_date, evidence, dedup_key, method, confidence,"
    " confidence_basis, evidence_sentence, sentence_start, sentence_end) VALUES %s"
    " ON CONFLICT (dedup_key) DO NOTHING RETURNING id, dedup_key"
)

_UPSERT_RUN = (
    "INSERT INTO content_signal_runs (hash_id, detector, detector_ver, status, anchor_date,"
    " chars_total, chars_scanned, signal_count, trigger, job_id, error, ran_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
    " ON CONFLICT (hash_id, detector) DO UPDATE SET detector_ver = EXCLUDED.detector_ver,"
    " status = EXCLUDED.status, anchor_date = EXCLUDED.anchor_date,"
    " chars_total = EXCLUDED.chars_total, chars_scanned = EXCLUDED.chars_scanned,"
    " signal_count = EXCLUDED.signal_count, trigger = EXCLUDED.trigger,"
    " job_id = EXCLUDED.job_id, error = EXCLUDED.error, ran_at = NOW()"
)


def _row(hash_id: int, detector: str, sig) -> Tuple:
    return (hash_id, detector, sig.detector_ver, sig.signal_type, sig.value,
            sig.surface, sig.char_start, sig.char_end, sig.language, sig.calendar,
            sig.resolution, sig.date_from, sig.date_to, sig.text_orientation, sig.anchor_date,
            psycopg2.extras.Json(sig.evidence), sig.dedup_key(hash_id), sig.method,
            sig.confidence, sig.confidence_basis, sig.sentence, sig.sentence_start,
            sig.sentence_end)


def store_detection(cur, hash_id: int, result, *, trigger: str,
                    job_id: Optional[str] = None) -> Dict[str, Any]:
    """Replace this content's signals from ``result.detector`` and record the run.

    Place signals also get their candidate places (``content_signal_places``)
    in the same transaction, so a place signal never exists without them.
    """
    detector = getattr(result, "detector", temporal_intel.DETECTOR_NAME)
    cur.execute("DELETE FROM content_signals WHERE hash_id = %s AND detector = %s",
                (hash_id, detector))
    replaced = cur.rowcount
    inserted = 0
    if result.signals:
        rows = [_row(hash_id, detector, s) for s in result.signals]
        returned = psycopg2.extras.execute_values(
            cur, _INSERT, rows, page_size=500, fetch=True)
        inserted = len(returned)
        ids = {key: sid for sid, key in returned}
        links = [(ids[s.dedup_key(hash_id)], pid) for s in result.signals
                 if s.dedup_key(hash_id) in ids for pid in getattr(s, "place_ids", ())]
        missing = [s.value for s in result.signals if s.signal_type == "place_mention"
                   and s.dedup_key(hash_id) in ids and not getattr(s, "place_ids", ())]
        if missing:
            raise ValueError(f"place signals without candidate places: {missing[:3]}")
        if links:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO content_signal_places (signal_id, place_id) VALUES %s",
                links, page_size=1000)
    status = "truncated" if result.truncated else "complete"
    cur.execute(_UPSERT_RUN, (hash_id, detector, result.detector_ver, status,
                              result.anchor_date, result.chars_total, result.chars_scanned,
                              inserted, trigger, job_id, None))
    return {"hash_id": hash_id, "detector": detector, "status": status, "signals": inserted,
            "duplicates_skipped": len(result.signals) - inserted, "replaced": replaced,
            "truncated": result.truncated}


def record_run(cur, hash_id: int, *, status: str, trigger: str, error: Optional[str] = None,
               anchor_date: Optional[datetime.date] = None, job_id: Optional[str] = None,
               chars_total: Optional[int] = None,
               detector: str = temporal_intel.DETECTOR_NAME,
               detector_ver: Optional[str] = None) -> None:
    """Record a run that produced no signals by design (no text) or failed."""
    from services.detection import detectors

    if status == "failed" and not error:
        raise ValueError("a failed run must record its error")
    if detector_ver is None:
        detector_ver = detectors.recorded_version(cur, detector)
    if status == "no_text":
        cur.execute("DELETE FROM content_signals WHERE hash_id = %s AND detector = %s",
                    (hash_id, detector))
    cur.execute(_UPSERT_RUN, (hash_id, detector, detector_ver, status, anchor_date, chars_total,
                              0 if chars_total is not None else None, 0, trigger, job_id,
                              error[:2000] if error else None))


#: Why no stored date is used as the anchor for relative expressions.
NO_ANCHOR_REASON = (
    "no authored document date is recorded for content: contents.content_date is "
    "the first date *mentioned in the text* (pipeline _extract_date_from_text), and "
    "paths.file_date is a per-occurrence filesystem date - neither is when the "
    "document was written, so relative expressions are left unresolved")


def anchor_for(cur, hash_id: int) -> Tuple[Optional[datetime.date], Optional[str]]:
    """The document's own (authored) date for ``hash_id`` - currently none.

    Returns ``(None, reason)``. Anchoring "tomorrow" on a date merely
    mentioned in the text would fabricate a resolution (a memo citing 1990
    would put "tomorrow" in 1990). When extractors record an authored date
    (e.g. an e-mail ``Date`` header) this is the single place to read it.
    """
    return None, NO_ANCHOR_REASON


def stored_text(cur, hash_id: int) -> Optional[str]:
    """The stored display text (``contents_raw`` chunks in order), or None."""
    cur.execute("SELECT content FROM contents_raw WHERE hash_id = %s ORDER BY chunk_seq",
                (hash_id,))
    rows = cur.fetchall()
    if not rows:
        return None
    return "".join(r[0] for r in rows)


def detect_and_store(cur, hash_id: int, text: Optional[str], anchor_date: Optional[datetime.date],
                     *, trigger: str, job_id: Optional[str] = None,
                     detector: str = temporal_intel.DETECTOR_NAME) -> Dict[str, Any]:
    """Run one detector over ``text`` and store its signals and run row."""
    from services.detection import detectors

    impl = detectors.get(detector)
    if text is None or not text.strip():
        record_run(cur, hash_id, status="no_text", trigger=trigger, anchor_date=anchor_date,
                   job_id=job_id, chars_total=len(text or ""), detector=detector)
        return {"hash_id": hash_id, "detector": detector, "status": "no_text", "signals": 0}
    result = impl.detect(cur, text, anchor_date)
    return store_detection(cur, hash_id, result, trigger=trigger, job_id=job_id)


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


#: The one column list every signal read uses (per content, Explorer, Horizon),
#: so all of them serialise a signal identically via ``signal_rows_to_dicts``.
SIGNAL_COLUMNS = (
    "s.id, s.detector, s.signal_type, s.value, s.surface, s.char_start, s.char_end, s.language,"
    " s.calendar, s.resolution, s.date_from, s.date_to, s.text_orientation, s.anchor_date,"
    " s.evidence, s.detector_ver, s.method, s.confidence, s.confidence_basis,"
    " s.evidence_sentence, s.sentence_start, s.sentence_end, s.hash_id, s.detected_at")
SIGNAL_COLUMN_COUNT = 24


def signal_rows_to_dicts(cur, rows: Sequence[tuple], reference_date: datetime.date
                         ) -> List[Dict[str, Any]]:
    """API form of ``SIGNAL_COLUMNS`` rows: typed through the detector's signal
    class, clock orientation against ``reference_date``, place candidates for
    place signals (one query for the whole batch, never one per row)."""
    from core.detection.signal_model import ContentSignal

    candidates: Dict[int, List[Dict[str, Any]]] = {}
    place_ids = [r[0] for r in rows if r[1] == "places"]
    if place_ids:
        # ``feature_type`` is the curated category word (city/country/region),
        # joined in because geo_places stores a category, not a type name.
        # The public place key is derived from the row id: geo_places has no
        # external-identifier column, and the id is stable across seed syncs
        # (the upsert matches on label + category + country_codes).
        cur.execute("SELECT csp.signal_id, 'place:' || g.id, g.label, c.word, g.country_codes,"
                    " g.latitude, g.longitude, g.retired FROM content_signal_places csp"
                    " JOIN geo_places g ON g.id = csp.place_id"
                    " JOIN categorys cat ON cat.id = g.category_id"
                    " JOIN words c ON c.id = cat.word_id"
                    " WHERE csp.signal_id = ANY(%s)"
                    " ORDER BY csp.signal_id, g.id", (place_ids,))
        for c in cur.fetchall():
            candidates.setdefault(c[0], []).append({
                "place_key": c[1], "label": c[2], "feature_type": c[3],
                "country_codes": list(c[4] or []), "latitude": c[5], "longitude": c[6],
                "retired": c[7]})
    signals: List[Dict[str, Any]] = []
    for row in rows:
        fields = dict(
            signal_type=row[2], value=row[3], surface=row[4], char_start=row[5],
            char_end=row[6], language=row[7], calendar=row[8], resolution=row[9],
            date_from=row[10], date_to=row[11], text_orientation=row[12], anchor_date=row[13],
            evidence=row[14] or {}, detector_ver=row[15], method=row[16],
            confidence=row[17], confidence_basis=row[18], sentence=row[19],
            sentence_start=row[20], sentence_end=row[21])
        if row[1] == temporal_intel.DETECTOR_NAME:
            sig = temporal_intel.TemporalSignal(**fields)
            item = sig.to_dict()
            item["clock_orientation"] = (sig.clock_orientation(reference_date)
                                         if sig.signal_type != temporal_intel.SIGNAL_ORIENTATION
                                         else None)
        else:
            item = ContentSignal(**fields).to_dict()
            item["clock_orientation"] = None
        item["detector"] = row[1]
        item["signal_id"] = row[0]
        item["hash_id"] = row[22]
        item["detected_at"] = row[23].isoformat() if row[23] else None
        if row[1] == "places":
            item["places"] = candidates.get(row[0], [])
        signals.append(item)
    return signals


def signals_for(cur, hash_id: int, reference_date: datetime.date, *,
                scope: AccessScope, detectors: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Signals and run status for one content, with clock orientation computed
    against ``reference_date`` (explicit; never an implicit clock).

    Authorization happens here, before any signal is read: the content must
    exist and - when ``scope`` restricts sources - appear in at least one
    allowed source. Both failures raise the same ``LookupError`` so callers
    cannot distinguish "absent" from "not yours".
    """
    if not isinstance(reference_date, datetime.date):
        raise ValueError("reference_date is required")
    if not isinstance(scope, AccessScope):
        raise TypeError("an AccessScope is required")
    if scope.allowed_source_ids is None:
        cur.execute("SELECT 1 FROM hashs WHERE id = %s", (hash_id,))
    elif not scope.allowed_source_ids:
        raise LookupError(hash_id)
    else:
        cur.execute("SELECT 1 FROM hash_contexts WHERE hash_id = %s"
                    " AND source_id = ANY(%s) LIMIT 1",
                    (hash_id, list(scope.allowed_source_ids)))
    if cur.fetchone() is None:
        raise LookupError(hash_id)
    from services.detection import detectors as registry

    wanted = registry.validate(list(detectors) if detectors else None)
    cur.execute("SELECT detector, detector_ver, status, anchor_date, chars_total, chars_scanned,"
                " signal_count, trigger, job_id, error, ran_at FROM content_signal_runs"
                " WHERE hash_id = %s AND detector = ANY(%s)", (hash_id, list(wanted)))
    runs: Dict[str, Optional[Dict[str, Any]]] = {name: None for name in wanted}
    for r in cur.fetchall():
        current = registry.get(r[0]).version(cur)
        runs[r[0]] = {"detector": r[0], "detector_ver": r[1], "status": r[2],
                      "anchor_date": r[3].isoformat() if r[3] else None,
                      "chars_total": r[4], "chars_scanned": r[5], "signal_count": r[6],
                      "trigger": r[7], "job_id": r[8], "error": r[9],
                      "ran_at": r[10].isoformat() if r[10] else None,
                      "current_version": r[1] == current}
    cur.execute(f"SELECT {SIGNAL_COLUMNS} FROM content_signals s"  # nosec B608 # SIGNAL_COLUMNS is a module constant; values are bound parameters
                " WHERE s.hash_id = %s AND s.detector = ANY(%s)"
                " ORDER BY s.char_start, s.char_end, s.detector, s.signal_type, s.value",
                (hash_id, list(wanted)))
    signals = signal_rows_to_dicts(cur, cur.fetchall(), reference_date)
    temporal_run = runs.get(temporal_intel.DETECTOR_NAME)
    return {"hash_id": hash_id,
            "status": temporal_run["status"] if temporal_run else "not_analysed",
            "run": temporal_run,
            "runs": {name: info or {"detector": name, "status": "not_analysed"}
                     for name, info in runs.items()},
            "reference_date": reference_date.isoformat(), "signals": signals}
