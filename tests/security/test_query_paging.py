"""Pagination values a browser can produce must stay inside PostgreSQL's types.

``?page=99999999999999999999`` becomes ``(page - 1) * size`` and that OFFSET is
past bigint; the driver refused the statement and the view answered 500. A
page that cannot exist is simply past the end of the list, and a negative
offset is not a page at all.
"""

import pytest

HUGE_PAGE = "99999999999999999999"        # past 2**63, PostgreSQL's bigint

PAGED_ENDPOINTS = [
    "/files",
    "/files/types/pdf/documents",
    "/api/paths",
    "/api/words/search",
    "/api/notifications/paginated",
    "/api/lexis/search",
    "/api/analyst/categorizations",
    "/api/analyst/log",
]


@pytest.mark.parametrize("path", PAGED_ENDPOINTS)
def test_an_impossible_page_number_does_not_break_the_statement(
        app, client_factory, path):
    client = client_factory("admin")
    response = client.get(path, query_string={"page": HUGE_PAGE})
    assert response.status_code < 500, (
        f"GET {path}?page={HUGE_PAGE} answered {response.status_code}: "
        f"{response.get_data(as_text=True)[:200]}")


@pytest.mark.parametrize("path", ["/api/paths", "/files"])
def test_a_negative_offset_is_clamped_not_passed_on(app, client_factory, path):
    client = client_factory("admin")
    response = client.get(path, query_string={"offset": "-5", "limit": "10"})
    assert response.status_code < 500, (
        f"GET {path}?offset=-5 answered {response.status_code}: "
        f"{response.get_data(as_text=True)[:200]}")


def test_the_page_clamp_is_bounded():
    from core.table_paging import MAX_PAGE, clamp_page

    assert clamp_page("abc") == 1
    assert clamp_page(-9) == 1
    assert clamp_page("7") == 7
    assert clamp_page(HUGE_PAGE) == MAX_PAGE
    assert clamp_page(10 ** 30) == MAX_PAGE
