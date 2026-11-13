"""Isolation canary (2 of 2): the previous module's rows and session are gone.

The partner module plants a marker row and leaves a connection idle in
transaction. Whichever order the two run in, by the time this module's tests
execute:

* the marker must be absent - the reset emptied the tables;
* an application query must work - the reset terminated the idle session and
  the pool recovered instead of handing out a dead socket;
* the marker must still be absent after a query, so nothing resurrects it.

Run before the planter this module passes trivially; run after it (the
directory order) it is the assertion that would fail if the reset stopped
working. ``TEST_EDITS.md`` records both orders and the negative control.
"""

from __future__ import annotations

import psycopg2
import pytest

pytestmark = pytest.mark.integration

MARKER = "isolation-canary-residue"


def _marker_rows(cfg) -> int:
    conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM words WHERE word = %s", (MARKER,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_the_previous_module_row_is_not_visible_here(pg_db):
    assert _marker_rows(pg_db) == 0, (
        "a row planted by test_isolation_canary_plants survived the module "
        "boundary: the reset fixture did not run")


def test_the_application_pool_works_after_the_boundary(pg_db):
    """A terminated session must not stay in the pool as a dead connection."""
    from Api.utils import execute_query

    assert execute_query("SELECT 1", fetch="one") == (1,)
    assert execute_query("SELECT count(*) FROM words WHERE word = %s", (MARKER,)) \
        .__class__ is list


def test_this_module_is_treated_as_a_database_module(request):
    from suite_isolation import module_needs_database

    assert module_needs_database(request.node.path, request.session.items) is True
