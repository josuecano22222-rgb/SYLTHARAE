"""The category tree API: reads, writes, refusals and authorisation.

Written before the endpoints existed; every test here was red against the
previous surface (there was no ``/api/categories/tree`` at all, so the first run
failed with 404 rather than a wrong payload).

The fixtures are real rows in the real schema - a chain of categories is built
through the API itself, term counts come from ``words_categorys`` inserts, and
the cycle rules are checked against the trigger installed by ``m0004`` as well as
the readable refusal the service produces first.
"""
from __future__ import annotations

import itertools

import pytest

from tests.integration import _seed

SEQ = itertools.count()

TREE = "/api/categories/tree"


def _name(prefix="cat"):
    return f"{prefix}-{next(SEQ):04d}-{id(SEQ) % 1000}"


def _create(client, name, *, mode="root", parent_id=None, children=None):
    body = {"name": name, "mode": mode}
    if parent_id is not None:
        body["parent_id"] = parent_id
    if children is not None:
        body["children"] = children
    resp = client.post(TREE, json=body)
    assert resp.status_code == 201, (resp.status_code, resp.get_data(as_text=True))
    payload = resp.get_json()
    node = payload["node"]
    node["children"] = payload.get("children", [])
    return node


def _all_rows(client, **params):
    """Walk every page of the tree and return the rows in path order."""
    rows, cursor = [], None
    for _ in range(50):
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        resp = client.get(TREE, query_string=query)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.get_json()
        rows.extend(payload["rows"])
        cursor = payload["next_cursor"]
        if not cursor:
            return rows
    raise AssertionError("pagination did not terminate")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def test_tree_requires_authentication(client):
    resp = client.get(TREE)
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "unauthenticated"


def test_tree_reports_depth_and_materialised_path(admin_client):
    root = _create(admin_client, _name("depth-root"))
    child = _create(admin_client, _name("depth-child"), mode="child", parent_id=root["id"])
    grandchild = _create(admin_client, _name("depth-grand"),
                         mode="child", parent_id=child["id"])

    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[root["id"]]["depth"] == 1
    assert rows[child["id"]]["depth"] == 2
    assert rows[grandchild["id"]]["depth"] == 3
    assert rows[grandchild["id"]]["path"] == " / ".join(
        [root["name"], child["name"], grandchild["name"]])
    assert rows[grandchild["id"]]["path_ids"] == [root["id"], child["id"], grandchild["id"]]
    assert rows[child["id"]]["parent_id"] == root["id"]


def test_tree_counts_own_and_total_terms_through_descendants(admin_client, pg_db):
    root = _create(admin_client, _name("term-root"))
    child = _create(admin_client, _name("term-child"), mode="child", parent_id=root["id"])
    leaf = _create(admin_client, _name("term-leaf"), mode="child", parent_id=child["id"])

    conn = _seed.connect(pg_db)
    try:
        with conn.cursor() as cur:
            for category_id, how_many in ((root["id"], 2), (child["id"], 3), (leaf["id"], 5)):
                for n in range(how_many):
                    word = f"term-{category_id}-{n}-{next(SEQ)}"
                    cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (word,))
                    word_id = cur.fetchone()[0]
                    cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                                " VALUES (%s, %s)", (word_id, category_id))
        conn.commit()
    finally:
        conn.close()

    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[leaf["id"]]["own_terms"] == 5
    assert rows[leaf["id"]]["total_terms"] == 5
    assert rows[child["id"]]["own_terms"] == 3
    assert rows[child["id"]]["total_terms"] == 8
    assert rows[child["id"]]["descendant_count"] == 1
    assert rows[root["id"]]["own_terms"] == 2
    assert rows[root["id"]]["total_terms"] == 10
    assert rows[root["id"]]["descendant_count"] == 2
    assert rows[root["id"]]["child_count"] == 1


def test_tree_pagination_is_keyset_and_complete(admin_client):
    root = _create(admin_client, _name("page-root"), children=["a", "b", "c"])
    names = {root["name"]} | {child["name"] for child in root["children"]}

    pages, seen, cursor = [], [], None
    for _ in range(50):
        query = {"limit": 2}
        if cursor:
            query["cursor"] = cursor
        payload = admin_client.get(TREE, query_string=query).get_json()
        pages.append(len(payload["rows"]))
        seen.extend(row["name"] for row in payload["rows"])
        cursor = payload["next_cursor"]
        if not cursor:
            break
    else:
        raise AssertionError("pagination did not terminate")

    assert len(pages) > 1, "the tree fit on one page; the page size was not honoured"
    assert max(pages) == 2
    assert len(seen) >= 4
    assert len(seen) == len(set(seen)), "a node was returned on two pages"
    assert names <= set(seen)
    # Path order: the parent is emitted before its children.
    assert seen.index(root["name"]) < seen.index("a")


def test_tree_search_returns_hits_and_their_ancestors(admin_client):
    token = f"needle{next(SEQ)}"
    root = _create(admin_client, f"{token}-root")
    middle = _create(admin_client, _name("search-mid"), mode="child", parent_id=root["id"])
    hit = _create(admin_client, f"{token}-deep", mode="child", parent_id=middle["id"])
    _create(admin_client, _name("search-other"))

    payload = admin_client.get(TREE, query_string={"search": token}).get_json()
    rows = {row["id"]: row for row in payload["rows"]}
    assert set(rows) == {root["id"], middle["id"], hit["id"]}
    assert rows[hit["id"]]["is_match"] is True
    assert rows[root["id"]]["is_match"] is True
    assert rows[middle["id"]]["is_match"] is False, "an ancestor must come back as context"


def test_tree_single_node_lookup_and_unknown_id(admin_client):
    node = _create(admin_client, _name("one"))
    resp = admin_client.get(f"{TREE}/{node['id']}")
    assert resp.status_code == 200
    assert resp.get_json()["node"]["id"] == node["id"]

    missing = admin_client.get(f"{TREE}/99999999")
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def test_create_child_requires_a_parent(admin_client):
    resp = admin_client.post(TREE, json={"name": _name("orphan"), "mode": "child"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "missing_parent"


def test_batch_children_are_created_under_the_new_node(admin_client):
    names = [_name("batch-a"), _name("batch-b"), _name("batch-c")]
    node = _create(admin_client, _name("batch-root"), children=names)
    rows = {row["id"]: row for row in _all_rows(admin_client)}
    child_ids = [child["id"] for child in node["children"]]
    assert len(child_ids) == 3
    for child_id in child_ids:
        assert rows[child_id]["parent_id"] == node["id"]
        assert rows[child_id]["depth"] == 2
    assert {rows[cid]["name"] for cid in child_ids} == set(names)


def test_duplicate_sibling_name_is_refused(admin_client):
    parent = _create(admin_client, _name("dup-root"))
    name = _name("dup-child")
    _create(admin_client, name, mode="child", parent_id=parent["id"])
    resp = admin_client.post(TREE, json={"name": name, "mode": "child",
                                        "parent_id": parent["id"]})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "sibling_name_taken"
    assert body["details"]["existing_id"]


def test_sibling_name_clash_is_case_insensitive(admin_client):
    parent = _create(admin_client, _name("case-root"))
    base = _name("CaseChild")
    _create(admin_client, base, mode="child", parent_id=parent["id"])
    resp = admin_client.post(TREE, json={"name": base.lower(), "mode": "child",
                                        "parent_id": parent["id"]})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "sibling_name_taken"


def test_a_name_already_used_elsewhere_is_refused_with_the_owner(admin_client):
    """``categorys.word_id`` and ``words.word`` are both UNIQUE, so a name is
    global to the vocabulary, not just to the siblings."""
    first = _create(admin_client, _name("global-root"))
    other_parent = _create(admin_client, _name("global-other"))
    resp = admin_client.post(TREE, json={"name": first["name"], "mode": "child",
                                        "parent_id": other_parent["id"]})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "name_in_use"
    assert body["details"]["existing_id"] == first["id"]


def test_create_parent_above_adopts_the_chosen_categories(admin_client):
    first = _create(admin_client, _name("adopt-a"))
    second = _create(admin_client, _name("adopt-b"))
    resp = admin_client.post(f"{TREE}/parent-above", json={
        "name": _name("adopt-new"), "child_ids": [first["id"], second["id"]]})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    new_id = body["node"]["id"]
    assert body["moved"] == [first["id"], second["id"]]
    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[new_id]["depth"] == 1
    for node_id in (first["id"], second["id"]):
        assert rows[node_id]["parent_id"] == new_id
        assert rows[node_id]["depth"] == 2


def test_create_parent_above_refuses_an_empty_child_list(admin_client):
    resp = admin_client.post(f"{TREE}/parent-above",
                             json={"name": _name("adopt-none"), "child_ids": []})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_children"


def test_move_reparents_a_whole_subtree(admin_client):
    old_parent = _create(admin_client, _name("move-old"))
    new_parent = _create(admin_client, _name("move-new"))
    subtree = _create(admin_client, _name("move-sub"), mode="child",
                      parent_id=old_parent["id"])
    leaf = _create(admin_client, _name("move-leaf"), mode="child", parent_id=subtree["id"])

    resp = admin_client.post(f"{TREE}/{subtree['id']}/move",
                             json={"parent_id": new_parent["id"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["previous_parent_id"] == old_parent["id"]

    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[subtree["id"]]["parent_id"] == new_parent["id"]
    assert rows[leaf["id"]]["parent_id"] == subtree["id"]
    assert rows[leaf["id"]]["depth"] == 3
    assert rows[leaf["id"]]["path"].startswith(new_parent["name"])


def test_move_to_root_is_allowed(admin_client):
    root = _create(admin_client, _name("promote-root"))
    child = _create(admin_client, _name("promote-child"), mode="child",
                    parent_id=root["id"])
    resp = admin_client.post(f"{TREE}/{child['id']}/move", json={"parent_id": None})
    assert resp.status_code == 200
    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[child["id"]]["parent_id"] is None
    assert rows[child["id"]]["depth"] == 1


def test_move_under_itself_is_refused(admin_client):
    node = _create(admin_client, _name("self-root"))
    resp = admin_client.post(f"{TREE}/{node['id']}/move", json={"parent_id": node["id"]})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "cycle"


def test_move_under_its_own_descendant_is_refused(admin_client):
    root = _create(admin_client, _name("cycle-root"))
    child = _create(admin_client, _name("cycle-child"), mode="child",
                    parent_id=root["id"])
    grandchild = _create(admin_client, _name("cycle-grand"), mode="child",
                         parent_id=child["id"])
    resp = admin_client.post(f"{TREE}/{root['id']}/move",
                             json={"parent_id": grandchild["id"]})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "cycle"
    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[root["id"]]["parent_id"] is None, "the refusal must not half-apply"


def test_move_onto_a_sibling_name_clash_is_refused(admin_client):
    left = _create(admin_client, _name("clash-left"))
    right = _create(admin_client, _name("clash-right"))
    shared = _name("clash-shared")
    moved = _create(admin_client, shared, mode="child", parent_id=left["id"])
    _create(admin_client, shared.upper(), mode="child", parent_id=right["id"])

    resp = admin_client.post(f"{TREE}/{moved['id']}/move",
                             json={"parent_id": right["id"]})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "sibling_name_taken"


def test_delete_blocks_a_node_with_children(admin_client):
    root = _create(admin_client, _name("del-root"))
    _create(admin_client, _name("del-child"), mode="child", parent_id=root["id"])
    resp = admin_client.post(f"{TREE}/{root['id']}/delete", json={"mode": "block"})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "has_children"
    assert body["details"]["child_count"] == 1
    assert admin_client.get(f"{TREE}/{root['id']}").status_code == 200


def test_delete_with_reparent_moves_children_up(admin_client):
    root = _create(admin_client, _name("rep-root"))
    child = _create(admin_client, _name("rep-child"), mode="child", parent_id=root["id"])
    resp = admin_client.post(f"{TREE}/{root['id']}/delete", json={"mode": "reparent"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["children_moved"] == 1
    assert body["children_moved_to"] is None
    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert root["id"] not in rows
    assert rows[child["id"]]["parent_id"] is None
    assert rows[child["id"]]["depth"] == 1


def test_delete_with_explicit_reparent_target(admin_client):
    keeper = _create(admin_client, _name("keep-root"))
    doomed = _create(admin_client, _name("doom-root"))
    child = _create(admin_client, _name("doom-child"), mode="child", parent_id=doomed["id"])
    resp = admin_client.post(f"{TREE}/{doomed['id']}/delete",
                             json={"mode": "reparent", "reparent_to": keeper["id"]})
    assert resp.status_code == 200
    rows = {row["id"]: row for row in _all_rows(admin_client)}
    assert rows[child["id"]]["parent_id"] == keeper["id"]


def test_delete_refuses_reparenting_into_its_own_subtree(admin_client):
    root = _create(admin_client, _name("doomcycle-root"))
    child = _create(admin_client, _name("doomcycle-child"), mode="child",
                    parent_id=root["id"])
    grand = _create(admin_client, _name("doomcycle-grand"), mode="child",
                    parent_id=child["id"])
    resp = admin_client.post(f"{TREE}/{root['id']}/delete",
                             json={"mode": "reparent", "reparent_to": grand["id"]})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "cycle"


def test_delete_of_a_leaf_removes_it(admin_client):
    leaf = _create(admin_client, _name("leaf-root"))
    resp = admin_client.post(f"{TREE}/{leaf['id']}/delete", json={"mode": "block"})
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == leaf["id"]
    assert admin_client.get(f"{TREE}/{leaf['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Refusals: malformed bodies, authorisation
# ---------------------------------------------------------------------------

def test_non_object_json_body_is_refused(admin_client):
    resp = admin_client.post(TREE, json=["not", "an", "object"])
    assert resp.status_code == 400
    assert resp.get_json()["code"] in ("invalid_body", "invalid_json", "invalid_json_body")


@pytest.mark.parametrize("body", [
    {},
    {"name": ""},
    {"name": "   "},
    {"name": 42},
    {"name": "x" * 300},
    {"name": "ok", "mode": "upside-down"},
    {"name": "ok", "mode": "child", "parent_id": "abc"},
    {"name": "ok", "mode": "child", "parent_id": True},
    {"name": "ok", "mode": "child", "parent_id": 99999999},
    {"name": "ok", "children": "not-a-list"},
])
def test_invalid_create_bodies_are_refused_with_4xx(admin_client, body):
    resp = admin_client.post(TREE, json=body)
    assert 400 <= resp.status_code < 500, (resp.status_code, resp.get_data(as_text=True))
    assert "error" in resp.get_json()


@pytest.mark.parametrize("path,body", [
    (f"{TREE}/abc/move", {"parent_id": None}),
    (f"{TREE}/99999999/move", {"parent_id": None}),
    (f"{TREE}/abc/delete", {"mode": "block"}),
    (f"{TREE}/1/move", {}),
    (f"{TREE}/1/delete", {"mode": "explode"}),
])
def test_invalid_node_operations_are_refused_with_4xx(admin_client, path, body):
    resp = admin_client.post(path, json=body)
    assert 400 <= resp.status_code < 500, (resp.status_code, resp.get_data(as_text=True))


def test_a_non_numeric_id_on_the_node_route_is_a_json_400(admin_client):
    """``probe_param_routes`` found the app-wide 404 handler answering an
    ``/api/`` path with the HTML 404 page, because ``<int:node_id>`` refuses to
    match and routing falls through. A JSON surface must answer JSON."""
    resp = admin_client.get(f"{TREE}/abc")
    assert resp.status_code == 400, (resp.status_code, resp.get_data(as_text=True)[:120])
    assert resp.is_json, resp.get_data(as_text=True)[:120]
    assert resp.get_json()["code"] == "invalid_id"


def test_an_unknown_node_is_reported_before_the_body_is_judged(admin_client):
    """The same probe: ``POST .../999999999/move`` with a body that has no
    ``parent_id`` answered 400 about the body while the node did not exist at
    all. The resource is checked first, so the answer names the real problem."""
    resp = admin_client.post(f"{TREE}/99999999/move", json={})
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "not_found"

    resp = admin_client.post(f"{TREE}/99999999/delete", json={})
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "not_found"


def test_a_control_character_in_a_name_is_refused_not_a_500(admin_client):
    """Found by ``probe_malformed``: a NUL byte in the name reached psycopg2,
    which refuses to send it, and the request answered 500. A name that cannot
    be stored is a client error, and refusal must not depend on the driver."""
    resp = admin_client.post(TREE, json={"name": "bad\u0000name"})
    assert resp.status_code == 400, (resp.status_code, resp.get_data(as_text=True))
    assert resp.get_json()["code"] in ("invalid_name", "invalid_characters")


def test_other_control_characters_are_refused_in_a_name(admin_client):
    resp = admin_client.post(TREE, json={"name": "bad\u0007bell"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] in ("invalid_name", "invalid_characters")


def test_a_viewer_may_read_the_tree_but_not_write_it(viewer_client):
    assert viewer_client.get(TREE).status_code == 200
    resp = viewer_client.post(TREE, json={"name": _name("viewer-no")})
    assert resp.status_code == 403


def test_an_anonymous_client_cannot_write(client):
    resp = client.post(TREE, json={"name": _name("anon-no")})
    assert resp.status_code == 401


def test_depth_twenty_round_trip(admin_client):
    node = _create(admin_client, _name("deep-00"))
    chain = [node]
    for level in range(1, 20):
        node = _create(admin_client, _name(f"deep-{level:02d}"), mode="child",
                       parent_id=node["id"])
        chain.append(node)

    rows = {row["id"]: row for row in _all_rows(admin_client)}
    deepest = rows[chain[-1]["id"]]
    assert deepest["depth"] == 20
    assert deepest["path_ids"] == [item["id"] for item in chain]
    assert deepest["path"].count(" / ") == 19
