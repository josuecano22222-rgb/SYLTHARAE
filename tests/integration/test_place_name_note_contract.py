"""Regression: curated place aliases must not touch a column that does not exist.

``Api/routes/places.py`` carried ``note`` through the alias create/update paths
(``INSERT INTO geo_place_names (..., note)`` and ``SELECT n.name_type, n.note``).
The frozen schema - ``database/migrations/m0001_baseline.py``,
``CREATE TABLE geo_place_names`` - has exactly seven columns:

    id, place_id, name, match_key, language, script, name_type

There is no ``note``. Under the schema freeze the column cannot be added, so the
two failure modes this file pins down were:

* creating any alias at all raised ``KeyError: 'note'`` (the cleaned payload has
  no such key) and answered 500;
* updating an alias raised ``IndexError: tuple index out of range`` reading
  ``old[5]`` from a five-column ``SELECT`` and answered 500.

A note that cannot be stored must be refused with a validation error rather than
silently dropped: a dropped ``note`` is the same class of defect as the dropped
``title_content_id`` parent in ``test_title_parent_linkage.py``.
"""

from __future__ import annotations

import uuid

import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_LABEL = f"Test Alias Host {_U}"


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _ensure_category(cur):
    cur.execute("SELECT id FROM words WHERE word = %s", ("city",))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", ("city",))
        row = cur.fetchone()
    cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT DO NOTHING", (row[0],))
    cur.execute("SELECT id FROM categorys WHERE word_id = %s", (row[0],))
    return cur.fetchone()[0]


@pytest.fixture
def hosted_place(pg_db):
    """An active place created through the API, plus its id."""
    from flask import Flask

    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        category_id = _ensure_category(cur)
        cur.execute(
            "INSERT INTO geo_places (label, category_id, country_codes)"
            " VALUES (%s, %s, %s) RETURNING id",
            (_LABEL, category_id, ["NL"]),
        )
        place_id = cur.fetchone()[0]
    conn.close()
    yield place_id
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM geo_place_names WHERE place_id = %s", (place_id,))
        cur.execute("DELETE FROM geo_places WHERE id = %s", (place_id,))
    conn.close()


def test_creating_an_alias_does_not_500(client_factory, hosted_place):
    admin = client_factory("admin")
    resp = admin.post(
        "/api/place-names",
        json={"place_id": hosted_place, "name": f"Alias {_U}",
              "language": "en", "name_type": "endonym"},
        headers={"X-CSRFToken": _csrf(admin)},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)


def test_updating_an_alias_does_not_500(client_factory, hosted_place):
    admin = client_factory("admin")
    created = admin.post(
        "/api/place-names",
        json={"place_id": hosted_place, "name": f"Alias {_U}",
              "language": "en", "name_type": "endonym"},
        headers={"X-CSRFToken": _csrf(admin)},
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    name_id = created.get_json()["id"] if "id" in created.get_json() else created.get_json()["name"]["id"]
    resp = admin.put(
        f"/api/place-names/{name_id}",
        json={"name": f"Renamed {_U}"},
        headers={"X-CSRFToken": _csrf(admin)},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_a_note_that_cannot_be_stored_is_refused_not_dropped(client_factory, hosted_place):
    admin = client_factory("admin")
    created = admin.post(
        "/api/place-names",
        json={"place_id": hosted_place, "name": f"Note {_U}",
              "language": "en", "name_type": "endonym"},
        headers={"X-CSRFToken": _csrf(admin)},
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    name_id = created.get_json()["id"] if "id" in created.get_json() else created.get_json()["name"]["id"]

    resp = admin.put(
        f"/api/place-names/{name_id}",
        json={"note": "curator note"},
        headers={"X-CSRFToken": _csrf(admin)},
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is False
    assert "note" in body["error"]["message"].lower(), body
