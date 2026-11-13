"""Isolation canary (1 of 2): plant a row another module must not see.

The suite empties every data table at a module boundary (see
``tests/suite_isolation.py``). That is the fix for a real defect - a module's
ingested corpus changed what a later module's resolver returned - and a fix
nobody checks is a fix that quietly stops working: a refactor that makes the
fixture no-op leaves every test green while the leakage comes back.

This module plants a marker row and leaves a connection *idle in transaction*
on the application's pool, which is the state the boundary has to clear. Its
partner, ``test_isolation_canary_asserts.py``, runs after it in one order and
before it in the other, and requires both facts to be gone:

* the marker row (so the reset really emptied the tables), and
* a working application connection (so terminating the idle session did not
  poison the pool).

Run alone, this module passes and proves nothing; run in a pair with its
partner - which is what the suite does - it is the "does the reset still run?"
alarm. ``TEST_EDITS.md`` records the measurement that the pair fails when the
reset is disabled.
"""

from __future__ import annotations

import psycopg2
import pytest

pytestmark = pytest.mark.integration

#: Fixed, not random: the partner module has to look for exactly this.
MARKER = "isolation-canary-residue"


def _connect(cfg):
    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])


def marker_rows(cfg) -> int:
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM words WHERE word = %s", (MARKER,))
            return cur.fetchone()[0]
    finally:
        conn.close()


@pytest.fixture(scope="module")
def planted(pg_db):
    """Insert the marker on a session this module never closes cleanly."""
    idle = _connect(pg_db)
    with idle.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES (%s)", (MARKER,))
    idle.commit()
    # Leave it *idle in transaction* holding a read lock on the tables the
    # boundary truncates. That is the state an earlier module leaves behind when
    # a fixture forgets to commit, and the reason the reset terminates sessions
    # before truncating instead of waiting for a lock that never comes.
    with idle.cursor() as cur:
        cur.execute("SELECT count(*) FROM words")
        cur.fetchone()
    yield idle
    try:
        idle.close()
    except Exception:
        pass


def test_the_marker_is_stored_and_visible_to_the_application(pg_db, planted):
    """The plant has to be real, or the partner's assertion proves nothing."""
    assert marker_rows(pg_db) == 1

    from Api.utils import execute_query

    rows = execute_query("SELECT word FROM words WHERE word = %s", (MARKER,))
    assert rows == [(MARKER,)], "the application's own pool cannot see the marker"


def test_the_database_decision_is_true_for_this_module(request):
    """The module asks for ``pg_db`` through a fixture; the decision says so."""
    from suite_isolation import module_needs_database

    assert module_needs_database(request.node.path, request.session.items) is True
