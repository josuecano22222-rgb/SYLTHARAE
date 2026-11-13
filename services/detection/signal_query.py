"""Signal Explorer and Horizon: set-based reads over durable signals.

Both views read ``content_signals`` - what ingestion and re-detection stored -
and never re-run a detector (detect once, consume durable signals).

* **Explorer** (``explore``, ``signal_detail``): every stored signal, filtered
  by signal dimensions and by *document criteria*, with exact facet counts.
* **Horizon** (``horizon``): resolved temporal signals grouped by where their
  date falls relative to an explicit reference date.

Document criteria (source, side, category, keyword, analyst category, file
type, text, document date - or a saved search) are a canonical
:class:`~core.criteria.model.Criteria` compiled by the one criteria compiler
with the caller's :class:`AccessScope`, and applied as
``EXISTS (<canonical FROM> WHERE hc.hash_id = s.hash_id AND <compiled WHERE>)``.
There is no second filter language, and scope is applied in SQL before any
row is read: a signal is visible only when at least one of its content's
file occurrences is.

Unit: **signal** (one stored detection). ``contents`` counts distinct content
hashes. Nothing is capped silently: every page reports the exact total.

Horizon buckets (``BUCKETS``), for a reference date R and a signal whose
resolved range is [from, to]:

====================  ==========================================================
``overdue``           to < R *and* the sentence is future-oriented (the text
                      said it would happen; the date has passed)
``past``              to < R, not future-oriented - historical, not on the
                      horizon (counted, listed only on request)
``week``              to >= R and from < R + 7 (includes ranges in progress)
``month``             R + 7 <= from < R + 30
``quarter``           R + 30 <= from < R + 90
``later``             from >= R + 90
====================  ==========================================================

The 7/30/90-day windows are the bands the future-date notifications already
use (TEMPORAL_SIGNALS.md); they are rolling windows from R, not calendar
weeks/months/quarters. Bucketing uses the earliest possible day of a range
(Hijri dates are stored as +/-2-day ranges), and ``overdue``/``past`` require
the *whole* range to have passed. Dated signals are the only ones a date can
be assigned to: ambiguous and unresolved references are reported as separate
counts (``undated``), never dropped and never placed in a bucket. Content the
temporal detector has not analysed with its current version is reported in
``coverage`` - "not measured" is not "nothing due".
"""

from __future__ import annotations

import datetime
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.criteria.compiler import AccessScope, CompiledQuery, compile_criteria
from core.criteria.model import Criteria, CriteriaError, Sort, from_dict, sha256_hex
from core.criteria.sql import CANONICAL_FROM, escape_like
from core.detection import identifiers_intel, place_intel, temporal_intel
from services.detection import detectors as registry
from services.detection import signal_store

#: Largest page any signal list returns.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
#: Per-statement ceiling for interactive signal reads (milliseconds). A read
#: that exceeds it fails loudly (``QueryCanceled``), it is never truncated.
STATEMENT_TIMEOUT_MS = 15_000
#: Occurrences listed in ``signal_detail`` (the exact count is always given).
DETAIL_OCCURRENCE_LIMIT = 50

UNRECORDED = "unrecorded"       # value stored as NULL (e.g. pre-1.1 confidence)
UNSPECIFIED = "unspecified"     # language NULL: undetermined or several languages

SIGNAL_TYPES = temporal_intel.SIGNAL_TYPES + place_intel.SIGNAL_TYPES + identifiers_intel.SIGNAL_TYPES
RESOLUTIONS = tuple(dict.fromkeys(
    temporal_intel.RESOLUTIONS + place_intel.RESOLUTIONS + identifiers_intel.RESOLUTIONS))
CONFIDENCE_FILTER = temporal_intel.CONFIDENCE_LEVELS + (UNRECORDED,)
LANGUAGE_FILTER = temporal_intel.LANGUAGES + (UNSPECIFIED,)
CALENDARS = ("gregorian", "hijri", "jalali")
ORIENTATION_FILTER = ("future", "present", "past", UNRECORDED)
#: Text-match operators for evidence / surface / value filters.
TEXT_MATCH_MODES = ("contains", "equals", "starts_with", "ends_with", "not_contains")
_METHOD_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}\Z")
_PLACE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9_.-]+\Z")
MAX_FILTER_VALUES = 100
MAX_EVIDENCE_TEXT = 200
MAX_SURFACE_TEXT = 200
MAX_VALUE_TEXT = 200

# Quick-filter presets for the Signal Explorer UI (ids → detector / signal_type sets).
EXPLORER_KIND_PRESETS = (
    {"id": "dates", "detectors": ("temporal",), "signal_types": ("date_reference", "relative_reference")},
    {"id": "places", "detectors": ("places",), "signal_types": ("place_mention",)},
    {"id": "email", "detectors": ("identifiers",), "signal_types": ("identifier",),
     "identifier_kinds": ("email",)},
    {"id": "phone", "detectors": ("identifiers",), "signal_types": ("identifier",),
     "identifier_kinds": ("phone",)},
    {"id": "ip", "detectors": ("identifiers",), "signal_types": ("identifier",),
     "identifier_kinds": ("ipv4", "ipv6")},
    {"id": "url", "detectors": ("identifiers",), "signal_types": ("identifier",),
     "identifier_kinds": ("url",)},
    {"id": "identifiers", "detectors": ("identifiers",), "signal_types": identifiers_intel.SIGNAL_TYPES},
)

EXPLORER_SORTS = {
    "event_date": "s.date_from ASC NULLS LAST, s.date_to ASC NULLS LAST, s.hash_id ASC,"
                  " s.char_start ASC",
    "detected": "s.detected_at DESC, s.hash_id ASC, s.char_start ASC",
    "document": "s.hash_id ASC, s.char_start ASC, s.char_end ASC",
}

# The horizon buckets are defined once in core.detection.horizon (the report
# datasets share them; core never imports services).
from core.detection.horizon import (BUCKETS, BUCKET_KEYS, DEFAULT_BUCKETS,  # noqa: E402
                                    HORIZON_SIGNAL_TYPES,
                                    REF_SQL as _REF_SQL,
                                    bucket_ranges as _bucket_ranges_public,
                                    bucket_sql as _bucket_sql_impl)

#: Document-criteria request parameters -> canonical Criteria fields.
_DOC_LIST_PARAMS = {"source_id": "sources", "side_id": "sides", "category_id": "categories",
                    "analyst_category_id": "analyst_categories", "keyword_id": "keywords",
                    "file_type": "file_types"}
_DOC_SCALAR_PARAMS = {"text": "text", "keyword_logic": "keyword_logic",
                      "doc_date_field": "date_field", "doc_date_from": "date_from",
                      "doc_date_to": "date_to"}


class SignalQueryError(ValueError):
    """A request that cannot be answered as written (HTTP 400)."""


# ---------------------------------------------------------------------------
# Filter model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalFilter:
    """Signal-dimension filters. Every tuple is sorted and de-duplicated, so
    two equal filters serialise - and fingerprint - identically."""

    detectors: Tuple[str, ...] = ()
    signal_types: Tuple[str, ...] = ()
    resolutions: Tuple[str, ...] = ()
    confidence: Tuple[str, ...] = ()
    languages: Tuple[str, ...] = ()
    calendars: Tuple[str, ...] = ()
    methods: Tuple[str, ...] = ()
    orientations: Tuple[str, ...] = ()
    place_keys: Tuple[str, ...] = ()
    #: Evidence ``kind`` values (email, phone, ipv4, ipv6, url) for the
    #: identifier detector; its signal_type is uniformly ``identifier``.
    identifier_kinds: Tuple[str, ...] = ()
    hash_ids: Tuple[int, ...] = ()
    evidence_text: Optional[str] = None
    evidence_mode: str = "contains"
    surface_text: Optional[str] = None
    surface_mode: str = "contains"
    value_text: Optional[str] = None
    value_mode: str = "contains"
    event_from: Optional[str] = None
    event_to: Optional[str] = None

    def canonical(self) -> Dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()}


def _bounded_text(args, name: str, limit: int) -> Optional[str]:
    text = (args.get(name) or "").strip() or None
    if text and len(text) > limit:
        raise SignalQueryError(f"{name} is limited to {limit} characters")
    return text


def _match_mode(args, name: str) -> str:
    mode = (args.get(name) or "contains").strip().lower() or "contains"
    if mode not in TEXT_MATCH_MODES:
        raise SignalQueryError(f"{name} must be one of: {', '.join(TEXT_MATCH_MODES)}")
    return mode


def _text_match_sql(columns: Sequence[str], mode: str, value: str,
                    params: List[Any]) -> str:
    """Build a bound ILIKE / equality predicate over one or more columns."""
    escaped = escape_like(value)
    if mode == "equals":
        pattern = escaped
    elif mode == "starts_with":
        pattern = escaped + "%"
    elif mode == "ends_with":
        pattern = "%" + escaped
    else:  # contains / not_contains
        pattern = "%" + escaped + "%"
    parts = []
    for column in columns:
        parts.append(f"{column} ILIKE %s ESCAPE '\\'")
        params.append(pattern)
    joined = "(" + " OR ".join(parts) + ")"
    if mode == "not_contains":
        return f"NOT {joined}"
    return joined


def _values(get_list: Callable[[str], List[str]], name: str, allowed=None,
            pattern: Optional[re.Pattern] = None, *,
            split_commas: bool = False) -> Tuple[str, ...]:
    """A validated list of values for one filter parameter.

    ``split_commas`` is for parameters whose values cannot contain a comma and
    whose screens send several at once in a single field: ``identifier_kind`` is
    a hidden input the kind presets fill with ``','.join(kinds)``, so the "ip"
    preset sent the literal ``ipv4,ipv6`` and the request was refused with
    ``(got 'ipv4,ipv6')`` - a payload the API itself made the screen produce.
    Each item is validated on its own, so the error names the item that is
    wrong, and duplicates collapse. Empty items are dropped, which is what
    makes an empty field mean "no filter" and a trailing comma a typo rather
    than an error. Parameters whose values may legitimately contain a comma
    (free text, for instance) must not set this.
    """
    raw: List[str] = []
    for value in get_list(name):
        if value is None:
            continue
        for part in (value.split(",") if split_commas else [value]):
            part = part.strip()
            if part:
                raw.append(part)
    if len(raw) > MAX_FILTER_VALUES:
        raise SignalQueryError(f"at most {MAX_FILTER_VALUES} values for {name}")
    for v in raw:
        if allowed is not None and v not in allowed:
            raise SignalQueryError(f"{name} must be one of: {', '.join(allowed)} (got {v!r})")
        if pattern is not None and not pattern.match(v):
            raise SignalQueryError(f"invalid {name} {v!r}")
    return tuple(sorted(set(raw)))


_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]{0,17}\Z")


def _positive_int(value: Any, name: str, message: Optional[str] = None) -> int:
    """ASCII decimal, > 0 - the rule Werkzeug's ``<int:...>`` converter applies.
    ``str.isdigit`` is not enough: it accepts superscripts (which ``int()``
    then rejects with an unrelated error) and other scripts' digits."""
    text = str(value).strip()
    if not _POSITIVE_INT_RE.match(text):
        raise SignalQueryError(message or f"{name} must be a positive integer")
    return int(text)


def _iso_date(value: Optional[str], name: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise SignalQueryError(f"{name} must be an ISO date (YYYY-MM-DD)")


def parse_filter(args) -> SignalFilter:
    """Strictly parse request arguments (a werkzeug ``MultiDict`` or anything
    with ``getlist``/``get``). Unknown enum values are errors, not ignored."""
    get_list = args.getlist
    try:
        detectors = registry.validate(_values(get_list, "detector") or None) \
            if get_list("detector") else ()
    except ValueError as exc:
        raise SignalQueryError(str(exc))
    hash_ids = []
    for raw in get_list("hash_id"):
        hash_ids.append(_positive_int(raw, "hash_id"))
    evidence_text = _bounded_text(args, "evidence_text", MAX_EVIDENCE_TEXT)
    surface_text = _bounded_text(args, "surface_text", MAX_SURFACE_TEXT)
    value_text = _bounded_text(args, "value_text", MAX_VALUE_TEXT)
    f = SignalFilter(
        detectors=tuple(sorted(detectors)),
        signal_types=_values(get_list, "signal_type", SIGNAL_TYPES),
        resolutions=_values(get_list, "resolution", RESOLUTIONS),
        confidence=_values(get_list, "confidence", CONFIDENCE_FILTER),
        languages=_values(get_list, "language", LANGUAGE_FILTER),
        calendars=_values(get_list, "calendar", CALENDARS),
        methods=_values(get_list, "method", pattern=_METHOD_RE),
        orientations=_values(get_list, "orientation", ORIENTATION_FILTER),
        place_keys=_values(get_list, "place_key", pattern=_PLACE_KEY_RE),
        identifier_kinds=_values(get_list, "identifier_kind",
                                 allowed=identifiers_intel.KINDS,
                                 split_commas=True),
        hash_ids=tuple(sorted(set(hash_ids))),
        evidence_text=evidence_text,
        evidence_mode=_match_mode(args, "evidence_mode") if evidence_text else "contains",
        surface_text=surface_text,
        surface_mode=_match_mode(args, "surface_mode") if surface_text else "contains",
        value_text=value_text,
        value_mode=_match_mode(args, "value_mode") if value_text else "contains",
        event_from=_iso_date(args.get("event_from"), "event_from"),
        event_to=_iso_date(args.get("event_to"), "event_to"),
    )
    if f.event_from and f.event_to and f.event_from > f.event_to:
        raise SignalQueryError("event_from must not be after event_to")
    return f


def parse_document_criteria(args, *, load_saved_search: Callable[[int], Optional[Criteria]]
                            ) -> Tuple[Criteria, Optional[int]]:
    """Document criteria from request arguments, or from a saved search.

    ``load_saved_search(id)`` returns the stored criteria of a search the
    caller may read, or None (absent and not permitted are the same answer).
    Mixing a saved search with explicit document filters is refused: which
    one wins would be a guess.
    """
    explicit = [p for p in list(_DOC_LIST_PARAMS) + list(_DOC_SCALAR_PARAMS)
                if any(v not in (None, "") for v in args.getlist(p))]
    raw_saved = args.get("saved_search_id")
    if raw_saved not in (None, ""):
        if explicit:
            raise SignalQueryError("saved_search_id cannot be combined with "
                                   f"document filters ({', '.join(sorted(explicit))})")
        saved_id = _positive_int(raw_saved, "saved_search_id")
        criteria = load_saved_search(saved_id)
        if criteria is None:
            raise LookupError(f"saved search {saved_id}")
        return criteria, saved_id
    payload: Dict[str, Any] = {}
    for param, field_name in _DOC_LIST_PARAMS.items():
        values = [v for v in args.getlist(param) if v not in (None, "")]
        if values:
            if field_name != "file_types":
                values = [_positive_int(v, param, f"{param} must be positive integers")
                          for v in values]
            payload[field_name] = values
    for param, field_name in _DOC_SCALAR_PARAMS.items():
        value = args.get(param)
        if value not in (None, ""):
            payload[field_name] = value
    try:
        return from_dict(payload), None
    except CriteriaError as exc:
        raise SignalQueryError(f"document criteria: {exc}")


def _compile(criteria: Criteria, scope: AccessScope) -> CompiledQuery:
    """Compile document criteria at content level. Sort and unit do not
    affect which content matches; they are normalised so the compiler does
    not annotate an ordering that this view does not use."""
    try:
        return compile_criteria(
            criteria.with_changes(unit="hash", sort=(Sort(field="id", direction="asc"),)),
            scope)
    except CriteriaError as exc:
        raise SignalQueryError(f"document criteria: {exc}")


def _where(f: SignalFilter, compiled: Optional[CompiledQuery], *,
           include_event_window: bool = True) -> Tuple[List[str], List[Any]]:
    """Signal predicates on alias ``s``. With ``compiled``, a signal also
    needs a visible file occurrence matching those criteria. Without it
    (scenario conditions, services/monitoring/scenario_engine.py) the caller
    has already restricted the contents to a population compiled under the
    owner's scope."""
    conds: List[str] = []
    params: List[Any] = []

    def any_of(column: str, values: Sequence, null_token: Optional[str] = None) -> None:
        if not values:
            return
        concrete = [v for v in values if v != null_token]
        parts = []
        if concrete:
            parts.append(f"{column} = ANY(%s)")
            params.append(list(concrete))
        if null_token is not None and null_token in values:
            parts.append(f"{column} IS NULL")
        conds.append("(" + " OR ".join(parts) + ")")

    any_of("s.detector", f.detectors)
    any_of("s.signal_type", f.signal_types)
    any_of("s.resolution", f.resolutions)
    any_of("s.confidence", f.confidence, UNRECORDED)
    any_of("s.language", f.languages, UNSPECIFIED)
    any_of("s.calendar", f.calendars)
    any_of("s.method", f.methods)
    any_of("s.text_orientation", f.orientations, UNRECORDED)
    if f.hash_ids:
        conds.append("s.hash_id = ANY(%s)")
        params.append(list(f.hash_ids))
    if f.identifier_kinds:
        conds.append("s.evidence->>'kind' = ANY(%s)")
        params.append(list(f.identifier_kinds))
    if f.place_keys:
        conds.append("EXISTS (SELECT 1 FROM content_signal_places _csp"
                     " JOIN geo_places _g ON _g.id = _csp.place_id"
                     " WHERE _csp.signal_id = s.id"
                     "   AND ('place:' || _g.id) = ANY(%s))")
        params.append(list(f.place_keys))
    if f.evidence_text:
        conds.append(_text_match_sql(
            ("s.evidence_sentence", "s.surface"), f.evidence_mode, f.evidence_text, params))
    if f.surface_text:
        conds.append(_text_match_sql(("s.surface",), f.surface_mode, f.surface_text, params))
    if f.value_text:
        conds.append(_text_match_sql(("s.value",), f.value_mode, f.value_text, params))
    if include_event_window:
        # Overlap with the requested event window; undated signals never overlap.
        if f.event_from:
            conds.append("s.date_to >= %s")
            params.append(f.event_from)
        if f.event_to:
            conds.append("s.date_from <= %s")
            params.append(f.event_to)
    if compiled is not None:
        conds.append(f"EXISTS (SELECT 1 FROM {CANONICAL_FROM}"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                     f" WHERE hc.hash_id = s.hash_id AND ({compiled.where_sql}))")
        params.extend(compiled.params)
    return conds, params


#: ``read_consistency`` values: one snapshot for every statement of the
#: response (count, page, facets, buckets, coverage agree), or the isolation of
#: a transaction the caller had already opened (which cannot be changed).
SNAPSHOT = "repeatable_read_snapshot"
CALLER_TRANSACTION = "caller_transaction"


def _begin_read(cur) -> str:
    """Start the read: one REPEATABLE READ, READ ONLY snapshot when this is
    the first statement of the transaction (the API routes always are), and a
    statement timeout. Returns the consistency actually obtained."""
    import psycopg2.extensions as ext

    consistency = CALLER_TRANSACTION
    if cur.connection.info.transaction_status == ext.TRANSACTION_STATUS_IDLE:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        consistency = SNAPSHOT
    cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
    return consistency


def _page(limit, offset) -> Tuple[int, int]:
    try:
        limit = int(DEFAULT_PAGE_SIZE if limit in (None, "") else limit)
        offset = int(0 if offset in (None, "") else offset)
    except (TypeError, ValueError):
        raise SignalQueryError("limit and offset must be integers")
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise SignalQueryError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if offset < 0:
        raise SignalQueryError("offset must be >= 0")
    return limit, offset


def _current_versions(cur) -> Dict[str, Optional[str]]:
    return {name: registry.get(name).version(cur) for name in registry.NAMES}


def _documents(cur, hash_ids: Sequence[int], compiled: CompiledQuery) -> Dict[int, Dict[str, Any]]:
    """One representative visible occurrence per content (lowest path id)
    and the number of visible occurrences - one query for the page."""
    if not hash_ids:
        return {}
    cur.execute(
        "SELECT DISTINCT ON (hc.hash_id) hc.hash_id, p.id, p.file_name, hc.source_id,"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
        " hc.side_id, COUNT(*) OVER (PARTITION BY hc.hash_id)"
        f" FROM {CANONICAL_FROM} WHERE hc.hash_id = ANY(%s) AND ({compiled.where_sql})"
        " ORDER BY hc.hash_id, p.id",
        (list(hash_ids),) + tuple(compiled.params))
    return {r[0]: {"hash_id": r[0], "path_id": r[1], "file_name": r[2], "source_id": r[3],
                   "side_id": r[4], "occurrences": r[5]} for r in cur.fetchall()}


def _decorate(cur, rows, compiled, reference_date, versions) -> List[Dict[str, Any]]:
    items = signal_store.signal_rows_to_dicts(cur, rows, reference_date)
    docs = _documents(cur, sorted({i["hash_id"] for i in items}), compiled)
    for item in items:
        item["document"] = docs.get(item["hash_id"])
        item["detector_version_current"] = item["detector_ver"] == versions.get(item["detector"])
    return items


def _fingerprint(kind: str, f: SignalFilter, criteria: Criteria, **extra) -> str:
    return sha256_hex({"kind": kind, "filter": f.canonical(),
                       "criteria_fingerprint": criteria.fingerprint(), **extra})


def _envelope(kind, f, criteria, compiled, saved_search_id, reference_date, **extra):
    return {
        "unit": "signal",
        "reference_date": reference_date.isoformat(),
        "filter": f.canonical(),
        "criteria": criteria.to_dict(),
        "criteria_fingerprint": criteria.fingerprint(),
        "saved_search_id": saved_search_id,
        "query_fingerprint": _fingerprint(kind, f, criteria,
                                          reference_date=reference_date.isoformat(), **extra),
        "explain": compiled.explain(criteria),
    }


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------

_FACET_DIMENSIONS = ("detector", "signal_type", "confidence", "language", "calendar",
                     "resolution", "method")
_FACET_NULL = {"confidence": UNRECORDED, "language": UNSPECIFIED, "calendar": None,
               "method": UNRECORDED}


def _facets(cur, conds: List[str], params: List[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Exact counts per value of each dimension over the filtered set - one
    ``GROUPING SETS`` query, not one query per dimension."""
    cols = ", ".join(f"s.{d}" for d in _FACET_DIMENSIONS)
    sets = ", ".join(f"(s.{d})" for d in _FACET_DIMENSIONS)
    cur.execute(f"SELECT {cols}, GROUPING({cols}), COUNT(*) FROM content_signals s"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" WHERE {' AND '.join(conds)} GROUP BY GROUPING SETS ({sets})", tuple(params))
    width = len(_FACET_DIMENSIONS)
    facets: Dict[str, List[Dict[str, Any]]] = {d: [] for d in _FACET_DIMENSIONS}
    for row in cur.fetchall():
        mask = row[width]
        for i, dim in enumerate(_FACET_DIMENSIONS):
            if not (mask >> (width - 1 - i)) & 1:
                value = row[i]
                if value is None:
                    value = _FACET_NULL.get(dim) or "none"
                facets[dim].append({"value": value, "count": row[width + 1]})
    for dim in facets:
        facets[dim].sort(key=lambda e: (-e["count"], str(e["value"])))
    return facets


def explore(cur, f: SignalFilter, criteria: Criteria, scope: AccessScope,
            reference_date: datetime.date, *, sort: str = "event_date", limit=None,
            offset=None, saved_search_id: Optional[int] = None) -> Dict[str, Any]:
    if sort not in EXPLORER_SORTS:
        raise SignalQueryError(f"sort must be one of: {', '.join(EXPLORER_SORTS)}")
    limit, offset = _page(limit, offset)
    compiled = _compile(criteria, scope)
    consistency = _begin_read(cur)
    conds, params = _where(f, compiled)
    where = " AND ".join(conds)
    cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT s.hash_id) FROM content_signals s"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" WHERE {where}", tuple(params))
    total, contents = cur.fetchone()
    cur.execute(f"SELECT {signal_store.SIGNAL_COLUMNS} FROM content_signals s WHERE {where}"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" ORDER BY {EXPLORER_SORTS[sort]}, s.id ASC LIMIT %s OFFSET %s",
                tuple(params) + (limit, offset))
    rows = cur.fetchall()
    versions = _current_versions(cur)
    body = _envelope("signals.explore", f, criteria, compiled, saved_search_id, reference_date,
                     sort=sort)
    body.update({
        "total": total, "contents": contents, "limit": limit, "offset": offset, "sort": sort,
        "items": _decorate(cur, rows, compiled, reference_date, versions),
        "facets": _facets(cur, conds, params),
        "detector_versions": versions, "read_consistency": consistency,
    })
    return body


def signal_detail(cur, signal_id: int, scope: AccessScope,
                  reference_date: datetime.date) -> Dict[str, Any]:
    """One signal with its run, its detector's current version and every
    visible occurrence of its content. ``LookupError`` when the signal does
    not exist *or* the caller may not see any occurrence (same answer)."""
    compiled = _compile(Criteria(), scope)
    consistency = _begin_read(cur)
    cur.execute(f"SELECT {signal_store.SIGNAL_COLUMNS} FROM content_signals s WHERE s.id = %s"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" AND EXISTS (SELECT 1 FROM {CANONICAL_FROM}"
                f" WHERE hc.hash_id = s.hash_id AND ({compiled.where_sql}))",
                (signal_id,) + tuple(compiled.params))
    row = cur.fetchone()
    if row is None:
        raise LookupError(signal_id)
    versions = _current_versions(cur)
    item = _decorate(cur, [row], compiled, reference_date, versions)[0]
    cur.execute("SELECT detector_ver, status, anchor_date, chars_total, chars_scanned,"
                " signal_count, trigger, job_id, error, ran_at FROM content_signal_runs"
                " WHERE hash_id = %s AND detector = %s", (item["hash_id"], item["detector"]))
    r = cur.fetchone()
    item["run"] = None if r is None else {
        "detector_ver": r[0], "status": r[1], "anchor_date": r[2].isoformat() if r[2] else None,
        "chars_total": r[3], "chars_scanned": r[4], "signal_count": r[5], "trigger": r[6],
        "job_id": r[7], "error": r[8], "ran_at": r[9].isoformat() if r[9] else None,
        "current_version": r[0] == versions.get(item["detector"])}
    cur.execute(f"SELECT COUNT(*) FROM {CANONICAL_FROM} WHERE hc.hash_id = %s"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" AND ({compiled.where_sql})", (item["hash_id"],) + tuple(compiled.params))
    occurrence_total = cur.fetchone()[0]
    cur.execute(f"SELECT p.id, p.file_name, p.file_path, hc.source_id, hc.side_id, p.file_date"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" FROM {CANONICAL_FROM} WHERE hc.hash_id = %s AND ({compiled.where_sql})"
                " ORDER BY p.id LIMIT %s",
                (item["hash_id"],) + tuple(compiled.params) + (DETAIL_OCCURRENCE_LIMIT,))
    item["occurrences"] = {
        "total": occurrence_total, "limit": DETAIL_OCCURRENCE_LIMIT,
        "truncated": occurrence_total > DETAIL_OCCURRENCE_LIMIT,
        "items": [{"path_id": o[0], "file_name": o[1], "file_path": o[2], "source_id": o[3],
                   "side_id": o[4], "file_date": o[5].isoformat() if o[5] else None}
                  for o in cur.fetchall()]}
    item["detector_versions"] = versions
    item["reference_date"] = reference_date.isoformat()
    item["read_consistency"] = consistency
    return item


# ---------------------------------------------------------------------------
# Horizon
# ---------------------------------------------------------------------------


#: Public names kept importable from here (the definitions live in
#: core.detection.horizon); ``_bucket_sql`` predates the move and is kept
#: for its existing callers.
def bucket_ranges(reference_date: datetime.date) -> List[Dict[str, Any]]:
    return _bucket_ranges_public(reference_date)


def _bucket_sql() -> str:
    return _bucket_sql_impl()


def _horizon_filter(f: SignalFilter) -> SignalFilter:
    if f.detectors and f.detectors != (temporal_intel.DETECTOR_NAME,):
        raise SignalQueryError("the horizon shows temporal signals only")
    if set(f.signal_types) - set(HORIZON_SIGNAL_TYPES):
        raise SignalQueryError("the horizon shows date and relative references only "
                               f"({', '.join(HORIZON_SIGNAL_TYPES)})")
    return SignalFilter(**{**asdict(f), "detectors": (temporal_intel.DETECTOR_NAME,),
                           "signal_types": f.signal_types or HORIZON_SIGNAL_TYPES})


def horizon(cur, f: SignalFilter, criteria: Criteria, scope: AccessScope,
            reference_date: datetime.date, *, buckets: Sequence[str] = (), limit=None,
            offset=None, sort: str = "event_date",
            saved_search_id: Optional[int] = None) -> Dict[str, Any]:
    if not isinstance(reference_date, datetime.date):
        raise SignalQueryError("reference_date is required")
    if sort not in EXPLORER_SORTS:
        raise SignalQueryError(f"sort must be one of: {', '.join(EXPLORER_SORTS)}")
    unknown = set(buckets) - set(BUCKET_KEYS)
    if unknown:
        raise SignalQueryError(f"bucket must be among: {', '.join(BUCKET_KEYS)}")
    listed = tuple(b for b in BUCKET_KEYS if b in set(buckets)) or DEFAULT_BUCKETS
    limit, offset = _page(limit, offset)
    f = _horizon_filter(f)
    compiled = _compile(criteria, scope)
    consistency = _begin_read(cur)
    conds, params = _where(f, compiled)
    where = " AND ".join(conds + ["s.date_from IS NOT NULL"])
    bucket = _bucket_sql()

    cur.execute(f"SELECT {bucket} AS b, COUNT(*), COUNT(DISTINCT s.hash_id)"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" FROM content_signals s{_REF_SQL} WHERE {where} GROUP BY b",
                (reference_date,) + tuple(params))
    counts = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    summary = []
    for rng in bucket_ranges(reference_date):
        n, contents = counts.get(rng["key"], (0, 0))
        summary.append({**rng, "signals": n, "contents": contents,
                        "listed": rng["key"] in listed})

    # Undated references that match the same filters (event window aside:
    # an undated signal cannot be inside or outside a date window).
    u_conds, u_params = _where(f, compiled, include_event_window=False)
    cur.execute(f"SELECT s.resolution, COUNT(*) FROM content_signals s"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f" WHERE {' AND '.join(u_conds)} AND s.date_from IS NULL GROUP BY s.resolution",
                tuple(u_params))
    undated = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute(f"SELECT {signal_store.SIGNAL_COLUMNS}, {bucket} AS b FROM content_signals s"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
                f"{_REF_SQL} WHERE {where} AND {bucket} = ANY(%s)"
                f" ORDER BY {EXPLORER_SORTS[sort]}, s.id ASC LIMIT %s OFFSET %s",
                (reference_date,) + tuple(params) + (list(listed), limit, offset))
    rows = cur.fetchall()
    listed_total = sum(counts.get(b, (0, 0))[0] for b in listed)
    versions = _current_versions(cur)
    items = _decorate(cur, [r[:signal_store.SIGNAL_COLUMN_COUNT] for r in rows], compiled,
                      reference_date, versions)
    for item, row in zip(items, rows):
        item["bucket"] = row[signal_store.SIGNAL_COLUMN_COUNT]

    body = _envelope("signals.horizon", f, criteria, compiled, saved_search_id, reference_date,
                     buckets=list(listed), sort=sort)
    body.update({
        "buckets": summary,
        "listed_buckets": list(listed),
        "total": listed_total, "limit": limit, "offset": offset, "sort": sort,
        "items": items,
        "undated": {"ambiguous": undated.get("ambiguous", 0),
                    "unresolved": undated.get("unresolved", 0),
                    "note": "references whose date could not be determined; not placed "
                            "on the horizon"},
        "coverage": coverage(cur, compiled, versions[temporal_intel.DETECTOR_NAME]),
        "detector_versions": versions, "read_consistency": consistency,
    })
    return body


def coverage(cur, compiled: CompiledQuery, current_version: Optional[str]) -> Dict[str, Any]:
    """How much of the matching content the temporal detector has measured
    with its current version. Only ``current`` content contributes to the
    horizon; the rest is *not measured*, which is not the same as empty."""
    cur.execute(
        "SELECT COUNT(*),"  # nosec B608 # constant column lists, whitelisted facets/sorts, WHERE from _where's fixed fragments and the shared criteria compiler; values are bound parameters
        " COUNT(*) FILTER (WHERE r.hash_id IS NULL),"
        " COUNT(*) FILTER (WHERE r.status = 'failed'),"
        " COUNT(*) FILTER (WHERE r.status <> 'failed' AND r.detector_ver IS DISTINCT FROM %s),"
        " COUNT(*) FILTER (WHERE r.status <> 'failed' AND r.detector_ver = %s),"
        " COUNT(*) FILTER (WHERE r.status = 'truncated' AND r.detector_ver = %s),"
        " COUNT(*) FILTER (WHERE r.status = 'no_text' AND r.detector_ver = %s)"
        f" FROM (SELECT DISTINCT hc.hash_id FROM {CANONICAL_FROM}"
        f" WHERE hc.hash_id IS NOT NULL AND ({compiled.where_sql})) m"
        " LEFT JOIN content_signal_runs r ON r.hash_id = m.hash_id AND r.detector = 'temporal'",
        (current_version,) * 4 + tuple(compiled.params))
    total, never, failed, stale, current, truncated, no_text = cur.fetchone()
    return {"unit": "content", "matching_contents": total, "current": current,
            "current_truncated": truncated, "current_no_text": no_text,
            "stale_version": stale, "failed": failed, "never_analysed": never,
            "not_measured": never + failed + stale,
            "detector_version": current_version}
