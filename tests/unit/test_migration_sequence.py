"""Migration chain integrity: unique, contiguous, self-consistent versions.

Guards the reconciliation performed for the reporting/detection work: the
design documents proposed two different ``m0016`` migrations
(``m0016_content_signals`` and ``m0016_reporting``). The runner already
refuses duplicate versions at start-up; this test fails in CI first, and also
catches gaps and a filename/version mismatch, which the runner does not.
"""

import pkgutil
import re

import database.migrations as pkg
from database.migration_runner import discover_migrations


def test_versions_are_unique_contiguous_and_match_filenames():
    migrations = discover_migrations()
    versions = [m.version for m in migrations]
    assert len(versions) == len(set(versions)), "duplicate migration version"
    assert versions == sorted(versions)
    numbers = [int(v) for v in versions]
    assert numbers == list(range(1, len(numbers) + 1)), f"gap in chain: {versions}"
    modules = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__)
                     if m.name.startswith("m"))
    for module_name, migration in zip(modules, migrations):
        match = re.match(r"m(\d{4})_", module_name)
        assert match, module_name
        assert match.group(1) == migration.version, (module_name, migration.version)


def test_every_new_migration_is_reversible():
    """Migrations added by this work ship a downgrade (DB-02 best-effort rule)."""
    for migration in discover_migrations():
        if int(migration.version) >= 16:
            assert callable(getattr(migration.module, "downgrade", None)), migration.version
