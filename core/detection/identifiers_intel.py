"""Structured identifier detection (email, phone, IP, URL).

Pattern-based extraction over stored document text — not NER. Each signal is
the matched span with normalised ``value``, evidence and a link to the source
file via char offsets (same contract as temporal and place detectors).

Designed for analyst workflows: list every email / phone / IP in the corpus,
filter by source and side, export, and open the containing document.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.detection import textspan
from core.detection.signal_model import ContentSignal

DETECTOR_NAME = "identifiers"
DETECTOR_VERSION = "identifiers-1.0.0"

#: The kind of identifier a match is. These are *evidence kinds*, not signal
#: types: ``content_signals.signal_type`` has one value for identifiers
#: (``identifier``), and the kind travels in the evidence JSON, which is what
#: the explorer's identifier presets filter on.
SIGNAL_EMAIL = "email"
SIGNAL_PHONE = "phone"
SIGNAL_IPV4 = "ipv4"
SIGNAL_IPV6 = "ipv6"
SIGNAL_URL = "url"
KINDS = (SIGNAL_EMAIL, SIGNAL_PHONE, SIGNAL_IPV4, SIGNAL_IPV6, SIGNAL_URL)
#: The single ``signal_type`` every signal from this detector carries.
SIGNAL_IDENTIFIER = "identifier"
SIGNAL_TYPES = (SIGNAL_IDENTIFIER,)
RESOLUTIONS = ("literal",)
CONFIDENCE_LEVELS = ("high", "medium", "low")
MAX_SCAN_CHARS = 20_000_000

# Priority when spans overlap (higher wins at equal length).
_PRIORITY = {
    SIGNAL_EMAIL: 50,
    SIGNAL_URL: 45,
    SIGNAL_IPV6: 40,
    SIGNAL_IPV4: 35,
    SIGNAL_PHONE: 30,
}

_EMAIL = re.compile(
    r"(?<![\w.@+-])"
    r"([a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+)"
    r"(?![\w.@+-])",
)

_IPV4 = re.compile(
    r"(?<![\d.])"
    r"(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?![\d])",
)

# Common compressed and full IPv6 forms (heuristic, not exhaustive validation).
_IPV6 = re.compile(
    r"(?<![\w:])"
    r"((?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,7}:|"
    r"(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|"
    r"(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|"
    r"[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,6}|"
    r":(?::[0-9a-fA-F]{1,4}){1,7}|"
    r"::(?:[fF]{4}:)?(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))"
    r"(?![\w:])",
)

_URL = re.compile(
    r"(?<![\w./:-])"
    r"(https?://[^\s<>\"']+|www\.[a-zA-Z0-9][-a-zA-Z0-9.]*[^\s<>\"']*)"
    r"(?![\w./:-])",
)

_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:\+|00)?\d{1,3}[\s.\-()]?"
    r"(?:\(?\d{2,4}\)?[\s.\-]?)?"
    r"\d{2,4}[\s.\-]?\d{2,4}(?:[\s.\-]?\d{2,4})?"
    r"(?!\d)",
)


@dataclass(frozen=True)
class IdentifierSignal(ContentSignal):
    """A detected email, phone number, IP or URL span."""


@dataclass(frozen=True)
class DetectionResult:
    signals: Tuple[IdentifierSignal, ...]
    detector_ver: str
    anchor_date: Optional[object]
    chars_total: int
    chars_scanned: int
    truncated: bool
    detector: str = DETECTOR_NAME


def _digits(text: str) -> int:
    return sum(c.isdigit() for c in text)


def _normalise_email(raw: str) -> str:
    return raw.strip().lower()


def _normalise_phone(raw: str) -> str:
    digits = "".join(c for c in raw if c.isdigit())
    if raw.strip().startswith("+"):
        return "+" + digits
    if raw.strip().startswith("00"):
        return "+" + digits[2:] if len(digits) > 2 else digits
    return digits


def _normalise_ipv4(raw: str) -> str:
    parts = raw.split(".")
    return ".".join(str(int(p)) for p in parts)


def _normalise_ipv6(raw: str) -> str:
    return raw.strip().lower()


def _normalise_url(raw: str) -> str:
    t = raw.strip()
    if t.lower().startswith("www."):
        return "http://" + t
    return t


def _candidates(text: str) -> List[Tuple[int, int, str, str, str, str]]:
    """(start, end, signal_type, surface, value, method) in original offsets."""
    out: List[Tuple[int, int, str, str, str, str]] = []

    for m in _EMAIL.finditer(text):
        surface = m.group(1)
        out.append((m.start(1), m.end(1), SIGNAL_EMAIL, surface, _normalise_email(surface), "pattern.email"))

    for m in _URL.finditer(text):
        surface = m.group(1)
        out.append((m.start(1), m.end(1), SIGNAL_URL, surface, _normalise_url(surface), "pattern.url"))

    for m in _IPV6.finditer(text):
        surface = m.group(1)
        if len(surface) < 4:
            continue
        out.append((m.start(1), m.end(1), SIGNAL_IPV6, surface, _normalise_ipv6(surface), "pattern.ipv6"))

    for m in _IPV4.finditer(text):
        surface = m.group(0)
        out.append((m.start(), m.end(), SIGNAL_IPV4, surface, _normalise_ipv4(surface), "pattern.ipv4"))

    for m in _PHONE.finditer(text):
        surface = m.group(0)
        if _IPV4.fullmatch(surface.strip()):
            continue
        d = _digits(surface)
        if d < 7 or d > 15:
            continue
        if surface.count(".") >= 2 and all(p.isdigit() for p in surface.split(".") if p):
            continue
        if d == 10 and not re.search(r"[\s.\-()+]", surface):
            continue
        out.append((m.start(), m.end(), SIGNAL_PHONE, surface, _normalise_phone(surface), "pattern.phone"))

    return out


def _merge_overlaps(cands: List[Tuple[int, int, str, str, str, str]]
                    ) -> List[Tuple[int, int, str, str, str, str]]:
    """Keep the longest candidate of every overlapping group.

    Longest first, then by identifier priority, then by start offset; a
    candidate overlapping anything already kept is dropped. Kept spans are
    therefore disjoint and can be held in a sorted list: a candidate can only
    overlap the kept span that starts before it (if that one ends after it) or
    the one that starts after it (if it starts before the candidate ends).

    Sorting the kept spans and bisecting replaces the previous scan of every
    kept span for every candidate, which is what made identifier detection
    quadratic on large documents (see
    audit_report/evidence_scale/ingest_profile.json).
    """
    ranked = sorted(
        cands,
        key=lambda c: (-(c[1] - c[0]), -_PRIORITY.get(c[2], 0), c[0]),
    )
    starts: List[int] = []
    kept: List[Tuple[int, int, str, str, str, str]] = []
    for cand in ranked:
        start, end = cand[0], cand[1]
        at = bisect_left(starts, start)
        if at and kept[at - 1][1] > start:
            continue
        if at < len(kept) and starts[at] < end:
            continue
        starts.insert(at, start)
        kept.insert(at, cand)
    kept.sort(key=lambda c: (c[0], c[1], c[2], c[4]))
    return kept


def detect(text: Optional[str], *, anchor_date: Optional[object] = None) -> DetectionResult:
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    total = len(text)
    truncated = total > MAX_SCAN_CHARS
    scanned = text[:MAX_SCAN_CHARS] if truncated else text
    sentence_index = textspan.SentenceIndex(textspan.sentences(scanned))

    signals: List[IdentifierSignal] = []
    for start, end, signal_type, surface, value, method in _merge_overlaps(_candidates(scanned)):
        confidence = "high" if signal_type in (SIGNAL_EMAIL, SIGNAL_IPV4) else "medium"
        if signal_type == SIGNAL_PHONE:
            confidence = "medium" if _digits(surface) >= 10 else "low"
        sentence, s_start, s_end, cut = textspan.quote_sentence(
            scanned, lambda a, b: (a, b), sentence_index, start, start, end,
        )
        evidence = {"kind": signal_type, "pattern": method, "normalised": value}
        if cut:
            evidence["sentence_truncated"] = True
        signals.append(IdentifierSignal(
            signal_type=SIGNAL_IDENTIFIER,
            value=value,
            surface=surface,
            char_start=start,
            char_end=end,
            resolution="literal",
            evidence=evidence,
            detector_ver=DETECTOR_VERSION,
            method=method,
            confidence=confidence,
            confidence_basis=f"{signal_type}_pattern",  # kind, for the analyst
            sentence=sentence,
            sentence_start=s_start,
            sentence_end=s_end,
        ))

    return DetectionResult(
        signals=tuple(signals),
        detector_ver=DETECTOR_VERSION,
        anchor_date=anchor_date,
        chars_total=total,
        chars_scanned=len(scanned),
        truncated=truncated,
    )
