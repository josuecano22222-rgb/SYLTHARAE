"""Integration: a failure after the commit must not be reported as a failure.

The dangerous shape is not "the write failed" - a rollback is clean. It is
"the write committed and the response failed", because the caller then retries
a mutation that already happened: a duplicate, or a 409 for an object the
client believes it never created.

Every mutating place route is driven twice:

1. **inside the transaction** - the revision recorder raises. The transaction
   must roll back: no row, and a retry afterwards succeeds.
2. **after the commit** - the function that builds the ``gazetteer_revision``
   block raises. The mutation has already committed, so the answer must stay a
   success with ``fingerprint: null``; a 5xx here is the defect this file
   exists to prevent.

The matrix is printed by ``test_report_the_matrix`` so the same numbers can go
into the round-20 report instead of being retyped.
"""

from __future__ import annotations

import uuid

import pytest

import Api.routes.places as places_routes
from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_MATRIX: list[dict] = []


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


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


def _count(pg_db, sql, params=()):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        conn.close()


def _places(pg_db, label):
    return _count(pg_db, "SELECT count(*) FROM geo_places WHERE label = %s", (label,))


def _create_place(client, label):
    return client.post("/api/places", json={"label": label, "feature_type": "city"},
                       headers={"X-CSRFToken": _csrf(client)})


@pytest.fixture()
def after_commit_boom(monkeypatch):
    """The revision block is built after ``conn.commit()``."""
    def boom(revision):
        raise RuntimeError("injected: revision rendering failed after commit")

    monkeypatch.setattr(places_routes, "_revision_payload", boom)


@pytest.fixture()
def inside_transaction_boom(monkeypatch):
    """The revision recorder runs inside the transaction ``_write`` commits."""
    def boom(cur, action):
        raise RuntimeError("injected: revision recording failed")

    monkeypatch.setattr(places_routes, "_record_curation", boom)


# ---------------------------------------------------------------------------
# 1. Failure inside the transaction: rollback, and a retry is a first write
# ---------------------------------------------------------------------------

def test_a_failure_inside_the_transaction_leaves_no_row(client_factory, pg_db,
                                                        inside_transaction_boom):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Inside {_U}"
    resp = _create_place(admin, label)
    persisted = _places(pg_db, label)
    _MATRIX.append({"path": "POST /api/places", "fault": "inside transaction",
                    "status": resp.status_code, "persisted": persisted,
                    "retry": "n/a"})
    assert resp.status_code >= 500, resp.status_code
    assert persisted == 0, "the transaction should have rolled back"


def test_the_retry_after_a_rolled_back_create_succeeds(client_factory, pg_db,
                                                       inside_transaction_boom,
                                                       monkeypatch):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Retry {_U}"
    assert _create_place(admin, label).status_code >= 500
    monkeypatch.setattr(places_routes, "_record_curation",
                        lambda cur, action: None, raising=False)
    monkeypatch.undo()
    retry = _create_place(admin, label)
    assert retry.status_code == 201, retry.get_data(as_text=True)[:200]
    assert _places(pg_db, label) == 1


# ---------------------------------------------------------------------------
# 2. Failure after the commit: the write happened, so the answer must not fail
# ---------------------------------------------------------------------------

def test_create_after_commit_failure_stays_a_success(client_factory, pg_db,
                                                     after_commit_boom):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Commit Create {_U}"
    resp = _create_place(admin, label)
    persisted = _places(pg_db, label)
    _MATRIX.append({"path": "POST /api/places", "fault": "after commit",
                    "status": resp.status_code, "persisted": persisted,
                    "retry": "not attempted"})
    assert persisted == 1, "the row should be committed before the response is built"
    assert resp.status_code == 201, (
        f"a committed create answered {resp.status_code}: the client will retry "
        f"and be told 409 for a place it created")


def test_update_after_commit_failure_stays_a_success(client_factory, pg_db,
                                                     after_commit_boom):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Commit Update {_U}"
    created = _create_place(admin, label)
    place_id = (created.get_json() or {}).get("place") or {}
    place_id = place_id.get("id") if isinstance(place_id, dict) else place_id
    assert place_id, created.get_data(as_text=True)[:300]
    resp = admin.put(f"/api/places/{place_id}",
                     json={"label": f"{label} renamed"},
                     headers={"X-CSRFToken": _csrf(admin)})
    renamed = _places(pg_db, f"{label} renamed")
    _MATRIX.append({"path": "PUT /api/places/<id>", "fault": "after commit",
                    "status": resp.status_code, "persisted": renamed,
                    "retry": "not attempted"})
    assert renamed == 1, "the rename should be committed"
    assert resp.status_code == 200, resp.status_code


def test_delete_after_commit_failure_stays_a_success(client_factory, pg_db,
                                                     after_commit_boom):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Commit Delete {_U}"
    created = _create_place(admin, label)
    place = (created.get_json() or {}).get("place") or {}
    place_id = place.get("id") if isinstance(place, dict) else place
    assert place_id, created.get_data(as_text=True)[:300]
    resp = admin.delete(f"/api/places/{place_id}",
                        headers={"X-CSRFToken": _csrf(admin)})
    remaining = _places(pg_db, label)
    _MATRIX.append({"path": "DELETE /api/places/<id>", "fault": "after commit",
                    "status": resp.status_code, "persisted": remaining,
                    "retry": "not attempted"})
    assert remaining == 0, "the delete should be committed"
    assert resp.status_code == 200, resp.status_code


def test_place_name_routes_survive_a_post_commit_failure(client_factory, pg_db,
                                                         after_commit_boom):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Commit Names {_U}"
    created = _create_place(admin, label)
    place = (created.get_json() or {}).get("place") or {}
    place_id = place.get("id") if isinstance(place, dict) else place
    assert place_id, created.get_data(as_text=True)[:300]

    added = admin.post("/api/place-names", json={"place_id": place_id,
                                                 "name": f"Mali {_U}"},
                       headers={"X-CSRFToken": _csrf(admin)})
    names = _count(pg_db, "SELECT count(*) FROM geo_place_names WHERE place_id = %s",
                   (place_id,))
    _MATRIX.append({"path": "POST /api/place-names", "fault": "after commit",
                    "status": added.status_code, "persisted": names,
                    "retry": "not attempted"})
    assert names >= 1, "the name should be committed"
    assert added.status_code == 201, (
        f"a committed name create answered {added.status_code}")
    # The route answers the new id at the top level ({"id": …, "success": …}),
    # not nested under "name": read from the shape it actually returns.
    added_body = added.get_json() or {}
    alias_id = added_body.get("id")
    assert alias_id, added.get_data(as_text=True)[:300]

    renamed = admin.put(f"/api/place-names/{alias_id}",
                        json={"name": f"Mali renamed {_U}"},
                        headers={"X-CSRFToken": _csrf(admin)})
    _MATRIX.append({"path": "PUT /api/place-names/<id>", "fault": "after commit",
                    "status": renamed.status_code, "persisted": "renamed",
                    "retry": "not attempted"})
    assert renamed.status_code == 200, renamed.status_code

    removed = admin.delete(f"/api/place-names/{alias_id}",
                           headers={"X-CSRFToken": _csrf(admin)})
    left = _count(pg_db, "SELECT count(*) FROM geo_place_names WHERE id = %s",
                  (alias_id,))
    _MATRIX.append({"path": "DELETE /api/place-names/<id>", "fault": "after commit",
                    "status": removed.status_code, "persisted": left,
                    "retry": "not attempted"})
    assert left == 0, "the name should be deleted"
    assert removed.status_code == 200, removed.status_code


# ---------------------------------------------------------------------------
# 3. Idempotency: the same request twice
# ---------------------------------------------------------------------------

def test_the_same_create_twice_writes_one_row_and_answers_409(client_factory, pg_db):
    _ensure_city_category(pg_db)
    admin = client_factory("admin")
    label = f"Fault Idempotent {_U}"
    first = _create_place(admin, label)
    second = _create_place(admin, label)
    rows = _places(pg_db, label)
    _MATRIX.append({"path": "POST /api/places (x2)", "fault": "none",
                    "status": f"{first.status_code}/{second.status_code}",
                    "persisted": rows, "retry": "409 on the duplicate"})
    assert first.status_code == 201, first.get_data(as_text=True)[:200]
    assert second.status_code == 409, second.get_data(as_text=True)[:200]
    assert rows == 1


def test_report_the_matrix():
    """Not a test of the product: the measured matrix, written where it is read."""
    if not _MATRIX:
        pytest.skip("the fault-injection tests did not run")
    lines = ["| path | injected fault | status | rows after | retry |",
             "| --- | --- | --- | --- | --- |"]
    lines += [f"| {row['path']} | {row['fault']} | {row['status']} | "
              f"{row['persisted']} | {row['retry']} |" for row in _MATRIX]
    print("\n".join(lines))
