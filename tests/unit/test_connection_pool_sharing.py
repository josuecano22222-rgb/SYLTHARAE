"""Connection-pool sharing, reconnection and slot-release guarantees.

These tests pin the behaviours behind the production failures:

* every ``Database`` handle in the process shares one pool per connection
  target, so the number of PostgreSQL backends is bounded by configuration
  instead of by the number of objects created ("FATAL: sorry, too many
  clients already");
* closing one handle (for example a nested reader finishing an extraction)
  must not tear the pool down for everybody else ("Database connection
  unhealthy, attempting reconnect..." cascade);
* a broken connection must be *returned* to the pool so its slot is freed -
  psycopg2 keeps the slot in ``_used`` until ``putconn`` is called, so merely
  closing the connection leaked pool capacity until the pool reported
  exhaustion while the server had capacity to spare;
* reconnection is for lost connections, not for load: a healthy pool is never
  rebuilt, and concurrent rebuild attempts are rate-limited.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

pytestmark = pytest.mark.usefixtures("pg_db")


def _make_database(**overrides):
    from database.database.config import DatabaseConfig
    from database.database.database import Database

    cfg = DatabaseConfig(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        min_connections=overrides.pop("min_connections", 1),
        max_connections=overrides.pop("max_connections", 5),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return Database(cfg)


def test_databases_share_one_pool_per_target(pg_db):
    first = _make_database()
    second = _make_database()
    try:
        assert first._pool is second._pool, "each Database built its own pool"
    finally:
        first.close_all()
        second.close_all()


def test_closing_one_handle_keeps_the_pool_usable_for_others(pg_db):
    first = _make_database()
    second = _make_database()
    try:
        first.close_all()  # a nested reader finishing its work
        with second.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
            cur.close()
    finally:
        second.close_all()


def test_closed_handle_rejoins_the_pool(pg_db):
    """A hub closed at the end of one ingestion must still be usable later."""
    db = _make_database()
    db.close_all()
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
        cur.close()
    db.close_all()


def test_reconnect_is_a_no_op_while_the_pool_is_healthy(pg_db):
    from database import DatabaseHub

    hub = DatabaseHub()
    try:
        generation = hub.db._shared.generation
        assert hub.reconnect() is None
        assert hub.db._shared.generation == generation, (
            "a healthy pool was rebuilt, which multiplies connections under load"
        )
    finally:
        hub.db.close_all()


def test_rebuild_pool_reports_success_without_touching_a_healthy_pool(pg_db):
    db = _make_database()
    try:
        generation = db._shared.generation
        assert db.rebuild_pool() is True
        assert db._shared.generation == generation
    finally:
        db.close_all()


def test_concurrent_reconnect_requests_do_not_stampede(pg_db):
    """Several workers noticing 'unhealthy' must not each build a new pool."""
    from database.database.database import Database

    db = _make_database()
    try:
        start_generation = db._shared.generation

        def worker():
            local = Database(db.config)
            try:
                local.rebuild_pool(force=True)
            finally:
                local.close_all()

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        # The cooldown allows at most one rebuild per window; six simultaneous
        # requests must not produce six pools.
        assert db._shared.generation - start_generation <= 1
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
            cur.close()
    finally:
        db.close_all()


def test_broken_connection_releases_its_pool_slot(pg_db):
    """Closing a connection must not permanently shrink the pool."""
    db = _make_database(max_connections=2, min_connections=1)
    try:
        # Simulate three consecutive connections dying underneath the pool.
        for _ in range(3):
            conn = db.connect()
            try:
                assert conn.closed == 0
            finally:
                conn.close()          # the driver connection is gone...
                db.putconn(conn)      # ...and the slot must still be freed
        # A pool of 2 must still be able to serve after three broken ones.
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
            cur.close()
        assert len(db._pool._used) == 0, "connections are still marked as in use"
    finally:
        db.close_all()


def test_pool_is_capped_to_server_capacity(pg_db):
    """A pool configured beyond the server's limit is clamped, not attempted.

    The registry is reset first: pools are shared per connection target and are
    built once, so an earlier test's pool for the same target would otherwise
    be reused (correctly - the first builder's size wins - but that would make
    this test order-dependent rather than testing the cap).
    """
    from database.database.database import reset_connection_pools

    reset_connection_pools()
    db = _make_database(max_connections=100000, min_connections=1)
    try:
        assert db.config.max_connections < 100000, "configured size was not capped"
        assert db.config.max_connections >= 1
        assert db._pool.maxconn == db.config.max_connections
        # The pool must still work at whatever size was affordable.
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
            cur.close()
    finally:
        db.close_all()


def test_reset_connection_pools_closes_and_forgets(pg_db):
    """Settings invalidation must drop pools built for the previous target."""
    from database.database.database import (
        _POOL_REGISTRY,
        reset_connection_pools,
    )

    db = _make_database()
    try:
        assert _POOL_REGISTRY, "no pool registered"
        reset_connection_pools()
        assert not _POOL_REGISTRY, "registry still holds pools after reset"
        # The handle re-joins a fresh pool transparently.
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
            cur.close()
    finally:
        db.close_all()


def test_small_pool_advice_is_logged_once_per_pool(pg_db, caplog):
    """The size recommendation belongs to the pool, not to every handle.

    It used to be emitted by each Database construction, which repeated the
    same line dozens of times in one ingestion.
    """
    from database.database.database import reset_connection_pools

    reset_connection_pools()
    handles = []
    try:
        with caplog.at_level(logging.WARNING, logger="database.database.database"):
            for _ in range(4):
                handles.append(_make_database(max_connections=4, min_connections=1))
        advice = [r for r in caplog.records if "may be too small" in r.getMessage()]
        assert len(advice) == 1, f"advice emitted {len(advice)} times for one pool"
    finally:
        for handle in handles:
            handle.close_all()
        reset_connection_pools()


def test_failed_initialization_does_not_leak_a_pool_reference(pg_db, monkeypatch):
    """A handle that failed to initialize must not pin the shared pool.

    ``_initialize_pool`` takes a reference to the shared pool before the
    advice/schema-repair steps. If one of those raises, the reference has to be
    given back - otherwise the pool is never closed, even after every usable
    handle has been closed, and it leaks a server-side backend for the life of
    the process.
    """
    from database.database import database as db_module

    baseline = _make_database()
    try:
        shared = baseline._shared
        refs_before = shared.refs

        def _explode():
            raise RuntimeError("schema repair failed")

        monkeypatch.setattr(db_module.Database, "_ensure_large_file_sizes", _explode)
        with pytest.raises(ConnectionError):
            _make_database()

        assert shared.refs == refs_before, "failed init leaked a pool reference"
        assert baseline._pool is shared.pool, "the working handle lost its pool"
    finally:
        baseline.close_all()
