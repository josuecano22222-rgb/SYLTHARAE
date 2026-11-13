"""Bug B: `POST /api/places` returned 500 on an empty gazetteer.

The traceback, from the application log:

    File "Api\\routes\\places.py", line 400, in api_places_create
        "fingerprint": revision["fingerprint"]})
    TypeError: 'NoneType' object is not subscriptable

`revision` is the third value `do_create()` returns. It returns
`(None, "category not found", None)` when the gazetteer has no category row for
the requested ``feature_type`` - which is exactly the state of a database whose
gazetteer has never been loaded: ``geo_places.category_id`` references
``categorys``, and ``categorys`` rows are created by the seed sync, not by the
migration baseline. The route only inspected ``error == "conflict"``, so this
error fell through to the success path, and line 400 subscripted ``None``.

What the tests below pin:

* the request must come back as an error response in the documented envelope,
  not as an unhandled 500;
* no place row may be written when the request is refused (measured: the early
  return happens before the INSERT, so nothing was written - the risk is the
  second test, where the revision is missing *after* a successful insert);
* a revision that comes back ``None`` must not crash the route after the write
  has already committed, and the response shape must not change when it does;
* the same unguarded ``revision["fingerprint"]`` pattern in the update, delete
  and place-name routes must be guarded too.

Every test here was written before the fix and failed against it.
"""
from __future__ import annotations

import uuid

import pytest

import Api.routes.places as places_routes
from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _feature_type_without_places(pg_db):
    """A feature type with no category row, i.e. the empty-gazetteer state.

    Returns ``(feature_type, category_id)``; ``category_id`` is None when the
    row is already absent (the database has no seeded gazetteer at all, which is
    exactly the state the bug was reported from). ``geo_places`` references
    ``categorys``, so a category already used by a place cannot be removed; when
    all three are in use the caller skips.
    """
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            for feature_type in ("region", "country", "city"):
                cur.execute(
                    "SELECT c.id, (SELECT count(*) FROM geo_places p WHERE p.category_id = c.id)"
                    " FROM categorys c JOIN words w ON w.id = c.word_id WHERE w.word = %s",
                    (feature_type,))
                row = cur.fetchone()
                if row is None:
                    return feature_type, None          # already absent
                if row[1] == 0:
                    return feature_type, row[0]        # present, unused
        return None, None
    finally:
        conn.close()


def _delete_category(pg_db, category_id):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM categorys WHERE id = %s", (category_id,))
    conn.close()


def _restore_category(pg_db, feature_type):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM words WHERE word = %s", (feature_type,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (feature_type,))
            word_id = cur.fetchone()[0]
        else:
            word_id = row[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (word_id,))
    conn.close()


def _count_places(pg_db, label):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM geo_places WHERE label = %s", (label,))
            return cur.fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 1. Empty gazetteer: an error response, not a crash
# --------------------------------------------------------------------------- #
def test_create_answers_with_an_error_when_the_gazetteer_has_no_category(client_factory, pg_db):
    feature_type, category_id = _feature_type_without_places(pg_db)
    if feature_type is None:
        pytest.skip("every feature type already has places in this database")
    if category_id is not None:
        _delete_category(pg_db, category_id)
    try:
        admin = client_factory("admin")
        csrf = _csrf(admin)
        label = f"Bug B City {_U}"
        resp = admin.post("/api/places",
                          json={"label": label, "feature_type": feature_type,
                                "country_codes": ["NL"]},
                          headers={"X-CSRFToken": csrf})
        body = resp.get_json() or {}
        assert resp.status_code != 500, body
        assert resp.status_code in (400, 409, 503), (resp.status_code, body)
        # The documented error envelope, not the generic crash page.
        assert body.get("success") is False, body
        assert body.get("error", {}).get("code"), body
        assert body.get("error", {}).get("message"), body
        assert "category" in body["error"]["message"].lower(), body
    finally:
        _restore_category(pg_db, feature_type)


def test_a_refused_create_writes_no_place(client_factory, pg_db):
    """Measured: the early return happens before the INSERT, so nothing is written."""
    feature_type, category_id = _feature_type_without_places(pg_db)
    if feature_type is None:
        pytest.skip("every feature type already has places in this database")
    label = f"Bug B No Write {_U}"
    if category_id is not None:
        _delete_category(pg_db, category_id)
    try:
        admin = client_factory("admin")
        resp = admin.post("/api/places",
                          json={"label": label, "feature_type": feature_type,
                                "country_codes": ["NL"]},
                          headers={"X-CSRFToken": _csrf(admin)})
        assert resp.status_code != 500, resp.get_data(as_text=True)[:300]
        assert _count_places(pg_db, label) == 0
    finally:
        _restore_category(pg_db, feature_type)


# --------------------------------------------------------------------------- #
# 2. A missing revision must not crash, and must not change the shape
# --------------------------------------------------------------------------- #
def _categories_present(pg_db):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM categorys c JOIN words w ON w.id = c.word_id"
                        " WHERE w.word = 'city'")
            return cur.fetchone()[0] > 0
    finally:
        conn.close()


def _ensure_city_category(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM words WHERE word = 'city'")
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO words (word) VALUES ('city') RETURNING id")
            word_id = cur.fetchone()[0]
        else:
            word_id = row[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (word_id,))
    conn.close()


@pytest.fixture
def no_revision(monkeypatch):
    """The revision recorder returns None - the shape that crashed the route."""
    monkeypatch.setattr(places_routes, "_record_curation", lambda cur, action: None)


def test_create_survives_a_missing_revision(client_factory, pg_db, no_revision):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Bug B No Revision {_U}"
    resp = admin.post("/api/places",
                      json={"label": label, "feature_type": "city", "country_codes": ["NL"]},
                      headers={"X-CSRFToken": _csrf(admin)})
    body = resp.get_json() or {}
    assert resp.status_code == 201, (resp.status_code, body)
    # Shape is unchanged; the value says the truth: there is no revision.
    assert set(body) == {"success", "place", "gazetteer_revision"}, body
    assert body["gazetteer_revision"] == {"fingerprint": None, "changed": False}, body
    # The write did happen: the crash would have been *after* the commit, so a
    # retry by the client would have been answered with 409. This asserts the
    # fact rather than hiding it.
    assert _count_places(pg_db, label) == 1


def test_update_survives_a_missing_revision(client_factory, pg_db, no_revision):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    created = admin.post("/api/places",
                         json={"label": f"Bug B Update {_U}", "feature_type": "city"},
                         headers={"X-CSRFToken": _csrf(admin)})
    assert created.status_code == 201, created.get_data(as_text=True)[:300]
    place_id = created.get_json()["place"]["id"]
    resp = admin.put(f"/api/places/{place_id}", json={"latitude": 1.5, "longitude": 2.5},
                     headers={"X-CSRFToken": _csrf(admin)})
    body = resp.get_json() or {}
    assert resp.status_code == 200, (resp.status_code, body)
    assert body["gazetteer_revision"] == {"fingerprint": None, "changed": False}, body


def test_delete_survives_a_missing_revision(client_factory, pg_db):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    created = admin.post("/api/places",
                         json={"label": f"Bug B Delete {_U}", "feature_type": "city"},
                         headers={"X-CSRFToken": _csrf(admin)})
    assert created.status_code == 201, created.get_data(as_text=True)[:300]
    place_id = created.get_json()["place"]["id"]
    # Patch only for the delete call, so the create above recorded for real.
    original = places_routes._record_curation
    places_routes._record_curation = lambda cur, action: None
    try:
        resp = admin.delete(f"/api/places/{place_id}", headers={"X-CSRFToken": _csrf(admin)})
    finally:
        places_routes._record_curation = original
    body = resp.get_json() or {}
    assert resp.status_code == 200, (resp.status_code, body)
    assert body["gazetteer_revision"] == {"fingerprint": None, "changed": False}, body


def test_place_name_routes_survive_a_missing_revision(client_factory, pg_db):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    created = admin.post("/api/places",
                         json={"label": f"Bug B Names {_U}", "feature_type": "city"},
                         headers={"X-CSRFToken": _csrf(admin)})
    assert created.status_code == 201, created.get_data(as_text=True)[:300]
    place_id = created.get_json()["place"]["id"]

    original = places_routes._record_curation
    places_routes._record_curation = lambda cur, action: None
    try:
        made = admin.post("/api/place-names",
                          json={"place_id": place_id, "name": f"Bug B Alias {_U}",
                                "language": "en", "name_type": "variant"},
                          headers={"X-CSRFToken": _csrf(admin)})
        assert made.status_code == 201, made.get_data(as_text=True)[:300]
        assert made.get_json()["gazetteer_revision"] == {"fingerprint": None,
                                                        "changed": False}
        name_id = made.get_json()["id"]

        edited = admin.put(f"/api/place-names/{name_id}",
                           json={"name": f"Bug B Alias Renamed {_U}"},
                           headers={"X-CSRFToken": _csrf(admin)})
        assert edited.status_code == 200, edited.get_data(as_text=True)[:300]
        assert edited.get_json()["gazetteer_revision"] == {"fingerprint": None,
                                                          "changed": False}

        removed = admin.delete(f"/api/place-names/{name_id}",
                               headers={"X-CSRFToken": _csrf(admin)})
        assert removed.status_code == 200, removed.get_data(as_text=True)[:300]
        assert removed.get_json()["gazetteer_revision"] == {"fingerprint": None,
                                                           "changed": False}
    finally:
        places_routes._record_curation = original


def test_a_missing_category_is_reported_as_unavailable(client_factory, pg_db):
    """The refused create names the state, and says the gazetteer is the reason."""
    feature_type, category_id = _feature_type_without_places(pg_db)
    if feature_type is None:
        pytest.skip("every feature type already has places in this database")
    if category_id is not None:
        _delete_category(pg_db, category_id)
    try:
        admin = client_factory("admin")
        resp = admin.post("/api/places",
                          json={"label": f"Bug B Code {_U}", "feature_type": feature_type},
                          headers={"X-CSRFToken": _csrf(admin)})
        body = resp.get_json() or {}
        assert resp.status_code == 503, (resp.status_code, body)
        assert body["error"]["code"] == "GAZETTEER_UNAVAILABLE", body
    finally:
        _restore_category(pg_db, feature_type)
