"""Regression tests for the startup schema-upgrade path.

Deployment defect found during PR #11 acceptance: migrations ran only via
the first-install setup wizard, so an EXISTING installation that pulled
new code containing a new migration started against a stale schema
(``relation "analyst_categories" does not exist``). ``initialize_system``
now applies pending migrations on every (non-first) startup.
"""

import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def _connect(pg_db):
    return psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )


class _DbConfig:
    """Duck-typed stand-in for ``settings.config.get_database_config()``."""

    def __init__(self, cfg):
        self.host, self.port = cfg["host"], int(cfg["port"])
        self.user, self.password = cfg["user"], cfg["password"]
        self.database = cfg["database"]


def _point_settings(monkeypatch, cfg):
    """Make the production startup hook talk to ``cfg`` and nothing else."""
    import settings.config as settings_config

    monkeypatch.setattr(
        settings_config, "get_database_config", lambda: _DbConfig(cfg)
    )


@pytest.fixture(scope="module")
def private_db(pg_db):
    """A database of this module's own, cloned from the migrated test database.

    These tests rewind the schema and let the startup hook repair it. Doing
    that in the shared database deletes tables that later tests need: the
    startup hook can only re-create what a migration creates, so under the
    frozen chain (m0001-m0003) the damaged database stayed damaged and every
    later search test failed with ``relation "analyst_file_categories" does
    not exist``. A private database makes the rewind invisible to everyone
    else - a test-isolation fix, not a schema change.
    """
    name = f"{pg_db['database']}_startup_upgrade"
    admin = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname="postgres",
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            # Created empty and migrated here, NOT cloned with TEMPLATE.
            # ``CREATE DATABASE ... TEMPLATE`` is refused while any other
            # session is attached to the source database - and in a full run
            # the connection pool of the test module before this one still is:
            # ``psycopg2.errors.ObjectInUse: source database ... is being
            # accessed by other users``, which made these two tests error
            # depending on test order. A database this module builds itself
            # has no such dependency, and it is the same frozen chain either
            # way (this is test isolation, not a schema change).
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    cfg = dict(pg_db, database=name)
    from database.bootstrap import bootstrap_database
    from database.migration_runner import discover_migrations

    report = bootstrap_database({k: cfg[k] for k in
                                 ("host", "port", "user", "password", "database")})
    # The tip of the chain, not a literal: the chain grew by one authorised
    # migration (m0004, category hierarchy) and a hard-coded "0003" turned a
    # correct bootstrap into a failure. What this fixture needs is "bootstrap
    # applied the whole chain", which is what the last discovered version says.
    assert report["current_version"] == discover_migrations()[-1].version, report
    yield cfg

    admin = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname="postgres",
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


def test_startup_applies_pending_migrations_to_existing_install(private_db, monkeypatch):
    """The field condition: a database one migration behind that just received
    the new code. The startup upgrade hook must bring it current, and a second
    startup must be a no-op.

    The rewind uses the frozen chain (m0002 adds ``categorys.parent_id``): the
    column is dropped and the version unrecorded, which is exactly the state an
    installation that predates the migration is in.
    """
    from core.initialization import upgrade_database_schema

    _point_settings(monkeypatch, private_db)

    conn = _connect(private_db)
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE categorys DROP COLUMN IF EXISTS parent_id")
            cur.execute("DELETE FROM schema_migrations WHERE version = '0002'")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name ="
                " 'categorys' AND column_name = 'parent_id'")
            assert cur.fetchone() is None, "rewind failed"
    finally:
        conn.close()

    applied = upgrade_database_schema()
    assert "0002" in applied, f"upgrade did not apply m0002: {applied}"

    conn = _connect(private_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name ="
                " 'categorys' AND column_name = 'parent_id'")
            assert cur.fetchone() is not None, "categorys.parent_id missing after upgrade"
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = '0002'")
            assert cur.fetchone() is not None
    finally:
        conn.close()

    # Idempotent: restarting an up-to-date system applies nothing.
    assert upgrade_database_schema() == []


def test_startup_upgrade_tolerates_unreachable_database(monkeypatch):
    """A database that cannot be reached must not crash startup: the hook
    logs a warning and returns an empty list (the setup gate / health
    checks surface the real problem)."""
    import settings.config as settings_config
    from core.initialization import upgrade_database_schema

    class _Unreachable:
        host = "127.0.0.1"
        port = 1  # nothing listens here; refused immediately
        user = "nobody"
        password = "nobody"
        database = "does_not_exist"

    monkeypatch.setattr(
        settings_config, "get_database_config", lambda: _Unreachable()
    )
    assert upgrade_database_schema() == []


# --- start-up without the initialisation marker (field report, step 12) -----
#
# The marker is written only when config.json exists. An installation
# configured through .env never gets one, took the "first startup" branch on
# every start, and never had its schema upgraded: after pulling m0020 the
# server logged `column "recipient_user_id" does not exist`.

def _no_marker_no_config(monkeypatch, tmp_path):
    import core.initialization as init

    monkeypatch.chdir(tmp_path)
    # the `app` fixture points the marker at a session directory where it
    # exists; this scenario needs an installation without one
    monkeypatch.setattr(init, "INIT_MARKER_FILE", tmp_path / ".system_initialized")
    monkeypatch.setattr(init, "load_config_from_json", lambda *a, **k: {})
    assert not init.is_system_initialized()
    return init


def test_startup_without_marker_or_config_upgrades_an_installed_database(
        private_db, monkeypatch, tmp_path):
    """Configured through .env, never marked: every start is a "first start".

    Such an installation must still have its schema upgraded. Rewound on the
    frozen chain (m0003 adds ``sources.attachments``).
    """
    _point_settings(monkeypatch, private_db)

    conn = _connect(private_db)
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE sources DROP COLUMN IF EXISTS attachments")
            cur.execute("DELETE FROM schema_migrations WHERE version = '0003'")
        conn.commit()
    finally:
        conn.close()

    init = _no_marker_no_config(monkeypatch, tmp_path)
    init.ensure_system_initialized()

    conn = _connect(private_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name ="
                " 'sources' AND column_name = 'attachments'")
            assert cur.fetchone() is not None, "startup did not apply m0003"
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = '0003'")
            assert cur.fetchone() is not None
    finally:
        conn.close()
    assert not (tmp_path / ".system_initialized").exists(), \
        "no config.json: the marker is still not written (unchanged behaviour)"


def test_startup_without_marker_never_creates_an_uninstalled_database(
        pg_db, app, monkeypatch, tmp_path):
    import settings.config as settings_config

    name = "syltharae_setup_pending_db"

    class _Pending:
        host, port, user = pg_db["host"], pg_db["port"], pg_db["user"]
        password, database = pg_db["password"], name

    monkeypatch.setattr(settings_config, "get_database_config", lambda: _Pending())
    init = _no_marker_no_config(monkeypatch, tmp_path)
    assert init.installed_schema_present(init._schema_db_config()) is False
    init.ensure_system_initialized()

    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname="postgres")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            assert cur.fetchone() is None, "startup created a database before setup"
    finally:
        conn.close()


def test_a_failing_migration_is_reported_as_a_failure_not_as_unreachable(
        monkeypatch, capsys):
    import database.bootstrap as bootstrap
    from core.initialization import upgrade_database_schema

    def _fail(cfg):
        try:
            raise FileNotFoundError("places_seed.json")
        except FileNotFoundError as exc:
            raise RuntimeError("Migration 0019 (gazetteer) failed: SeedIntegrityError") from exc

    monkeypatch.setattr(bootstrap, "bootstrap_database", _fail)
    assert upgrade_database_schema() == []
    out = capsys.readouterr().out
    assert "[ERROR] Schema upgrade FAILED" in out and "0019" in out
    assert "places_seed.json" in out and "unreachable" not in out


def test_the_app_fixture_does_not_mark_the_checkout(app):
    """The suite must not leave `.system_initialized` in the working tree.

    A marker left behind by the tests made a later `run_web.py` from the same
    checkout take the marker branch, masking the missing-upgrade defect.
    """
    import os
    from pathlib import Path

    import core.initialization as init

    assert init.INIT_MARKER_FILE.is_absolute()
    assert init.INIT_MARKER_FILE.exists()
    assert init.INIT_MARKER_FILE.resolve().parent != Path(os.getcwd()).resolve()
    assert init.INIT_MARKER_FILE.resolve().parent != Path(__file__).resolve().parents[2]


def test_a_refused_connection_reports_the_servers_reason(pg_db, app, monkeypatch, tmp_path,
                                                         capsys):
    """Field report: no DB password yet -> the warning said "unreachable"."""
    import core.initialization as init

    init_mod = _no_marker_no_config(monkeypatch, tmp_path)
    cfg = dict(init_mod._schema_db_config())
    cfg.update(host="127.0.0.1", port=1)            # nothing listens on port 1
    monkeypatch.setattr(init_mod, "_schema_db_config", lambda: cfg)
    assert init_mod.upgrade_installed_schema() == []
    out = capsys.readouterr().out
    assert "[WARNING] Schema upgrade skipped - cannot connect to 127.0.0.1:1/" in out
    assert "Connection refused" in out


def test_connection_failure_reason_strips_the_libpq_preamble():
    from core.initialization import _connection_failure_reason

    exc = Exception('connection to server at "localhost" (::1), port 5432 failed: '
                    'fe_sendauth: no password supplied\n'
                    'connection to server at "localhost" (127.0.0.1), port 5432 failed: x')
    assert _connection_failure_reason(exc) == "fe_sendauth: no password supplied"
