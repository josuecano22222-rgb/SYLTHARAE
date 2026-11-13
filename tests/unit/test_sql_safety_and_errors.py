"""Unit tests: SQL identifier safety (SEC-03) and error sanitization (SEC-08)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.sql_safety import validate_identifier, qualified_identifier, sort_direction, IdentifierError


SORT_ALLOWLIST = {"id": "tc.id", "title_data": "tc.title_data", "file_count": "tc.id"}


class TestValidateIdentifier:
    def test_allowlisted_value_mapped(self):
        assert validate_identifier("id", SORT_ALLOWLIST) == "tc.id"
        assert validate_identifier("file_count", SORT_ALLOWLIST) == "tc.id"

    def test_non_allowlisted_rejected(self):
        with pytest.raises(IdentifierError):
            validate_identifier("title_data; DROP TABLE users", SORT_ALLOWLIST)

    def test_injection_payloads_rejected(self):
        payloads = [
            "id; DROP TABLE users; --",
            "id --",
            "1=1",
            "(SELECT 1)",
            "id UNION SELECT password FROM users",
            "tc.id; DELETE FROM paths",
            "",
            None,
        ]
        for p in payloads:
            with pytest.raises(IdentifierError):
                validate_identifier(p, SORT_ALLOWLIST)

    def test_set_allowlist(self):
        assert validate_identifier("col_a", frozenset({"col_a", "col_b"})) == "col_a"
        with pytest.raises(IdentifierError):
            validate_identifier("col_c", frozenset({"col_a", "col_b"}))


class TestQualifiedIdentifier:
    def test_simple(self):
        from psycopg2 import sql

        comp = qualified_identifier("paths")
        assert isinstance(comp, sql.Composable)

    def test_rejects_bad(self):
        with pytest.raises(IdentifierError):
            qualified_identifier("paths; DROP TABLE x")


class TestSortDirection:
    def test_valid(self):
        assert sort_direction("asc") == "ASC"
        assert sort_direction("DESC") == "DESC"

    def test_invalid_raises(self):
        with pytest.raises(IdentifierError):
            sort_direction("bogus; DROP TABLE x", default="ASC")
