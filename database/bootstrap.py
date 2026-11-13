"""Single authoritative database bootstrap (DB-01).

This module is the ONLY supported way to create the application schema.
It replaces the legacy ``database/createsTables.py`` module-level DDL (which
used hardcoded credentials, executed at import time, and could not create a
fresh schema) with an explicit, idempotent, transactional bootstrap:

    ensure_database(cfg)      -> create the database if missing (UTF8)
    bootstrap_database(cfg)   -> ensure database + run all migrations
    schema_status(cfg)        -> report current migration state

Requirements implemented:
* Correct dependency order (migrations are ordered and transactional)
* No import-time DDL - nothing runs until explicitly invoked
* Parameterized connections from the unified configuration
* Extension preflight (plpgsql required; pg_trgm deliberately NOT required)
* Version tracking via schema_migrations
* Idempotency (safe to call repeatedly)
* Clear failure messages
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from database.migration_runner import (
    applied_versions,
    current_version,
    discover_migrations,
    migration_status,
    run_migrations,
)

logger = logging.getLogger(__name__)

REQUIRED_EXTENSIONS = ("plpgsql",)  # pg_trgm deliberately not required (DB-07)


class BootstrapError(RuntimeError):
    """Raised with a clear, client-safe message when bootstrap cannot proceed."""


def _admin_connect(cfg: Dict[str, Any]) -> psycopg2.extensions.connection:
    """Connect to the maintenance database ('postgres')."""
    try:
        conn = psycopg2.connect(
            dbname="postgres",
            user=cfg["user"],
            password=cfg["password"],
            host=cfg["host"],
            port=cfg["port"],
            connect_timeout=int(cfg.get("connect_timeout", 10)),
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return conn
    except psycopg2.OperationalError as exc:
        raise BootstrapError(
            "Cannot connect to PostgreSQL server. Check DB_HOST, DB_PORT, DB_USER "
            "and DB_PASSWORD configuration."
        ) from exc


def database_exists(cfg: Dict[str, Any], dbname: Optional[str] = None) -> bool:
    dbname = dbname or cfg["database"]
    conn = _admin_connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def ensure_database(cfg: Dict[str, Any], dbname: Optional[str] = None) -> bool:
    """Create the application database if missing. Returns True if created.

    Uses template0 with UTF8 encoding to avoid locale conflicts. The database
    name is composed with sql.Identifier (never string interpolation).
    """
    dbname = dbname or cfg["database"]
    if database_exists(cfg, dbname):
        return False
    conn = _admin_connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE DATABASE {} WITH ENCODING 'UTF8' TEMPLATE template0"
                ).format(sql.Identifier(dbname))
            )
        logger.info("Created database %r with UTF8 encoding", dbname)
        return True
    except psycopg2.errors.DuplicateDatabase:
        return False
    finally:
        conn.close()


def connect_application_db(cfg: Dict[str, Any]) -> psycopg2.extensions.connection:
    """Connect to the application database using unified configuration."""
    try:
        return psycopg2.connect(
            dbname=cfg["database"],
            user=cfg["user"],
            password=cfg["password"],
            host=cfg["host"],
            port=cfg["port"],
            connect_timeout=int(cfg.get("connect_timeout", 10)),
        )
    except psycopg2.OperationalError as exc:
        raise BootstrapError(
            f"Cannot connect to application database {cfg['database']!r}. "
            "Verify the database exists and credentials are correct."
        ) from exc


def preflight_extensions(conn) -> List[str]:
    """Verify required extensions are available; return missing ones."""
    missing: List[str] = []
    with conn.cursor() as cur:
        for ext in REQUIRED_EXTENSIONS:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (ext,))
            if cur.fetchone() is None:
                missing.append(ext)
    return missing


#: The only encoding the application stores text in. Corpora are Arabic,
#: Hebrew and Persian as well as Latin, and psycopg2 maps a SQL_ASCII server to
#: the ``ascii`` codec, so anything else degrades documents on ingest.
REQUIRED_ENCODING = "UTF8"


def preflight_encoding(conn) -> str:
    """Return the database's server encoding (``UTF8``, ``SQL_ASCII``, ...)."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_encoding_to_char(encoding) FROM pg_database "
                    "WHERE datname = current_database()")
        row = cur.fetchone()
    return row[0] if row else ""


def bootstrap_encoding_guard(conn) -> str:
    """Raise :class:`BootstrapError` unless the database is UTF8.

    ``ensure_database`` creates a *new* database with ``ENCODING 'UTF8'
    TEMPLATE template0``; this covers the pre-existing database an operator
    points us at, which bootstrap used to connect to without looking.
    """
    encoding = preflight_encoding(conn)
    if encoding != REQUIRED_ENCODING:
        raise BootstrapError(
            f"The database is encoded {encoding or 'unknown'!r}; SYLTHARAE "
            f"requires {REQUIRED_ENCODING} because it stores Arabic, Hebrew "
            f"and Persian text. Changing a database's encoding is not "
            f"supported in place: create a {REQUIRED_ENCODING} database and "
            f"recreate the schema (pg_dump/restore into the new database), "
            f"then re-import."
        )
    return encoding


def bootstrap_database(cfg: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Create the database if missing and apply all pending migrations.

    Returns a report dict.  Raises :class:`BootstrapError` on failure with a
    clear message; migration failures include the failing migration version.
    """
    created = ensure_database(cfg)
    conn = connect_application_db(cfg)
    try:
        bootstrap_encoding_guard(conn)
        missing = preflight_extensions(conn)
        if missing:
            raise BootstrapError(
                f"Required PostgreSQL extensions missing: {', '.join(missing)}"
            )
        if dry_run:
            pending = [m for m in migration_status(conn) if not m["applied"]]
            return {"database_created": created, "dry_run": True, "pending": pending}
        applied = run_migrations(conn)
        report = {
            "database_created": created,
            "applied_migrations": applied,
            "current_version": current_version(conn),
        }
        logger.info("Database bootstrap complete: %s", report)
        return report
    finally:
        conn.close()


def schema_status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Report the migration status of the application database."""
    if not database_exists(cfg):
        return {"database_exists": False, "migrations": [], "current_version": None}
    conn = connect_application_db(cfg)
    try:
        return {
            "database_exists": True,
            "migrations": migration_status(conn),
            "current_version": current_version(conn),
        }
    finally:
        conn.close()


def schema_is_current(cfg: Dict[str, Any]) -> bool:
    """True when the database exists and every known migration is applied."""
    if not database_exists(cfg):
        return False
    conn = connect_application_db(cfg)
    try:
        done = set(applied_versions(conn))
        return all(m.version in done for m in discover_migrations())
    finally:
        conn.close()
