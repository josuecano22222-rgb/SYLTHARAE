"""Authoritative search export: the server decides what is in the file.

The export used to take the browser's own array of rows and turn it into a
spreadsheet. That is a professional defect, not a convenience: the browser can
send a stale page, a truncated list, a filtered view the operator has since
changed, or rows that never existed, and the file that leaves the building
carries whatever it was handed. An export is evidence - it has to be produced
from the query, by the same code that answered the screen.

So this service takes a *query definition* - what was searched for, under which
filters, in which order - and re-runs it against the database. The rows in the
file are the rows the server found. The client's version of the results is
never consulted, and a request that tries to supply one is refused with an
explanation rather than quietly ignored.

Three scopes, because they are three different operations and the operator has
to say which one they mean:

``page``
    Exactly the page of results the reader is looking at.
``filtered``
    The whole result set of the query and filters, not just the visible page.
``dataset``
    Everything the filters would let through, with the query itself dropped.

A scope is never guessed from the payload: an export that quietly widens from
one page to the whole corpus, or narrows from the corpus to a page, is how
someone exports the wrong thing and does not notice until it matters.

Volume is handled honestly. Up to ``max_rows`` rows are produced in one file;
beyond that the export says so - in a response header and in the log - instead
of silently truncating an evidence file. Larger exports belong to the job
architecture, and this service reports the size that makes that decision
possible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: What the operator is exporting. Never inferred.
#: ``selected`` is not a query at all - it is an explicit, caller-supplied
#: list of path ids (e.g. "export the metadata of the document I am looking
#: at"). It bypasses the search engine entirely and reads the rows directly,
#: the same way the other explicit-selection endpoints in this codebase do
#: (see Api/blueprints/files.py's id-list export routes) rather than forcing
#: a single-record lookup through query re-execution machinery built for
#: multi-row result sets.
EXPORT_SCOPES: Tuple[str, ...] = ("page", "filtered", "dataset", "selected")

#: What the file will be.
EXPORT_FORMATS: Tuple[str, ...] = ("csv", "excel", "json")

#: Deliberate ceiling for one synchronous export. Above it the honest answer is
#: a persistent job, not a longer request: the size is reported so that
#: decision can be made by the person asking.
MAX_ROWS = 50_000

#: How many rows are fetched per round when the whole result set is wanted.
CHUNK = 1_000

#: Legacy positional shape returned by older search query paths. It is kept
#: only to map tuple rows into the public export schema below.
_RESULT_COLUMNS: Tuple[str, ...] = (
    "id", "file_name", "file_type", "file_size", "file_date",
    "file_status", "source_name", "source_id", "side_name", "side_id",
    "relevance_score", "categories", "snippet",
)

#: The public shape of an exported row, in order. CSV, Excel and JSON all use
#: this one definition; smart and analyst taxonomies stay separate.
COLUMNS: Tuple[str, ...] = (
    "id", "file_name", "file_type", "file_size", "file_date",
    "file_status", "source_name", "source_id", "side_name", "side_id",
    "relevance_score", "smart_categories", "analyst_categories", "snippet",
    "path", "hash", "processing_status",
)

#: Human labels for the public columns, in the same order. This is the one
#: place the labels live; the column picker in the UI reads them from here
#: (via the columns metadata route) instead of hard-coding its own copy that
#: could drift from what the server actually publishes.
COLUMN_LABELS: Dict[str, str] = {
    "id": "File ID",
    "file_name": "File Name",
    "file_type": "File Type",
    "file_size": "File Size",
    "file_date": "File Date",
    "file_status": "Status",
    "source_name": "Source",
    "source_id": "Source ID",
    "side_name": "Side",
    "side_id": "Side ID",
    "relevance_score": "Relevance Score",
    "smart_categories": "Smart Categories",
    "analyst_categories": "Analyst Categories",
    "snippet": "Match Snippet",
    "path": "Stored Path",
    "hash": "Content Hash",
    "processing_status": "Processing Status",
}

#: Path, hash and processing status are only populated by the ``selected``
#: scope (an explicit id lookup - see ``_lookup_by_ids``); the query-based
#: scopes (page/filtered/dataset) do not carry them today. They remain
#: selectable everywhere for one column picker, and are honestly blank - not
#: fabricated - where the underlying rows do not have them.
_SELECTED_ONLY_COLUMNS: Tuple[str, ...] = ("path", "hash", "processing_status")

#: Columns selected by default when the caller does not choose. Kept smaller
#: than the full set so a first-time export is readable, not just complete.
DEFAULT_COLUMNS: Tuple[str, ...] = (
    "file_name", "file_type", "file_size", "file_date", "file_status",
    "source_name", "side_name", "smart_categories", "analyst_categories",
)

#: Query-definition fields this service accepts. Anything else in the payload
#: is refused, so a client cannot smuggle a result set through an unknown key
#: (`results`, `rows`, `data`, ...) and have it pass unnoticed.
_ACCEPTED = frozenset({
    "query", "export_scope", "analyst_scope", "format", "filename", "page", "per_page",
    "file_type", "source_id", "source_ids", "side_id", "side_ids",
    "category_id", "category_ids", "analyst_category_id",
    "analyst_category_ids", "status", "date_from", "date_to", "sort_by", "sort_order",
    "use_advanced", "use_fulltext", "use_bm25", "use_expansion", "use_fuzzy",
    "case_sensitive", "whole_word", "hide_duplicates", "columns",
    "path_ids", "file_ids",
})

#: Ceiling for an explicit ``selected`` scope id list. Generous enough for a
#: batch of open documents, small enough that a request cannot be used to
#: smuggle an unbounded dump through what is meant to be a bounded selection.
MAX_SELECTED_IDS = 500

#: Keys that mean "here are the rows, please write them out". Named explicitly
#: so the refusal can say what is wrong instead of "unexpected field".
_RESULT_PAYLOAD_KEYS = frozenset({"results", "rows", "records", "items", "data"})

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ExportRequestError(ValueError):
    """The request cannot be honoured, and the caller is told why."""


@dataclass(frozen=True)
class SearchExportRequest:
    """A validated description of what to export."""

    query: str = ""
    scope: str = "filtered"
    format: str = "csv"
    filename: str = ""
    page: int = 1
    per_page: int = 50
    file_types: Tuple[str, ...] = ()
    source_ids: Tuple[int, ...] = ()
    side_ids: Tuple[int, ...] = ()
    category_ids: Tuple[int, ...] = ()
    analyst_category_ids: Tuple[int, ...] = ()
    file_statuses: Optional[Tuple[str, ...]] = None
    analyst_scope: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    sort_by: str = "relevance"
    sort_order: str = "desc"
    use_advanced: bool = True
    use_fulltext: bool = True
    use_bm25: bool = True
    use_expansion: bool = True
    use_fuzzy: bool = True
    case_sensitive: bool = False
    whole_word: bool = False
    hide_duplicates: bool = False
    columns: Tuple[str, ...] = ()
    path_ids: Tuple[int, ...] = ()
    definition: Dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_columns(self) -> Tuple[str, ...]:
        """The columns to publish, in the order asked for.

        An empty selection means "everything" - the full published schema,
        in its stable order - rather than an empty file. A selection is
        never reordered or filtered against anything other than the public
        ``COLUMNS`` tuple, so the file can never contain a column the
        dataset does not have.
        """
        return self.columns or COLUMNS

    @property
    def limit_offset(self) -> Tuple[int, int]:
        """How many rows to fetch in the first round, and from where."""
        if self.scope == "page":
            return self.per_page, (max(self.page, 1) - 1) * self.per_page
        return min(CHUNK, MAX_ROWS), 0

    @property
    def bounded_query(self) -> str:
        """``dataset`` means the query is dropped; the filters are kept.

        The operator asked for "everything my filters allow", which is the one
        scope where the search terms are deliberately not part of the export.
        """
        return "" if self.scope == "dataset" else self.query


@dataclass
class SearchExportResult:
    """What was exported, measured after the fact."""

    request: SearchExportRequest
    rows: List[Dict[str, Any]] = field(default_factory=list)
    total: int = 0
    truncated: bool = False
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    @property
    def exported(self) -> int:
        return len(self.rows)

    @property
    def headers(self) -> Dict[str, str]:
        """What the response says about itself, so the client need not guess."""
        return {
            "X-Export-Scope": self.request.scope,
            "X-Export-Format": self.request.format,
            "X-Export-Rows": str(self.exported),
            "X-Export-Total": str(self.total),
            "X-Export-Truncated": "true" if self.truncated else "false",
            "X-Export-Cap": str(MAX_ROWS),
        }


def _as_ids(value: Any, what: str) -> Tuple[int, ...]:
    if value in (None, "", []):
        return ()
    values: Sequence[Any]
    if isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    out: List[int] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit():
            raise ExportRequestError(f"{what} must be numeric, got {item!r}")
        out.append(int(text))
    return tuple(sorted(set(out)))


def _as_strings(value: Any, what: str) -> Tuple[str, ...]:
    """Validate one or more bounded string filters without losing selections."""
    if value in (None, "", []):
        return ()
    values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    out: List[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 128:
            raise ExportRequestError(f"{what} values must be at most 128 characters.")
        if text not in out:
            out.append(text)
    return tuple(out)


def _as_statuses(payload: Dict[str, Any]) -> Optional[Tuple[str, ...]]:
    """Validate the UI's Read/Unread path status filter."""
    if "status" not in payload:
        return None
    raw = payload.get("status")
    values: Sequence[Any]
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    elif isinstance(raw, str) and "," in raw:
        values = raw.split(",")
    else:
        values = [raw]
    normalized: List[str] = []
    for item in values:
        text = str(item).strip().lower() if item is not None else ""
        if not text:
            continue
        if text == "none":
            if len(values) == 1:
                return ()
            raise ExportRequestError("status 'none' cannot be combined with another status.")
        if text not in ("read", "unread"):
            raise ExportRequestError("status must contain only 'Read', 'Unread', or 'none'.")
        status = text.capitalize()
        if status not in normalized:
            normalized.append(status)
    return tuple(normalized)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_date(value: Any, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not _ISO_DATE.match(text):
        raise ExportRequestError(f"{what} must be YYYY-MM-DD, got {value!r}")
    return text


def parse(payload: Optional[Dict[str, Any]]) -> SearchExportRequest:
    """Validate a request, or explain precisely why it cannot be honoured."""
    if not payload:
        raise ExportRequestError("A JSON body is required.")

    supplied = [key for key in payload if key in _RESULT_PAYLOAD_KEYS]
    if supplied:
        raise ExportRequestError(
            "This endpoint no longer accepts results from the browser "
            f"({', '.join(sorted(supplied))}). Send the query definition - "
            "query, filters, sort, scope, format - and the server will "
            "regenerate the result set itself, so the file cannot contain rows "
            "the database would not return.")

    unknown = sorted(set(payload) - _ACCEPTED)
    if unknown:
        raise ExportRequestError(
            "Unknown export parameters: " + ", ".join(unknown) + ". A query "
            "definition is the only input this endpoint accepts.")

    scope = str(payload.get("export_scope") or "filtered").strip().lower()
    if scope not in EXPORT_SCOPES:
        raise ExportRequestError(
            f"Unknown export_scope {scope!r}. Say which set you mean: "
            + ", ".join(EXPORT_SCOPES) + ".")

    export_format = str(payload.get("format") or "csv").strip().lower()
    if export_format == "xlsx":
        export_format = "excel"
    if export_format not in EXPORT_FORMATS:
        raise ExportRequestError(
            f"Unknown format {export_format!r}. Supported: "
            + ", ".join(EXPORT_FORMATS) + ".")

    try:
        page = max(int(payload.get("page", 1) or 1), 1)
        # Same ceiling as the results page-size control (20/50/100/200/500),
        # so "Current page only" exports exactly the rows on screen.
        per_page = min(max(int(payload.get("per_page", 50) or 50), 1), 500)
    except (TypeError, ValueError):
        raise ExportRequestError("page and per_page must be whole numbers.") from None

    sort_by = str(payload.get("sort_by") or "relevance").strip().lower()
    sort_order = str(payload.get("sort_order") or "desc").strip().lower()
    if sort_by not in {"relevance", "date", "name", "type", "size"}:
        raise ExportRequestError("sort_by must be relevance, date, name, type, or size.")
    if sort_order not in ("asc", "desc"):
        raise ExportRequestError("sort_order must be 'asc' or 'desc'.")

    path_ids: Tuple[int, ...] = ()
    if scope == "selected":
        raw_ids = payload.get("path_ids")
        if raw_ids is None:
            raw_ids = payload.get("file_ids")
        if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
            raise ExportRequestError(
                "export_scope 'selected' requires a non-empty path_ids list - "
                "the explicit id(s) of the document(s) to export.")
        path_ids = _as_ids(raw_ids, "path_ids")
        if len(path_ids) > MAX_SELECTED_IDS:
            raise ExportRequestError(
                f"selected scope accepts at most {MAX_SELECTED_IDS} ids "
                f"({len(path_ids)} given). Use the filtered/dataset scopes for "
                "larger exports.")

    query = str(payload.get("query") or "").strip()
    analyst_scope = payload.get("analyst_scope")
    if analyst_scope is not None:
        analyst_scope = str(analyst_scope).strip().lower()
        if analyst_scope not in {"uncategorized", "categorized", "all"}:
            raise ExportRequestError("analyst_scope must be uncategorized, categorized, or all.")
    file_statuses = _as_statuses(payload)

    raw_columns = payload.get("columns")
    columns: Tuple[str, ...] = ()
    if raw_columns is not None:
        if not isinstance(raw_columns, (list, tuple)):
            raise ExportRequestError("columns must be a list of column names.")
        seen: List[str] = []
        for name in raw_columns:
            name = str(name).strip()
            if name not in COLUMNS:
                raise ExportRequestError(
                    f"Unknown column {name!r}. Available columns: "
                    + ", ".join(COLUMNS) + ".")
            if name not in seen:
                seen.append(name)
        if not seen:
            raise ExportRequestError("columns cannot be an empty list; omit it for all columns.")
        columns = tuple(seen)

    request = SearchExportRequest(
        query=query,
        scope=scope,
        format=export_format,
        filename=re.sub(
            r"\.(?:csv|xlsx?|json)$", "",
            str(payload.get("filename") or "").strip(),
            flags=re.IGNORECASE,
        ),
        page=page,
        per_page=per_page,
        file_types=_as_strings(payload.get("file_type"), "file_type"),
        source_ids=_as_ids(payload.get("source_ids")
                           if payload.get("source_ids") is not None
                           else payload.get("source_id"), "source_id"),
        side_ids=_as_ids(payload.get("side_ids")
                         if payload.get("side_ids") is not None
                         else payload.get("side_id"), "side_id"),
        category_ids=_as_ids(payload.get("category_ids")
                             if payload.get("category_ids") is not None
                             else payload.get("category_id"), "category_id"),
        analyst_category_ids=_as_ids(
            payload.get("analyst_category_ids")
            if payload.get("analyst_category_ids") is not None
            else payload.get("analyst_category_id"), "analyst_category_id"),
        file_statuses=file_statuses,
        analyst_scope=analyst_scope,
        date_from=_as_date(payload.get("date_from"), "date_from"),
        date_to=_as_date(payload.get("date_to"), "date_to"),
        sort_by=sort_by,
        sort_order=sort_order,
        use_advanced=_as_bool(payload.get("use_advanced"), True),
        use_fulltext=_as_bool(payload.get("use_fulltext"), True),
        use_bm25=_as_bool(payload.get("use_bm25"), True),
        use_expansion=_as_bool(payload.get("use_expansion"), True),
        use_fuzzy=_as_bool(payload.get("use_fuzzy"), True),
        case_sensitive=_as_bool(payload.get("case_sensitive"), False),
        whole_word=_as_bool(payload.get("whole_word"), False),
        hide_duplicates=_as_bool(payload.get("hide_duplicates"), False),
        columns=columns,
        path_ids=path_ids,
    )
    # The definition is kept beside the request: it is what the audit line and
    # any future export record need, and it is the whole of what was asked for.
    object.__setattr__(request, "definition", {
        "query": request.bounded_query,
        "scope": request.scope,
        "format": request.format,
        "file_type": list(request.file_types),
        "file_statuses": list(request.file_statuses) if request.file_statuses is not None else None,
        "analyst_scope": request.analyst_scope,
        "source_ids": list(request.source_ids),
        "side_ids": list(request.side_ids),
        "category_ids": list(request.category_ids),
        "analyst_category_ids": list(request.analyst_category_ids),
        "date_from": request.date_from,
        "date_to": request.date_to,
        "sort_by": request.sort_by,
        "sort_order": request.sort_order,
        "use_advanced": request.use_advanced,
        "use_fulltext": request.use_fulltext,
        "use_bm25": request.use_bm25,
        "use_expansion": request.use_expansion,
        "use_fuzzy": request.use_fuzzy,
        "case_sensitive": request.case_sensitive,
        "whole_word": request.whole_word,
        "hide_duplicates": request.hide_duplicates,
        "columns": list(request.resolved_columns),
        "path_ids": list(request.path_ids),
    })
    return request


# ---------------------------------------------------------------------------
# Running the query
# ---------------------------------------------------------------------------
def _search_once(request: SearchExportRequest, limit: int, offset: int,
                 analyst_scope: str):
    """One round of the same search the screen ran."""
    from Api.services.search_service import SearchService

    query = request.bounded_query
    if (request.use_advanced or request.hide_duplicates
            or request.case_sensitive or request.whole_word):
        return SearchService.advanced_search(
            query=query,
            file_type=list(request.file_types) or None,
            source_id=request.source_ids[0] if len(request.source_ids) == 1 else None,
            side_id=request.side_ids[0] if len(request.side_ids) == 1 else None,
            date_from=request.date_from,
            date_to=request.date_to,
            category_id=(request.category_ids[0]
                         if len(request.category_ids) == 1 else None),
            source_ids=list(request.source_ids) or None,
            side_ids=list(request.side_ids) or None,
            category_ids=list(request.category_ids) or None,
            sort_by=request.sort_by,
            sort_order=request.sort_order,
            limit=limit,
            offset=offset,
            use_bm25=request.use_bm25,
            use_expansion=request.use_expansion,
            use_fuzzy=request.use_fuzzy,
            analyst_scope=analyst_scope,
            analyst_category_ids=(list(request.analyst_category_ids) or None),
            file_statuses=(list(request.file_statuses)
                           if request.file_statuses is not None else None),
            hide_duplicates=request.hide_duplicates,
            case_sensitive=request.case_sensitive,
            whole_word=request.whole_word,
        )
    if request.use_fulltext and query:
        return SearchService.full_text_search(
            query=query,
            file_type=request.file_types[0] if len(request.file_types) == 1 else None,
            source_id=request.source_ids[0] if len(request.source_ids) == 1 else None,
            side_id=request.side_ids[0] if len(request.side_ids) == 1 else None,
            date_from=request.date_from,
            date_to=request.date_to,
            category_id=(request.category_ids[0]
                         if len(request.category_ids) == 1 else None),
            sort_by=request.sort_by,
            sort_order=request.sort_order,
            limit=limit,
            offset=offset,
            analyst_scope=analyst_scope,
            hide_duplicates=request.hide_duplicates,
        )
    return SearchService.simple_search(
        query=query, limit=limit, offset=offset, analyst_scope=analyst_scope)


def _lookup_by_ids(path_ids: Sequence[int]) -> List[Dict[str, Any]]:
    """Read the exact rows for an explicit id list - no query, no ranking.

    This is a direct record lookup, the same shape of operation as the
    other explicit-selection routes in this codebase (Api/blueprints/files.py's
    id-list export endpoints): it reads authoritative columns straight from
    ``paths`` and its joins, plus smart and analyst categories from the same
    services the file-detail view already uses, so a single-document export
    can never disagree with what the Content Viewer shows for that document.
    """
    if not path_ids:
        return []

    from Api.utils import execute_query
    from Api.utils.utils import get_categories_by_file
    from Api.services.analyst_categories import AnalystCategoryService

    placeholders = ",".join(["%s"] * len(path_ids))
    rows = execute_query(
        f"""
        SELECT p.id, p.file_name, p.file_type, p.file_size, p.file_date,
               p.file_status, COALESCE(s.name, 'Unknown') AS source_name,
               hc.source_id, COALESCE(si.name, 'Unknown') AS side_name,
               hc.side_id, p.file_path, COALESCE(h.hash, '') AS hash,
               p.processing_status
        FROM paths p
        LEFT JOIN hash_contexts hc ON p.context_id = hc.id
        LEFT JOIN hashs h ON hc.hash_id = h.id
        LEFT JOIN sources s ON hc.source_id = s.id
        LEFT JOIN sides si ON hc.side_id = si.id
        WHERE p.id IN ({placeholders})
        """,  # nosec B608 # IN list of %s placeholders; the ids are bound parameters
        tuple(path_ids), fetch="all",
    ) or []

    by_id = {int(row[0]): row for row in rows}
    analyst_map = AnalystCategoryService.categories_for_files(list(path_ids))

    out: List[Dict[str, Any]] = []
    for path_id in path_ids:
        row = by_id.get(path_id)
        if row is None:
            continue
        try:
            smart_categories = [
                cat.get("name") for cat in (get_categories_by_file(path_id) or [])
                if cat.get("name")
            ]
        except Exception:
            logger.warning("search export: could not load smart categories for %s",
                            path_id, exc_info=True)
            smart_categories = []
        analyst_categories = [
            cat.get("name") for cat in analyst_map.get(path_id, []) if cat.get("name")
        ]
        out.append({
            "id": row[0],
            "file_name": row[1],
            "file_type": row[2],
            "file_size": row[3],
            "file_date": row[4],
            "file_status": row[5],
            "source_name": row[6],
            "source_id": row[7],
            "side_name": row[8],
            "side_id": row[9],
            "relevance_score": None,
            "smart_categories": smart_categories or None,
            "analyst_categories": analyst_categories or None,
            "snippet": None,
            "path": row[10] or "",
            "hash": row[11] or "",
            "processing_status": row[12] or "",
        })
    return out


def normalise(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """Every row in one shape, whatever the search path returned.

    The search service answers with tuples on some paths and dictionaries on
    others. A file that changes shape depending on how the query happened to be
    answered is a file nobody can trust, so the shape is fixed here. Older
    query tuples and dictionaries call the smart taxonomy ``categories``;
    exports publish it as ``smart_categories`` beside analyst categories.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            values = list(row) if isinstance(row, (list, tuple)) else [row]
            item = {
                column: values[index] if index < len(values) else None
                for index, column in enumerate(_RESULT_COLUMNS)
            }

        if "id" not in item:
            item["id"] = item.get("file_id")
        if "snippet" not in item:
            item["snippet"] = item.get("match_snippet") or item.get("line_content")
        if item.get("smart_categories") is None:
            item["smart_categories"] = item.get("categories")
        out.append({column: _clean(item.get(column)) for column in COLUMNS})
    return out


def _clean(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return value


def resolve(request: SearchExportRequest, analyst_scope: str) -> SearchExportResult:
    """Re-run the query the request describes and collect the rows to export."""
    result = SearchExportResult(request=request,
                                started_at=datetime.now().isoformat(timespec="seconds"))

    if request.scope == "selected":
        # An explicit id list, not a query: read the rows directly and stop -
        # there is no "rest of the result set" to page through.
        collected = normalise(_lookup_by_ids(request.path_ids))
        result.total = len(collected)
        result.rows = collected
        result.completed_at = datetime.now().isoformat(timespec="seconds")
        logger.info("search export: scope=selected ids=%s rows=%d",
                    list(request.path_ids), result.exported)
        return result

    limit, offset = request.limit_offset
    rows, total = _search_once(request, limit, offset, analyst_scope)
    collected = normalise(rows)
    result.total = int(total or 0)

    if request.scope != "page":
        # The whole set, in rounds, up to the cap. Nothing is silently cut: the
        # result says whether it was, and by how much.
        while len(collected) < MAX_ROWS and len(collected) < result.total:
            more, _ = _search_once(request, min(CHUNK, MAX_ROWS - len(collected)),
                                   len(collected), analyst_scope)
            if not more:
                break
            collected.extend(normalise(more))
        result.truncated = result.total > len(collected)

    result.rows = collected
    result.completed_at = datetime.now().isoformat(timespec="seconds")
    logger.info(
        "search export: scope=%s format=%s query=%r filters=%s rows=%d total=%d "
        "truncated=%s", request.scope, request.format, request.bounded_query,
        {key: value for key, value in request.definition.items()
         if key.endswith("_ids") and value}, result.exported, result.total,
        result.truncated)
    return result


def criteria_for(request: SearchExportRequest, analyst_scope: Optional[str] = None):
    """The canonical ``Criteria`` this export's matching set corresponds to.

    Used for the disclosure record's ``criteria_fingerprint``: the same
    fingerprint a saved search or monitoring rule with the same conditions
    carries, so the register can answer "which exports disclosed what this
    rule watches?". ``selected`` exports are an explicit id list, not a
    query, and have no criteria (``None``).
    """
    from core.criteria import Criteria, SearchOptions, Sort

    if request.scope == "selected":
        return None
    from core.criteria.model import normalize_analyst_scope

    scope = normalize_analyst_scope(analyst_scope or request.analyst_scope)
    sort_by = request.sort_by if request.sort_by in (
        "relevance", "date", "name", "type", "size") else "relevance"
    return Criteria(
        text=request.bounded_query or None,
        case_sensitive=request.case_sensitive,
        whole_word=request.whole_word,
        categories=tuple(sorted(set(request.category_ids))),
        analyst_categories=tuple(sorted(set(request.analyst_category_ids))),
        analyst_scope=scope,
        sources=tuple(sorted(set(request.source_ids))),
        sides=tuple(sorted(set(request.side_ids))),
        file_types=tuple(sorted(set(request.file_types))),
        file_statuses=(tuple(sorted(set(request.file_statuses)))
                       if request.file_statuses is not None else None),
        date_from=request.date_from,
        date_to=request.date_to,
        hide_duplicates=request.hide_duplicates,
        sort=(Sort(sort_by, "asc" if request.sort_order == "asc" else "desc"),),
        options=SearchOptions(use_fuzzy=request.use_fuzzy,
                              use_expansion=request.use_expansion,
                              use_bm25=request.use_bm25),
    )


def disclosure_detail(result: SearchExportResult, analyst_scope: Optional[str],
                      kind: str = "search_export") -> Dict[str, Any]:
    """Facts about this export for the DATA_EXPORTED audit record."""
    from core.criteria import sha256_hex

    req = result.request
    criteria = None
    try:
        criteria = criteria_for(req, analyst_scope)
    except Exception as exc:  # a definition the criteria model refuses is recorded as such
        logger.warning("export criteria not representable: %s", exc)
    return {
        "kind": kind,
        "scope": req.scope,
        "format": req.format,
        "row_count": result.exported,
        "total": result.total,
        "truncated": result.truncated,
        "cap": MAX_ROWS,
        "criteria_fingerprint": criteria.fingerprint() if criteria else None,
        "query_fingerprint": sha256_hex(req.definition),
        "sources": sorted({int(r.get("source_id")) for r in result.rows
                           if str(r.get("source_id") or "").isdigit()}),
        "columns": list(req.resolved_columns),
        "path_ids": list(req.path_ids) if req.scope == "selected" else None,
    }


def suggested_filename(request: SearchExportRequest) -> str:
    """A stable, harmless name: the export's own description, sanitised."""
    def _safe(value: str, limit: int) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
        # A name is a name: no separators, no traversal, no double dots.
        cleaned = re.sub(r"\.{2,}", ".", cleaned).strip("._-")
        return cleaned[:limit]

    if request.filename:
        # A name supplied by the operator is the name of the export, not a
        # prefix to which the server silently appends a timestamp.
        return _safe(request.filename, 80) or "export"
    if request.scope == "selected":
        ids = "_".join(str(pid) for pid in request.path_ids[:3])
        suffix = "" if len(request.path_ids) <= 3 else f"_plus{len(request.path_ids) - 3}"
        base = f"document_{ids}{suffix}_metadata"
        return f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    stem = _safe(request.query or "all", 40)
    base = f"search_{stem or 'all'}_{request.scope}"
    return f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def export_bytes(result: SearchExportResult):
    """Serialize the same published columns to CSV, Excel, or JSON."""
    from Api.services.document_intelligence import spreadsheet_safe_text

    rows = result.rows
    columns = result.request.resolved_columns
    if result.request.format == "csv":
        import csv
        from io import BytesIO, StringIO

        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: spreadsheet_safe_text(row.get(column, ""))
                             for column in columns})
        return BytesIO(output.getvalue().encode("utf-8-sig")), "text/csv", "csv"

    if result.request.format == "excel":
        from io import BytesIO

        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise ImportError("openpyxl is required for Excel export") from exc

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Search Results"
        sheet.append(list(columns))
        for row in rows:
            sheet.append([spreadsheet_safe_text(row.get(column, ""))
                          for column in columns])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, column in enumerate(columns, start=1):
            width = max(12, min(60, len(column) + 4))
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        output.seek(0)
        return (output,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xlsx")

    from Api.services.export_service import ExportService

    projected = [{column: row.get(column, "") for column in columns} for row in rows]
    return (ExportService.export_search_results_json(projected),
            "application/json", "json")


__all__ = [
    "CHUNK",
    "COLUMNS",
    "COLUMN_LABELS",
    "DEFAULT_COLUMNS",
    "EXPORT_FORMATS",
    "EXPORT_SCOPES",
    "ExportRequestError",
    "MAX_ROWS",
    "SearchExportRequest",
    "SearchExportResult",
    "criteria_for",
    "disclosure_detail",
    "export_bytes",
    "normalise",
    "parse",
    "resolve",
    "suggested_filename",
]
