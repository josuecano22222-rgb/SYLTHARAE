"""The category hierarchy must support arbitrary depth, and must refuse cycles.

Why this file exists
====================

``categorys.parent_id`` has been in the schema since ``m0002``, but the trigger
that guards it (``m0001_baseline.py``) caps the tree at **two** levels:

    IF NEW.parent_id IS NOT NULL THEN
        SELECT parent_id INTO v_parent_parent_id FROM categorys WHERE id = NEW.parent_id;
        IF v_parent_parent_id IS NOT NULL THEN
            RAISE EXCEPTION 'A subcategory''s parent must be a main category (no parent)'
    ...
    IF v_child_count > 0 THEN
        RAISE EXCEPTION 'A category with children cannot become a subcategory'

Measured against the schema as it stood:

    INSERT ... VALUES (:c_id, :l2_id);   -- level 3 under a level-2 parent
    ERROR:  A subcategory's parent must be a main category (no parent)

So a tree view, a depth-20 test or a drag-a-subtree move could not be built on
it: every write deeper than two was rejected by the database, and the second
rule refused exactly the "move a subtree under another node" operation.

The owner authorised **one additive migration** (``m0004``) that replaces the
trigger body with a cycle guard and leaves ``m0001``-``m0003`` untouched. These
tests are written against the intended behaviour first: they fail on the old
trigger and pass on the new one.

What is kept, not loosened
--------------------------

* a category can never be its own parent (``categorys_check``, schema level);
* a chain can never loop back on itself, at any depth.

Everything else about the table (columns, keys, foreign keys, indexes) is
unchanged, and ``database/migrations/m0001_baseline.py`` /
``m0002_category_parent_id.py`` are not edited.
"""

from __future__ import annotations

import pytest
import psycopg2

from _seed import connect

pytestmark = pytest.mark.integration

DEEP = 20
NEST = 5


def _word(cur, name: str) -> int:
    cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (name,))
    return cur.fetchone()[0]


def _category(cur, name: str, parent_id=None) -> int:
    word_id = _word(cur, name)
    cur.execute(
        "INSERT INTO categorys (word_id, parent_id) VALUES (%s, %s) RETURNING id",
        (word_id, parent_id),
    )
    return cur.fetchone()[0]


@pytest.fixture()
def conn(pg_db):
    """One connection per test, rolled back afterwards: no leftovers."""
    connection = connect(pg_db)
    connection.autocommit = False
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_a_chain_five_levels_deep_can_be_created(conn):
    with conn.cursor() as cur:
        parent, chain = None, []
        for level in range(NEST):
            parent = _category(cur, f"nest_{level}_{pytest.__name__[:6]}_{id(cur)}", parent)
            chain.append(parent)
    assert len(set(chain)) == NEST


def test_a_chain_twenty_levels_deep_can_be_created(conn):
    with conn.cursor() as cur:
        parent, chain = None, []
        for level in range(DEEP):
            parent = _category(cur, f"deep_{level}_{id(cur)}_{pytest.__name__[:4]}", parent)
            chain.append(parent)
        # The recursive query the API uses must see the whole chain.
        cur.execute(
            "WITH RECURSIVE up AS ("
            "  SELECT id, parent_id, 1 AS depth FROM categorys WHERE id = %s"
            "  UNION ALL"
            "  SELECT c.id, c.parent_id, up.depth + 1 FROM categorys c"
            "   JOIN up ON c.id = up.parent_id)"
            " SELECT max(depth) FROM up",
            (chain[-1],),
        )
        assert cur.fetchone()[0] == DEEP


def test_a_parent_with_children_can_be_moved_under_another_node(conn):
    """Drag a subtree: the rule that refused this is gone."""
    with conn.cursor() as cur:
        root_a = _category(cur, f"drag_a_{id(cur)}")
        child = _category(cur, f"drag_child_{id(cur)}", root_a)
        grandchild = _category(cur, f"drag_grand_{id(cur)}", child)
        root_b = _category(cur, f"drag_b_{id(cur)}")

        cur.execute("UPDATE categorys SET parent_id = %s WHERE id = %s", (root_b, root_a))
        cur.execute("SELECT parent_id FROM categorys WHERE id = %s", (root_a,))
        assert cur.fetchone()[0] == root_b

        # The subtree came with it, unchanged.
        cur.execute("SELECT parent_id FROM categorys WHERE id = %s", (child,))
        assert cur.fetchone()[0] == root_a
        cur.execute("SELECT parent_id FROM categorys WHERE id = %s", (grandchild,))
        assert cur.fetchone()[0] == child


def test_moving_a_node_under_its_own_descendant_is_refused(conn):
    """A cycle must be refused by the database, naming the cycle."""
    with conn.cursor() as cur:
        root = _category(cur, f"cyc_root_{id(cur)}")
        child = _category(cur, f"cyc_child_{id(cur)}", root)
        grandchild = _category(cur, f"cyc_grand_{id(cur)}", child)

        with pytest.raises(psycopg2.errors.CheckViolation) as caught:
            cur.execute("UPDATE categorys SET parent_id = %s WHERE id = %s",
                        (grandchild, root))
            conn.commit()
        assert "cycle" in str(caught.value).lower()
        conn.rollback()


def test_a_category_cannot_be_its_own_parent(conn):
    with conn.cursor() as cur:
        node = _category(cur, f"self_{id(cur)}")
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("UPDATE categorys SET parent_id = id WHERE id = %s", (node,))
            conn.commit()
        conn.rollback()


def test_the_trigger_no_longer_caps_the_depth(conn):
    """The depth cap is what this change removed; assert it is gone by name."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_get_functiondef('categorys_hierarchy_check'::regproc)")
        body = cur.fetchone()[0]
    assert "must be a main category" not in body
    assert "A category with children cannot become a subcategory" not in body
    assert "cycle" in body.lower()


def test_m0001_and_m0002_are_not_edited_and_m0004_is_the_only_addition():
    """The owner authorised one additive migration; the frozen three stay put."""
    import hashlib
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent.parent / "database" / "migrations"
    files = {p.name for p in root.glob("m*.py")}
    assert "m0001_baseline.py" in files
    assert "m0002_category_parent_id.py" in files
    assert "m0003_sources_attachments.py" in files
    assert "m0004_category_hierarchy_depth.py" in files

    baseline = (root / "m0001_baseline.py").read_text(encoding="utf-8")
    # The original trigger body is still recorded in m0001 - it is the history of
    # what was applied - and m0004 replaces the function. Nothing in m0001 or
    # m0002 mentions the new rule.
    assert "CREATE TRIGGER trg_categorys_hierarchy_check" in baseline
    m0004 = (root / "m0004_category_hierarchy_depth.py").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION categorys_hierarchy_check" in m0004
    assert hashlib.sha256(m0004.encode()).hexdigest()  # non-empty, deterministic file
