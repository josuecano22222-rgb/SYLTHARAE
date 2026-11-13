"""The cache statistics endpoints report statistics, not the SQL they cache.

The query cache is keyed by the SQL text it caches, and
``/api/performance/cache/stats`` returned the first ten keys verbatim: table
names, column names and filter literals, to any reader of the endpoint. They
are reported as short digests now.
"""

import pytest

CACHE_ENDPOINTS = ["/api/performance/cache/stats", "/api/cache/stats"]

SECRET_SQL = ("SELECT secret_note FROM hidden_table WHERE token = 'abc'",
              "INSERT INTO hidden_table (a) VALUES (1)")


def _prime_cache_with(*statements):
    from Api.utils import get_query_cache
    cache = get_query_cache()
    for statement in statements:
        cache.set(statement, {"rows": 1})


@pytest.mark.parametrize("path", CACHE_ENDPOINTS)
def test_cache_statistics_do_not_disclose_the_cached_sql(
        app, client_factory, path):
    _prime_cache_with(*SECRET_SQL)
    client = client_factory("admin")
    response = client.get(path)
    body = response.get_data(as_text=True)

    assert response.status_code == 200, body[:300]
    for statement in SECRET_SQL:
        assert statement not in body, f"GET {path} echoed cached SQL: {body[:300]}"
    for fragment in ("SELECT secret_note", "hidden_table"):
        assert fragment not in body, f"GET {path} leaked {fragment!r}"
    for marker in ("SELECT ", "INSERT INTO ", "WHERE token"):
        assert marker not in body, f"GET {path} leaked {marker!r}"


def test_cache_statistics_still_report_the_size(app, client_factory):
    """The fix is a redaction, not the removal of the numbers around it."""
    _prime_cache_with(*SECRET_SQL)
    client = client_factory("admin")
    payload = client.get("/api/performance/cache/stats").get_json() or {}
    statistics = (payload.get("data") or {}).get("statistics") or {}

    assert payload.get("success") is True
    assert statistics.get("size", 0) >= len(SECRET_SQL)
    assert statistics.get("max_size") == 1000
    assert isinstance(statistics.get("key_digests"), list)
    assert statistics.get("key_count") == statistics.get("size")
