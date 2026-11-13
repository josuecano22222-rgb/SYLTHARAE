"""Multilingual temporal signal detection (Phase 1).

Finds date references, document-relative time expressions and grammatical
time orientation in English, Arabic, Hebrew, Persian and Croatian text, in
the Gregorian, Hijri and Jalali calendars, with ASCII, Arabic-Indic and
Persian digits.

Design rules (each one replaces a defect of the former ``core/monitoring/future_events``, now removed):

* **No fabricated dates.** A relative expression ("tomorrow", "next month")
  is resolved against the *document's* date, never the clock. Without a
  document date it is stored ``unresolved`` - "soon" is not "today + 7".
* **Ambiguity is preserved, not guessed away.** ``03/04/2026`` without a
  day-first cue yields both readings; ``1447/09/15`` without a calendar
  marker yields both the Hijri and the Jalali reading.
* **Imprecision is a range.** Month-only references resolve to the whole
  month; Hijri dates carry the tabular-calendar uncertainty (``calendars``).
* **Stored signals do not depend on when detection ran.** Whether a date is
  in the future is a property of the date *and the moment you ask*, so it is
  computed at read time (:meth:`TemporalSignal.clock_orientation`) with an
  explicit reference date. ``detect()`` takes no hidden ``date.today()``.
* **Every signal carries its evidence**: the pattern, the components, the
  normalised surface, the surrounding text and the detector version.

Out of scope, stated: spelled-out numbers ("three days"), times of day,
Hebrew-calendar dates, weekday names ("on Tuesday"), and anything requiring
syntactic parsing. Orientation markers are lexical cues, not a tense parser.
"""

from __future__ import annotations

import calendar as _calendar
import datetime
from bisect import bisect_left
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.detection import calendars as cal
from core.detection import textspan
from core.detection.signal_model import ContentSignal

DETECTOR_NAME = "temporal"
DETECTOR_VERSION = "temporal-1.1.0"
LANGUAGES = ("en", "ar", "he", "fa", "hr")

SIGNAL_DATE = "date_reference"
SIGNAL_RELATIVE = "relative_reference"
SIGNAL_ORIENTATION = "orientation"
SIGNAL_TYPES = (SIGNAL_DATE, SIGNAL_RELATIVE, SIGNAL_ORIENTATION)

RESOLUTIONS = ("absolute", "approximate", "document_relative", "ambiguous", "unresolved")
CONFIDENCE_LEVELS = ("high", "medium", "low")

#: Ordinal detection confidence: how sure the detector is that the span is
#: the temporal reference it reports. Assigned by rule from what matched -
#: there is no calibration corpus, so no numeric probability is claimed.
#: (basis id -> (level, description)); documented in TEMPORAL_SIGNALS.md.
CONFIDENCE_RULES = {
    "month_name": ("high", "a month name from the lexicon with day and/or year"),
    "year_first_numeric": ("high", "four-digit year first (ISO-like) or a calendar marker"),
    "relative_lexicon": ("high", "a relative expression from the per-language lexicon"),
    "single_valid_reading": ("medium", "numeric date; only one day/month order is a valid date"),
    "convention": ("medium", "numeric date resolved by a stated convention, not by the text"),
    "no_year": ("medium", "day and month name without a year; value is unresolved"),
    "ambiguous": ("low", "several valid readings are kept; none is chosen"),
    "lexical_cue": ("low", "a lexical orientation cue, not a parsed tense"),
}

#: Longest evidence sentence stored; longer ones are cut around the signal
#: and flagged ``evidence["sentence_truncated"]`` - never silently.
MAX_SENTENCE_CHARS = textspan.MAX_SENTENCE_CHARS
ORIENTATIONS = ("future", "present", "past")

#: Texts longer than this are scanned up to the limit and the run is recorded
#: as truncated (``DetectionResult.truncated``) - never silently.
MAX_SCAN_CHARS = 20_000_000

GREGORIAN_YEARS = (1900, 2100)
HIJRI_YEARS = (1300, 1500)
JALALI_YEARS = (1300, 1500)

# ---------------------------------------------------------------------------
# Normalisation: a same-meaning, match-friendly copy of the text with a map
# back to original offsets (so evidence always quotes the original bytes).
# ---------------------------------------------------------------------------

_DIGITS = {}
for _i in range(10):
    _DIGITS[chr(0x0660 + _i)] = str(_i)   # Arabic-Indic
    _DIGITS[chr(0x06F0 + _i)] = str(_i)   # Extended Arabic-Indic (Persian/Urdu)

_CHAR_MAP = {
    "\u06CC": "\u064A",  # Persian yeh -> Arabic yeh
    "\u0649": "\u064A",  # alef maqsura -> yeh
    "\u06A9": "\u0643",  # keheh -> kaf
    "\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627", "\u0671": "\u0627",
    "\u200C": " ",       # ZWNJ (Persian half-space) -> space
    "\u05BE": "-",       # Hebrew maqaf -> hyphen
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u00A0": " ", "\u202F": " ",
    "\u066B": ".",       # Arabic decimal separator
    "\u060C": ",",       # Arabic comma
}
_LATIN_FOLD = {"č": "c", "ć": "c", "š": "s", "ž": "z", "đ": "d"}


_dropped = textspan.dropped


def normalize(text: str) -> Tuple[str, List[int], Dict[str, int]]:
    """Return (normalised text, index map, digit-script counts)."""
    out: List[str] = []
    index: List[int] = []
    scripts = {"ascii": 0, "arabic-indic": 0, "persian": 0}
    for pos, ch in enumerate(text):
        if _dropped(ch):
            continue
        if ch in _DIGITS:
            scripts["persian" if ord(ch) >= 0x06F0 else "arabic-indic"] += 1
            ch = _DIGITS[ch]
        elif ch.isdigit() and ch.isascii():
            scripts["ascii"] += 1
        else:
            ch = _CHAR_MAP.get(ch, ch)
            low = ch.lower()
            if len(low) == 1:
                ch = low
            ch = _LATIN_FOLD.get(ch, ch)
        out.append(ch)
        index.append(pos)
    return "".join(out), index, scripts


def _norm(s: str) -> str:
    return normalize(s)[0]


# ---------------------------------------------------------------------------
# Lexicons. Every entry is normalised with the same function as the text, so
# they are written naturally (with hamza, Persian letters, diacritics).
# ---------------------------------------------------------------------------

# (calendar, month number, languages)
_MONTHS: Dict[str, Tuple[str, int, Tuple[str, ...]]] = {}


def _add_months(calendar_name: str, lang: Tuple[str, ...], names: Sequence[Sequence[str]]):
    for number, variants in enumerate(names, start=1):
        for name in variants:
            key = _norm(name)
            prev = _MONTHS.get(key)
            if prev and prev[:2] == (calendar_name, number):
                _MONTHS[key] = (calendar_name, number, tuple(sorted(set(prev[2]) | set(lang))))
            else:
                _MONTHS[key] = (calendar_name, number, lang)


_add_months("gregorian", ("en",), [
    ["january", "jan"], ["february", "feb"], ["march", "mar"], ["april", "apr"], ["may"],
    ["june", "jun"], ["july", "jul"], ["august", "aug"], ["september", "sep", "sept"],
    ["october", "oct"], ["november", "nov"], ["december", "dec"]])
# Croatian: nominative and genitive (dates use the genitive: "5. listopada").
_add_months("gregorian", ("hr",), [
    ["siječanj", "siječnja"], ["veljača", "veljače"], ["ožujak", "ožujka"],
    ["travanj", "travnja"], ["svibanj", "svibnja"], ["lipanj", "lipnja"],
    ["srpanj", "srpnja"], ["kolovoz", "kolovoza"], ["rujan", "rujna"],
    ["listopad", "listopada"], ["studeni", "studenoga", "studenog"],
    ["prosinac", "prosinca"]])
# Arabic Gregorian: Egyptian/Gulf transliterations and Levantine/Iraqi names.
_add_months("gregorian", ("ar",), [
    ["يناير", "كانون الثاني"], ["فبراير", "شباط"], ["مارس", "آذار"],
    ["أبريل", "إبريل", "نيسان"], ["مايو", "أيار"], ["يونيو", "يونيه", "حزيران"],
    ["يوليو", "يوليه", "تموز"], ["أغسطس", "آب"], ["سبتمبر", "أيلول"],
    ["أكتوبر", "تشرين الأول"], ["نوفمبر", "تشرين الثاني"], ["ديسمبر", "كانون الأول"]])
_add_months("gregorian", ("he",), [
    ["ינואר"], ["פברואר"], ["מרץ", "מרס"], ["אפריל"], ["מאי"], ["יוני"], ["יולי"],
    ["אוגוסט"], ["ספטמבר"], ["אוקטובר"], ["נובמבר"], ["דצמבר"]])
_add_months("gregorian", ("fa",), [
    ["ژانویه"], ["فوریه"], ["مارس"], ["آوریل"], ["مه"], ["ژوئن"], ["ژوئیه", "جولای"],
    ["اوت", "آگوست"], ["سپتامبر"], ["اکتبر"], ["نوامبر"], ["دسامبر"]])
_add_months("hijri", ("ar", "fa"), [
    ["محرم", "محرّم"], ["صفر"], ["ربيع الأول", "ربيع الاول", "ربیع الاول"],
    ["ربيع الآخر", "ربيع الثاني", "ربیع الثانی"], ["جمادى الأولى", "جمادى الاولى", "جمادی الاول"],
    ["جمادى الآخرة", "جمادى الثانية", "جمادی الثانی"], ["رجب"], ["شعبان"], ["رمضان"],
    ["شوال"], ["ذو القعدة", "ذي القعدة", "ذیقعده"], ["ذو الحجة", "ذي الحجة", "ذیحجه"]])
_add_months("jalali", ("fa",), [
    ["فروردین"], ["اردیبهشت"], ["خرداد"], ["تیر"], ["مرداد", "امرداد"], ["شهریور"],
    ["مهر"], ["آبان"], ["آذر"], ["دی"], ["بهمن"], ["اسفند"]])

# Calendar markers that may follow a year.
_HIJRI_MARKERS = [_norm(m) for m in ("هـ", "ه", "ه.ق", "هجري", "هجرية", "للهجرة", "قمری", "ah", "a.h.")]
_JALALI_MARKERS = [_norm(m) for m in ("ه.ش", "هـ.ش", "ش", "شمسی", "خورشیدی", "sh", "s.h.")]
_GREGORIAN_MARKERS = [_norm(m) for m in ("م", "ميلادي", "ميلادية", "میلادی", "ce", "ad")]

# Relative expressions: normalised phrase -> (amount, unit, languages)
_RELATIVE_FIXED: Dict[str, Tuple[int, str, str]] = {}


def _rel(lang: str, entries: Dict[str, Tuple[int, str]]):
    for phrase, (amount, unit) in entries.items():
        _RELATIVE_FIXED[_norm(phrase)] = (amount, unit, lang)


_rel("en", {"today": (0, "d"), "tomorrow": (1, "d"), "yesterday": (-1, "d"),
            "the day after tomorrow": (2, "d"), "the day before yesterday": (-2, "d"),
            "next week": (1, "w"), "last week": (-1, "w"), "this week": (0, "w"),
            "next month": (1, "m"), "last month": (-1, "m"), "this month": (0, "m"),
            "next year": (1, "y"), "last year": (-1, "y"), "this year": (0, "y")})
_rel("ar", {"اليوم": (0, "d"), "غدا": (1, "d"), "بعد غد": (2, "d"), "أمس": (-1, "d"),
            "البارحة": (-1, "d"), "أول أمس": (-2, "d"),
            "الأسبوع المقبل": (1, "w"), "الأسبوع القادم": (1, "w"), "الأسبوع الماضي": (-1, "w"),
            "هذا الأسبوع": (0, "w"), "الشهر المقبل": (1, "m"), "الشهر القادم": (1, "m"),
            "الشهر الماضي": (-1, "m"), "هذا الشهر": (0, "m"), "العام المقبل": (1, "y"),
            "العام القادم": (1, "y"), "السنة المقبلة": (1, "y"), "السنة القادمة": (1, "y"),
            "العام الماضي": (-1, "y"), "السنة الماضية": (-1, "y"), "هذا العام": (0, "y")})
_rel("he", {"היום": (0, "d"), "מחר": (1, "d"), "מחרתיים": (2, "d"), "אתמול": (-1, "d"),
            "שלשום": (-2, "d"), "בשבוע הבא": (1, "w"), "השבוע הבא": (1, "w"),
            "בשבוע שעבר": (-1, "w"), "השבוע": (0, "w"), "בחודש הבא": (1, "m"),
            "החודש הבא": (1, "m"), "בחודש שעבר": (-1, "m"), "החודש": (0, "m"),
            "בשנה הבאה": (1, "y"), "השנה הבאה": (1, "y"), "בשנה שעברה": (-1, "y"),
            "השנה": (0, "y")})
_rel("fa", {"امروز": (0, "d"), "فردا": (1, "d"), "پس فردا": (2, "d"), "دیروز": (-1, "d"),
            "پریروز": (-2, "d"), "هفته آینده": (1, "w"), "هفته بعد": (1, "w"),
            "هفته گذشته": (-1, "w"), "هفته پیش": (-1, "w"), "این هفته": (0, "w"),
            "ماه آینده": (1, "m"), "ماه بعد": (1, "m"), "ماه گذشته": (-1, "m"),
            "ماه پیش": (-1, "m"), "این ماه": (0, "m"), "سال آینده": (1, "y"),
            "سال بعد": (1, "y"), "سال گذشته": (-1, "y"), "سال پیش": (-1, "y"),
            "امسال": (0, "y")})
_rel("hr", {"danas": (0, "d"), "sutra": (1, "d"), "prekosutra": (2, "d"), "jučer": (-1, "d"),
            "prekjučer": (-2, "d"), "sljedeći tjedan": (1, "w"), "sljedećeg tjedna": (1, "w"),
            "idući tjedan": (1, "w"), "idućeg tjedna": (1, "w"), "prošli tjedan": (-1, "w"),
            "prošlog tjedna": (-1, "w"), "ovaj tjedan": (0, "w"), "ovog tjedna": (0, "w"),
            "sljedeći mjesec": (1, "m"), "sljedećeg mjeseca": (1, "m"), "idući mjesec": (1, "m"),
            "idućeg mjeseca": (1, "m"), "prošli mjesec": (-1, "m"), "prošlog mjeseca": (-1, "m"),
            "ovaj mjesec": (0, "m"), "ovog mjeseca": (0, "m"), "sljedeće godine": (1, "y"),
            "iduće godine": (1, "y"), "sljedeća godina": (1, "y"), "prošle godine": (-1, "y"),
            "prošla godina": (-1, "y"), "ove godine": (0, "y"), "ova godina": (0, "y")})

# Numeric relative forms: (regex on normalised text, direction, languages)
# group 'n' = amount, group 'u' = unit word.
_UNIT_WORDS: Dict[str, str] = {}
for _unit, _words in {
    "d": ["day", "days", "يوم", "يوما", "أيام", "ايام", "יום", "ימים", "روز", "dan", "dana"],
    "w": ["week", "weeks", "أسبوع", "أسبوعا", "أسابيع", "שבוע", "שבועות", "هفته",
          "tjedan", "tjedna", "tjedana"],
    "m": ["month", "months", "شهر", "شهرا", "أشهر", "شهور", "חודש", "חודשים", "ماه",
          "mjesec", "mjeseca", "mjeseci"],
    "y": ["year", "years", "سنة", "سنوات", "عام", "أعوام", "שנה", "שנים", "سال",
          "godina", "godine", "godinu"],
}.items():
    for _w in _words:
        _UNIT_WORDS[_norm(_w)] = _unit

_UNIT_ALT = "|".join(sorted((re.escape(w) for w in _UNIT_WORDS), key=len, reverse=True))
_N = r"(?P<n>\d{1,3})"
_U = rf"(?P<u>{_UNIT_ALT})"
_RELATIVE_NUMERIC = [
    ("en.in_n", re.compile(rf"\b(?:in|within)\s+{_N}\s+{_U}\b"), +1, "en"),
    ("en.n_ago", re.compile(rf"\b{_N}\s+{_U}\s+ago\b"), -1, "en"),
    ("ar.after_n", re.compile(rf"(?<!\w)(?:بعد|خلال)\s+{_N}\s+{_U}(?!\w)"), +1, "ar"),
    ("ar.before_n", re.compile(rf"(?<!\w)(?:قبل|منذ)\s+{_N}\s+{_U}(?!\w)"), -1, "ar"),
    ("he.in_n", re.compile(rf"(?<!\w)(?:בעוד|תוך)\s+{_N}\s+{_U}(?!\w)"), +1, "he"),
    ("he.n_ago", re.compile(rf"(?<!\w)לפני\s+{_N}\s+{_U}(?!\w)"), -1, "he"),
    ("fa.n_later", re.compile(rf"(?<!\w){_N}\s+{_U}\s+(?:دیگر|بعد)(?!\w)".replace("ی", "ي")), +1, "fa"),
    ("fa.n_ago", re.compile(rf"(?<!\w){_N}\s+{_U}\s+(?:پیش|قبل)(?!\w)".replace("ی", "ي")), -1, "fa"),
    ("hr.za_n", re.compile(rf"\bza\s+{_N}\s+{_U}\b"), +1, "hr"),
    ("hr.prije_n", re.compile(rf"\bprije\s+{_N}\s+{_U}\b"), -1, "hr"),
]

# Orientation cues: (orientation, language, phrases). Lexical, not parsed.
_ORIENTATION_CUES: List[Tuple[str, str, List[str]]] = [
    ("future", "en", ["will", "shall", "going to", "scheduled for", "scheduled to", "upcoming",
                      "forthcoming", "planned for", "is expected to", "are expected to"]),
    ("past", "en", ["was held", "were held", "took place", "happened", "ago", "previously"]),
    ("present", "en", ["currently", "right now", "is underway", "ongoing"]),
    ("future", "ar", ["سوف", "وسوف", "فسوف", "المقبل", "المقبلة", "القادم", "القادمة", "من المقرر", "مرتقب",
                      "المرتقب", "المرتقبة"]),
    ("past", "ar", ["الماضي", "الماضية", "عقد", "عقدت", "جرى", "جرت", "وقع", "وقعت"]),
    ("present", "ar", ["حاليا", "الآن", "جار", "يجري"]),
    ("future", "he", ["יתקיים", "תתקיים", "יתקיימו", "מתוכנן", "מתוכננת", "צפוי", "צפויה",
                      "הקרוב", "הקרובה", "בעתיד"]),
    ("past", "he", ["התקיים", "התקיימה", "התקיימו", "שעבר", "שעברה", "בעבר"]),
    ("present", "he", ["כעת", "עכשיו", "כרגע", "מתקיים", "מתקיימת"]),
    ("future", "fa", ["خواهد", "خواهند", "خواهیم", "خواهم", "قرار است", "آینده"]),
    ("past", "fa", ["گذشته", "برگزار شد", "انجام شد"]),
    ("present", "fa", ["اکنون", "هم اکنون", "در حال حاضر", "در حال"]),
    ("future", "hr", ["ću", "ćeš", "će", "ćemo", "ćete", "planirano", "predviđeno", "održat će"]),
    ("past", "hr", ["održan", "održana", "održano", "prošli", "prošle", "prošlog"]),
    ("present", "hr", ["sada", "trenutno", "upravo"]),
]

_CUE_INDEX: Dict[str, Tuple[str, str]] = {}
for _orientation, _lang, _phrases in _ORIENTATION_CUES:
    for _p in _phrases:
        _CUE_INDEX[_norm(_p)] = (_orientation, _lang)
#: Cues whose normalised (diacritic-folded) form is an ordinary Latin word.
_NEEDS_DIACRITIC = {_norm(p) for p in ("ću", "ćeš", "će", "ćemo", "ćete")}
_CUE_RE = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(p) for p in sorted(_CUE_INDEX, key=len, reverse=True)) + r")(?!\w)")

# ---------------------------------------------------------------------------
# Date patterns (on normalised text)
# ---------------------------------------------------------------------------

_MONTH_ALT = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))
# Hebrew one/two-letter proclitics (ב, ל, מ, ו, ה, כ, ש) and an Arabic
# conjunction/preposition may be attached to a month name.
_PFX = r"(?:[ובלמהכש]{1,2}-?|[وفبلك](?=\S))?"
_YEAR_MARK = r"(?:\s*(?P<mark>" + "|".join(
    re.escape(m) for m in sorted(set(_HIJRI_MARKERS + _JALALI_MARKERS + _GREGORIAN_MARKERS),
                                 key=len, reverse=True)) + r")(?![\w]))?"

_P_ISO = re.compile(r"(?<![\d./-])(?P<y>\d{4})(?P<s>[-/.])(?P<m>\d{1,2})(?P=s)(?P<d>\d{1,2})(?![\d])"
                    + _YEAR_MARK)
_P_DMY = re.compile(r"(?<![\d./-])(?P<a>\d{1,2})(?P<s>[-/.])\s?(?P<b>\d{1,2})(?P=s)\s?(?P<y>\d{4})"
                    r"(?![\d])\.?" + _YEAR_MARK)
_P_D_MONTH_Y = re.compile(
    r"(?<![\w.])(?:ה-?|ב-?)?(?P<d>\d{1,2})(?:st|nd|rd|th|\.)?(?:\s+of)?\s*(?:[-/]\s*)?\s+"
    + _PFX + r"(?P<month>" + _MONTH_ALT + r")(?!\w)"
    r"(?:\s*,?\s*(?P<y>\d{4})(?![\d])\.?" + _YEAR_MARK + r")?")
_P_MONTH_D_Y = re.compile(
    r"(?<!\w)(?P<month>" + _MONTH_ALT + r")\.?\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})(?![\d])")
_P_MONTH_Y = re.compile(
    r"(?<![\w.])" + _PFX + r"(?P<month>" + _MONTH_ALT + r")(?!\w)\s*,?\s*(?P<y>\d{4})(?![\d])"
    + _YEAR_MARK)

_RELATIVE_FIXED_RE = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(p) for p in sorted(_RELATIVE_FIXED, key=len, reverse=True)) + r")(?!\w)")

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemporalSignal(ContentSignal):
    """A temporal signal (fields: ``core.detection.signal_model.ContentSignal``)."""
    detector_ver: str = DETECTOR_VERSION

    def clock_orientation(self, reference_date: datetime.date) -> Optional[str]:
        """future/present/past relative to ``reference_date`` - or ``None``.

        ``None`` means *unknown* (unresolved or ambiguous with readings on
        both sides of the reference date), never "not in the future".
        """
        if reference_date is None:
            raise ValueError("reference_date is required (no implicit clock)")
        if self.date_from is not None:
            return _orientation_of(self.date_from, self.date_to, reference_date)
        readings = self.evidence.get("alternatives") or []
        verdicts = {_orientation_of(datetime.date.fromisoformat(a["date_from"]),
                                    datetime.date.fromisoformat(a["date_to"]), reference_date)
                    for a in readings}
        return verdicts.pop() if len(verdicts) == 1 else None


@dataclass(frozen=True)
class DetectionResult:
    signals: Tuple[TemporalSignal, ...]
    detector_ver: str
    anchor_date: Optional[datetime.date]
    chars_total: int
    chars_scanned: int
    truncated: bool
    detector: str = DETECTOR_NAME

    def by_type(self, signal_type: str) -> List[TemporalSignal]:
        return [s for s in self.signals if s.signal_type == signal_type]


def _orientation_of(start: datetime.date, end: datetime.date, ref: datetime.date) -> str:
    if start > ref:
        return "future"
    if end < ref:
        return "past"
    return "present"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    start: int      # normalised offsets
    end: int
    priority: int   # higher wins on overlap at equal length
    build: object   # callable(original_start, original_end, surface) -> TemporalSignal | None


def _year_calendar(year: int, mark: Optional[str]) -> List[str]:
    """Calendars a numeric year can belong to, honouring an explicit marker."""
    if mark in _HIJRI_MARKERS:
        return ["hijri"] if HIJRI_YEARS[0] <= year <= HIJRI_YEARS[1] else []
    if mark in _JALALI_MARKERS:
        return ["jalali"] if JALALI_YEARS[0] <= year <= JALALI_YEARS[1] else []
    if GREGORIAN_YEARS[0] <= year <= GREGORIAN_YEARS[1]:
        return ["gregorian"]
    if HIJRI_YEARS[0] <= year <= HIJRI_YEARS[1]:
        return ["hijri", "jalali"]  # unmarked 13xx/14xx: genuinely ambiguous
    return []


def _day_range(calendar_name: str, y: int, m: int, d: int):
    """(date_from, date_to, resolution, method) or raise CalendarError/ValueError."""
    if calendar_name == "gregorian":
        day = datetime.date(y, m, d)
        return day, day, "absolute", None
    if calendar_name == "jalali":
        day = cal.jalali_to_gregorian(y, m, d)
        return day, day, "absolute", cal.JALALI_METHOD
    lo, hi = cal.hijri_to_gregorian_range(y, m, d)
    return lo, hi, "approximate", cal.HIJRI_METHOD


def _month_range(calendar_name: str, y: int, m: int):
    if calendar_name == "gregorian":
        last = _calendar.monthrange(y, m)[1]
        return datetime.date(y, m, 1), datetime.date(y, m, last), "absolute", None
    if calendar_name == "jalali":
        lo, hi = cal.jalali_month_range(y, m)
        return lo, hi, "absolute", cal.JALALI_METHOD
    lo, hi = cal.hijri_month_range(y, m)
    return lo, hi, "approximate", cal.HIJRI_METHOD


def _dominant_script(norm_text: str) -> str:
    counts = {"latin": 0, "arabic": 0, "hebrew": 0}
    for ch in norm_text[:20000]:
        o = ord(ch)
        if 0x0600 <= o <= 0x06FF:
            counts["arabic"] += 1
        elif 0x0590 <= o <= 0x05FF:
            counts["hebrew"] += 1
        elif ch.isalpha() and o < 0x0250:
            counts["latin"] += 1
    return max(counts, key=counts.get) if any(counts.values()) else "latin"


def _week_start(lang: Optional[str]) -> Tuple[int, str]:
    """(weekday index Monday=0, name) - the conventional first day of week."""
    if lang == "fa":
        return 5, "saturday"
    if lang in ("ar", "he"):
        return 6, "sunday"
    return 0, "monday"  # ISO 8601; used for en and hr


def _shift_months(anchor: datetime.date, months: int) -> Tuple[datetime.date, datetime.date]:
    y, m = divmod(anchor.month - 1 + months, 12)
    year, month = anchor.year + y, m + 1
    return datetime.date(year, month, 1), datetime.date(year, month,
                                                        _calendar.monthrange(year, month)[1])


def _resolve_relative(amount: int, unit: str, anchor: datetime.date, lang: Optional[str],
                      exact_offset: bool):
    """Resolve a relative expression against the document date."""
    notes: Dict = {}
    if unit == "d":
        day = anchor + datetime.timedelta(days=amount)
        return day, day, notes
    if unit == "w":
        if exact_offset:  # "in 3 weeks" = that many days later
            day = anchor + datetime.timedelta(weeks=amount)
            return day, day, notes
        first, name = _week_start(lang)
        notes["week_start"] = name
        start = anchor - datetime.timedelta(days=(anchor.weekday() - first) % 7)
        start += datetime.timedelta(weeks=amount)
        return start, start + datetime.timedelta(days=6), notes
    if unit == "m":
        if exact_offset:
            lo, _ = _shift_months(anchor, amount)
            day = min(anchor.day, _calendar.monthrange(lo.year, lo.month)[1])
            target = lo.replace(day=day)
            return target, target, notes
        lo, hi = _shift_months(anchor, amount)
        return lo, hi, notes
    year = anchor.year + amount
    if exact_offset:
        try:
            target = anchor.replace(year=year)
        except ValueError:  # 29 February
            target = datetime.date(year, 2, 28)
        return target, target, notes
    return datetime.date(year, 1, 1), datetime.date(year, 12, 31), notes


class _Context:
    def __init__(self, original: str, norm: str, index: List[int], anchor: Optional[datetime.date],
                 scripts: Dict[str, int]):
        self.original = original
        self.norm = norm
        self.index = index
        self.anchor = anchor
        self.scripts = scripts
        self.script = _dominant_script(norm)

    def span(self, ns: int, ne: int) -> Tuple[int, int]:
        end = self.index[ne - 1] + 1
        while end < len(self.original) and _dropped(self.original[end]):
            end += 1
        return self.index[ns], end

    def context(self, start: int, end: int, width: int = 80) -> str:
        return self.original[max(0, start - width):min(len(self.original), end + width)].strip()

    def digits(self, start: int, end: int) -> str:
        found = set()
        for ch in self.original[start:end]:
            if ch in _DIGITS:
                found.add("persian" if ord(ch) >= 0x06F0 else "arabic-indic")
            elif ch.isascii() and ch.isdigit():
                found.add("ascii")
        return "+".join(sorted(found)) or "none"


def _signal(ctx: _Context, ns: int, ne: int, **kwargs) -> TemporalSignal:
    start, end = ctx.span(ns, ne)
    evidence = dict(kwargs.pop("evidence", {}))
    evidence.setdefault("normalized", ctx.norm[ns:ne])
    evidence["context"] = ctx.context(start, end)
    evidence["digits"] = ctx.digits(start, end)
    return TemporalSignal(surface=ctx.original[start:end], char_start=start, char_end=end,
                          evidence=evidence, **kwargs)


def _date_signal(ctx, ns, ne, pattern_id, calendar_name, y, m, d, language, extra=None):
    try:
        lo, hi, resolution, method = (_day_range(calendar_name, y, m, d) if d
                                      else _month_range(calendar_name, y, m))
    except (ValueError, cal.CalendarError) as exc:
        return None, str(exc)
    value = (f"{calendar_name}:{y:04d}-{m:02d}-{d:02d}" if d else f"{calendar_name}:{y:04d}-{m:02d}")
    evidence = {"pattern": pattern_id, "components": {"year": y, "month": m, "day": d},
                "precision": "day" if d else "month"}
    if method:
        evidence["calendar_method"] = method
    if extra:
        evidence.update(extra)
    return _signal(ctx, ns, ne, signal_type=SIGNAL_DATE, value=value, resolution=resolution,
                   language=language, calendar=calendar_name, date_from=lo, date_to=hi,
                   evidence=evidence), None


def _ambiguous_signal(ctx, ns, ne, pattern_id, readings, language=None, extra=None):
    """Several valid readings: keep them all, resolve none."""
    alternatives = []
    for calendar_name, y, m, d, label in readings:
        try:
            lo, hi, resolution, method = _day_range(calendar_name, y, m, d)
        except (ValueError, cal.CalendarError):
            continue
        alternatives.append({"reading": label, "calendar": calendar_name,
                             "components": {"year": y, "month": m, "day": d},
                             "value": f"{calendar_name}:{y:04d}-{m:02d}-{d:02d}",
                             "date_from": lo.isoformat(), "date_to": hi.isoformat(),
                             "resolution": resolution, "calendar_method": method})
    if not alternatives:
        return None
    if len(alternatives) == 1:
        a = alternatives[0]
        c = a["components"]
        sig, _ = _date_signal(ctx, ns, ne, pattern_id, a["calendar"], c["year"], c["month"],
                              c["day"], language, dict(extra or {}, reading=a["reading"]))
        return sig
    alternatives.sort(key=lambda a: a["value"])
    calendars = sorted({a["calendar"] for a in alternatives})
    evidence = {"pattern": pattern_id, "alternatives": alternatives}
    if extra:
        evidence.update(extra)
    return _signal(ctx, ns, ne, signal_type=SIGNAL_DATE,
                   value="ambiguous:" + "|".join(a["value"] for a in alternatives),
                   resolution="ambiguous", language=language,
                   calendar=calendars[0] if len(calendars) == 1 else None, evidence=evidence)


#: English month tokens that are also ordinary words ("may", "march") or
#: abbreviations: accepted only when capitalised in the original text.
_EN_CASE_SENSITIVE = {"may", "march", "mar", "jan", "feb", "apr", "jun", "jul", "aug",
                      "sep", "sept", "oct", "nov", "dec"}


def _month_ok(ctx: "_Context", m: re.Match) -> bool:
    token = m["month"]
    if token not in _EN_CASE_SENSITIVE:
        return True
    start, end = ctx.span(m.start("month"), m.end("month"))
    return ctx.original[start:end][:1].isupper()


def _month_entry(norm_month: str):
    entry = _MONTHS.get(norm_month)
    if entry is None:
        return None, None, None
    calendar_name, number, langs = entry
    return calendar_name, number, (langs[0] if len(langs) == 1 else None)


def _candidates(ctx: _Context) -> List[_Candidate]:
    text = ctx.norm
    found: List[_Candidate] = []

    # --- ISO-shaped Y-M-D (Gregorian, or Hijri/Jalali by year + marker) ----
    for m in _P_ISO.finditer(text):
        y, mo, d = int(m["y"]), int(m["m"]), int(m["d"])
        mark = m["mark"]
        calendars = _year_calendar(y, mark)
        end = m.end() if mark else m.end("d")
        if not calendars:
            continue

        def build(m=m, y=y, mo=mo, d=d, calendars=calendars, end=end, mark=mark):
            extra = {"calendar_marker": mark} if mark else {}
            if len(calendars) == 1:
                sig, _ = _date_signal(ctx, m.start(), end, "ymd", calendars[0], y, mo, d, None, extra)
                return sig
            extra["note"] = "13xx/14xx year without a calendar marker: Hijri or Jalali"
            return _ambiguous_signal(ctx, m.start(), end, "ymd",
                                     [(c, y, mo, d, c) for c in calendars], None, extra)
        found.append(_Candidate(m.start(), end, 50, build))

    # --- numeric D-M-Y / M-D-Y --------------------------------------------
    for m in _P_DMY.finditer(text):
        a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
        sep, mark = m["s"], m["mark"]
        calendars = _year_calendar(y, mark)
        end = m.end() if mark else m.end("y")
        if not calendars:
            continue

        def build(m=m, a=a, b=b, y=y, sep=sep, calendars=calendars, end=end, mark=mark):
            extra = {"separator": sep}
            if mark:
                extra["calendar_marker"] = mark
            readings = []
            for c in calendars:
                if c != "gregorian":
                    readings.append((c, y, b, a, f"{c}:day-first"))  # Hijri/Jalali are day-first
                    continue
                if sep == ".":
                    extra["convention"] = "dot separator: day-first (European/Croatian)"
                    readings.append((c, y, b, a, "day-first"))
                elif a > 12 or b > 12 or a == b:
                    readings.append((c, y, b, a, "day-first"))
                    readings.append((c, y, a, b, "month-first"))
                elif ctx.script in ("arabic", "hebrew"):
                    extra["convention"] = f"day-first ({ctx.script}-script document)"
                    readings.append((c, y, b, a, "day-first"))
                else:
                    readings.append((c, y, b, a, "day-first"))
                    readings.append((c, y, a, b, "month-first"))
            # identical readings collapse (a == b); invalid ones are dropped
            seen, unique = set(), []
            for r in readings:
                if r[:4] not in seen:
                    seen.add(r[:4])
                    unique.append(r)
            return _ambiguous_signal(ctx, m.start(), end, "numeric_dmy", unique, None, extra)
        found.append(_Candidate(m.start(), end, 40, build))

    # --- day + month name (+ year) ------------------------------------------
    for m in _P_D_MONTH_Y.finditer(text):
        calendar_name, month, lang = _month_entry(m["month"])
        if not _month_ok(ctx, m) or calendar_name is None:
            continue
        d = int(m["d"])
        if m["y"]:
            y, mark = int(m["y"]), m["mark"]
            end = m.end() if mark else m.end("y")
            # A named month fixes the calendar; a trailing marker only
            # confirms it (a contradicting marker is kept in the evidence).

            def build(m=m, d=d, y=y, month=month, calendar_name=calendar_name, lang=lang, end=end):
                extra = {"month_name": m["month"]}
                if m["mark"]:
                    extra["calendar_marker"] = m["mark"]
                sig, _ = _date_signal(ctx, m.start(), end, "day_month_year", calendar_name, y,
                                      month, d, lang, extra)
                return sig
            found.append(_Candidate(m.start(), end, 60, build))
        else:
            def build(m=m, d=d, month=month, calendar_name=calendar_name, lang=lang):
                if not 1 <= d <= 31:
                    return None
                return _signal(ctx, m.start(), m.end("month"), signal_type=SIGNAL_DATE,
                               value=f"{calendar_name}:--{month:02d}-{d:02d}",
                               resolution="unresolved", language=lang, calendar=calendar_name,
                               evidence={"pattern": "day_month", "month_name": m["month"],
                                         "components": {"month": month, "day": d},
                                         "reason": "no year stated; the year is not guessed"})
            found.append(_Candidate(m.start(), m.end("month"), 30, build))

    # --- English month name + day + year ------------------------------------
    for m in _P_MONTH_D_Y.finditer(text):
        calendar_name, month, lang = _month_entry(m["month"])
        if not _month_ok(ctx, m) or calendar_name != "gregorian":
            continue
        d, y = int(m["d"]), int(m["y"])

        def build(m=m, d=d, y=y, month=month, lang=lang):
            sig, _ = _date_signal(ctx, m.start(), m.end(), "month_day_year", "gregorian", y,
                                  month, d, lang, {"month_name": m["month"]})
            return sig
        found.append(_Candidate(m.start(), m.end(), 60, build))

    # --- month name + year (month precision) --------------------------------
    for m in _P_MONTH_Y.finditer(text):
        calendar_name, month, lang = _month_entry(m["month"])
        if not _month_ok(ctx, m) or calendar_name is None:
            continue
        y = int(m["y"])
        valid = {"gregorian": GREGORIAN_YEARS, "hijri": HIJRI_YEARS, "jalali": JALALI_YEARS}[calendar_name]
        if not valid[0] <= y <= valid[1]:
            continue
        end = m.end() if m["mark"] else m.end("y")

        def build(m=m, y=y, month=month, calendar_name=calendar_name, lang=lang, end=end):
            sig, _ = _date_signal(ctx, m.start(), end, "month_year", calendar_name, y, month,
                                  None, lang, {"month_name": m["month"]})
            return sig
        found.append(_Candidate(m.start(), end, 45, build))

    # --- relative expressions ----------------------------------------------
    for m in _RELATIVE_FIXED_RE.finditer(text):
        amount, unit, lang = _RELATIVE_FIXED[m.group(0)]
        found.append(_Candidate(m.start(), m.end(), 20,
                                _relative_builder(ctx, m.start(), m.end(), amount, unit, lang,
                                                  f"{lang}.fixed", exact=False)))
    for pattern_id, rx, direction, lang in _RELATIVE_NUMERIC:
        for m in rx.finditer(text):
            unit = _UNIT_WORDS.get(m["u"])
            if unit is None:
                continue
            amount = direction * int(m["n"])
            found.append(_Candidate(m.start(), m.end(), 25,
                                    _relative_builder(ctx, m.start(), m.end(), amount, unit, lang,
                                                      pattern_id, exact=True)))
    return found


def _relative_builder(ctx, ns, ne, amount, unit, lang, pattern_id, exact):
    # The expression's own direction relative to the document ("tomorrow"
    # points forward from the day it was written) - lexical, clock-free.
    intrinsic = "future" if amount > 0 else "past" if amount < 0 else "present"

    def build():
        # For days an exact offset and a calendar period coincide ("tomorrow"
        # = "in 1 day"); for weeks/months/years they differ ("in 1 month" is
        # a day, "next month" is a whole month) and the value says which.
        n = f"{amount:+d}" if amount else "0"
        if unit == "d":
            value = f"rel:{n}d"
        else:
            value = f"rel:{'offset' if exact else 'period'}:{n}{unit}"
        evidence = {"pattern": pattern_id, "amount": amount, "unit": unit,
                    "offset": "exact" if exact else "calendar_period"}
        if ctx.anchor is None:
            evidence["reason"] = "no document date: relative expression not resolved"
            return _signal(ctx, ns, ne, signal_type=SIGNAL_RELATIVE, value=value,
                           resolution="unresolved", language=lang, calendar="gregorian",
                           text_orientation=intrinsic, evidence=evidence)
        lo, hi, notes = _resolve_relative(amount, unit, ctx.anchor, lang, exact)
        evidence.update(notes)
        return _signal(ctx, ns, ne, signal_type=SIGNAL_RELATIVE, value=value,
                       resolution="document_relative", language=lang, calendar="gregorian",
                       date_from=lo, date_to=hi, anchor_date=ctx.anchor,
                       text_orientation=intrinsic, evidence=evidence)
    return build


_SENTENCE_END = textspan.SENTENCE_END


def _confidence_basis(sig: "TemporalSignal") -> str:
    pattern = sig.evidence.get("pattern") or ""
    if sig.signal_type == SIGNAL_ORIENTATION:
        return "lexical_cue"
    if sig.signal_type == SIGNAL_RELATIVE:
        return "relative_lexicon"
    if sig.resolution == "ambiguous":
        return "ambiguous"
    if pattern == "day_month":
        return "no_year"
    if pattern in ("day_month_year", "month_day_year", "month_year"):
        return "month_name"
    if pattern == "ymd" or sig.evidence.get("calendar_marker"):
        return "year_first_numeric"
    if sig.evidence.get("convention"):
        return "convention"
    return "single_valid_reading"


def _with_provenance(ctx, sig: "TemporalSignal", sentences, reverse) -> "TemporalSignal":
    """Add method, ordinal confidence and the quoted evidence sentence."""
    basis = _confidence_basis(sig)
    level = CONFIDENCE_RULES[basis][0]
    evidence = sig.evidence
    sentence, s_start, s_end, truncated = textspan.quote_sentence(
        ctx.original, ctx.span, sentences, reverse.get(sig.char_start),
        sig.char_start, sig.char_end, MAX_SENTENCE_CHARS)
    if truncated:
        evidence = dict(evidence, sentence_truncated=True)
    return TemporalSignal(**{**sig.__dict__, "evidence": evidence,
                             "method": evidence.get("pattern"), "confidence": level,
                             "confidence_basis": basis,
                             "sentence": sentence,
                             "sentence_start": s_start, "sentence_end": s_end})


_sentences = textspan.sentences


def _orientation_cues(ctx: _Context, sentences):
    """Per sentence: {orientation: [cue matches]}."""
    out = []
    for s_start, s_end in sentences:
        cues: Dict[str, List[re.Match]] = {}
        for m in _CUE_RE.finditer(ctx.norm, s_start, s_end):
            orientation, lang = _CUE_INDEX[m.group(0)]
            if m.group(0) in _NEEDS_DIACRITIC:
                start, end = ctx.span(m.start(), m.end())
                if not any(ch in "ćĆčČšŠžŽđĐ" for ch in ctx.original[start:end]):
                    continue  # "ce" (e.g. English CE) is not the Croatian clitic "će"
            cues.setdefault(orientation, []).append(m)
        out.append(cues)
    return out


class _Spans:
    """Accepted spans, kept sorted so an overlap test costs O(log n).

    Accepted spans never overlap one another, so a candidate can only clash
    with the kept span that starts before it (if that one ends after the
    candidate starts) or the one that starts after it (if it starts before the
    candidate ends). Scanning every kept span for every candidate made temporal
    detection quadratic (M-3 profile: 106.7 million generator steps on a 2 MB
    document; audit_report/evidence_scale/ingest_profile_before.json).
    """

    __slots__ = ("_starts", "_ends")

    def __init__(self) -> None:
        self._starts: List[int] = []
        self._ends: List[int] = []

    def overlaps(self, start: int, end: int) -> bool:
        at = bisect_left(self._starts, start)
        if at and self._ends[at - 1] > start:
            return True
        return at < len(self._starts) and self._starts[at] < end

    def add(self, start: int, end: int) -> None:
        at = bisect_left(self._starts, start)
        self._starts.insert(at, start)
        self._ends.insert(at, end)


def detect(text: Optional[str], *, anchor_date: Optional[datetime.date] = None,
           languages: Optional[Iterable[str]] = None) -> DetectionResult:
    """Detect temporal signals in ``text``.

    ``anchor_date`` is the document's own date, used only to resolve
    document-relative expressions; it is never defaulted to the clock.
    ``languages`` restricts the language-specific lexicons (numeric dates
    are language-neutral and always detected).
    """
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if anchor_date is not None and not isinstance(anchor_date, datetime.date):
        raise TypeError("anchor_date must be a date")
    if isinstance(anchor_date, datetime.datetime):
        anchor_date = anchor_date.date()
    wanted = set(languages) if languages else set(LANGUAGES)
    unknown = wanted - set(LANGUAGES)
    if unknown:
        raise ValueError(f"unsupported languages: {sorted(unknown)}")

    total = len(text)
    truncated = total > MAX_SCAN_CHARS
    scanned = text[:MAX_SCAN_CHARS] if truncated else text
    norm, index, scripts = normalize(scanned)
    ctx = _Context(scanned, norm, index, anchor_date, scripts)

    # Overlap resolution: longest span wins, then priority, then position.
    candidates = sorted(_candidates(ctx), key=lambda c: (-(c.end - c.start), -c.priority, c.start))
    taken = _Spans()
    signals: List[TemporalSignal] = []
    for cand in candidates:
        if taken.overlaps(cand.start, cand.end):
            continue
        sig = cand.build()
        if sig is None:
            continue
        if sig.language is not None and sig.language not in wanted:
            continue
        taken.add(cand.start, cand.end)
        signals.append(sig)

    # Orientation: one signal per (sentence, orientation) and a sentence-level
    # text_orientation attached to the dates in that sentence when unanimous.
    sentences = _sentences(norm)
    cue_sets = _orientation_cues(ctx, sentences)
    orientation_signals: List[TemporalSignal] = []
    by_sentence: List[Optional[str]] = []
    for (s_start, s_end), cues in zip(sentences, cue_sets):
        kept = {o: [m for m in ms if _CUE_INDEX[m.group(0)][1] in wanted] for o, ms in cues.items()}
        kept = {o: ms for o, ms in kept.items() if ms}
        by_sentence.append(next(iter(kept)) if len(kept) == 1 else None)
        for orientation, matches in sorted(kept.items()):
            first = matches[0]
            if taken.overlaps(first.start(), first.end()):
                matches = [m for m in matches if not taken.overlaps(m.start(), m.end())]
                if not matches:
                    continue
                first = matches[0]
            cue_lang = _CUE_INDEX[first.group(0)][1]
            orientation_signals.append(_signal(
                ctx, first.start(), first.end(), signal_type=SIGNAL_ORIENTATION,
                value=f"orientation:{orientation}", resolution="unresolved", language=cue_lang,
                text_orientation=orientation,
                evidence={"pattern": f"{cue_lang}.cue", "cue": first.group(0),
                          "cues_in_sentence": sorted({m.group(0) for m in matches}),
                          "conflicting_cues": sorted(o for o in kept if o != orientation),
                          "note": "lexical cue, not a parsed tense"}))

    sentence_index = textspan.SentenceIndex(sentences)

    def sentence_of(pos: int) -> int:
        return sentence_index.find(pos)

    finished: List[TemporalSignal] = []
    reverse = {orig: i for i, orig in enumerate(index)}
    for sig in signals:
        i = sentence_of(reverse.get(sig.char_start, 0))
        orientation = by_sentence[i] if i >= 0 else None
        if orientation and sig.signal_type == SIGNAL_DATE:
            ev = dict(sig.evidence, sentence_orientation=orientation)
            sig = TemporalSignal(**{**sig.__dict__, "text_orientation": orientation, "evidence": ev})
        finished.append(sig)

    everything = sorted((_with_provenance(ctx, sig, sentence_index, reverse)
                         for sig in finished + orientation_signals),
                        key=lambda s: (s.char_start, s.char_end, s.signal_type, s.value))
    return DetectionResult(signals=tuple(everything), detector_ver=DETECTOR_VERSION,
                           anchor_date=anchor_date, chars_total=total,
                           chars_scanned=len(scanned), truncated=truncated)
