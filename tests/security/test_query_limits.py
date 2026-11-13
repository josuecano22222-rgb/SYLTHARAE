"""A limit that is not a number is answered, not crashed on.

``int(request.args.get("limit", 50))`` raises ``ValueError`` for
``?limit=abc``, and seven list endpoints answered 500 for a URL the browser
allows. ``core.validation.query.query_int`` reads the value and falls back to
the default, clamped to the endpoint's own bounds.
"""

import pytest

UNPARSABLE_LIMIT_ENDPOINTS = [
    "/api/experience/contracts",
    "/api/experience/actions",
    "/api/import/jobs",
    "/api/input/jobs",
    "/api/performance/metrics",
    "/api/search/autocomplete",
    "/api/search/suggestions",
    "/api/search/history",
]


@pytest.mark.parametrize("path", UNPARSABLE_LIMIT_ENDPOINTS)
def test_an_unparsable_limit_falls_back_to_the_default(app, client_factory, path):
    client = client_factory("admin")
    response = client.get(path, query_string={"limit": "abc"})
    assert response.status_code < 500, (
        f"GET {path}?limit=abc answered {response.status_code}: "
        f"{response.get_data(as_text=True)[:200]}")


def test_query_int_is_bounded_and_never_raises():
    from core.validation import query_int

    assert query_int("limit", 50, minimum=1, maximum=200, args={}) == 50
    assert query_int("limit", 50, minimum=1, maximum=200,
                     args={"limit": "abc"}) == 50
    assert query_int("limit", 50, minimum=1, maximum=200,
                     args={"limit": "9999"}) == 200
    assert query_int("page", 1, minimum=1, args={"page": "-7"}) == 1
