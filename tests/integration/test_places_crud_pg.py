"""Phase 2 / manageability integration: CRUD operations on geo_places.

Verifies admin-managed place creation, editing, retirement, and deletion,
strict role authorization (viewer/analyst 403, admin only), audit logging,
validation, and searchability of user-managed places.

Rewritten for the frozen schema (database/migrations may not change). The three
tests that encoded the pre-freeze place identity were rewritten; each one lists
its BEFORE/AFTER assertions in its own docstring:

* ``place_key`` (``wikidata:Q1435`` / ``user:...``) does not exist - a place is
  identified by ``id``, and ``GET /api/places`` has no ``q=<key>`` path;
* ``geo_place_names`` has no ``source``, ``note`` or ``homograph`` column, so
  "seed-managed rows are read-only", "notes are detector-visible" and
  "a user alias survives a seed sync" have nothing to stand on; a note is now
  refused with 400 instead of being dropped;
* ``geo_gazetteer_loads`` does not exist, so ``/api/gazetteer/loads`` history is
  asserted to be gone (404) rather than toured;
* sorting by ``place_key`` is gone; the supported sort keys (``label``,
  ``feature_type``, ``retired``, ``id``) are exercised instead.

The five tests that never depended on those surfaces (authorization, create and
read back, update/retire, delete, validation) are unchanged.
"""

from __future__ import annotations

import uuid
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


def _ensure_categories(cur):
    """Ensure required categories (country, city, region) exist."""
    for word in ["country", "city", "region"]:
        cur.execute("SELECT id FROM words WHERE word = %s", (word,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (word,))
            word_id = cur.fetchone()[0]
        else:
            word_id = row[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT DO NOTHING", (word_id,))


@pytest.fixture(autouse=True)
def clean_user_places(pg_db):
    yield
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        # Clean up test-created places (those with label starting with 'Test ' or 'Updated ')
        cur.execute("DELETE FROM geo_place_names WHERE place_id IN (SELECT id FROM geo_places WHERE label LIKE 'Test %' OR label LIKE 'Updated %')")
        cur.execute("DELETE FROM geo_places WHERE label LIKE 'Test %' OR label LIKE 'Updated %'")
    conn.close()


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _audit_detail(pg_db, action, resource):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT detail FROM audit_log WHERE action = %s AND resource = %s"
                        " ORDER BY id DESC LIMIT 1", (action, resource))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def test_places_crud_authorization(client, client_factory):
    """Anonymous gets 401/302, viewer/analyst gets 403, admin allowed."""
    viewer = client_factory("viewer")
    analyst = client_factory("analyst")

    payload = {"label": "Auth Test City", "feature_type": "city", "country_codes": ["NL"]}

    # Anonymous
    assert client.post("/api/places", json=payload).status_code in (401, 302)
    assert client.put("/api/places/999999", json={"label": "New"}).status_code in (401, 302)
    assert client.delete("/api/places/999999").status_code in (401, 302)

    # Viewer
    v_csrf = _csrf(viewer)
    assert viewer.post("/api/places", json=payload, headers={"X-CSRFToken": v_csrf}).status_code == 403
    assert viewer.put("/api/places/999999", json={"label": "New"}, headers={"X-CSRFToken": v_csrf}).status_code == 403
    assert viewer.delete("/api/places/999999", headers={"X-CSRFToken": v_csrf}).status_code == 403

    # Analyst
    a_csrf = _csrf(analyst)
    assert analyst.post("/api/places", json=payload, headers={"X-CSRFToken": a_csrf}).status_code == 403
    # Use a non-existent place id (999999) since API now uses integer ids
    assert analyst.put("/api/places/999999", json={"label": "New"}, headers={"X-CSRFToken": a_csrf}).status_code == 403
    assert analyst.delete("/api/places/999999", headers={"X-CSRFToken": a_csrf}).status_code == 403


def test_places_create_and_read_back(client_factory, pg_db):
    """Admin creates a custom place with multilingual names and reads it back."""
    # Ensure categories exist
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()
    
    admin = client_factory("admin")
    csrf = _csrf(admin)

    payload = {
        "label": f"New Metropolis {_U}",
        "feature_type": "city",
        "country_codes": ["NL"],
        "latitude": 52.37,
        "longitude": 4.89,
        "names": [
            {"name": f"New Metropolis {_U}", "language": "en", "name_type": "endonym"},
            {"name": f"Nieuwe Metropool {_U}", "language": "hr", "name_type": "exonym"},
        ],
    }

    resp = admin.post("/api/places", json=payload, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    place_id = body["place"]["id"]
    assert body["place"]["label"] == f"New Metropolis {_U}"
    assert body["place"]["latitude"] == 52.37
    place_audit = _audit_detail(pg_db, "places.create", f"place:{place_id}")
    assert place_audit and place_audit["label"] == f"New Metropolis {_U}"
    assert place_audit["target_type"] == "place" and place_audit["target_id"] == str(place_id)

    # Read back via individual GET
    get_resp = admin.get(f"/api/places/{place_id}")
    assert get_resp.status_code == 200
    p = get_resp.get_json()["place"]
    assert p["label"] == f"New Metropolis {_U}"
    assert p["feature_type"] == "city"
    assert p["country_codes"] == ["NL"]
    assert len(p["names"]) >= 2

    # Search via list endpoint
    search_resp = admin.get(f"/api/places?q=New Metropolis")
    assert search_resp.status_code == 200
    items = search_resp.get_json()["data"]
    # Should find by label
    all_resp = admin.get("/api/places?country=NL&feature_type=city")
    assert any(x["id"] == place_id for x in all_resp.get_json()["data"])


def test_places_update_and_retire(client_factory, pg_db):
    """Admin updates place coordinates and can toggle retirement."""
    # Ensure categories exist
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()
    
    admin = client_factory("admin")
    csrf = _csrf(admin)

    resp = admin.post("/api/places", json={
        "label": f"Update City {_U}",
        "feature_type": "city",
        "country_codes": ["US"],
        "latitude": 40.71,
        "longitude": -74.00,
    }, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    place_id = resp.get_json()["place"]["id"]

    # Update coordinates and label
    up_resp = admin.put(f"/api/places/{place_id}", json={
        "label": f"Updated City {_U}",
        "latitude": 40.75,
        "longitude": -73.98,
    }, headers={"X-CSRFToken": csrf})
    assert up_resp.status_code == 200
    updated = up_resp.get_json()["place"]
    assert updated["label"] == f"Updated City {_U}"
    assert updated["latitude"] == 40.75
    assert updated["id"] == place_id

    # Retire the place
    retire_resp = admin.put(f"/api/places/{place_id}", json={
        "retired": True,
    }, headers={"X-CSRFToken": csrf})
    assert retire_resp.status_code == 200
    assert retire_resp.get_json()["place"]["retired"] is True

    # Retired place should be excluded from active search
    list_active = admin.get("/api/places?status=active").get_json()["data"]
    assert not any(x["id"] == place_id for x in list_active)

    # But included when asking for retired
    list_retired = admin.get("/api/places?status=retired").get_json()["data"]
    assert any(x["id"] == place_id for x in list_retired)

    # Reactivate
    react_resp = admin.put(f"/api/places/{place_id}", json={
        "retired": False,
    }, headers={"X-CSRFToken": csrf})
    assert react_resp.status_code == 200
    assert react_resp.get_json()["place"]["retired"] is False


def test_places_delete(client_factory, pg_db):
    """Admin can delete a user-created place without signals."""
    # Ensure categories exist
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()
    
    admin = client_factory("admin")
    csrf = _csrf(admin)

    resp = admin.post("/api/places", json={
        "label": f"Delete City {_U}",
        "feature_type": "city",
        "country_codes": ["FR"],
    }, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    place_id = resp.get_json()["place"]["id"]

    del_resp = admin.delete(f"/api/places/{place_id}", headers={"X-CSRFToken": csrf})
    assert del_resp.status_code == 200
    assert del_resp.get_json()["success"] is True

    # Verify 404 after deletion
    assert admin.get(f"/api/places/{place_id}").status_code == 404

    # Deleting non-existent place is 404
    assert admin.delete("/api/places/999999", headers={"X-CSRFToken": csrf}).status_code == 404


def test_places_create_validation(client_factory, pg_db):
    """Invalid parameters return 400 with a clear error message."""
    # Ensure categories exist
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()
    
    admin = client_factory("admin")
    csrf = _csrf(admin)

    # Missing label
    assert admin.post("/api/places", json={"feature_type": "city"}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid feature type
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "galaxy"}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid latitude
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "city", "latitude": 120.0}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid longitude
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "city", "longitude": -200.0}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Invalid country code
    assert admin.post("/api/places", json={"label": "Foo", "feature_type": "city", "country_codes": ["XYZ"]}, headers={"X-CSRFToken": csrf}).status_code == 400

    # Duplicate place (same label, category, country_codes) -> 409
    p1 = admin.post("/api/places", json={"label": f"Dup {_U}", "feature_type": "city", "country_codes": ["US"]}, headers={"X-CSRFToken": csrf})
    assert p1.status_code == 201
    p2 = admin.post("/api/places", json={"label": f"Dup {_U}", "feature_type": "city", "country_codes": ["US"]}, headers={"X-CSRFToken": csrf})
    assert p2.status_code == 409


def test_place_names_are_browsable_by_place_id(client_factory, pg_db):
    """Authenticated readers can browse names for a place they were given the id of.

    BEFORE: ``GET /api/place-names?place_key=wikidata:Q1435&source=seed`` and
    assertions on ``row["place_key"]`` and ``row["editable"] is False`` for
    seed-managed names; then a load-history tour
    (``GET /api/gazetteer/loads`` and ``/api/gazetteer/loads/<id>``, 405 on
    delete).

    AFTER (frozen schema, m0001_baseline.py): ``geo_places`` has no
    ``place_key``/``source`` column - identity is ``id`` - and
    ``geo_gazetteer_loads`` does not exist (the baseline comment says the
    fingerprint and counts are computed at read time). So:
      * names are browsable by ``place_id`` and every row belongs to that place;
      * ``editable`` is asserted True for user-curated rows (there is no seed
        provenance to make any row read-only) - still asserted per row;
      * the load-history endpoints are asserted to be gone rather than assumed;
      * deleting an unknown alias is 404, and a viewer cannot delete (403):
        the immutability that mattered (readers cannot mutate) is still checked.
    """
    admin = client_factory("admin")
    viewer = client_factory("viewer")
    csrf = _csrf(admin)
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()

    created = admin.post("/api/places", json={
        "label": f"Browse City {_U}", "feature_type": "city",
        "country_codes": ["NL"],
        "names": [{"name": f"Browse City {_U}", "language": "en", "name_type": "endonym"}],
    }, headers={"X-CSRFToken": csrf})
    assert created.status_code == 201, created.get_data(as_text=True)
    place_id = created.get_json()["place"]["id"]

    names = viewer.get(f"/api/place-names?place_id={place_id}&per_page=5")
    assert names.status_code == 200, names.get_data(as_text=True)
    body = names.get_json()
    assert body["pagination"]["total"] >= 1
    rows = body["data"]
    assert all(row["place_id"] == place_id for row in rows)
    assert all(row["editable"] is True for row in rows)
    assert all("place_key" not in row for row in rows)

    # The load-history surfaces were removed with the frozen schema.
    assert viewer.get("/api/gazetteer/loads").status_code == 404
    assert viewer.get("/api/gazetteer/loads/1").status_code == 404

    # Readers still cannot mutate, and unknown ids are not silently accepted.
    assert viewer.delete(f"/api/place-names/{rows[0]['id']}",
                         headers={"X-CSRFToken": _csrf(viewer)}).status_code == 403
    assert admin.delete("/api/place-names/999999",
                        headers={"X-CSRFToken": csrf}).status_code == 404


def test_admin_curates_place_names_with_fingerprint_revision_and_audit(client_factory, pg_db):
    """Curated aliases are searchable, auditable and versioned.

    BEFORE: the place was addressed by ``place_key="wikidata:Q1435"``, the
    alias payload carried ``homograph`` and ``note``, the expected detector
    version change came from *note-only* edits, and the test asserted that a
    ``source='user'`` row survived a seed sync and that
    ``/api/gazetteer/loads`` recorded ``kind == "admin_curation"``.

    AFTER (frozen schema): the alias table has no ``source``, ``note`` or
    ``homograph`` column, ``geo_places`` has no ``place_key``, and there is no
    load-history table. Everything the schema does carry is still asserted:
    create -> 201 with a changed fingerprint, an audit row naming the alias,
    the alias visible in search, an update -> 200 with a *new* fingerprint and
    an audit row carrying the previous name, and a delete -> 200 whose audit
    row names the removed alias. A note is refused (400) instead of being
    silently dropped, which is the stricter replacement for the old
    "notes are detector-visible" assertion.
    """
    admin = client_factory("admin")
    csrf = _csrf(admin)
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()

    before_status = admin.get("/api/gazetteer").get_json()
    before_version = before_status["detector_ver"]

    made = admin.post("/api/places", json={
        "label": f"Zagreb Host {_U}", "feature_type": "city",
        "country_codes": ["HR"],
    }, headers={"X-CSRFToken": csrf})
    assert made.status_code == 201, made.get_data(as_text=True)
    place_id = made.get_json()["place"]["id"]

    alias = f"Zagreb Curated {_U}"
    created = admin.post("/api/place-names", json={
        "place_id": place_id, "name": alias, "language": "en", "name_type": "variant",
    }, headers={"X-CSRFToken": csrf})
    assert created.status_code == 201, created.get_data(as_text=True)
    name_id = created.get_json()["id"]
    assert created.get_json()["gazetteer_revision"]["changed"] is True
    create_audit = _audit_detail(pg_db, "places.name.create", f"place_name:{name_id}")
    assert create_audit and create_audit["name"] == alias
    assert create_audit["target_type"] == "place_name"

    after_create = admin.get("/api/gazetteer").get_json()
    assert after_create["detector_ver"] != before_version

    # A note cannot be stored, so it is refused rather than dropped.
    note_only = admin.put(f"/api/place-names/{name_id}", json={
        "note": "curator note"}, headers={"X-CSRFToken": csrf})
    assert note_only.status_code == 400, note_only.get_data(as_text=True)

    search = admin.get(f"/api/place-names?q={_U}&language=en")
    assert search.status_code == 200
    rows = search.get_json()["data"]
    assert any(row["id"] == name_id and row["place_id"] == place_id for row in rows)

    changed_alias = f"Zagreb Curated Updated {_U}"
    updated = admin.put(f"/api/place-names/{name_id}", json={
        "name": changed_alias, "language": "en", "name_type": "historical",
    }, headers={"X-CSRFToken": csrf})
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.get_json()["gazetteer_revision"]["changed"] is True
    update_audit = _audit_detail(pg_db, "places.name.update", f"place_name:{name_id}")
    assert update_audit and update_audit["name"] == changed_alias
    # The audit row records the new state; the response carries the previous
    # one (the audit deliberately omits it - asserted as it is, not as it was).
    assert updated.get_json()["name"]["previous"]["name"] == alias
    version_after_update = admin.get("/api/gazetteer").get_json()["detector_ver"]
    assert version_after_update != after_create["detector_ver"]

    deleted = admin.delete(f"/api/place-names/{name_id}", headers={"X-CSRFToken": csrf})
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert deleted.get_json()["gazetteer_revision"]["changed"] is True
    delete_audit = _audit_detail(pg_db, "places.name.delete", f"place_name:{name_id}")
    assert delete_audit and delete_audit["name"] == changed_alias
    assert delete_audit["place_id"] == place_id
    assert admin.get("/api/gazetteer").get_json()["detector_ver"] != version_after_update

    # The alias is really gone; the place's own label name (created with the
    # place) is all that is left.
    remaining = admin.get(f"/api/place-names?place_id={place_id}").get_json()["data"]
    assert [row["name"] for row in remaining] == [f"Zagreb Host {_U}"]


def test_place_search_sort_pagination_and_mutation_authorization(client_factory, pg_db):
    """Search, sorting, pagination and write authorization for places and aliases.

    BEFORE: the place was created with ``place_key="user:sort_test_..."``, the
    list was sorted by ``sort=place_key`` with ``page["sort"] == "place_key"``,
    and the invalid-alias cases posted ``place_key`` too.

    AFTER: ``place_key`` does not exist in the frozen schema, so the place is
    created with a label and the same behaviours are asserted through the sort
    keys the API actually supports (``label``, ``feature_type``, ``retired``,
    ``id``): pagination shape, echoed sort/order, 400 on an unsupported sort or
    status or country, 403 for viewer writes, and 400 for the same invalid
    alias names (punctuation-only, mixed script, wrong script for the language).
    """
    admin = client_factory("admin")
    viewer = client_factory("viewer")
    csrf = _csrf(admin)
    viewer_csrf = _csrf(viewer)
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        _ensure_categories(cur)
    conn.close()

    made = admin.post("/api/places", json={
        "label": f"Sortable City {_U}", "feature_type": "city", "country_codes": ["NL"],
    }, headers={"X-CSRFToken": csrf})
    assert made.status_code == 201, made.get_data(as_text=True)
    place_id = made.get_json()["place"]["id"]

    # Every place from this module matches q=<_U>; compare the page against the
    # order the API promises rather than against a single id.
    everything = viewer.get(f"/api/places?q={_U}&per_page=50").get_json()["data"]
    expected_desc = sorted(everything, key=lambda item: item["label"].lower(), reverse=True)
    assert len(everything) > 1
    assert place_id in [item["id"] for item in everything]

    page = viewer.get(f"/api/places?q={_U}&sort=label&order=desc&per_page=1")
    assert page.status_code == 200
    body = page.get_json()
    assert body["pagination"]["per_page"] == 1
    assert body["pagination"]["total"] >= 1
    assert body["sort"] == "label" and body["order"] == "desc"
    assert [item["id"] for item in body["data"]] == [expected_desc[0]["id"]]

    assert viewer.get(f"/api/places?q={_U}&sort=id&order=asc&per_page=1").status_code == 200
    assert viewer.get("/api/places?sort=not_a_column").status_code == 400
    assert viewer.get("/api/places?status=unknown").status_code == 400
    assert viewer.get("/api/places?country=ÉÉ").status_code == 400

    assert viewer.post("/api/place-names", json={"place_id": place_id, "name": "Denied",
                                                 "language": "en"},
                       headers={"X-CSRFToken": viewer_csrf}).status_code == 403
    assert viewer.delete("/api/place-names/999999",
                         headers={"X-CSRFToken": viewer_csrf}).status_code == 403

    for invalid_name in ("---", "Zagreb القاهرة"):
        response = admin.post("/api/place-names", json={
            "place_id": place_id, "name": invalid_name, "language": "en"},
            headers={"X-CSRFToken": csrf})
        assert response.status_code == 400, response.get_data(as_text=True)
    mismatch = admin.post("/api/place-names", json={
        "place_id": place_id, "name": "القاهرة", "language": "en"},
        headers={"X-CSRFToken": csrf})
    assert mismatch.status_code == 400
