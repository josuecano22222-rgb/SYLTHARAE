"""``DELETE /api/sides/<id>`` answered 500 for every side.

The "is this side in use" check selected ``hashs.side_id``, a column the frozen
baseline does not have: a document belongs to one source *and* one side
through ``hash_contexts``, and ``paths.context_id`` points at that row. Every
delete, of any side, ended in UndefinedColumn masked as "An internal error
occurred". A side still carrying documents must be refused with 409; an unused
one is deleted.

Found by the parameterised-route probe (``tools/audit/probe_param_routes.py``).
"""


from __future__ import annotations

import pathlib
import sys
import uuid

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def _side(client, name):
    resp = client.post("/api/sides", json={"name": name})
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _source(client, name):
    resp = client.post("/api/sources", json={"name": name})
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _attach_document(pg_db, source_id, side_id, marker):
    """One document on (source, side), written the way ingestion writes it."""
    from tests.integration._seed import connect

    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                    (uuid.uuid4().hex,))  # the digest is the only required column
        hash_id = cur.fetchone()[0]
        cur.execute("INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                    " VALUES (%s, %s, %s) RETURNING id", (hash_id, source_id, side_id))
        context_id = cur.fetchone()[0]
        cur.execute("INSERT INTO paths (file_name, file_type, file_path, file_size,"
                    " file_date, date_creation, context_id)"
                    " VALUES (%s, 'txt', %s, 0, CURRENT_DATE, CURRENT_DATE, %s)"
                    " RETURNING id",
                    (f"{marker}.txt", f"/tmp/{marker}.txt", context_id))
    return hash_id


def test_deleting_a_side_in_use_is_refused_not_a_server_error(app, admin_client, pg_db):
    tag = uuid.uuid4().hex[:8]
    source_id, side_id = _source(admin_client, f"side_del_src_{tag}"), _side(admin_client, f"side_del_{tag}")
    _attach_document(pg_db, source_id, side_id, f"side_del_{tag}")

    resp = admin_client.delete(f"/api/sides/{side_id}")
    assert resp.status_code == 409, (resp.status_code, resp.get_data(as_text=True)[:300])
    body = resp.get_json()
    assert body["success"] is False and "in use" in body["error"]


def test_deleting_an_unused_side_succeeds(app, admin_client):
    tag = uuid.uuid4().hex[:8]
    side_id = _side(admin_client, f"side_free_{tag}")
    resp = admin_client.delete(f"/api/sides/{side_id}")
    assert resp.status_code == 200, (resp.status_code, resp.get_data(as_text=True)[:300])
    assert admin_client.get(f"/api/sides/{side_id}").status_code == 404
