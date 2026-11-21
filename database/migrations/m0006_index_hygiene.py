"""Index hygiene: one redundant index out, two missing foreign-key indexes in.

Two problems measured on an installed database (43 tables, 159 indexes):

1. ``paths.idx_paths_hash_id`` is declared as
   ``CREATE INDEX ... ON paths (context_id) INCLUDE (id) WHERE context_id IS NOT NULL``
   in ``m0001``. It indexes ``context_id`` - there is no ``hash_id`` column on
   ``paths`` and there has not been one for several schema revisions - so the
   name describes nothing, and it duplicates ``idx_paths_context_id``. Two
   btrees are written on every insert and updated on every ``context_id``
   change; one of them has a name that sends the next reader looking for a
   column that does not exist.

2. ``alerts.scenario_id`` and ``alerts.scenario_evaluation_id`` are foreign
   keys with no index. PostgreSQL does not index the referencing side
   automatically, so deleting a scenario (or re-evaluating one) scans ``alerts``
   row by row to enforce the constraint - an O(n) table scan per deleted parent,
   and the alerts table is the one that grows without bound.

The migration is reversible: the downgrade re-creates the dropped index exactly
as ``m0001`` defined it and drops the two new ones. Nothing in ``m0001``-``m0005``
is edited.
"""

version = "0006"
name = "index_hygiene"

#: Exactly the definition in m0001, kept so the downgrade restores the schema
#: the previous release produced.
_REDUNDANT_INDEX_DDL = (
    "CREATE INDEX idx_paths_hash_id ON paths (context_id) INCLUDE (id) "
    "WHERE context_id IS NOT NULL"
)


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        # IF EXISTS: a database created before m0001 introduced the index (or one
        # where an operator already removed it) must still migrate cleanly.
        cur.execute("DROP INDEX IF EXISTS idx_paths_hash_id")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_scenario_id "
                    "ON alerts (scenario_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_scenario_evaluation_id "
                    "ON alerts (scenario_evaluation_id)")


def downgrade(conn) -> None:
    """Restore the redundant index, remove the two additions."""
    with conn.cursor() as cur:
        cur.execute("DROP INDEX IF EXISTS idx_alerts_scenario_evaluation_id")
        cur.execute("DROP INDEX IF EXISTS idx_alerts_scenario_id")
        cur.execute(_REDUNDANT_INDEX_DDL)
