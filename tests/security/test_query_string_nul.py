"""A NUL byte in the request target is refused, not passed to the driver.

``?q=a%00b`` reached PostgreSQL and psycopg2 raised while the statement was
being built ("A string literal cannot contain NUL"), which answered 500 for a
search. A NUL cannot be meant in a URL, so it is refused once at the request
boundary with 400 ``invalid_request_target`` - including when it arrives
percent-encoded, which is how a browser sends it.
"""

import pytest

NUL_ENDPOINTS = [
    "/api/input/sources",
    "/api/input/sides",
    "/api/analytics/search-files",
]


@pytest.mark.parametrize("path", NUL_ENDPOINTS)
def test_a_nul_in_the_query_string_is_refused(app, client_factory, path):
    client = client_factory("admin")
    response = client.get(path, query_string={"q": "a\x00b"})
    assert response.status_code == 400, (
        f"GET {path}?q=a%00b answered {response.status_code}: "
        f"{response.get_data(as_text=True)[:200]}")
    assert (response.get_json() or {}).get("code") == "invalid_request_target"


def test_a_normal_query_string_is_untouched(app, client_factory):
    client = client_factory("admin")
    response = client.get("/api/input/sources", query_string={"q": "news"})
    assert response.status_code == 200, response.get_data(as_text=True)


def test_query_text_drops_a_nul_and_is_bounded():
    from core.validation import query_text

    assert query_text("q", args={"q": "a\x00b"}) == "ab"
    assert query_text("q", max_length=5, args={"q": "abcdefgh"}) == "abcde"
    assert query_text("q", args={}) == ""
