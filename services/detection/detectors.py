"""The content detectors, behind one small interface.

Storage (``signal_store``), ingestion (step 10) and re-detection iterate
this registry instead of naming a detector, so adding a detector never adds
a second store, job type or ingestion path.

Each detector provides:

* ``version(cur)`` - the version a run would record now (``None`` when it
  cannot run, e.g. no gazetteer loaded);
* ``detect(cur, text, anchor_date)`` - a result with ``signals``,
  ``detector``, ``detector_ver``, ``anchor_date``, ``chars_total``,
  ``chars_scanned`` and ``truncated``.
"""

from __future__ import annotations

import datetime
from typing import Dict, Optional, Tuple

from core.detection import identifiers_intel, place_intel, temporal_intel


class TemporalDetector:
    name = temporal_intel.DETECTOR_NAME
    base_version = temporal_intel.DETECTOR_VERSION

    def version(self, cur) -> Optional[str]:
        return temporal_intel.DETECTOR_VERSION

    def detect(self, cur, text: str, anchor_date: Optional[datetime.date]):
        return temporal_intel.detect(text, anchor_date=anchor_date)


class PlaceDetector:
    name = place_intel.DETECTOR_NAME
    base_version = place_intel.DETECTOR_BASE_VERSION

    def version(self, cur) -> Optional[str]:
        from services.geo.gazetteer import current_detector_version
        return current_detector_version(cur)

    def detect(self, cur, text: str, anchor_date: Optional[datetime.date]):
        from services.geo.gazetteer import detector_gazetteer
        return place_intel.detect(text, gazetteer=detector_gazetteer(cur))


class IdentifiersDetector:
    name = identifiers_intel.DETECTOR_NAME
    base_version = identifiers_intel.DETECTOR_VERSION

    def version(self, cur) -> Optional[str]:
        return identifiers_intel.DETECTOR_VERSION

    def detect(self, cur, text: str, anchor_date: Optional[datetime.date]):
        return identifiers_intel.detect(text, anchor_date=anchor_date)


DETECTORS: Dict[str, object] = {
    d.name: d for d in (TemporalDetector(), PlaceDetector(), IdentifiersDetector())
}
NAMES: Tuple[str, ...] = tuple(DETECTORS)


def get(name: str):
    try:
        return DETECTORS[name]
    except KeyError:
        raise ValueError(f"unknown detector {name!r}; known: {', '.join(NAMES)}") from None


def recorded_version(cur, name: str) -> str:
    """The version to record on a run row: the current one, or the code's
    base version when the current one is unavailable (the run is then a
    recorded failure and stays stale)."""
    detector = get(name)
    return detector.version(cur) or detector.base_version


def validate(names) -> Tuple[str, ...]:
    """Validate a user-supplied detector list (``None`` = all)."""
    if names is None:
        return NAMES
    if isinstance(names, str) or not isinstance(names, (list, tuple)) or not names:
        raise ValueError("detectors must be a non-empty list")
    for name in names:
        get(name)
    return tuple(n for n in NAMES if n in set(names))
