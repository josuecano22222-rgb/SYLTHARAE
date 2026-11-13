"""Previous/Next navigation for the per-file pages (File Detail, Reader).

Why this module exists
----------------------
The detail page and the full-content reader can each show exactly one file,
and until now there was no way to step from one file to the next: an operator
reviewing a folder of attachments had to go back to the library list, find
their place again, and open the following row. Those two pages now carry a
Previous/Next control, and this module provides its two inputs: *which* file
comes next and *which browsing context* the operator is in.

The browsed set
---------------
"Next file" is only meaningful relative to a set. The set is the same one the
library list shows: the whole library, or the library narrowed by the list's
filters (file-name search, source, side, status, file type, date range, size
range). Those filters arrive as ``nav_<field>`` query parameters.

The ``nav_`` prefix is not decoration. On the detail page ``search`` and ``q``
already mean *find this text inside the document* and drive the in-document
search box and highlighter, while on the library list ``search`` filters by
*file name*. Carrying one page's filter into the other under the same name
would silently change what the search box does, so the browsing context is
namespaced instead of shared.

Ordering
--------
The neighbours come from the same ORDER BY the library list uses
(:data:`ORDER_BY`). Both call sites read that constant, so the sequence the
operator walks with Previous/Next is exactly the sequence the list displayed -
including for files sharing a creation date, which the tie-break on ``p.id``
keeps stable between two requests.

Implementation
--------------
Neighbours are found with a keyset (seek) query - one row either side of the
current file, ``ORDER BY ... LIMIT 1`` - never by loading the list and
searching in Python: the library is expected to hold millions of files and
this code runs on every detail page view. The position counter uses a single
aggregate over the same filtered set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Optional

from flask import url_for

logger = logging.getLogger(__name__)

__all__ = [
    "CONTEXT_PREFIX",
    "ORDER_BY",
    "PREVIOUS_ORDER_BY",
    "LibraryFilters",
    "Neighbour",
    "FileNavigation",
    "build_library_filters",
    "parse_context",
    "navigation_for",
    "context_params",
]

#: Query-string prefix for the browsing context (see the module docstring).
CONTEXT_PREFIX = "nav_"

#: The library-list filters that define the browsed set, in the order they are
#: applied. Names match the parameters the library list itself accepts.
CONTEXT_FIELDS = (
    "search",
    "source",
    "side",
    "status",
    "file_type",
    "date_from",
    "date_to",
    "size_min",
    "size_max",
)

#: Parameters that describe how the *current file* is displayed rather than
#: which files are browsed. They travel with Previous/Next so a reader does
#: not lose the in-document search term or the page size when stepping files.
VIEW_FIELDS = ("q", "search", "case_sensitive", "whole_word", "per_page")

#: Ascending positions of a status filter as the list accepts them.
_STATUS_ALIASES = {"Analyzed": "Read", "Pending": "Unread"}
_VALID_STATUSES = ("Read", "Unread", "Analyzed", "Pending")

#: One row either side of the current file; see the module docstring.
ORDER_BY = "p.date_creation DESC NULLS LAST, p.id DESC"

#: The neighbour *before* the current file is the closest one, which in the
#: list's order is the last row of that direction - so it is selected with the
#: order reversed, not with :data:`ORDER_BY`. (Ordering the "before" set by
#: ORDER_BY and taking one row returns the *first* file of the view, i.e. the
#: Previous button would jump to the top of the list.) Undated rows sort last
#: in the list, so they are the first ones encountered walking backwards.
PREVIOUS_ORDER_BY = "p.date_creation ASC NULLS FIRST, p.id ASC"

#: Joins needed by the filter columns (source/side live on the hash record).
LIBRARY_JOINS = (
    "LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id",
    "LEFT JOIN sources s ON hc.source_id = s.id",
    "LEFT JOIN sides si ON hc.side_id = si.id",
)


@dataclass(frozen=True)
class LibraryFilters:
    """A validated, parameterised WHERE clause for the browsed set.

    ``where_parts``/``params`` are ready to interpolate into a query;
    ``context`` is the same filter expressed as query-string values, so links
    can carry exactly the filters that were applied (already-normalised: an
    out-of-range date never comes back as a filter that was silently ignored).
    """

    where_parts: tuple[str, ...] = ()
    params: tuple[Any, ...] = ()
    context: tuple[tuple[str, str], ...] = ()

    @property
    def filtering(self) -> bool:
        return bool(self.where_parts)

    def where_clause(self, prefix: str = "WHERE ") -> str:
        """``WHERE a = %s AND b = %s``, or ``""`` when nothing is filtered."""
        if not self.where_parts:
            return ""
        return prefix + " AND ".join(self.where_parts)

    def context_dict(self) -> dict[str, str]:
        return dict(self.context)


def _coerce_date(value: Any) -> Optional[date]:
    """``YYYY-MM-DD`` -> date, or None when the value is unusable."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _coerce_megabytes(value: Any) -> Optional[int]:
    """A size filter given in MB (as the list form sends it) -> bytes."""
    if value in (None, ""):
        return None
    try:
        return int(float(value) * 1024 * 1024)
    except (ValueError, TypeError):
        return None


def build_library_filters(
    args: Mapping[str, Any], *, include_status: bool = True
) -> LibraryFilters:
    """Validate library filters and render them as SQL + context values.

    This is the single implementation of "what the library list is filtering
    on". The list route, its statistics counters and the navigation controls
    all call it, so a filter can never mean one thing on the list and another
    thing for Previous/Next. Values that cannot be honoured are logged and
    dropped, exactly as the list has always done, rather than being passed to
    the database.

    Args:
        args: mapping of filter name -> raw value (request args or ``nav_*``).
        include_status: whether the status filter participates. The list's
            statistics counters ask for it to be left out because they append
            their own status condition.
    """
    parts: list[str] = []
    params: list[Any] = []
    context: list[tuple[str, str]] = []

    search = (args.get("search") or "").strip()
    if search:
        parts.append("p.file_name ILIKE %s")
        params.append(f"%{search}%")
        context.append(("search", search))

    for field_name, column in (("source", "hc.source_id"), ("side", "hc.side_id")):
        raw = args.get(field_name)
        if raw in (None, ""):
            continue
        try:
            value = int(raw)
        except (ValueError, TypeError):
            logger.warning("Invalid %s_filter: %s", field_name, raw)
            continue
        parts.append(f"{column} = %s")
        params.append(value)
        context.append((field_name, str(value)))

    status_filter = (args.get("status") or "").strip()
    if include_status and status_filter:
        if status_filter in _VALID_STATUSES:
            db_status = _STATUS_ALIASES.get(status_filter, status_filter)
            parts.append("p.file_status = %s")
            params.append(db_status)
            context.append(("status", status_filter))
        else:
            logger.warning("Invalid status_filter: %s", status_filter)

    # A file type may be one value or a comma-separated set - the column
    # filter's "show only the chosen formats". Each value must be exact; an
    # empty segment is dropped, and a set filters with ANY, not LIKE.
    type_values = [v.strip() for v in (args.get("file_type") or "").split(",")
                   if v.strip()]
    if len(type_values) == 1:
        parts.append("p.file_type = %s")
        params.append(type_values[0])
        context.append(("file_type", type_values[0]))
    elif type_values:
        parts.append("p.file_type = ANY(%s)")
        params.append(type_values)
        context.append(("file_type", ",".join(type_values)))

    # The column filter's "hide the chosen formats": the same values, negated.
    excluded = [v.strip() for v in (args.get("exclude_file_type") or "").split(",")
                if v.strip()]
    if excluded:
        parts.append("COALESCE(p.file_type, '') <> ALL(%s)")
        params.append(excluded)
        context.append(("exclude_file_type", ",".join(excluded)))

    date_from = _coerce_date(args.get("date_from"))
    date_to = _coerce_date(args.get("date_to"))
    if date_from and date_to and date_from > date_to:
        # The list has always treated a reversed range as "from" only.
        logger.warning(
            "Invalid date range: date_from (%s) > date_to (%s)", date_from, date_to
        )
        date_to = None
    if date_from and date_to:
        parts.append("p.file_date BETWEEN %s AND %s")
        params.extend([date_from, date_to])
        context.extend([("date_from", date_from.isoformat()),
                        ("date_to", date_to.isoformat())])
    elif date_from:
        parts.append("p.file_date >= %s")
        params.append(date_from)
        context.append(("date_from", date_from.isoformat()))
    elif date_to:
        parts.append("p.file_date <= %s")
        params.append(date_to)
        context.append(("date_to", date_to.isoformat()))

    size_min = _coerce_megabytes(args.get("size_min"))
    size_max = _coerce_megabytes(args.get("size_max"))
    if size_min is not None and size_max is not None and size_min > size_max:
        logger.warning(
            "Invalid size range: size_min (%s MB) > size_max (%s MB)",
            args.get("size_min"), args.get("size_max"),
        )
        size_max = None
    if size_min is not None and size_max is not None:
        parts.append("p.file_size BETWEEN %s AND %s")
        params.extend([size_min, size_max])
        context.extend([("size_min", str(args.get("size_min")).strip()),
                        ("size_max", str(args.get("size_max")).strip())])
    elif size_min is not None:
        parts.append("p.file_size >= %s")
        params.append(size_min)
        context.append(("size_min", str(args.get("size_min")).strip()))
    elif size_max is not None:
        parts.append("p.file_size <= %s")
        params.append(size_max)
        context.append(("size_max", str(args.get("size_max")).strip()))

    return LibraryFilters(tuple(parts), tuple(params), tuple(context))


def parse_context(args: Mapping[str, Any]) -> LibraryFilters:
    """Read the ``nav_*`` browsing context out of a request's arguments."""
    prefixed = {}
    for name in CONTEXT_FIELDS:
        value = args.get(CONTEXT_PREFIX + name)
        if value not in (None, ""):
            prefixed[name] = value
    return build_library_filters(prefixed)


def context_params(filters: LibraryFilters, extra: Optional[Mapping[str, str]] = None
                   ) -> dict[str, str]:
    """Turn a validated filter set into ``nav_*`` query parameters.

    ``extra`` carries page-level view preferences (the in-document search
    term, page size) that should survive a jump as well.
    """
    params = {CONTEXT_PREFIX + name: value for name, value in filters.context}
    if extra:
        for name, value in extra.items():
            if value not in (None, ""):
                params[name] = str(value)
    return params


def _view_params(args: Mapping[str, Any]) -> dict[str, str]:
    """Page-level display preferences from the current request."""
    view: dict[str, str] = {}
    for name in VIEW_FIELDS:
        value = args.get(name)
        if value not in (None, ""):
            view[name] = str(value)
    return view


def _order_by_for(direction: str) -> str:
    """The order in which to take the *closest* row of the given direction."""
    return PREVIOUS_ORDER_BY if direction == "previous" else ORDER_BY


def _keyset_predicate(direction: str, row: tuple[Optional[datetime], int]
                      ) -> tuple[str, tuple[Any, ...]]:
    """SQL for the rows *before* or *after* ``row`` in :data:`ORDER_BY`.

    ``ORDER_BY`` sorts on ``(date_creation DESC, id DESC)`` with undated rows
    last, so for a dated file "later in the list" means an older date, or the
    same date and a smaller id; an undated file precedes every dated file.
    Both predicates compare plain columns, which keeps an index on
    ``(date_creation, id)`` usable.
    """
    current_date, current_id = row

    if direction == "previous":
        if current_date is None:
            # Undated files sort last: everything with a date is before them.
            return (
                "(p.date_creation IS NOT NULL"
                " OR (p.date_creation IS NULL AND p.id > %s))",
                (current_id,),
            )
        return (
            "((p.date_creation > %s)"
            " OR (p.date_creation = %s AND p.id > %s))",
            (current_date, current_date, current_id),
        )

    if direction == "next":
        if current_date is None:
            return ("(p.date_creation IS NULL AND p.id < %s)", (current_id,))
        return (
            "((p.date_creation < %s)"
            " OR (p.date_creation = %s AND p.id < %s)"
            " OR p.date_creation IS NULL)",
            (current_date, current_date, current_id),
        )

    raise ValueError(f"unknown navigation direction: {direction!r}")


@dataclass(frozen=True)
class Neighbour:
    """The file on the other side of a Previous/Next button."""

    id: int
    file_name: str
    file_type: Optional[str] = None
    file_status: Optional[str] = None
    date_creation: Optional[datetime] = None


@dataclass(frozen=True)
class FileNavigation:
    """Everything the Previous/Next control needs to render."""

    file_id: int
    endpoint: str
    context: LibraryFilters = field(default_factory=LibraryFilters)
    previous: Optional[Neighbour] = None
    next: Optional[Neighbour] = None
    position: Optional[int] = None
    total: Optional[int] = None
    in_view: bool = True
    link_params: Mapping[str, str] = field(default_factory=dict)

    # -- state -------------------------------------------------------------
    @property
    def has_previous(self) -> bool:
        return self.previous is not None

    @property
    def has_next(self) -> bool:
        return self.next is not None

    @property
    def filtered(self) -> bool:
        return self.context.filtering

    @property
    def navigable(self) -> bool:
        """False when the browsed set holds a single file (or the file alone)."""
        return self.has_previous or self.has_next

    # -- rendering helpers -------------------------------------------------
    @property
    def caption(self) -> str:
        """Position line, e.g. ``File 3 of 128 in the filtered view``."""
        if self.position is None or self.total is None:
            return ""
        return f"{self.position} / {self.total}"

    def url(self, endpoint: str, file_id: int, **extra: Any) -> str:
        """A link to ``file_id`` that keeps the browsing context."""
        params = dict(self.link_params)
        params.pop("page", None)  # every file starts at its first page
        params.update({k: v for k, v in extra.items() if v not in (None, "")})
        return url_for(endpoint, file_id=file_id, **params)

    @property
    def previous_url(self) -> Optional[str]:
        if self.previous is None:
            return None
        return self.url(self.endpoint, self.previous.id)

    @property
    def next_url(self) -> Optional[str]:
        if self.next is None:
            return None
        return self.url(self.endpoint, self.next.id)

    @property
    def details_url(self) -> str:
        return self.url("files.file_detail", self.file_id)

    @property
    def reader_url(self) -> str:
        return self.url("files.file_full_content", self.file_id)


def _fetch_neighbour(direction: str, current_row, filters: LibraryFilters
                     ) -> Optional[Neighbour]:
    """The adjacent file in the browsed set, or None at the boundary."""
    from Api.utils import execute_query

    predicate, predicate_params = _keyset_predicate(direction, current_row)
    where = filters.where_clause()
    query = f"""
        SELECT p.id, p.file_name, p.file_type, p.file_status, p.date_creation
        FROM paths p
        {' '.join(LIBRARY_JOINS)}
        {where}
        {'AND' if where else 'WHERE'} {predicate}
        ORDER BY {_order_by_for(direction)}
        LIMIT 1
    """
    params = list(filters.params) + list(predicate_params)
    rows = execute_query(query, tuple(params))
    if not rows:
        return None
    row = rows[0]
    return Neighbour(
        id=row[0],
        file_name=row[1],
        file_type=row[2],
        file_status=row[3],
        date_creation=row[4],
    )


def _fetch_position(current_row, filters: LibraryFilters, *, in_view: bool
                    ) -> tuple[Optional[int], Optional[int]]:
    """``(position, total)`` of the current file inside the browsed set.

    One aggregate: how many rows the set holds, and how many of them sort
    before the current file. Nothing is materialised in Python.
    """
    from Api.utils import execute_query

    where = filters.where_clause()
    if in_view:
        before_predicate, before_params = _keyset_predicate("previous", current_row)
        query = f"""
            SELECT COUNT(*),
                   COALESCE(SUM(CASE WHEN {before_predicate} THEN 1 ELSE 0 END), 0)
            FROM paths p
            {' '.join(LIBRARY_JOINS)}
            {where}
        """
        # The CASE expression sits in the SELECT list, so its placeholders are
        # bound before the WHERE clause's - order matters for positional args.
        params = list(before_params) + list(filters.params)
    else:
        query = f"""
            SELECT COUNT(*), NULL
            FROM paths p
            {' '.join(LIBRARY_JOINS)}
            {where}
        """
        params = list(filters.params)

    row = execute_query(query, tuple(params), fetch="one")
    if not row:
        return None, None
    total = row[0]
    if not in_view:
        return None, total
    return int(row[1]) + 1, total


def _in_view(file_id: int, filters: LibraryFilters) -> bool:
    """Is the current file part of the filtered set?"""
    from Api.utils import execute_query

    if not filters.filtering:
        return True
    where = filters.where_clause()
    query = f"""
        SELECT 1 FROM paths p
        {' '.join(LIBRARY_JOINS)}
        {where}
        {'AND' if where else 'WHERE'} p.id = %s
        LIMIT 1
    """
    params = list(filters.params) + [file_id]
    return bool(execute_query(query, tuple(params)))


def navigation_for(file_id: int, args: Mapping[str, Any], endpoint: str
                   ) -> FileNavigation:
    """Resolve the browsing context, its neighbours and the current position.

    Args:
        file_id: the file being displayed.
        args: the current request's arguments (``nav_*`` filters plus view
            preferences).
        endpoint: the route to link to for Previous/Next - the caller's own
            endpoint, so the operator never bounces between two surfaces.
    """
    from Api.utils import execute_query

    context = parse_context(args)
    view = _view_params(args)
    link_params = {CONTEXT_PREFIX + name: value for name, value in context.context}
    link_params.update(view)

    row = execute_query(
        "SELECT p.date_creation, p.id FROM paths p WHERE p.id = %s",
        (file_id,),
        fetch="one",
    )
    if not row:
        # The caller has already established that the file exists; treat a
        # missing row as "nothing to navigate from" rather than raising.
        return FileNavigation(file_id=file_id, endpoint=endpoint, context=context)

    current_row = (row[0], row[1])
    in_view = _in_view(file_id, context)
    position, total = _fetch_position(current_row, context, in_view=in_view)

    nav = FileNavigation(
        file_id=file_id,
        endpoint=endpoint,
        context=context,
        previous=_fetch_neighbour("previous", current_row, context),
        next=_fetch_neighbour("next", current_row, context),
        position=position,
        total=total,
        in_view=in_view,
        link_params=link_params,
    )
    logger.debug(
        "Navigation for file %s: previous=%s next=%s position=%s/%s filters=%s",
        file_id,
        nav.previous.id if nav.previous else None,
        nav.next.id if nav.next else None,
        nav.position,
        nav.total,
        dict(context.context),
    )
    return nav
