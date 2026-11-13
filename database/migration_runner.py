"""Migration runner (DB-02) - single authoritative schema evolution mechanism.

Usage::

    from database.migration_runner import run_migrations, current_version
    run_migrations(conn)          # apply pending migrations in order
    current_version(conn)         # latest applied version or None

Each migration runs inside its own transaction with version recorded in
``schema_migrations``.  Failure of any migration aborts the bootstrap with a
clear error message identifying the failing migration.
"""

from __future__ import annotations

import logging
import pkgutil
import importlib
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

MIGRATIONS_PACKAGE = "database.migrations"


@dataclass
class Migration:
    version: str
    name: str
    module: object


def discover_migrations() -> List[Migration]:
    """Discover and sort migration modules from ``database/migrations``."""
    import database.migrations as pkg

    migrations: List[Migration] = []
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if not mod_info.name.startswith("m"):
            continue
        module = importlib.import_module(f"{MIGRATIONS_PACKAGE}.{mod_info.name}")
        version = getattr(module, "version", None)
        name = getattr(module, "name", mod_info.name)
        upgrade = getattr(module, "upgrade", None)
        if version is None or upgrade is None:
            raise RuntimeError(
                f"Migration module {mod_info.name} is missing 'version' or 'upgrade'"
            )
        migrations.append(Migration(version=version, name=name, module=module))
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise RuntimeError("Duplicate migration versions detected")
    return migrations


def _ensure_tracking_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def applied_versions(conn) -> List[str]:
    _ensure_tracking_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        return [row[0] for row in cur.fetchall()]


def current_version(conn) -> Optional[str]:
    versions = applied_versions(conn)
    return versions[-1] if versions else None


def run_migrations(conn, dry_run: bool = False) -> List[str]:
    """Apply all pending migrations in order. Returns applied versions.

    Each migration runs in its own transaction.  On failure the transaction
    is rolled back and the exception is re-raised with the migration
    identified - fresh installs and upgrades fail loudly, never silently.
    """
    _ensure_tracking_table(conn)
    done = set(applied_versions(conn))
    applied_now: List[str] = []

    for migration in discover_migrations():
        if migration.version in done:
            continue
        if dry_run:
            logger.info("[dry-run] would apply migration %s (%s)", migration.version, migration.name)
            continue
        try:
            logger.info("Applying migration %s (%s)", migration.version, migration.name)
            migration.module.upgrade(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                    (migration.version, migration.name),
                )
            conn.commit()
            applied_now.append(migration.version)
        except Exception as exc:
            conn.rollback()
            logger.error(
                "Migration %s (%s) failed and was rolled back",
                migration.version, migration.name,
            )
            raise RuntimeError(
                f"Migration {migration.version} ({migration.name}) failed: "
                f"{exc.__class__.__name__}"
            ) from exc
    return applied_now


def migration_status(conn) -> List[dict]:
    """Report every migration and whether it is applied (for readiness UI)."""
    done = set(applied_versions(conn))
    return [
        {"version": m.version, "name": m.name, "applied": m.version in done}
        for m in discover_migrations()
    ]
