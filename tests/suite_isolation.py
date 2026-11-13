"""Database isolation between test modules, as plain functions.

The suite gives every database module an empty application schema, because a
module is the unit that seeds its own data and therefore the unit that must see
only its own. ``tests/conftest.py`` wires these functions into a module-scoped
autouse fixture; keeping them here means the decision ("does this module use the
database?") and the reset itself can be tested without running pytest.

Nothing here imports pytest: the functions take plain values so a unit test can
call them with stand-ins.
"""

from __future__ import annotations


#: Tables that describe the schema rather than hold test data, so they survive
#: the per-module reset. Everything else is emptied.
_SCHEMA_BOOKKEEPING = {"schema_migrations"}


def _connection(cfg):
    import psycopg2

    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])


def application_tables(cfg) -> list:
    """Every table in ``public`` that holds test data, bookkeeping excluded."""
    conn = _connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename")
            return [row[0] for row in cur.fetchall()
                    if row[0] not in _SCHEMA_BOOKKEEPING]
    finally:
        conn.close()


def reset_database(cfg) -> list:
    """Empty every data table - the schema and the migration record stay.

    This is what makes one module's leftovers invisible to the next. Before it,
    the suite shared a single database for the whole session, so a module that
    ingested a corpus file changed what a later module's resolver returned: an
    id in ``test_term_names_hostile_input`` failed only in a full run, because
    another module had stored a name its hostile query normalises onto.

    ``TRUNCATE`` needs an exclusive lock, and a pooled connection left
    *idle in transaction* by an earlier module holds a read lock on every table
    it touched, so those sessions are ended first. The alternative - waiting -
    hangs the run, which is how the first version of this fixture behaved.
    """
    import psycopg2

    tables = application_tables(cfg)
    if not tables:
        return []
    statement = ("TRUNCATE TABLE "
                 + ", ".join(f'public."{name}"' for name in tables)
                 + " RESTART IDENTITY CASCADE")
    conn = _connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = current_database()"
                        "   AND pid <> pg_backend_pid()"
                        "   AND state LIKE 'idle in transaction%%'")
            cur.execute("SET LOCAL lock_timeout = '10s'")
            cur.execute(statement)
        conn.commit()
    except psycopg2.Error:
        conn.rollback()
        # A session that is merely idle still holds its snapshot; if the lock
        # timeout above was not enough, end every other session on this
        # database. It is a disposable test database, and a deadlock here would
        # otherwise stall the whole suite.
        with conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = current_database()"
                        "   AND pid <> pg_backend_pid()")
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()
    _forget_connections()
    return tables


def _forget_connections() -> None:
    """Drop the process's pooled connections so the next module reconnects.

    Terminating a backed session leaves a closed socket in a pool. Clearing the
    pools and the ``Api.utils`` singletons is the same reset ``pg_db`` performs
    at session start, applied at every module boundary.
    """
    try:
        from database.database.database import reset_connection_pools

        reset_connection_pools()
    except Exception:
        pass
    # A cache that mirrors table rows is the same leftover as the rows: the
    # translation override cache holds what the previous module saved for up to
    # three seconds, and this reset just deleted those rows.
    try:
        from Api.services.translation_management import invalidate_translation_override_cache

        invalidate_translation_override_cache()
    except Exception:
        pass

    import sys as _sys

    for name in ("Api.utils_module", "Api.utils.utils"):
        module = _sys.modules.get(name)
        if module is None:
            continue
        for attr in ("_query_executor", "_query_cache"):
            if hasattr(module, attr):
                try:
                    setattr(module, attr, None)
                except Exception:
                    pass


def restore_reference_data(cfg) -> dict:
    """Reload the reviewed gazetteer seed into the emptied database (opt-in).

    Called by the modules that read places, not by the reset itself: several
    tests below assert the *unseeded* state (``test_gazetteer_readiness`` wants
    ``not-seeded``; the curation tests delete places), and seeding every module
    would make those assertions untestable.

    ``data/gazetteer/places_seed.json`` is reference data: a deployment loads it
    once and every read that needs a place expects it to be there. Emptying the
    tables per module therefore has to reload it, or the first module in a run
    that reads places fails - which is exactly what a reversed module order
    produced: ``test_signal_ingestion`` reported "no gazetteer is loaded" when
    it ran before ``test_places_pg``. Loading it here makes the loaded state a
    property of the database rather than of the order the modules happen to run.

    Returns the sync statistics so a failure shows counts rather than a bool.
    """
    from services.geo import gazetteer

    conn = _connection(cfg)
    try:
        with conn.cursor() as cur:
            stats = gazetteer.sync_seed(cur, loaded_by="test-suite")
        conn.commit()
    finally:
        conn.close()
    gazetteer._invalidate_cache()
    return stats


def module_needs_database(module_path, items) -> bool:
    """True when any collected test in ``module_path`` asks for ``pg_db``.

    ``items`` is any iterable of collected tests (``request.session.items``);
    each needs ``.path`` and ``.fixturenames``. ``fixturenames`` is pytest's
    whole closure for the item, so a test that asks for ``pg_db`` through a
    module fixture (``tenant``, ``seed``, ``admin_client``) counts - which is
    the case that matters, because those are most of the database modules.

    The decision gates a database reset *and* starting a PostgreSQL server, so a
    wrong True costs time on every unit module and a wrong False reintroduces
    the cross-module leakage the reset exists to remove. Both directions are
    tested in ``tests/unit/test_suite_isolation.py`` and, against the live
    session, by ``tests/integration/test_isolation_canary_*.py``.
    """
    target = str(module_path)
    return any(
        "pg_db" in getattr(item, "fixturenames", ())
        for item in items
        if str(getattr(item, "path", "")) == target
    )
