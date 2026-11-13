"""Shared pytest fixtures.

Provides:
* ``pg_db``      - a disposable PostgreSQL server + fresh application database
                   (per-session) with the full migration bootstrap applied.
* ``app``        - the real Flask application wired to the disposable database.
* ``client``     - Flask test client.
* ``admin_session`` - an authenticated admin client session.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Isolated application data root for tests (Phase 19).
os.environ.setdefault("APP_DATA_DIR", str(PROJECT_ROOT / ".test_runtime"))
# The map tree is tracked in the checkout (map_data/registry.db is the 15 MB
# tile cache), so a run that opened it left a modified binary in `git status`
# and made "did this run change anything?" unanswerable. The writable tree is
# moved into the same ignored runtime directory the rest of the test state
# uses; the shipped catalog is copied in, because the tests read it.
_MAP_DATA = PROJECT_ROOT / ".test_runtime" / "map_data"
os.environ.setdefault("MAP_DATA_DIR", str(_MAP_DATA))
_MAP_DATA.mkdir(parents=True, exist_ok=True)
_SHIPPED_CATALOG = PROJECT_ROOT / "map_data" / "catalog.json"
if _SHIPPED_CATALOG.is_file() and not (_MAP_DATA / "catalog.json").exists():
    (_MAP_DATA / "catalog.json").write_bytes(_SHIPPED_CATALOG.read_bytes())
# What the checkout looked like before this run touched it. A developer running
# the suite from a dirty tree is normal; a run that *adds* to that dirt is not.
# ``tests/unit/test_run_leaves_no_tracked_changes.py`` compares the two, so
# "the suite does not write the repository" is measured per run instead of
# assumed.
GIT_STATUS_AT_START = _MAP_DATA.parent / "git_status_at_start.txt"


def _record_git_status() -> None:
    import subprocess

    try:
        completed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                   cwd=str(PROJECT_ROOT), capture_output=True,
                                   text=True, timeout=120)
    except Exception:                                    # noqa: BLE001 - no git, no test
        return
    if completed.returncode == 0:
        GIT_STATUS_AT_START.write_text(completed.stdout, encoding="utf-8")


_record_git_status()

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-not-for-production-0123456789")
os.environ.setdefault("FLASK_ENV", "development")


def _probe(settings: dict, timeout: int = 5):
    """Can we actually connect with these settings? ``(ok, reason)``.

    Resolving a server address is not the same as reaching one. A wrong port
    or host silently connects to *something else* - on a workstation that
    usually means the application's own PostgreSQL, which answers with
    "fe_sendauth: no password supplied" or an authentication failure. Probing
    turns that into a precise answer instead of a wall of identical tracebacks
    from tests that never had a chance to run.
    """
    try:
        import psycopg2
    except Exception as exc:  # pragma: no cover - dependency missing
        return False, f"psycopg2 unavailable ({exc})"
    try:
        # Probe the *maintenance* database. The test database does not exist
        # until the bootstrap creates it, so probing it would fail on a
        # perfectly healthy server ("database ... does not exist") and skip
        # the whole suite.
        conn = psycopg2.connect(
            dbname="postgres",
            user=settings.get("user") or "postgres",
            password=settings.get("password") or "",
            host=settings.get("host") or "localhost",
            port=int(settings.get("port") or 5432),
            connect_timeout=timeout,
        )
        conn.close()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _pgserver_settings(server, db_name: str):
    """Settings for a pgserver instance, including the raw URI for diagnosis."""
    uri = server.get_uri()
    return pg_connection_settings(server, db_name), uri


def _candidate_databases(db_name: str):
    """Every reachable PostgreSQL this suite may use, best candidate first.

    Ordered so the disposable server wins when it works, and an explicitly
    configured server is honoured when it does not:

      1. pgserver's own instance (hermetic, trust authentication);
      2. ``DB_HOST``/``DB_PORT``/``DB_USER``/``DB_PASSWORD`` from the
         environment;
      3. the application's configured database (``get_database_config``).

    ``bootstrap_database`` creates and migrates a *test-named* database, so
    pointing the suite at an existing server does not touch application data.
    """
    candidates = []

    try:
        import pgserver

        data_dir = pathlib.Path(tempfile.mkdtemp(prefix="pgdata_"))
        server = pgserver.get_server(str(data_dir))
        settings, uri = _pgserver_settings(server, db_name)
        candidates.append((settings, "pgserver", server, uri))
    except Exception as exc:
        candidates.append((None, "pgserver", None,
                           f"could not start a disposable server: "
                           f"{type(exc).__name__}: {exc}"))

    if os.environ.get("DB_HOST"):
        candidates.append((
            {
                "host": os.environ.get("DB_HOST"),
                "port": int(os.environ.get("DB_PORT") or 5432),
                "user": os.environ.get("DB_USER") or "postgres",
                "password": os.environ.get("DB_PASSWORD") or "",
                "database": db_name,
            },
            "environment (DB_HOST/DB_PORT/DB_USER)", None, "from environment",
        ))

    try:
        from settings import get_database_config

        configured = get_database_config()
        candidates.append((
            {
                "host": getattr(configured, "host", None),
                "port": int(getattr(configured, "port", 5432) or 5432),
                "user": getattr(configured, "user", None) or "postgres",
                "password": getattr(configured, "password", "") or "",
                "database": db_name,
            },
            "application configuration", None, "from settings",
        ))
    except Exception:
        pass

    return candidates


@pytest.fixture(scope="session")
def pg_settings(tmp_path_factory):
    """A reachable PostgreSQL, found by probing rather than assuming.

    Skips - once, with the reason - when nothing is reachable, instead of
    letting every database test fail individually with the same error.
    """
    db_name = f"file_analysis_test_{os.getpid()}"
    attempts = []
    for settings, source, server, detail in _candidate_databases(db_name):
        if settings is None:
            attempts.append(f"{source}: {detail}")
            continue
        ok, reason = _probe(settings)
        if ok:
            pytest.pg_server_instance = server  # reused by the pg_server fixture
            return settings
        attempts.append(
            f"{source}: {settings.get('host')}:{settings.get('port')} -> {reason}"
            + (f" (uri: {detail})" if detail != "from environment" else ""))

    message = (
        "No reachable PostgreSQL for the integration suite. Tried:\n  - "
        + "\n  - ".join(attempts)
        + "\nPoint the suite at a server with DB_HOST/DB_PORT/DB_USER/DB_PASSWORD "
          "(a test-named database is created on it; application data is not touched)."
    )
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        # CI: a missing database must fail the run, not skip it into green.
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="session")
def pg_server(pg_settings):
    """The disposable server, when one could be reached (else the suite skips)."""
    server = getattr(pytest, "pg_server_instance", None)
    if server is None:
        pytest.skip("no disposable PostgreSQL server available in this environment")
    return server


def pg_connection_settings(server, db_name: str) -> dict:
    """Connection settings for the disposable PostgreSQL server.

    pgserver describes the server differently per platform, and the earlier
    code assumed the Linux/POSIX shape:

        POSIX   postgresql://postgres:@/postgres?host=/run/pgdata   (unix socket)
        Windows postgresql://postgres:@localhost:5432/postgres      (TCP)

    Taking ``parsed.path`` as a fallback host therefore handed the *database
    name* (``/postgres``) to psycopg2 as a socket directory.  On Windows that
    makes every database test fail with "connection to server on socket
    /postgres/.sPGSQL.5432 failed: Network is down" - not a code defect, but it
    hides every real defect behind it, so the parsing has to be right.

    Resolution order:
      1. an explicit ``host`` query parameter (the POSIX socket directory),
      2. the host in the URI authority (``localhost`` on Windows),
      3. ``localhost`` as a last resort - never a path component, which is
         never a host.
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(server.get_uri())
    query = urllib.parse.parse_qs(parsed.query)
    host = query.get("host", [None])[0] or parsed.hostname or "localhost"
    port = parsed.port or 5432
    return {
        "host": host,
        "port": int(port),
        "user": urllib.parse.unquote(parsed.username or "postgres"),
        "password": urllib.parse.unquote(parsed.password or ""),
        "database": db_name,
    }


@pytest.fixture(scope="session")
def pg_db(pg_settings, tmp_path_factory):
    """Create a fresh application database and run the migration bootstrap."""
    cfg = dict(pg_settings)
    db_name = cfg["database"]
    host_dir = cfg["host"]

    os.environ["DB_HOST"] = str(cfg["host"])
    os.environ["DB_PORT"] = str(cfg["port"])
    os.environ["DB_USER"] = str(cfg["user"])
    os.environ["DB_PASSWORD"] = str(cfg["password"])
    os.environ["DB_NAME"] = db_name

    # The settings file is gitignored runtime state; when it is absent, a
    # fresh SettingsManager defaults database.host to localhost and every
    # lazy pool (the app fixture, the auth service) misses the test server
    # even though DB_* are set. Seed the file with this session's target
    # before anything re-reads it. (It keeps any non-database settings the
    # tests stored earlier - retention days, theme, ... - and only the
    # database section is pinned here.)
    try:
        import json

        settings_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        # Use the server pg_settings actually discovered (host, port, user,
        # password), never a literal 5432: pgserver's port is not always 5432.
        data["database"] = {"host": str(host_dir), "port": int(cfg["port"]),
                            "database": db_name, "user": str(cfg["user"]),
                            "password": str(cfg["password"])}
        settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Reset ALL cached settings/config singletons from any earlier import.
    # NOTE: settings_adapter caches an adapter under `_interface_manager`
    # which wraps the manager; it must be cleared too, otherwise a stale
    # manager built before DB_* env vars were set keeps serving localhost.
    import settings.settings_manager as _sm
    import settings.settings_adapter as _sa
    import settings.config as _sc
    for mod, attrs in (
        (_sm, ("_settings_manager",)),
        (_sa, ("_interface_manager",)),
        (_sc, ("_config",)),
    ):
        for attr in attrs:
            if hasattr(mod, attr):
                try:
                    setattr(mod, attr, None)
                except Exception:
                    pass

    # The same staleness applies one layer up: Api.utils.utils keeps a
    # module-level ``DatabaseHub`` (and its query cache) built from whatever
    # configuration existed on first use. If anything touched it before the
    # DB_* variables above were set, every later search keeps talking to the
    # default server ("connection to server at localhost:5432 failed:
    # Connection refused") even though the fixtures are pointed somewhere else.
    # Clearing it here is what makes search results reflect the test database.
    # Api/utils/__init__.py loads utils.py as ``Api.utils_module`` and the
    # application imports through that name. ``import Api.utils.utils`` would
    # create a second module object whose singletons nothing uses, so the reset
    # must clear the instance the application actually holds. A stale hub
    # created at import time (before DB_NAME was set) otherwise keeps serving
    # the previous run's database, which is why search results depended on
    # leftover state from the last run. Both names are cleared to be safe.
    import sys as _sys

    for _mod_name in ("Api.utils_module", "Api.utils.utils"):
        _api_utils = _sys.modules.get(_mod_name)
        if _api_utils is None:
            continue
        for attr in ("_query_executor", "_query_cache"):
            if hasattr(_api_utils, attr):
                try:
                    setattr(_api_utils, attr, None)
                except Exception:
                    pass

    # Drop any pooled connections opened against the previous configuration.
    try:
        from database.database.database import reset_connection_pools

        reset_connection_pools()
    except Exception:
        pass

    from database.bootstrap import bootstrap_database

    # Bootstrap against the discovered server. The right-hand side reads the
    # discovered cfg before it is rebound, so port/user/password are preserved.
    cfg = {
        "host": host_dir,
        "port": int(cfg["port"]),
        "user": str(cfg["user"]),
        "password": str(cfg["password"]),
        "database": db_name,
    }
    report = bootstrap_database(cfg)
    assert report["database_created"] is True
    assert len(report["applied_migrations"]) >= 1

    yield cfg


# The isolation helpers live in tests/suite_isolation.py so they can be tested
# without pytest; this file only wires them into the session.
from suite_isolation import (  # noqa: E402  (import after PROJECT_ROOT is on sys.path)
    application_tables,
    module_needs_database,
    reset_database,
    restore_reference_data,
)


@pytest.fixture(scope="module", autouse=True)
def database_state_is_module_local(request):
    """Start and end every database module against an empty application schema.

    Module scope, not session scope: a module is the unit that seeds its own
    data, so it is the unit that has to see only its own. Modules that never
    ask for ``pg_db`` are untouched and never start a server.

    The administrative user is a module-scoped fixture as well (see
    ``admin_credentials``), so it is recreated after each reset instead of
    disappearing with the rows that created it.
    """
    # pytest no longer keeps the collected items on the node, so the module's
    # tests are read from the session: only modules that actually ask for
    # ``pg_db`` pay for a reset (and for starting a server).
    if not module_needs_database(getattr(request.node, "path", ""),
                                 request.session.items):
        yield
        return
    cfg = request.getfixturevalue("pg_db")
    reset_database(cfg)
    try:
        yield
    finally:
        reset_database(cfg)


_WEB_APP_PREPARED = False
_WEB_APP = None


def prepare_web_app():
    """Apply the test-time app configuration *before any request is dispatched*.

    Flask refuses ``add_url_rule`` once the application has handled a request
    ("The setup method 'add_url_rule' can no longer be called"), and the ``app``
    fixture is not guaranteed to run first: the readiness login check and any
    other in-process check drive the same singleton application. Doing this at
    session start - rather than lazily in the fixture - is what makes the suite
    order-independent; the fixture keeps only the state that genuinely needs a
    live database (the initialisation marker).

    Idempotent: importing twice, or calling from more than one place, must not
    double-register the route.
    """
    global _WEB_APP_PREPARED, _WEB_APP
    if _WEB_APP_PREPARED:
        return _WEB_APP

    from apps.web.app import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False  # API-level tests fetch tokens explicitly

    # API-01: per-route limits would trip the ~30 login fixtures; individual
    # rate-limit tests re-enable the limiter explicitly.
    from core.security.rate_limit import limiter as _limiter
    _limiter.enabled = False

    if "_test/sec08/boom" not in {r.rule for r in flask_app.url_map.iter_rules()}:
        def _boom():
            raise RuntimeError("SECRET postgresql://user:pass@host/db leaked")

        flask_app.add_url_rule("/_test/sec08/boom", view_func=_boom, endpoint="_sec08_boom")

    _WEB_APP = flask_app
    _WEB_APP_PREPARED = True
    return flask_app


@pytest.fixture(scope="session")
def app(pg_db, tmp_path_factory):
    """The real Flask application against the disposable database."""
    flask_app = prepare_web_app()

    # Re-initialize settings and database connections now that pg_db has
    # configured the environment variables and bootstrapped the database.
    # The app was created at session start (prepare_web_app), but the DB
    # configuration wasn't ready yet. We must refresh the cached singletons.
    import settings.settings_manager as _sm
    import settings.settings_adapter as _sa
    import settings.config as _sc
    for mod, attrs in (
        (_sm, ("_settings_manager",)),
        (_sa, ("_interface_manager",)),
        (_sc, ("_config",)),
    ):
        for attr in attrs:
            if hasattr(mod, attr):
                try:
                    setattr(mod, attr, None)
                except Exception:
                    pass

    # Also reset the Api.utils utils module's cached DatabaseHub
    import sys as _sys
    for _mod_name in ("Api.utils_module", "Api.utils.utils"):
        _api_utils = _sys.modules.get(_mod_name)
        if _api_utils is not None:
            for attr in ("_query_executor", "_query_cache"):
                if hasattr(_api_utils, attr):
                    try:
                        setattr(_api_utils, attr, None)
                    except Exception:
                        pass

    # Reset connection pools
    try:
        from database.database.database import reset_connection_pools
        reset_connection_pools()
    except Exception:
        pass

    # Refresh the app's SETTINGS config
    try:
        from settings import get_settings
        settings = get_settings()
        flask_app.config['SETTINGS'] = settings
        system_config = settings.get_system_config()
        flask_app.config['BABEL_DEFAULT_LOCALE'] = system_config.get('language', 'en')
        flask_app.config['BABEL_DEFAULT_TIMEZONE'] = system_config.get('timezone', 'UTC')
    except Exception:
        pass

    # The setup gate in Api/routes/setup.py redirects EVERY request to /setup
    # unless a filesystem marker exists and the critical tables are present.
    # pg_db bootstraps the tables, so only the marker is missing - which made
    # every authenticated fixture fail with 302 and blocked the API surface
    # from being tested at all. Mark it and leave it: the file is gitignored,
    # the gate's table check stays authoritative per database, and unlinking
    # it at teardown raced parallel xdist workers whose sessions outlived
    # ours (their in-flight requests 302'ed to /setup mid-suite).
    #
    # The marker lives in a session temporary directory, NOT in the working
    # directory: INIT_MARKER_FILE is CWD-relative, so marking it here used to
    # leave `.system_initialized` in the checkout. `run_web.py` started from
    # that checkout afterwards took the "marker present" start-up branch, which
    # hid the defect that installations without the marker never had their
    # schema upgraded (field report after step 12). Every reader goes through
    # core.initialization.INIT_MARKER_FILE, so redirecting it is sufficient.
    import core.initialization as _init

    _init.INIT_MARKER_FILE = tmp_path_factory.mktemp("init_marker") / ".system_initialized"
    _init.mark_system_initialized()

    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="module")
def admin_credentials(pg_db):
    """Ensure an initial admin exists; return (username, password).

    Module scope, matching ``database_state_is_module_local``: each module
    starts from an empty schema, so the admin has to be created again for each
    one. The fixture is idempotent, so the cost is one lookup per module.
    """
    from core.security.service import get_auth_service

    auth = get_auth_service()
    username = "testadmin"
    password = "test-admin-password-123"
    if auth.get_user_by_username(username) is None:
        auth.create_user(username, password, role="admin")
    return username, password


@pytest.fixture()
def admin_client(app, client, admin_credentials):
    """A test client authenticated as administrator."""
    username, password = admin_credentials
    resp = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


@pytest.fixture()
def viewer_client(app, admin_credentials):
    """A test client authenticated as a read-only user.

    Uses its OWN client instance: sharing one client across roles would let
    the later login overwrite the session cookie.
    """
    from core.security.service import get_auth_service

    auth = get_auth_service()
    username = "testviewer"
    password = "test-viewer-password-123"
    if auth.get_user_by_username(username) is None:
        auth.create_user(username, password, role="viewer")
    c = app.test_client()
    resp = c.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return c


@pytest.fixture()
def client_factory(app, client, admin_credentials):
    """Factory for clients authenticated with an arbitrary role."""
    from core.security.service import get_auth_service

    def _make(role: str):
        auth = get_auth_service()
        username = f"testrole_{role}"
        password = f"test-{role}-password-123"
        if auth.get_user_by_username(username) is None:
            auth.create_user(username, password, role=role)
        c = app.test_client()
        resp = c.post("/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200
        return c

    return _make
