"""Integration: error bodies say nothing about the database or the disk.

Round 19 fixed one 500 (an unseeded gazetteer) and round 20 turned the
remaining post-commit failure into a success. Both left a question this file
answers with a measurement rather than a reading: what does an error body
actually contain, for the callers who are *not* trusted with detail?

Three callers, three sets of endpoints, crafted inputs that reach the error
paths (wrong types, hostile ids, values that fail validation deep in the
route). The assertion is uniform: no table name, no SQL, no absolute path, no
traceback, no driver class, whichever status the route answers with.

The exception is a documented one: `GAZETTEER_UNAVAILABLE` explains *what the
operator has to load* ("no category for feature_type 'city'"), which is
curation vocabulary, not schema. It is asserted separately and on purpose.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

#: Fragments that must never reach a client. Schema names, driver names, SQL
#: verbs that only appear in a statement, and the paths this repository uses.
LEAK_MARKERS = (
    "geo_places", "geo_place_names", "categorys", "words_categorys", "paths ",
    "psycopg2", "sqlalchemy", "Traceback", "File \"", "UndefinedTable",
    "UndefinedColumn", "duplicate key value", "INSERT INTO", "SELECT ",
    "UPDATE ", "DELETE FROM", "/home/", "/tmp/", "C:\\", "site-packages",
)

#: (method, path, json body) — each one reaches a route body that handles an
#: exception, not a happy path.
CASES = [
    ("post", "/api/places", {"label": {"not": "a string"}, "feature_type": "city"}),
    ("post", "/api/places", {"label": "x" * 400, "feature_type": "no-such-type"}),
    ("post", "/api/places", {"label": "Probe", "feature_type": "city",
                             "latitude": "NaN", "longitude": "NaN"}),
    ("put", "/api/places/999999999", {"label": "Probe"}),
    ("delete", "/api/places/999999999", None),
    ("post", "/api/place-names", {"place_id": "1", "name": "Probe"}),
    ("post", "/api/place-names", {"place_id": 999999999, "name": "Probe"}),
    ("put", "/api/place-names/999999999", {"name": "Probe"}),
    ("delete", "/api/place-names/999999999", None),
    ("post", "/api/files/details/export", {"format": "csv; drop"}),
    ("post", "/api/files/names/export", {"scope": "selected", "format": "nope"}),
    ("post", "/files/export", {"file_ids": ["a"], "mode": "originals"}),
    ("post", "/api/lexis/export/columns", {"format": "nope"}),
    ("post", "/api/lexis/export/originals.zip", {"focus": "root"}),
    ("get", "/api/gazetteer", None),
    ("get", "/api/settings/theme", None),
    ("post", "/api/settings/theme/theme", {"value": {"nested": True}}),
    ("get", "/api/analytics/dashboard-summary", None),
    ("get", "/api/interfaces/999999999", None),
    ("get", "/api/experience/contracts/999999999", None),
    ("get", "/export/999999999", None),
]


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _call(client, method, path, body):
    kwargs = {"headers": {"X-CSRFToken": _csrf(client)}}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


def _assert_clean(response, who, method, path):
    text = response.get_data(as_text=True)
    for marker in LEAK_MARKERS:
        assert marker not in text, (
            f"{who} {method.upper()} {path} answered {response.status_code} with "
            f"{marker!r} in the body: {text[:300]!r}")


def test_anonymous_callers_get_no_detail(client, pg_db):
    tested = 0
    for method, path, body in CASES:
        response = _call(client, method, path, body)
        if response.status_code in (301, 302, 303, 307, 308):
            continue  # redirected to the login page, which is the point
        tested += 1
        _assert_clean(response, "anonymous", method, path)
    assert tested, "no case reached a route body anonymously"


def test_low_privilege_callers_get_no_detail(client_factory, pg_db):
    viewer = client_factory("viewer")
    tested = 0
    for method, path, body in CASES:
        response = _call(viewer, method, path, body)
        tested += 1
        _assert_clean(response, "viewer", method, path)
    assert tested == len(CASES)


def test_admin_failures_still_do_not_leak(client_factory, pg_db):
    admin = client_factory("admin")
    for method, path, body in CASES:
        response = _call(admin, method, path, body)
        _assert_clean(response, "admin", method, path)


def test_the_gazetteer_message_is_curation_vocabulary(client_factory, pg_db):
    """The one detail we keep: *what to load*, not what the schema looks like."""
    from _seed import connect

    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM categorys WHERE word_id IN"
                        " (SELECT id FROM words WHERE word = 'city')")
        conn.commit()
    finally:
        conn.close()

    admin = client_factory("admin")
    response = _call(admin, "post", "/api/places",
                     {"label": "Safety Probe City", "feature_type": "city"})
    body = response.get_json() or {}
    assert response.status_code == 503, (response.status_code, body)
    message = (body.get("error") or {}).get("message") or ""
    assert "city" in message, message
    _assert_clean(response, "admin (gazetteer)", "post", "/api/places")
    assert body["error"]["code"] == "GAZETTEER_UNAVAILABLE"
