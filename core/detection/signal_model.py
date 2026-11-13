"""The one shape of a detected content signal (a ``content_signals`` row).

Every detector (``temporal_intel``, ``place_intel``) returns instances of a
subclass of :class:`ContentSignal`, so storage, dedup and the read API are a
single implementation (``services/detection/signal_store.py``).
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class ContentSignal:
    signal_type: str
    value: str
    surface: str
    char_start: int
    char_end: int
    resolution: str
    language: Optional[str] = None
    calendar: Optional[str] = None
    date_from: Optional[datetime.date] = None
    date_to: Optional[datetime.date] = None
    text_orientation: Optional[str] = None
    anchor_date: Optional[datetime.date] = None
    evidence: Dict = field(default_factory=dict, compare=False, hash=False)
    detector_ver: str = ""
    #: The pattern/method that matched (``evidence["pattern"]``), as a column.
    method: Optional[str] = None
    #: Ordinal detection confidence ``high``/``medium``/``low`` and the rule
    #: that assigned it. Not a calibrated probability.
    confidence: Optional[str] = None
    confidence_basis: Optional[str] = None
    #: The sentence containing the signal, quoted from the original text,
    #: with its offsets (``sentence_start <= char_start < char_end <= sentence_end``).
    sentence: Optional[str] = None
    sentence_start: Optional[int] = None
    sentence_end: Optional[int] = None

    def dedup_key(self, hash_id: int) -> str:
        """Stable identity of this signal for this content (NULL-safe).

        JSON encodes a missing value as ``null``, which cannot collide with a
        string, so two signals differing only in NULL vs "None" never merge.
        """
        payload = json.dumps([int(hash_id), self.detector_ver, self.signal_type, self.value,
                              self.char_start, self.char_end,
                              self.anchor_date.isoformat() if self.anchor_date else None],
                             ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict:
        return {
            "signal_type": self.signal_type, "value": self.value, "surface": self.surface,
            "char_start": self.char_start, "char_end": self.char_end,
            "resolution": self.resolution, "language": self.language,
            "calendar": self.calendar,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "text_orientation": self.text_orientation,
            "anchor_date": self.anchor_date.isoformat() if self.anchor_date else None,
            "evidence": self.evidence, "detector_ver": self.detector_ver,
            "method": self.method, "confidence": self.confidence,
            "confidence_basis": self.confidence_basis, "sentence": self.sentence,
            "sentence_start": self.sentence_start, "sentence_end": self.sentence_end,
        }
