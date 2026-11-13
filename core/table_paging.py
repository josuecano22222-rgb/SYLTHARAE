"""One pagination model for every server-paged table.

The model (the same for every list page, the Show More batch included)::

    size     the Per Page value. It is the only row limit. A Show More batch is
             exactly one page of this size, so the two can never disagree.
    page     the page the view is on. A full page request shows that page; a
             Show More request appends the next one. The pager points at the
             last page loaded.
    total    the rows the current filters and sort produce. total_pages is
             derived from it: max(1, ceil(total / size)).

Rules that follow from the model:

* A page navigation, a size change, or a filter/sort change replaces the view
  with one page (filters and sort reset to page 1).
* A full-page request for a page past the end is clamped to the last page. The
  filters may have shrunk the list since the link was made; the page shown is
  then the one the pager says it is.
* A Show More batch past the end returns NO rows. Clamping a batch would
  append the last page a second time, which is a duplicate. An empty batch is
  how the table learns there is nothing more to load.

Routes call :func:`fetch_page` with a function that returns ``(rows, total)``
for one page; nothing else decides page numbers, sizes or boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Tuple

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 200
# The header the table engine sends with a Show More batch.
BATCH_HEADER = "X-Requested-With"
BATCH_VALUE = "fetch"


def clamp_size(value: Any, default: int = DEFAULT_PAGE_SIZE) -> int:
    """A usable page size: the default when the value is missing or not a
    number, otherwise bounded to [1, MAX_PAGE_SIZE]."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(size, MAX_PAGE_SIZE))


#: The farthest page the pager will look at. The page is normally clamped to
#: the real page count, but the first query still computes ``(page - 1) * size``
#: as an OFFSET, and ``?page=99999999999999999999`` made PostgreSQL raise
#: "bigint out of range" - a 500 for a page that simply does not exist. A
#: million pages of a thousand rows is a billion rows, far past any real list,
#: and keeps the arithmetic inside the type.
MAX_PAGE = 1_000_000


def clamp_page(value: Any) -> int:
    """A usable page number: between 1 and :data:`MAX_PAGE`. Anything past the
    end of the list itself is handled by :func:`fetch_page`, which needs the
    total to know what the end is."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return min(max(1, page), MAX_PAGE)


def total_pages_for(total: int, size: int) -> int:
    """Pages for ``total`` rows at ``size`` rows a page. An empty list is one
    (empty) page, so the pager always has a page to show."""
    if total <= 0:
        return 1
    return (total + size - 1) // size


@dataclass(frozen=True)
class PageRequest:
    page: int
    size: int
    is_batch: bool


def read_page_request(
    args: Mapping[str, Any],
    headers: Mapping[str, Any] | None = None,
    *,
    page_key: str = "page",
    size_key: str = "per_page",
    default_size: int = DEFAULT_PAGE_SIZE,
) -> PageRequest:
    """Read page and size from a request. ``is_batch`` is true when the table
    engine asked for this page as a Show More batch."""
    is_batch = bool(headers) and str(headers.get(BATCH_HEADER, "")).lower() == BATCH_VALUE
    return PageRequest(
        page=clamp_page(args.get(page_key, 1)),
        size=clamp_size(args.get(size_key, default_size), default_size),
        is_batch=is_batch,
    )


@dataclass(frozen=True)
class PageResult:
    rows: List[Any]
    total: int
    page: int          # the page actually shown (after clamping)
    size: int
    total_pages: int
    past_end: bool     # a batch asked for a page beyond the end: no rows


# Enough attempts to survive the list shrinking between the count and the
# page query; past that the last answer is used as it is.
_MAX_ATTEMPTS = 3


def fetch_page(
    fetch: Callable[[int, int], Tuple[List[Any], int]],
    request: PageRequest,
) -> PageResult:
    """Run ``fetch(page, size) -> (rows, total)`` under the model above."""
    page = request.page
    rows: List[Any] = []
    total = 0
    for _attempt in range(_MAX_ATTEMPTS):
        rows, total = fetch(page, request.size)
        pages = total_pages_for(total, request.size)
        if page <= pages:
            return PageResult(list(rows or []), total, page, request.size, pages, False)
        if request.is_batch:
            # Nothing past the end may be appended: no rows, the table stops.
            return PageResult([], total, page, request.size, pages, True)
        page = pages  # a full page request: show the last page that exists
    pages = total_pages_for(total, request.size)
    return PageResult(list(rows or []), total, min(page, pages), request.size, pages, False)
