"""Replace the two-level hierarchy trigger with a cycle guard.

What this migration is
======================

``categorys.parent_id`` has existed since ``m0002``, but the trigger that guards
it was written for the *legacy two-level* screen: a parent had to be a main
category, and a category with children could not become a subcategory. Its
effect, measured:

    INSERT INTO categorys (word_id, parent_id) VALUES (<level 3 word>, <level 2 id>);
    ERROR:  A subcategory's parent must be a main category (no parent)

That makes a real tree (depth 20, five-level nesting, moving a subtree) impossible
at the database level, whatever the application does. The owner authorised one
additive migration to replace the *function body*; ``m0001``-``m0003`` are not
edited, no table, column, index or constraint is added or removed, and the
trigger itself (``trg_categorys_hierarchy_check``, BEFORE INSERT OR UPDATE) is
untouched - only the rule it enforces changes, from "depth <= 2" to "no cycles".

What is enforced now
====================

* a category can never be its own parent (the ``categorys_check`` constraint
  already says so; this repeats it with a clear message before the recursive
  walk, which would otherwise have to find it);
* a category can never be moved under one of its own descendants, at any depth.
  The walk is bounded (``depth < 10000``) so a database that already contains a
  cycle cannot hang an insert.

How the walk costs
==================

One index scan per ancestor level (``idx_categorys_parent_id``). For a tree of
depth *d* that is *d* probes per write; the scale test in
``tests/integration/test_category_hierarchy_scale.py`` measures it at 100 000
nodes.
"""

version = "0004"
name = "category_hierarchy_depth"

#: The body that ``m0001_baseline.py`` installed. Restored by ``downgrade`` so
#: this migration is reversible (DB-02).
_ORIGINAL_BODY = """
    DECLARE
        v_parent_parent_id INTEGER;
        v_child_count INTEGER;
    BEGIN
        -- CHECK (id != parent_id) handles self-parent
        -- Prevent grandchild: parent must be a main category (no parent)
        IF NEW.parent_id IS NOT NULL THEN
            SELECT parent_id INTO v_parent_parent_id FROM categorys WHERE id = NEW.parent_id;
            IF v_parent_parent_id IS NOT NULL THEN
                RAISE EXCEPTION 'A subcategory''s parent must be a main category (no parent)'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        -- Prevent making a main category with children into a subcategory
        IF NEW.parent_id IS NOT NULL THEN
            SELECT count(*) INTO v_child_count FROM categorys WHERE parent_id = NEW.id;
            IF v_child_count > 0 THEN
                RAISE EXCEPTION 'A category with children cannot become a subcategory'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
"""

_CYCLE_GUARD_BODY = """
    DECLARE
        v_would_cycle BOOLEAN;
    BEGIN
        -- A root has nothing to check.
        IF NEW.parent_id IS NULL THEN
            RETURN NEW;
        END IF;

        IF NEW.parent_id = NEW.id THEN
            RAISE EXCEPTION 'A category cannot be its own parent (cycle)'
                USING ERRCODE = '23514';
        END IF;

        -- Walk up from the proposed parent. If the walk reaches this row, the
        -- write would close a loop. Bounded so a pre-existing cycle cannot hang.
        WITH RECURSIVE ancestors AS (
            SELECT c.id, c.parent_id, 1 AS depth
              FROM categorys c
             WHERE c.id = NEW.parent_id
            UNION ALL
            SELECT p.id, p.parent_id, a.depth + 1
              FROM categorys p
              JOIN ancestors a ON p.id = a.parent_id
             WHERE a.depth < 10000
        )
        SELECT EXISTS (SELECT 1 FROM ancestors WHERE id = NEW.id)
          INTO v_would_cycle;

        IF v_would_cycle THEN
            RAISE EXCEPTION 'A category cannot be moved under its own descendant (cycle)'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END;
"""


def _replace_function(conn, body: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE OR REPLACE FUNCTION categorys_hierarchy_check() RETURNS trigger AS $$"
            + body
            + "$$ LANGUAGE plpgsql;"
        )


def upgrade(conn) -> None:
    _replace_function(conn, _CYCLE_GUARD_BODY)


def downgrade(conn) -> None:
    """Put the two-level rule back (best effort, DB-02).

    Refuses nothing: if the database already holds a tree deeper than two
    levels, the restored trigger will reject only *new* writes, and the
    function definition itself is still restorable.
    """
    _replace_function(conn, _ORIGINAL_BODY)
