"""Q07 flat boolean OR compilation (candidate fan-out reduction)."""

from __future__ import annotations

import re

import pytest

from core.criteria.sql import literal_expression_sql, parse_boolean_expression
from core.search.boolean_or_flat import (
    flat_or_literal_sql,
    flatten_disjunction_terms,
    is_pure_disjunction,
)


Q07 = "report OR invoice OR contract OR memo"


def test_pure_disjunction_recognized():
    expr = parse_boolean_expression(Q07)
    assert expr is not None
    assert is_pure_disjunction(expr)
    terms = flatten_disjunction_terms(expr)
    assert terms == ["report", "invoice", "contract", "memo"]


def test_and_expression_not_pure():
    expr = parse_boolean_expression("report AND finance")
    assert expr is not None
    assert not is_pure_disjunction(expr)


def test_flat_sql_single_contents_raw_probe():
    sql, _params = flat_or_literal_sql(
        ["report", "invoice"],
        case_sensitive=False,
        whole_word=False,
    )
    assert sql.count("FROM contents_raw cr ") == 1
    assert "contents_raw cr_1" not in sql
    assert sql.count("FROM words_hashs _search_wp ") == 1


def test_nested_literal_duplicates_content_exists_per_term():
    expr = parse_boolean_expression(Q07)
    nested_sql, _ = literal_expression_sql(expr, case_sensitive=False, whole_word=False)
    assert nested_sql.count("FROM contents_raw cr ") >= 4


def test_flat_or_reduces_content_exists_fan_out():
    expr = parse_boolean_expression(Q07)
    terms = flatten_disjunction_terms(expr)
    flat_sql, _flat_params = flat_or_literal_sql(
        terms, case_sensitive=False, whole_word=False
    )
    nested_sql, _nested_params = literal_expression_sql(
        expr, case_sensitive=False, whole_word=False
    )
    assert flat_sql.count("FROM contents_raw cr ") == 1
    assert nested_sql.count("FROM contents_raw cr ") >= 4


def _vocabulary_cursor(vocabulary: set[str]):
    from Api.services import search_service  # noqa: F401

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, params))

        def fetchone(self):
            return (0,)

        def fetchall(self):
            sql = self.queries[-1][0] if self.queries else ""
            if "word = ANY" in sql:
                keys = self.queries[-1][1][0]
                return [(k, hash(k) % 10_000) for k in keys if k in vocabulary]
            return []

        def close(self):
            pass

    return Cursor


def test_advanced_search_uses_flat_or_for_q07(monkeypatch):
    from Api.services import search_service

    vocab = {"report", "invoice", "contract", "memo"}

    class Connection:
        def __init__(self):
            self.cursor_instance = _vocabulary_cursor(vocab)()

        def cursor(self):
            return self.cursor_instance

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    connection = Connection()
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

    search_service.SearchService.advanced_search(
        Q07,
        limit=5,
        use_bm25=False,
        use_expansion=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="exact",
    )
    assert search_service.SearchService._last_literal_compile == "flat_or"
    candidate_sql = next(
        (sql for sql, _ in connection.cursor_instance.queries if "SELECT DISTINCT" in sql),
        "",
    )
    assert candidate_sql.count("FROM contents_raw cr ") == 1


def test_or_missing_term_still_runs_literal(monkeypatch):
    from Api.services import search_service

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, params))

        def fetchone(self):
            return (0,)

        def fetchall(self):
            if "word = ANY" in self.queries[-1][0]:
                return [("report", 1)]
            return []

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(search_service, "get_connection", lambda: Connection())
    search_service.SearchService.advanced_search(
        "report OR zzzzmissing",
        limit=5,
        use_bm25=False,
        use_expansion=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="exact",
    )
    assert search_service.SearchService._last_literal_compile == "flat_or"


@pytest.mark.parametrize(
    "query,expect_flat,vocab",
    [
        ("report OR invoice", True, {"report", "invoice"}),
        ("report AND finance", False, {"report", "finance"}),
        ('"annual report" OR memo', True, {"annual report", "memo"}),
    ],
)
def test_literal_compile_mode(query, expect_flat, vocab, monkeypatch):
    from Api.services import search_service

    class Connection:
        def __init__(self):
            self.cursor_instance = _vocabulary_cursor(vocab)()

        def cursor(self):
            return self.cursor_instance

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(search_service, "get_connection", lambda: Connection())
    search_service.SearchService.advanced_search(
        query,
        limit=3,
        use_bm25=False,
        use_expansion=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="skip",
    )
    mode = search_service.SearchService._last_literal_compile
    if expect_flat:
        assert mode == "flat_or"
    else:
        assert mode == "nested_literal"
