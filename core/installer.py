"""
Shared installation services — single source of truth for all setup operations.

Both the web wizard (Api/routes/setup.py) and CLI installer (install.py)
consume these functions. Business logic lives here, not in the route handlers
or the CLI script.

Responsibilities:
  - System prerequisite checks
  - Database connectivity tests
  - .env file generation
  - Database creation and schema migration
  - Administrator account creation
  - Runtime directory setup
  - System initialization markers
  - Post-installation verification

Authoritative for: configuration writing, database initialization, admin
bootstrap, runtime directory creation.  All consumers delegate here.

INITIALIZATION STATE (single authoritative mechanism):
  The system is "initialized" when the filesystem marker (.system_initialized)
  exists AND the critical database tables (users, words, paths, contents) exist.
  The filesystem marker is the fast-path; the table check is authoritative.
  No redundant _setup_completed database table is used.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import secrets
import shutil
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Project root — always relative to this file, never to CWD.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ────────────────────────────────────────────────────────────────────
# System prerequisite checks
# ────────────────────────────────────────────────────────────────────
def pg_probe_target() -> Dict[str, Any]:
    """The PostgreSQL endpoint the prerequisite probe should try.

    The conventional default is ``localhost:5432``. When the application is
    already configured — environment or ``.env``, as written by the setup
    wizard, ``install.py`` or the offline Windows installer — the configured
    endpoint wins: the bundled private cluster may live on a socket directory
    (``DB_HOST=/data/pgdata``) or on a port other than 5432, and a healthy
    bundled database must not be reported as missing.
    """
    values: Dict[str, str] = {}
    try:  # python-dotenv is a runtime dependency, but the check must not fail over it
        from dotenv import dotenv_values

        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            values = {k: v for k, v in dotenv_values(env_file).items() if v}
    except Exception:  # pragma: no cover - dotenv missing or unreadable
        values = {}
    host = os.environ.get("DB_HOST") or values.get("DB_HOST") or "localhost"
    port_raw = os.environ.get("DB_PORT") or values.get("DB_PORT") or "5432"
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 5432
    return {"host": host, "port": port}


def check_system() -> Dict[str, Any]:
    """Return a dict of system prerequisite results.

    Each key maps to ``{ok: bool, detail: str, ...}``.
    """
    checks: Dict[str, Any] = {}

    # Python
    import sys
    v = sys.version_info
    py_ok = (v.major, v.minor) >= (3, 10)
    checks["python"] = {
        "ok": py_ok,
        "version": f"{v.major}.{v.minor}.{v.micro}",
        "message": "" if py_ok else f"Python {v.major}.{v.minor} is too old (need >= 3.10)",
    }

    # Disk space
    try:
        usage = shutil.disk_usage(str(_PROJECT_ROOT))
        free_mb = round(usage.free / (1024 * 1024))
        checks["disk"] = {
            "ok": free_mb >= 200,
            "free_mb": free_mb,
            "message": "" if free_mb >= 200 else f"Only {free_mb} MB free",
        }
    except OSError:
        checks["disk"] = {"ok": True, "free_mb": -1, "message": "Could not check"}

    # OS
    checks["os"] = {
        "ok": True,
        "name": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "message": "",
    }

    # Python dependencies: every declared requirement in pyproject.toml and
    # requirements.txt, checked for version and a successful import. "ok" is
    # True only when every required item is compatible.
    from core.dependency_readiness import build_report

    readiness = build_report()
    blocking = [item["name"] for item in readiness["items"] if item["blocking"]]
    checks["packages"] = {
        "ok": readiness["ready"],
        "missing": blocking,
        "message": "" if readiness["ready"] else f"Required dependencies need attention: {', '.join(blocking)}",
        "counts": readiness["counts"],
    }

    # PostgreSQL probe (just check if the configured endpoint responds)
    pg_ok = False
    pg_msg = ""
    try:
        import psycopg2
        target = pg_probe_target()
        probe = psycopg2.connect(
            host=target["host"], port=target["port"], user="postgres",
            password="", dbname="postgres", connect_timeout=3,
        )
        probe.close()
        pg_ok = True
    except psycopg2.OperationalError as e:
        err = str(e).lower()
        if "no password supplied" in err or "fe_sendauth" in err:
            pg_ok = True
            pg_msg = "PostgreSQL is running (password required)"
        elif "connection refused" in err or "could not connect" in err:
            pg_msg = "PostgreSQL is not running or not on port 5432"
        else:
            pg_msg = str(e).split("\n")[0]
    except Exception as e:
        pg_msg = str(e).split("\n")[0]
    checks["postgresql"] = {"ok": pg_ok, "message": pg_msg}

    return checks


# ────────────────────────────────────────────────────────────────────
# Database connectivity test
# ────────────────────────────────────────────────────────────────────
def test_database_connection(
    host: str = "localhost",
    port: int = 5432,
    user: str = "postgres",
    password: str = "",
    database: str = "analysis",
) -> Dict[str, Any]:
    """Test PostgreSQL connectivity and return structured result."""
    if not password:
        return {"ok": False, "message": "Password is required"}

    import psycopg2

    # Test 1: server connection
    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname="postgres", connect_timeout=10,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            pg_version = cur.fetchone()[0]
        conn.close()
    except Exception as e:
        return {
            "ok": False, "stage": "server",
            "message": f"Cannot connect to PostgreSQL: {str(e).splitlines()[0]}",
        }

    # Test 2: application database
    db_exists = False
    table_count = 0
    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname="postgres", connect_timeout=5,
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            db_exists = cur.fetchone() is not None
        conn.close()

        if db_exists:
            conn = psycopg2.connect(
                host=host, port=port, user=user, password=password,
                dbname=database, connect_timeout=5,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
                table_count = cur.fetchone()[0]
            conn.close()
    except Exception:
        pass

    return {
        "ok": True,
        "pg_version": pg_version.split(",")[0],
        "database_exists": db_exists,
        "table_count": table_count,
        "message": f"Connected to {pg_version.split(',')[0]}",
    }


# ────────────────────────────────────────────────────────────────────
# Write .env file
# ────────────────────────────────────────────────────────────────────
def _format_dotenv_value(value: Any) -> str:
    """Serialize one literal value for python-dotenv without interpolation.

    Simple values remain readable as ``KEY=value``. Values containing spaces,
    quotes, comments or other dotenv syntax are double-quoted and escaped so
    they round-trip exactly when loaded with ``interpolate=False``.
    """
    text = "" if value is None else str(value)
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:@,+!%-"
    if all(char in safe for char in text):
        return text
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _env_line(key: str, value: Any) -> str:
    return f"{key}={_format_dotenv_value(value)}"


def _waitress_installed() -> bool:
    try:
        import waitress  # noqa: F401
    except ImportError:
        return False
    return True


def write_env_file(config: Dict[str, str], project_root: Optional[Path] = None) -> Path:
    """Write a .env file from a flat config dict. Returns the path written.

    Values are serialized literally; all project loaders use
    ``interpolate=False`` so credentials containing ``${...}`` are preserved.
    A header and auto-generated ``FLASK_SECRET_KEY`` are added if not present.
    """
    root = project_root or _PROJECT_ROOT
    env_path = root / ".env"

    secret_key = config.get("FLASK_SECRET_KEY", "")
    if not secret_key:
        secret_key = secrets.token_hex(32)

    def _setting(key: str, default: str) -> str:
        """The wizard's value, else what the server was started with."""
        value = config.get(key) or os.environ.get(key) or ""
        return str(value).strip() or default

    environment = config.get("FLASK_ENV", "production")
    # Production serves with Waitress when it is installed; a restart from
    # this file alone must not fall back to the development server.
    wsgi_default = "waitress" if environment == "production" and _waitress_installed() else "flask"

    lines = [
        "# SYLTHARAE — Environment Configuration",
        "# Generated by the installation system",
        "",
        "# ── Flask / Application ──",
        _env_line("FLASK_SECRET_KEY", secret_key),
        _env_line("FLASK_ENV", config.get("FLASK_ENV", "production")),
        _env_line("FLASK_DEBUG", "true" if config.get("FLASK_ENV") == "development" else "false"),
        _env_line("FLASK_HOST", config.get("FLASK_HOST", "0.0.0.0")),
        _env_line("FLASK_PORT", config.get("FLASK_PORT", "5000")),
        "",
        "# ── Serving (docs/OPERATIONS.md) ──",
        _env_line("WSGI_SERVER", _setting("WSGI_SERVER", wsgi_default)),
        _env_line("WAITRESS_THREADS", _setting("WAITRESS_THREADS", "32")),
        "# Reverse proxies in front of the app; 1 behind one TLS proxy, never guessed.",
        _env_line("TRUSTED_PROXY_COUNT", _setting("TRUSTED_PROXY_COUNT", "0")),
        *([_env_line("SOURCE_CODE_URL", _setting("SOURCE_CODE_URL", ""))]
          if _setting("SOURCE_CODE_URL", "") else []),
        "",
        "# ── Database ──",
        _env_line("DB_HOST", config.get("DB_HOST", "localhost")),
        _env_line("DB_PORT", config.get("DB_PORT", "5432")),
        _env_line("DB_NAME", config.get("DB_NAME", "analysis")),
        _env_line("DB_USER", config.get("DB_USER", "postgres")),
        _env_line("DB_PASSWORD", config.get("DB_PASSWORD", "")),
        "",
        "# ── Connection Pool ──",
        _env_line("DB_POOL_MIN_CONNECTIONS", config.get("DB_POOL_MIN_CONNECTIONS", "2")),
        _env_line("DB_POOL_MAX_CONNECTIONS", config.get("DB_POOL_MAX_CONNECTIONS", "25")),
        "DB_POOL_TIMEOUT=30",
        "DB_QUERY_TIMEOUT=60",
        "",
        "# ── Security / Admin ──",
        _env_line("APP_ADMIN_USERNAME", config.get("APP_ADMIN_USERNAME", "admin")),
        # APP_ADMIN_PASSWORD is deliberately not stored: the administrator
        # now exists in the database, and a cleartext copy here would outlive
        # every password change.
        _env_line("PASSWORD_MIN_LENGTH", config.get("PASSWORD_MIN_LENGTH", "12")),
        _env_line("SECURITY_MAX_FAILED_LOGINS", config.get("SECURITY_MAX_FAILED_LOGINS", "5")),
        _env_line("SECURITY_LOCKOUT_MINUTES", config.get("SECURITY_LOCKOUT_MINUTES", "15")),
        _env_line("SECURITY_SESSION_HOURS", config.get("SECURITY_SESSION_HOURS", "12")),
        _env_line("SECURITY_SESSION_IDLE_HOURS", config.get("SECURITY_SESSION_IDLE_HOURS", "6")),
        "",
        "# ── Rate Limiting ──",
        _env_line("RATE_LIMIT_PER_MINUTE", config.get("RATE_LIMIT_PER_MINUTE", "60")),
        _env_line("RATE_LIMIT_PER_HOUR", config.get("RATE_LIMIT_PER_HOUR", "600")),
        "",
        "# ── Ingestion ──",
        _env_line("INGESTION_ROOTS", config.get("INGESTION_ROOTS", "")),
        "",
        "# ── Processing ──",
        _env_line("MAX_WORKERS", config.get("MAX_WORKERS", "8")),
        "FILE_CHUNK_SIZE=10485760",
        _env_line("FILE_PROCESSING_TIMEOUT", config.get("FILE_PROCESSING_TIMEOUT", "1200")),
        "",
        "# ── Logging ──",
        _env_line("LOG_LEVEL", config.get("LOG_LEVEL", "INFO")),
        "ACTION_LOGGING_ENABLED=true",
        "PERFORMANCE_MONITORING=true",
        "",
    ]

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return env_path


def apply_config_to_environ(config: Dict[str, str]) -> None:
    """Set environment variables from a config dict so the current process
    uses the new configuration immediately."""
    env_keys = [
        "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
        "FLASK_ENV", "FLASK_PORT", "FLASK_HOST", "FLASK_SECRET_KEY",
        "APP_ADMIN_USERNAME", "APP_ADMIN_PASSWORD", "PASSWORD_MIN_LENGTH",
        "SECURITY_MAX_FAILED_LOGINS", "SECURITY_LOCKOUT_MINUTES",
        "SECURITY_SESSION_HOURS", "SECURITY_SESSION_IDLE_HOURS",
        "RATE_LIMIT_PER_MINUTE", "RATE_LIMIT_PER_HOUR",
        "LOG_LEVEL", "MAX_WORKERS", "FILE_PROCESSING_TIMEOUT",
        "INGESTION_ROOTS",
    ]
    for key in env_keys:
        val = config.get(key)
        if val is not None and str(val):
            os.environ[key] = str(val)


# ────────────────────────────────────────────────────────────────────
# Database creation & schema migration
# ────────────────────────────────────────────────────────────────────
def ensure_database_and_schema(
    host: str, port: int, user: str, password: str, database: str,
) -> Dict[str, Any]:
    """Create the database if missing and run all pending migrations.

    Returns a report dict with ``database_created``, ``applied_migrations``.
    Safe to call on an existing database (idempotent).
    """
    from database.bootstrap import bootstrap_database
    return bootstrap_database({
        "host": host, "port": port, "user": user,
        "password": password, "database": database,
    })


def ensure_gazetteer_seed(
    host: str, port: int, user: str, password: str, database: str,
) -> Dict[str, Any]:
    """Load the reviewed gazetteer seed into the database (idempotent).

    The baseline migration creates ``geo_places``/``geo_place_names`` empty; the
    place detector matches against their rows and is disabled until the committed
    seed (``data/gazetteer/places_seed.json``) is loaded. Without this step a
    fresh install stored every document with "no gazetteer is loaded" and zero
    place signals. :func:`services.geo.gazetteer.sync_seed` only writes rows that
    differ and never retires by absence, so this is safe to call repeatedly.
    """
    from database.bootstrap import connect_application_db
    from services.geo import gazetteer as gz

    conn = connect_application_db({
        "host": host, "port": port, "user": user,
        "password": password, "database": database,
    })
    try:
        seed = gz.load_seed_file()
        with conn.cursor() as cur:
            result = gz.sync_seed(cur, seed, loaded_by="installer")
        conn.commit()
        return result
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────
# Administrator account
# ────────────────────────────────────────────────────────────────────
def ensure_admin_account(
    username: str, password: str, ip_address: str = "",
) -> Dict[str, Any]:
    """Create the initial admin account if no users exist.

    Returns ``{"created": bool, "username": str}``.
    """
    from core.security.service import get_auth_service, ROLE_ADMIN

    auth = get_auth_service()
    if auth.user_count() > 0:
        return {"created": False, "username": username}

    user = auth.create_user(
        username=username, password=password,
        role=ROLE_ADMIN, must_change_password=False,
    )
    auth.audit("setup.initial_admin_created",
               user_id=user.id, username=username,
               ip_address=ip_address)
    return {"created": True, "username": username}


# ────────────────────────────────────────────────────────────────────
# Runtime directories
# ────────────────────────────────────────────────────────────────────
def ensure_runtime_directories() -> Dict[str, str]:
    """Create all required runtime directories. Returns name→path mapping."""
    from core.app_paths import ensure_runtime_dirs
    dirs = ensure_runtime_dirs()
    return {name: str(path) for name, path in dirs.items()}


# ────────────────────────────────────────────────────────────────────
# System initialization marker (SINGLE AUTHORITATIVE MECHANISM)
# ────────────────────────────────────────────────────────────────────
def mark_system_initialized() -> bool:
    """Write the filesystem marker that indicates a successful installation.

    The authoritative initialization-state check combines this marker with
    a critical-tables database query (see Api/routes/setup.py:_is_initialized).
    No redundant _setup_completed database table is written.
    """
    try:
        from core.initialization import mark_system_initialized as _mark
        _mark()
        logger.info("System marked as initialized (filesystem marker)")
    except Exception as e:
        logger.warning("Could not write filesystem marker: %s", e)
    return True


# ────────────────────────────────────────────────────────────────────
# Post-installation verification
# ────────────────────────────────────────────────────────────────────
def verify_installation(
    host: str = "localhost", port: int = 5432,
    user: str = "postgres", password: str = "", database: str = "analysis",
) -> Dict[str, Any]:
    """Run post-install checks and return structured results."""
    results: List[Dict[str, Any]] = []

    # Flask app
    try:
        os.environ.setdefault("FLASK_SECRET_KEY",
                              "verify-temp-key-0123456789abcdef0123456789abcdef")
        from apps.web.app import app
        route_count = len(list(app.url_map.iter_rules()))
        results.append({"check": "flask_app", "ok": True,
                        "detail": f"{route_count} routes"})
    except Exception as e:
        results.append({"check": "flask_app", "ok": False,
                        "detail": str(e).splitlines()[0]})

    # Database
    if password:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=host, port=port, user=user, password=password,
                dbname=database, connect_timeout=10,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='public'")
                table_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name IN ('users','sessions','paths','words','contents')")
                critical = {r[0] for r in cur.fetchall()}
            conn.close()
            missing = {"users", "sessions", "paths", "words"} - critical
            results.append({
                "check": "database", "ok": len(missing) == 0,
                "detail": f"{table_count} tables; missing: {missing}" if missing
                          else f"{table_count} tables, all critical tables present",
            })
        except Exception as e:
            results.append({"check": "database", "ok": False,
                            "detail": str(e).splitlines()[0]})

    # Gazetteer (the place detector is disabled without at least one active place)
    if password:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=host, port=port, user=user, password=password,
                dbname=database, connect_timeout=10,
            )
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM geo_places WHERE NOT retired")
                active_places = cur.fetchone()[0]
            conn.close()
            results.append({
                "check": "gazetteer", "ok": active_places > 0,
                "detail": f"{active_places} active place(s)" if active_places
                          else "not seeded — place detection disabled",
            })
        except Exception as e:
            results.append({"check": "gazetteer", "ok": False,
                            "detail": str(e).splitlines()[0]})

    # Initialization marker
    try:
        from core.initialization import is_system_initialized
        marker = is_system_initialized()
        results.append({"check": "init_marker", "ok": marker,
                        "detail": "present" if marker else "missing"})
    except Exception as e:
        results.append({"check": "init_marker", "ok": False,
                        "detail": str(e).splitlines()[0]})

    all_ok = all(r["ok"] for r in results)
    return {"ok": all_ok, "checks": results}


# ────────────────────────────────────────────────────────────────────
# Full installation orchestrator (used by both web and CLI)
# ────────────────────────────────────────────────────────────────────
def run_installation(config: Dict[str, str], client_ip: str = "") -> Dict[str, Any]:
    """Execute the complete installation sequence.

    *config* is a flat dict of environment-style keys (DB_HOST, DB_PASSWORD,
    APP_ADMIN_USERNAME, etc.). *client_ip* is the address the setup wizard
    was used from, recorded with the administrator's creation; the command-line
    installer has none.

    Returns ``{"ok": bool, "steps": [...], "error": str | None}``.

    Steps:
      A. Write .env + apply to current process environment
      B. Create database + run migrations (idempotent)
      C. Create admin account (idempotent — skipped if users exist)
      D. Ensure runtime directories
      E. Mark system initialized (filesystem marker)

    Does NOT write .db_config.json — .env is the single authoritative
    database configuration persistence.
    """
    steps: List[Dict[str, Any]] = []

    # A: Write .env
    try:
        write_env_file(config)
        apply_config_to_environ(config)
        steps.append({"name": "Write configuration", "ok": True,
                       "detail": ".env created"})
    except Exception as e:
        steps.append({"name": "Write configuration", "ok": False, "detail": str(e)})
        return {"ok": False, "steps": steps,
                "error": f"Failed to write configuration: {e}"}

    # B: Create database & run migrations
    db_host = config.get("DB_HOST", "localhost")
    db_port = int(config.get("DB_PORT", 5432))
    db_user = config.get("DB_USER", "postgres")
    db_password = config.get("DB_PASSWORD", "")
    db_name = config.get("DB_NAME", "analysis")

    try:
        report = ensure_database_and_schema(db_host, db_port, db_user,
                                            db_password, db_name)
        created = report.get("database_created", False)
        applied = report.get("applied_migrations", [])
        steps.append({"name": "Database & schema", "ok": True,
                       "detail": (f"Created '{db_name}'" if created else
                                  f"'{db_name}' exists") +
                                  f"; {len(applied)} migration(s) applied"})
    except Exception as e:
        steps.append({"name": "Database & schema", "ok": False, "detail": str(e)})
        return {"ok": False, "steps": steps,
                "error": f"Database setup failed: {e}"}

    # B2: Load gazetteer seed (the place detector depends on it; idempotent).
    # Not fatal: a failure leaves place detection degraded but the rest of the
    # system usable, and run_web reconciles the seed again on startup.
    try:
        gz_report = ensure_gazetteer_seed(db_host, db_port, db_user,
                                          db_password, db_name)
        steps.append({"name": "Gazetteer seed", "ok": True,
                       "detail": f"{gz_report['place_count']} places, "
                                 f"{gz_report['name_count']} names"
                                 + ("" if gz_report["changed"] else " (already loaded)")})
    except Exception as e:
        steps.append({"name": "Gazetteer seed", "ok": False, "detail": str(e)})

    # C: Create admin account (idempotent)
    try:
        admin_user = config.get("APP_ADMIN_USERNAME", "admin")
        admin_pass = config.get("APP_ADMIN_PASSWORD", "")
        result = ensure_admin_account(admin_user, admin_pass, client_ip)
        steps.append({"name": "Administrator", "ok": True,
                       "detail": f"'{admin_user}' created" if result["created"]
                                 else "Admin already exists"})
    except Exception as e:
        # The one step whose failure leaves the system unusable: without an
        # account nobody can log in, and once the marker below is written
        # /setup refuses to run again. Stop here and leave the system
        # uninitialized so the installer (or the wizard) can be re-run.
        steps.append({"name": "Administrator", "ok": False, "detail": str(e)})
        return {"ok": False, "steps": steps,
                "error": f"Administrator account was not created: {e}. "
                         f"The system is left uninitialized so the installer "
                         f"or the /setup wizard can be run again."}

    # D: Runtime directories
    try:
        dirs = ensure_runtime_directories()
        steps.append({"name": "Runtime directories", "ok": True,
                       "detail": f"{len(dirs)} directories ensured"})
    except Exception as e:
        steps.append({"name": "Runtime directories", "ok": False, "detail": str(e)})

    # E: Mark initialized (filesystem marker only)
    try:
        mark_system_initialized()
        steps.append({"name": "Finalize", "ok": True,
                       "detail": "System marked as initialized"})
    except Exception as e:
        steps.append({"name": "Finalize", "ok": False, "detail": str(e)})

    all_ok = all(s["ok"] for s in steps)
    return {
        "ok": all_ok,
        "steps": steps,
        "error": None if all_ok else "Installation completed with errors",
    }
