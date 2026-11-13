"""Unit: the module-isolation decision, and the reset it gates.

``tests/suite_isolation.py`` decides whether a module gets a clean database.
Two wrong answers hurt in opposite ways: a wrong False leaves one module's rows
visible to the next (the defect this round fixed), and a wrong True starts a
PostgreSQL server and truncates the schema for a module that never asked for a
database - every unit module, on every run.

The decision is tested here against stand-in collected items, and against the
live session by ``tests/integration/test_isolation_canary_*.py`` (which run in
both orders).
"""

from __future__ import annotations

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
for candidate in (str(PROJECT_ROOT), str(PROJECT_ROOT / "tests")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import suite_isolation  # noqa: E402  (the path conftest uses)

DB_MODULE = "tests/integration/test_resume_real_database.py"
UNIT_MODULE = "tests/unit/test_map_packages.py"


class Item:
    """What ``module_needs_database`` reads off a collected test."""

    def __init__(self, path, fixturenames):
        self.path = pathlib.Path(path)
        self.fixturenames = tuple(fixturenames)


def test_a_module_that_asks_for_pg_db_is_a_database_module():
    items = [Item(DB_MODULE, ("pg", "database", "tmp_path")),
             Item(DB_MODULE, ("pg_db", "folder"))]
    assert suite_isolation.module_needs_database(DB_MODULE, items) is True


def test_a_module_that_asks_for_it_through_a_fixture_is_a_database_module():
    # Most database modules never name ``pg_db``: they ask for a module fixture
    # that does. pytest's ``fixturenames`` is the whole closure, which is the
    # only reason this decision is right for those modules.
    items = [Item("tests/integration/test_signal_ingestion.py",
                  ("tenant", "pg_db", "connect", "request"))]
    assert suite_isolation.module_needs_database(
        "tests/integration/test_signal_ingestion.py", items) is True


def test_a_pure_unit_module_is_not_a_database_module():
    items = [Item(UNIT_MODULE, ("monkeypatch", "tmp_path", "request"))]
    assert suite_isolation.module_needs_database(UNIT_MODULE, items) is False


def test_another_modules_items_do_not_make_this_one_a_database_module():
    items = [Item(DB_MODULE, ("pg_db",))]
    assert suite_isolation.module_needs_database(UNIT_MODULE, items) is False


def test_an_uncollected_module_is_not_a_database_module():
    assert suite_isolation.module_needs_database("tests/unit/test_nothing.py", []) is False


def test_todays_session_agrees_for_this_pure_unit_module(request):
    """The live decision for the module running this test: no database."""
    assert suite_isolation.module_needs_database(
        request.node.path, request.session.items) is False


def test_the_reset_skips_schema_bookkeeping_only():
    """``schema_migrations`` must survive, or every module would re-migrate."""
    assert "schema_migrations" in suite_isolation._SCHEMA_BOOKKEEPING
    assert suite_isolation._SCHEMA_BOOKKEEPING == {"schema_migrations"}
