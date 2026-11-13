"""The canonical search criteria object - one definition, many consumers.

A saved search, a monitoring rule, a report parameter set and an export are
four *uses* of one ``Criteria``. It is defined once, serialised to canonical
JSON, fingerprinted with SHA-256 and compiled to SQL in one place
(``core.criteria.compiler``). Nothing else in the product may hold its own
interpretation of "which documents match".

Design rules
------------
* **Strict parsing.** Unknown keys are refused with an explanation, never
  silently dropped - a criteria payload that loses a filter on the way in is
  how a monitor silently watches the wrong thing.
* **Canonical form.** Id lists are de-duplicated and sorted, strings are
  stripped, defaults are materialised. Two criteria that mean the same thing
  serialise to the same bytes and therefore share a fingerprint.
* **Nothing invented.** Fields exist only where the schema can honour them.
  ``include_child_categories`` is accepted for forward compatibility but the
  smart taxonomy (``categorys``) has no parent column, so ``True`` is refused
  rather than silently ignored.
* **Interactive-only options are declared, not hidden.** Fuzzy matching,
  query expansion and BM25 ranking change what the *interactive* engine
  returns; the compiled (non-interactive) path matches literally. They are
  part of the definition (and its fingerprint) and ``explain()`` says which
  consumers honour them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Bump when the canonical serialisation or the meaning of a field changes.
#: Stored beside every persisted criteria so old rows remain interpretable.
CRITERIA_SCHEMA_VERSION = 1

UNITS = ("hash", "path")
DATE_FIELDS = ("file_date", "content_date", "context_date")
KEYWORD_LOGIC = ("AND", "OR", "NOT")
ANALYST_SCOPES = ("all", "uncategorized", "categorized")

#: The product default (FR-2.1): a search with no explicit scope covers
#: files an analyst has not yet categorised. Interactive search, the saved
#: search replay in the UI and the legacy mapping below all apply it.
DEFAULT_ANALYST_SCOPE = "uncategorized"


def normalize_analyst_scope(scope) -> str:
    """The one scope normaliser (FR-2.1/2.2), shared with interactive search.

    ``None``/empty/unknown values resolve to the default ("uncategorized"),
    never to "all": widening an unrecognised scope would make a monitor or
    report cover records the analyst's own search never showed.
    """
    if scope is None:
        return DEFAULT_ANALYST_SCOPE
    value = str(scope).strip().lower()
    if value in ANALYST_SCOPES:
        return value
    if value in ("analyst_categorized", "analyst-categorized", "categorised"):
        return "categorized"
    return DEFAULT_ANALYST_SCOPE
SORT_FIELDS = ("relevance", "date", "name", "type", "size", "id")
SORT_DIRECTIONS = ("asc", "desc")
FILE_STATUSES = ("Read", "Unread")

#: Upper bounds - a criteria object is a definition, not a data smuggling
#: channel. Generous enough for any real analyst definition.
MAX_IDS_PER_DIMENSION = 1000
MAX_TEXT_LENGTH = 2000
MAX_PHRASES = 50


class CriteriaError(ValueError):
    """A criteria payload that cannot be accepted, with the reason."""


@dataclass(frozen=True)
class Sort:
    field: str = "relevance"
    direction: str = "desc"


@dataclass(frozen=True)
class SearchOptions:
    """Options that only the interactive ranking engine honours."""

    use_fuzzy: bool = True
    use_expansion: bool = True
    use_bm25: bool = True
    similarity_threshold: Optional[float] = None


@dataclass(frozen=True)
class Criteria:
    # --- text -----------------------------------------------------------
    text: Optional[str] = None             # boolean-capable literal expression
    phrases: Tuple[str, ...] = ()          # each phrase must occur (AND)
    case_sensitive: bool = False
    whole_word: bool = False
    # --- curated vocabulary ---------------------------------------------
    keywords: Tuple[int, ...] = ()         # keywords.id
    keyword_logic: str = "AND"             # AND: all | OR: any | NOT: none
    # --- taxonomy -------------------------------------------------------
    categories: Tuple[int, ...] = ()       # categorys.id (smart taxonomy)
    include_child_categories: bool = False
    analyst_categories: Tuple[int, ...] = ()
    analyst_scope: str = "all"
    # --- provenance -----------------------------------------------------
    sources: Tuple[int, ...] = ()
    sides: Tuple[int, ...] = ()
    file_types: Tuple[str, ...] = ()
    file_statuses: Optional[Tuple[str, ...]] = None   # None = unfiltered
    # --- explicit selection (File Manager / analyst pick) ----------------
    path_ids: Tuple[int, ...] = ()         # paths.id — exact file set
    # --- time -----------------------------------------------------------
    date_field: str = "file_date"
    date_from: Optional[str] = None        # ISO YYYY-MM-DD
    date_to: Optional[str] = None
    # --- result shape ---------------------------------------------------
    unit: str = "path"                     # declared, never implied
    hide_duplicates: bool = False
    sort: Tuple[Sort, ...] = (Sort(),)
    # --- interactive-engine options (declared, see module docstring) -----
    options: SearchOptions = field(default_factory=SearchOptions)

    # ------------------------------------------------------------------
    def __post_init__(self):
        _validate(self)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Canonical, JSON-serialisable form (the stored representation)."""
        data = asdict(self)
        data["phrases"] = list(self.phrases)
        for key in ("keywords", "categories", "analyst_categories", "sources",
                    "sides", "file_types", "path_ids"):
            data[key] = list(getattr(self, key))
        # Omit empty path_ids so fingerprints stay stable for criteria that
        # never used an explicit file selection (pre-path_ids payloads).
        if not data["path_ids"]:
            data.pop("path_ids")
        data["file_statuses"] = (list(self.file_statuses)
                                 if self.file_statuses is not None else None)
        data["sort"] = [{"field": s.field, "direction": s.direction} for s in self.sort]
        data["schema_version"] = CRITERIA_SCHEMA_VERSION
        return data

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def fingerprint(self) -> str:
        """SHA-256 of the canonical JSON, hex. Stable across processes."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    def explain(self) -> List[str]:
        """Human-readable statement of every condition, in a stable order.

        This is what a report prints in its criteria block and what the
        "why was I notified?" panel shows. It is derived from the stored
        object, never reconstructed from results.
        """
        lines: List[str] = []
        if self.text:
            mode = []
            if self.case_sensitive:
                mode.append("case-sensitive")
            if self.whole_word:
                mode.append("whole words")
            lines.append(f"Text matches expression: {self.text}"
                         + (f" ({', '.join(mode)})" if mode else ""))
        for phrase in self.phrases:
            lines.append(f"Contains phrase: \"{phrase}\"")
        if self.keywords:
            verb = {"AND": "all of", "OR": "any of", "NOT": "none of"}[self.keyword_logic]
            lines.append(f"Keywords: {verb} {list(self.keywords)}")
        if self.categories:
            lines.append(f"Smart categories (any): {list(self.categories)}")
        if self.analyst_categories:
            lines.append(f"Analyst categories (any): {list(self.analyst_categories)}")
        if self.analyst_scope != "all":
            lines.append(f"Analyst scope: {self.analyst_scope} files only")
        if self.sources:
            lines.append(f"Sources (any): {list(self.sources)}")
        if self.sides:
            lines.append(f"Sides (any): {list(self.sides)}")
        if self.file_types:
            lines.append(f"File types (any): {list(self.file_types)}")
        if self.path_ids:
            lines.append(f"Exact file selection: {len(self.path_ids)} path id(s)")
        if self.file_statuses is not None:
            lines.append(f"File status: {list(self.file_statuses) or 'none (matches nothing)'}")
        if self.date_from or self.date_to:
            lines.append(f"{self.date_field} between {self.date_from or '-inf'} "
                         f"and {self.date_to or '+inf'} (inclusive)")
        lines.append(f"Unit of analysis: {self.unit}")
        if self.hide_duplicates:
            lines.append("One result per content hash")
        lines.append("Order: " + ", ".join(f"{s.field} {s.direction}" for s in self.sort))
        if self.text and (self.options.use_fuzzy or self.options.use_expansion):
            lines.append("Note: fuzzy matching / query expansion widen recall in "
                         "interactive search only; monitoring, reports and exports "
                         "match the expression literally.")
        return lines

    def with_changes(self, **changes) -> "Criteria":
        return replace(self, **changes)


# ---------------------------------------------------------------------------
# Canonical JSON
# ---------------------------------------------------------------------------
def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_FIELD_NAMES = None


def _field_names() -> frozenset:
    global _FIELD_NAMES
    if _FIELD_NAMES is None:
        _FIELD_NAMES = frozenset(f.name for f in fields(Criteria))
    return _FIELD_NAMES


def _ids(value: Any, what: str) -> Tuple[int, ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        value = [value]
    out = set()
    for item in value:
        if isinstance(item, bool):
            raise CriteriaError(f"{what}: {item!r} is not an id")
        try:
            number = int(str(item).strip())
        except (TypeError, ValueError):
            raise CriteriaError(f"{what}: {item!r} is not an id") from None
        if number <= 0:
            raise CriteriaError(f"{what}: ids are positive integers, got {number}")
        out.add(number)
    if len(out) > MAX_IDS_PER_DIMENSION:
        raise CriteriaError(f"{what}: at most {MAX_IDS_PER_DIMENSION} ids")
    return tuple(sorted(out))


def _strings(value: Any, what: str) -> Tuple[str, ...]:
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        value = [value]
    out = set()
    for item in value:
        if not isinstance(item, str):
            raise CriteriaError(f"{what}: {item!r} is not text")
        item = item.strip()
        if item:
            out.add(item)
    if len(out) > MAX_IDS_PER_DIMENSION:
        raise CriteriaError(f"{what}: too many values")
    return tuple(sorted(out))


def _bool(value: Any, what: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("0", "false", "no", "off", ""):
        return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise CriteriaError(f"{what}: {value!r} is not a boolean")


def _date(value: Any, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not _ISO_DATE.match(text):
        raise CriteriaError(f"{what}: expected YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise CriteriaError(f"{what}: {value!r} is not a real date") from None
    return text


def _validate(c: Criteria) -> None:
    """Enforce invariants on a constructed object (also called on direct use)."""
    if c.unit not in UNITS:
        raise CriteriaError(f"unit must be one of {UNITS}")
    if c.date_field not in DATE_FIELDS:
        raise CriteriaError(f"date_field must be one of {DATE_FIELDS}")
    if c.keyword_logic not in KEYWORD_LOGIC:
        raise CriteriaError(f"keyword_logic must be one of {KEYWORD_LOGIC}")
    if c.analyst_scope not in ANALYST_SCOPES:
        raise CriteriaError(f"analyst_scope must be one of {ANALYST_SCOPES}")
    if c.include_child_categories:
        raise CriteriaError(
            "include_child_categories is not supported: the smart taxonomy "
            "(categorys) has no parent/child relation in this schema")
    if c.text is not None and len(c.text) > MAX_TEXT_LENGTH:
        raise CriteriaError(f"text: at most {MAX_TEXT_LENGTH} characters")
    if len(c.phrases) > MAX_PHRASES:
        raise CriteriaError(f"phrases: at most {MAX_PHRASES}")
    if c.file_statuses is not None and any(s not in FILE_STATUSES for s in c.file_statuses):
        raise CriteriaError(f"file_statuses must contain only {FILE_STATUSES}")
    if c.date_from and c.date_to and c.date_from > c.date_to:
        raise CriteriaError("date_from is after date_to")
    if not c.sort:
        raise CriteriaError("sort must contain at least one key")
    for s in c.sort:
        if s.field not in SORT_FIELDS or s.direction not in SORT_DIRECTIONS:
            raise CriteriaError(f"invalid sort key {s!r}")
    thr = c.options.similarity_threshold
    if thr is not None and not (0.0 < float(thr) < 1.0):
        raise CriteriaError("similarity_threshold must be between 0 and 1")


def from_dict(payload: Optional[Dict[str, Any]]) -> Criteria:
    """Strictly parse a canonical criteria payload (inverse of ``to_dict``)."""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise CriteriaError("criteria must be a JSON object")
    payload = dict(payload)
    version = payload.pop("schema_version", CRITERIA_SCHEMA_VERSION)
    if version != CRITERIA_SCHEMA_VERSION:
        raise CriteriaError(f"unsupported criteria schema_version {version!r}")
    unknown = set(payload) - _field_names()
    if unknown:
        raise CriteriaError(f"unknown criteria fields: {sorted(unknown)}")

    text = payload.get("text")
    if text is not None and not isinstance(text, str):
        raise CriteriaError("text must be a string")
    text = (text or "").strip() or None

    phrases_raw = payload.get("phrases") or []
    if not isinstance(phrases_raw, (list, tuple)):
        raise CriteriaError("phrases must be a list")
    phrases = []
    for p in phrases_raw:
        if not isinstance(p, str):
            raise CriteriaError("phrases must be strings")
        p = " ".join(p.split())
        if p and p not in phrases:
            phrases.append(p)
    phrases.sort()

    statuses = payload.get("file_statuses")
    if statuses is not None:
        statuses = _strings(statuses, "file_statuses") if statuses != [] else ()

    sort_raw = payload.get("sort") or [{"field": "relevance", "direction": "desc"}]
    if isinstance(sort_raw, dict):
        sort_raw = [sort_raw]
    sort = []
    for s in sort_raw:
        if not isinstance(s, dict) or set(s) - {"field", "direction"}:
            raise CriteriaError(f"invalid sort key {s!r}")
        sort.append(Sort(field=str(s.get("field", "relevance")).lower(),
                         direction=str(s.get("direction", "desc")).lower()))

    opts_raw = payload.get("options") or {}
    if not isinstance(opts_raw, dict):
        raise CriteriaError("options must be an object")
    unknown_opts = set(opts_raw) - {f.name for f in fields(SearchOptions)}
    if unknown_opts:
        raise CriteriaError(f"unknown options: {sorted(unknown_opts)}")
    thr = opts_raw.get("similarity_threshold")
    options = SearchOptions(
        use_fuzzy=_bool(opts_raw.get("use_fuzzy"), "options.use_fuzzy", True),
        use_expansion=_bool(opts_raw.get("use_expansion"), "options.use_expansion", True),
        use_bm25=_bool(opts_raw.get("use_bm25"), "options.use_bm25", True),
        similarity_threshold=(round(float(thr), 4) if thr not in (None, "") else None),
    )

    return Criteria(
        text=text,
        phrases=tuple(phrases),
        case_sensitive=_bool(payload.get("case_sensitive"), "case_sensitive", False),
        whole_word=_bool(payload.get("whole_word"), "whole_word", False),
        keywords=_ids(payload.get("keywords"), "keywords"),
        keyword_logic=str(payload.get("keyword_logic") or "AND").upper(),
        categories=_ids(payload.get("categories"), "categories"),
        include_child_categories=_bool(payload.get("include_child_categories"),
                                       "include_child_categories", False),
        analyst_categories=_ids(payload.get("analyst_categories"), "analyst_categories"),
        analyst_scope=str(payload.get("analyst_scope") or "all").lower(),
        sources=_ids(payload.get("sources"), "sources"),
        sides=_ids(payload.get("sides"), "sides"),
        file_types=_strings(payload.get("file_types"), "file_types"),
        path_ids=_ids(payload.get("path_ids"), "path_ids"),
        file_statuses=statuses,
        date_field=str(payload.get("date_field") or "file_date"),
        date_from=_date(payload.get("date_from"), "date_from"),
        date_to=_date(payload.get("date_to"), "date_to"),
        unit=str(payload.get("unit") or "path"),
        hide_duplicates=_bool(payload.get("hide_duplicates"), "hide_duplicates", False),
        sort=tuple(sort),
        options=options,
    )


def from_legacy_search(query: Optional[str], filters: Optional[Dict[str, Any]]) -> Criteria:
    """Map the Advanced Search page's saved definition onto ``Criteria``.

    The page stores ``query`` plus ``filters`` with keys ``scope``,
    ``sort_by``, ``sort_order``, ``similarity_threshold``, ``options``
    (``case_sensitive``/``whole_word``/``use_fuzzy``/...), ``hide_duplicates``,
    ``file_type``, ``category_id``, ``analyst_category_id``, ``source_id``,
    ``side_id``, ``date_from``, ``date_to`` and ``status`` (see
    ``Api/routes/search.py::_advanced_search_run_url``). Every one of those is
    mapped; keys this function does not recognise are *reported* via
    ``legacy_unmapped_keys`` so the importer can record them instead of
    losing them silently.
    """
    f = filters if isinstance(filters, dict) else {}
    options = f.get("options") if isinstance(f.get("options"), dict) else {}
    status = f.get("status")
    statuses: Optional[Tuple[str, ...]]
    if isinstance(status, list):
        statuses = tuple(sorted({s for s in status if s in FILE_STATUSES}))
    else:
        statuses = None
    # A definition saved without a scope was always *run* with the default
    # (the page replays ``f.scope || 'uncategorized'``), so it maps to the
    # default - mapping it to "all" would monitor a wider set than was saved.
    scope = normalize_analyst_scope(f.get("scope") or f.get("analyst_scope"))
    sort_by = str(f.get("sort_by") or "relevance").lower()
    if sort_by not in SORT_FIELDS:
        sort_by = "relevance"
    sort_order = str(f.get("sort_order") or "desc").lower()
    if sort_order not in SORT_DIRECTIONS:
        sort_order = "desc"
    thr = f.get("similarity_threshold")
    try:
        thr = round(float(thr), 4) if thr not in (None, "") else None
        if thr is not None and not (0.0 < thr < 1.0):
            thr = None
    except (TypeError, ValueError):
        thr = None
    return Criteria(
        text=(query or "").strip() or None,
        case_sensitive=_bool(options.get("case_sensitive"), "case_sensitive", False),
        whole_word=_bool(options.get("whole_word"), "whole_word", False),
        categories=_ids(f.get("category_id") or f.get("category_ids"), "category_id"),
        analyst_categories=_ids(f.get("analyst_category_id")
                                or f.get("analyst_category_ids"), "analyst_category_id"),
        analyst_scope=scope,
        sources=_ids(f.get("source_id") or f.get("source_ids"), "source_id"),
        sides=_ids(f.get("side_id") or f.get("side_ids"), "side_id"),
        file_types=_strings(f.get("file_type") or f.get("file_types"), "file_type"),
        file_statuses=statuses,
        date_from=_date(f.get("date_from"), "date_from"),
        date_to=_date(f.get("date_to"), "date_to"),
        hide_duplicates=_bool(f.get("hide_duplicates"), "hide_duplicates", False),
        sort=(Sort(sort_by, sort_order),),
        options=SearchOptions(
            use_fuzzy=_bool(options.get("use_fuzzy"), "use_fuzzy", True),
            use_expansion=_bool(options.get("use_expansion"), "use_expansion", True),
            use_bm25=_bool(options.get("use_bm25"), "use_bm25", True),
            similarity_threshold=thr,
        ),
    )


LEGACY_KNOWN_KEYS = frozenset({
    "scope", "analyst_scope", "sort_by", "sort_order", "similarity_threshold",
    "options", "hide_duplicates", "file_type", "file_types", "category_id",
    "category_ids", "analyst_category_id", "analyst_category_ids", "source_id",
    "source_ids", "side_id", "side_ids", "date_from", "date_to", "status",
    # UI-only state that does not change what matches:
    "page", "per_page",
})


def legacy_unmapped_keys(filters: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(filters, dict):
        return []
    return sorted(set(filters) - LEGACY_KNOWN_KEYS)


def iter_ids(c: Criteria) -> Iterable[Tuple[str, Tuple[int, ...]]]:
    for name in ("keywords", "categories", "analyst_categories", "sources", "sides"):
        yield name, getattr(c, name)
