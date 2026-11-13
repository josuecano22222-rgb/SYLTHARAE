"""Unit: the browsing context behind Previous/Next on the file pages.

The detail page and the reader can step to the neighbouring file. "Neighbour"
is only defined against a set, and that set is the one the library list shows.
These tests pin the two halves that make the two agree:

* the filter validation shared by the list, its counters and the navigation
  controls (so a filter cannot mean one thing on the list and another thing
  when stepping files);
* the ``nav_`` namespace, which keeps the list's ``search`` (file name) from
  colliding with the detail page's ``search`` (text inside the document);
* the keyset predicates that select the adjacent row in the list's order.

The real SQL is exercised against PostgreSQL in
tests/integration/test_file_navigation.py; here the shapes are checked.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from Api.services.file_navigation import (  # noqa: E402
    CONTEXT_PREFIX,
    ORDER_BY,
    PREVIOUS_ORDER_BY,
    FileNavigation,
    Neighbour,
    build_library_filters,
    context_params,
    parse_context,
    _keyset_predicate,
    _order_by_for,
)


# ---------------------------------------------------------------------------
# Filter validation - one implementation for list, counters and navigation
# ---------------------------------------------------------------------------

def test_no_arguments_means_no_filtering():
    filters = build_library_filters({})
    assert filters.filtering is False
    assert filters.where_clause() == ""
    assert filters.params == ()
    assert filters.context == ()


def test_search_filters_on_the_file_name():
    filters = build_library_filters({"search": "  passport  "})
    assert filters.where_parts == ("p.file_name ILIKE %s",)
    assert filters.params == ("%passport%",)
    assert filters.context_dict() == {"search": "passport"}


def test_source_and_side_are_integers():
    filters = build_library_filters({"source": "7", "side": "9"})
    assert filters.where_parts == ("hc.source_id = %s", "hc.side_id = %s")
    assert filters.params == (7, 9)
    assert filters.context_dict() == {"source": "7", "side": "9"}


def test_invalid_source_is_dropped_not_passed_to_the_database():
    filters = build_library_filters({"source": "not-a-number"})
    assert filters.filtering is False
    assert filters.params == ()


def test_status_aliases_match_the_list_form():
    assert build_library_filters({"status": "Read"}).params == ("Read",)
    assert build_library_filters({"status": "Unread"}).params == ("Unread",)
    assert build_library_filters({"status": "Analyzed"}).params == ("Read",)
    assert build_library_filters({"status": "Pending"}).params == ("Unread",)
    # Unknown statuses were always logged and ignored, never applied.
    assert build_library_filters({"status": "Nonsense"}).filtering is False


def test_status_can_be_left_out_for_the_counters():
    filters = build_library_filters({"status": "Read", "file_type": "pdf"},
                                    include_status=False)
    assert filters.where_parts == ("p.file_type = %s",)
    assert filters.params == ("pdf",)


def test_file_type_filter():
    filters = build_library_filters({"file_type": " .jpg "})
    assert filters.where_parts == ("p.file_type = %s",)
    assert filters.params == (".jpg",)


def test_date_range_between_and_open_ended():
    both = build_library_filters({"date_from": "2026-01-01", "date_to": "2026-01-31"})
    assert both.where_parts == ("p.file_date BETWEEN %s AND %s",)
    assert both.params == (date(2026, 1, 1), date(2026, 1, 31))

    from_only = build_library_filters({"date_from": "2026-01-01"})
    assert from_only.where_parts == ("p.file_date >= %s",)

    to_only = build_library_filters({"date_to": "2026-01-31"})
    assert to_only.where_parts == ("p.file_date <= %s",)


def test_reversed_date_range_falls_back_to_the_lower_bound():
    """The list has always treated a reversed range as "from" only."""
    filters = build_library_filters({"date_from": "2026-02-01", "date_to": "2026-01-01"})
    assert filters.where_parts == ("p.file_date >= %s",)
    assert filters.params == (date(2026, 2, 1),)
    # ... and the context it hands to links cannot re-introduce the range.
    assert filters.context_dict() == {"date_from": "2026-02-01"}


def test_unparseable_dates_are_dropped():
    filters = build_library_filters({"date_from": "yesterday", "date_to": "2026-13-40"})
    assert filters.filtering is False


def test_size_range_is_converted_from_megabytes_to_bytes():
    filters = build_library_filters({"size_min": "1", "size_max": "2.5"})
    assert filters.where_parts == ("p.file_size BETWEEN %s AND %s",)
    assert filters.params == (1048576, 2621440)

    upper = build_library_filters({"size_max": "10"})
    assert upper.where_parts == ("p.file_size <= %s",)
    assert upper.params == (10485760,)


def test_reversed_size_range_falls_back_to_the_lower_bound():
    filters = build_library_filters({"size_min": "5", "size_max": "1"})
    assert filters.where_parts == ("p.file_size >= %s",)
    assert filters.params == (5242880,)


def test_filters_combine_in_a_stable_order():
    filters = build_library_filters({
        "search": "report",
        "source": "3",
        "side": "4",
        "status": "Read",
        "file_type": ".pdf",
        "date_from": "2026-01-01",
        "size_min": "1",
    })
    assert filters.where_parts == (
        "p.file_name ILIKE %s",
        "hc.source_id = %s",
        "hc.side_id = %s",
        "p.file_status = %s",
        "p.file_type = %s",
        "p.file_date >= %s",
        "p.file_size >= %s",
    )
    assert filters.params == (
        "%report%", 3, 4, "Read", ".pdf", date(2026, 1, 1), 1048576,
    )


# ---------------------------------------------------------------------------
# The nav_ namespace
# ---------------------------------------------------------------------------

def test_context_reads_only_the_prefixed_namespace():
    """The detail page's own ``search`` must not become a file-name filter.

    On the detail page ``search``/``q`` mean "find this text in the document";
    on the library list ``search`` filters by file name. Reading the context
    from prefixed parameters is what keeps the two apart - a page opened with
    ``?search=passport`` (in-document) must not silently start browsing a
    filtered subset.
    """
    context = parse_context({"search": "passport", "q": "passport", "source": "12"})
    assert context.filtering is False

    context = parse_context({CONTEXT_PREFIX + "search": "passport"})
    assert context.where_parts == ("p.file_name ILIKE %s",)
    assert context.params == ("%passport%",)


def test_context_validates_exactly_like_the_list():
    """A filter the list would ignore is not carried into navigation."""
    context = parse_context({
        CONTEXT_PREFIX + "source": "abc",
        CONTEXT_PREFIX + "status": "Bogus",
        CONTEXT_PREFIX + "date_from": "not-a-date",
    })
    assert context.filtering is False


def test_context_ignores_unknown_prefixed_parameters():
    context = parse_context({CONTEXT_PREFIX + "drop_table": "paths"})
    assert context.filtering is False


def test_context_params_prefix_filters_and_keep_view_preferences():
    filters = build_library_filters({"source": "3", "file_type": ".jpg"})
    params = context_params(filters, {"q": "needle", "per_page": "50000"})
    assert params == {
        "nav_source": "3",
        "nav_file_type": ".jpg",
        "q": "needle",
        "per_page": "50000",
    }


def test_context_params_skip_empty_view_preferences():
    params = context_params(build_library_filters({}), {"q": "", "per_page": None})
    assert params == {}


# ---------------------------------------------------------------------------
# The neighbours: keyset predicates over the list's order
# ---------------------------------------------------------------------------

def test_the_order_is_the_lists_order():
    assert ORDER_BY.startswith("p.date_creation DESC")
    # A tie-break keeps two files ingested in the same second from swapping
    # places between two requests, which would make Next skip or repeat.
    assert "p.id DESC" in ORDER_BY
    # An undated file is not the newest file in the library.
    assert "NULLS LAST" in ORDER_BY


def test_previous_of_a_dated_file_looks_for_newer_rows():
    sql, params = _keyset_predicate("previous", (datetime(2026, 5, 1, 12, 0), 42))
    assert "p.date_creation > %s" in sql
    assert "p.date_creation = %s AND p.id > %s" in sql
    assert params == (datetime(2026, 5, 1, 12, 0), datetime(2026, 5, 1, 12, 0), 42)


def test_next_of_a_dated_file_looks_for_older_rows_and_undated_ones():
    sql, params = _keyset_predicate("next", (datetime(2026, 5, 1, 12, 0), 42))
    assert "p.date_creation < %s" in sql
    assert "p.id < %s" in sql
    # Undated rows sort last in ORDER_BY, so they follow every dated row.
    assert "p.date_creation IS NULL" in sql
    assert params == (datetime(2026, 5, 1, 12, 0), datetime(2026, 5, 1, 12, 0), 42)


def test_an_undated_file_precedes_nothing_but_the_undated_after_it():
    sql, params = _keyset_predicate("previous", (None, 7))
    # Dated rows come before it, and so do undated rows with a larger id.
    assert "p.date_creation IS NOT NULL" in sql
    assert "p.id > %s" in sql
    assert params == (7,)
    # After it: only undated rows with a smaller id.
    sql, params = _keyset_predicate("next", (None, 7))
    assert sql == "(p.date_creation IS NULL AND p.id < %s)"
    assert params == (7,)


def test_the_closest_neighbour_is_taken_from_each_direction():
    """Previous walks *backwards*; Next walks forwards.

    Both predicates describe the same set of rows, but taking "one row" only
    means "the neighbouring file" if it is the closest one: ordering the
    before-set by the list's order and taking the first row returns the first
    file of the whole view, so Previous from the last file would jump to the
    top of the list instead of the file just above it.
    """
    assert _order_by_for("next") == ORDER_BY
    assert _order_by_for("previous") == PREVIOUS_ORDER_BY
    assert PREVIOUS_ORDER_BY != ORDER_BY
    # Reversed on both keys, and undated rows first when walking backwards
    # because the list puts them last.
    assert PREVIOUS_ORDER_BY.startswith("p.date_creation ASC")
    assert "p.id ASC" in PREVIOUS_ORDER_BY
    assert "NULLS FIRST" in PREVIOUS_ORDER_BY


def test_unknown_direction_is_a_programming_error():
    with pytest.raises(ValueError):
        _keyset_predicate("sideways", (None, 1))


# ---------------------------------------------------------------------------
# The rendered state
# ---------------------------------------------------------------------------

def test_navigation_state_reports_what_the_control_needs():
    nav = FileNavigation(
        file_id=5,
        endpoint="files.file_detail",
        previous=Neighbour(id=4, file_name="a.txt"),
        next=Neighbour(id=6, file_name="b.txt"),
        position=3,
        total=9,
    )
    assert nav.has_previous and nav.has_next
    assert nav.caption == "3 / 9"
    assert nav.filtered is False


def test_a_single_file_in_the_view_is_not_navigable():
    nav = FileNavigation(file_id=5, endpoint="files.file_detail", position=1, total=1)
    assert nav.navigable is False
    assert nav.previous_url is None and nav.next_url is None


@pytest.fixture()
def url_calls():
    """A request context plus a recorder around ``url_for``.

    FileNavigation builds real links (so the templates stay dumb), which
    means the assertions below can check both the href and the parameters
    that went into it.
    """
    from flask import Flask, url_for as real_url_for
    import Api.services.file_navigation as module

    app = Flask(__name__)
    app.add_url_rule("/file/<int:file_id>", endpoint="files.file_detail",
                     view_func=lambda file_id: "")
    app.add_url_rule("/file/<int:file_id>/full-content",
                     endpoint="files.file_full_content", view_func=lambda file_id: "")

    calls = []

    def spy(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return real_url_for(endpoint, **kwargs)

    with app.test_request_context():
        module.url_for = spy
        try:
            yield calls
        finally:
            module.url_for = real_url_for


def test_links_drop_page_but_keep_the_context(url_calls):
    """Every file starts on its first page; the view travels along."""
    nav = FileNavigation(
        file_id=5,
        endpoint="files.file_detail",
        next=Neighbour(id=6, file_name="b.txt"),
        link_params={
            "nav_source": "3",
            "nav_status": "Read",
            "q": "needle",
            "page": "4",
        },
    )
    url = nav.next_url
    endpoint, kwargs = url_calls[-1]
    assert endpoint == "files.file_detail"
    assert kwargs == {
        "file_id": 6,
        "nav_source": "3",
        "nav_status": "Read",
        "q": "needle",
    }
    assert url == "/file/6?nav_source=3&nav_status=Read&q=needle"


def test_cross_page_links_keep_the_context_too(url_calls):
    nav = FileNavigation(
        file_id=5,
        endpoint="files.file_full_content",
        link_params={"nav_source": "3", "per_page": "1000"},
    )
    assert nav.details_url == "/file/5?nav_source=3&per_page=1000"
    assert nav.reader_url == "/file/5/full-content?nav_source=3&per_page=1000"
