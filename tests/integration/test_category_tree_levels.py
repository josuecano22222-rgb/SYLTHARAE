"""The lazy-loading endpoints: roots, children, per-node stats.

Painting the tree must not cost the whole tree. These endpoints answer one level
at a time, so the first paint is the size of the top level and an expansion is
the size of one node's children. ``/stats`` is the only read that walks a
subtree, and it is asked for explicitly.

Run red first: against the previous surface every test here failed with 404
(``/api/categories/tree/roots`` did not exist), which is what "the endpoint is
new" looks like from a test's point of view.
"""
from __future__ import annotations

import itertools

SEQ = itertools.count()

ROOTS = "/api/categories/tree/roots"


def _name(prefix="lvl"):
    return f"{prefix}-{next(SEQ)}-{id(SEQ) % 997}"


def _create(client, name, *, mode="root", parent_id=None):
    body = {"name": name, "mode": mode}
    if parent_id is not None:
        body["parent_id"] = parent_id
    resp = client.post("/api/categories/tree", json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["node"]


def test_roots_returns_only_the_top_level(admin_client):
    root = _create(admin_client, _name("roots"))
    child = _create(admin_client, _name("roots-child"), mode="child",
                    parent_id=root["id"])

    resp = admin_client.get(ROOTS, query_string={"limit": 500})
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.get_json()["rows"]}
    assert root["id"] in rows
    assert child["id"] not in rows, "a child must not appear in the roots level"
    assert rows[root["id"]]["depth"] == 1
    assert rows[root["id"]]["parent_id"] is None
    assert rows[root["id"]]["child_count"] >= 1
    assert "total_terms" not in rows[root["id"]], (
        "the level endpoint must not compute subtree totals")


def test_children_returns_one_nodes_direct_children_with_their_path(admin_client):
    root = _create(admin_client, _name("kids"))
    first = _create(admin_client, _name("kids-a"), mode="child", parent_id=root["id"])
    second = _create(admin_client, _name("kids-b"), mode="child", parent_id=root["id"])
    grandchild = _create(admin_client, _name("kids-a1"), mode="child",
                         parent_id=first["id"])

    resp = admin_client.get(f"/api/categories/tree/{root['id']}/children")
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.get_json()["rows"]}
    assert set(rows) == {first["id"], second["id"]}, "only direct children belong here"
    assert grandchild["id"] not in rows
    assert rows[first["id"]]["path"] == f"{root['name']} / {first['name']}"
    assert rows[first["id"]]["depth"] == 2
    assert rows[first["id"]]["child_count"] == 1


def test_children_of_an_unknown_node_is_a_404(admin_client):
    resp = admin_client.get("/api/categories/tree/99999999/children")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "not_found"

    resp = admin_client.get("/api/categories/tree/abc/children")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_id"


def test_children_paginate_without_repeating_a_row(admin_client):
    root = _create(admin_client, _name("page-kids"))
    created = [_create(admin_client, f"{_name('kid')}", mode="child",
                       parent_id=root["id"]) for _ in range(5)]

    seen, cursor, pages = [], None, 0
    while True:
        query = {"limit": 2}
        if cursor:
            query["cursor"] = cursor
        payload = admin_client.get(f"/api/categories/tree/{root['id']}/children",
                                   query_string=query).get_json()
        pages += 1
        seen.extend(row["id"] for row in payload["rows"])
        cursor = payload["next_cursor"]
        if not cursor:
            break
        assert pages < 10
    assert len(seen) == len(set(seen)), "a child was returned on two pages"
    assert set(seen) == {node["id"] for node in created}
    assert pages == 3


def test_stats_returns_descendant_count_and_total_terms(admin_client, pg_db):
    from tests.integration import _seed

    root = _create(admin_client, _name("stats-root"))
    child = _create(admin_client, _name("stats-child"), mode="child", parent_id=root["id"])
    leaf = _create(admin_client, _name("stats-leaf"), mode="child", parent_id=child["id"])

    conn = _seed.connect(pg_db)
    try:
        with conn.cursor() as cur:
            for category_id, how_many in ((root["id"], 1), (leaf["id"], 4)):
                for n in range(how_many):
                    word = f"stats-{category_id}-{n}-{next(SEQ)}"
                    cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (word,))
                    cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                                " VALUES (%s, %s)", (cur.fetchone()[0], category_id))
        conn.commit()
    finally:
        conn.close()

    payload = admin_client.get(f"/api/categories/tree/{root['id']}/stats").get_json()["stats"]
    assert payload["descendant_count"] == 2
    assert payload["total_terms"] == 5
    assert payload["own_terms"] == 1
    assert payload["child_count"] == 1

    leaf_stats = admin_client.get(f"/api/categories/tree/{leaf['id']}/stats").get_json()["stats"]
    assert leaf_stats["descendant_count"] == 0
    assert leaf_stats["total_terms"] == 4


def test_stats_of_an_unknown_node_is_a_404(admin_client):
    resp = admin_client.get("/api/categories/tree/99999999/stats")
    assert resp.status_code == 404


def test_whole_tree_answers_a_warning_when_it_is_large(admin_client, monkeypatch):
    """The full endpoint stays, but says when it is the wrong tool."""
    from services.categories import tree as tree_service

    monkeypatch.setattr(tree_service, "FULL_TREE_WARN", 1)
    payload = admin_client.get("/api/categories/tree", query_string={"limit": 5}).get_json()
    assert "warning" in payload
    assert "children" in payload["warning"]


def test_a_reader_may_use_the_level_endpoints(viewer_client):
    assert viewer_client.get(ROOTS).status_code == 200
    assert viewer_client.get("/api/categories/tree/99999999/children").status_code == 404
