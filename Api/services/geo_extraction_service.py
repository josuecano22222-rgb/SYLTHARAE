"""Geolocation scan (``POST /api/file-analysis/geolocation/scan``) - compatibility.

Place mentions are content signals from the ``places`` detector
(``core/detection/place_intel.py``) matched against the database gazetteer
(migration 0019). They are produced at ingestion and by the
``signal_redetection`` job; ``path_geo_mentions`` is a view over them.

This module used to hold a second implementation (125 English regexes run in
a Python loop, one query per match, results written to a table and the most
frequent place written into ``paths.coordinates``). It now delegates to the
shared re-detection path so there is one detector, one store and one job
type. Two behaviours were removed on purpose:

* ``paths.coordinates`` is **no longer written** from text mentions. That
  column holds GPS metadata read from files (``reader_file/readers``); the
  old scan overwrote it with the most-mentioned place, destroying real
  metadata. Mentions are read from ``path_geo_mentions`` / the signals API.
* a name recorded for several places (Tripoli, Perth, ...) is no longer
  resolved to one of them; it is stored as an ambiguous signal with every
  candidate and is not shown in the single-place ``path_geo_mentions`` view.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def scan_and_tag_geolocations(force: bool = False, *, created_by: str = "system") -> Dict[str, Any]:
    """Run place detection over stored content through the job manager.

    ``force=False`` analyses content whose place signals are missing, failed
    or from an older detector/gazetteer version (``stale``); ``force=True``
    re-analyses everything. Returns the job and a summary in the old keys
    where they still have a meaning.
    """
    from services.jobs.manager import JobManager
    from services.jobs.models import job_to_api

    scope = "all" if force else "stale"
    manager = JobManager.get_instance()
    job = manager.create_job(
        "signal_redetection", source=f"signals:{scope}:places",
        options={"scope": scope, "hash_ids": None, "detectors": ["places"]},
        created_by=created_by)
    job = manager.get(job["job_id"]) if manager.synchronous else job
    summary: Dict[str, Any] = {"job_id": job["job_id"], "status": job.get("status"),
                               "coordinates_written": 0}
    result = job.get("result_summary") or {}
    places = (result.get("by_detector") or {}).get("places")
    if places is not None:
        summary.update(hashes_scanned=places.get("runs", 0),
                       hashes_matched=places.get("with_signals", 0),
                       signals=places.get("signals", 0), failed=places.get("failed", 0))
    return {"job": job_to_api(job), "summary": summary}
