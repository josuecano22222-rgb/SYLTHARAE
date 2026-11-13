"""Zero-result indexed-vocabulary short-circuit (bounded perf slice)."""

from __future__ import annotations

import pytest

from core.search.short_circuit import (
    assess_indexed_vocabulary_short_circuit,
    indexed_expression_provably_empty,
    looks_like_regex_pattern,
)
from core.search.words import normalize_search_token


class _WordCursor:
    """Minimal cursor: vocabulary contains only tokens present in ``vocabulary``."""

    def __init__(self, vocabulary: set[str]):
        self.vocabulary = {normalize_search_token(w) for w in vocabulary}
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, query, params=()):
        self.queries.append((query, tuple(params)))

    def fetchall(self):
        sql = self.queries[-1][0]
        if "word = ANY" not in sql:
            return []
        keys = self.queries[-1][1][0]
        return [(k, hash(k) % 10_000) for k in keys if k in self.vocabulary]


class _SearchConnection:
    def __init__(self, vocabulary: set[str]):
        self.cursor_instance = _WordCursor(vocabulary)

    def cursor(self):
        return self.cursor_instance

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _candidate_sql_calls(connection: _SearchConnection) -> list[str]:
    return [
        sql
        for sql, _ in connection.cursor_instance.queries
        if "FROM paths p" in sql
        and ("SELECT DISTINCT" in sql or "WITH candidate_paths" in sql)
    ]


def _count_sql_calls(connection: _SearchConnection) -> list[str]:
    return [sql for sql, _ in connection.cursor_instance.queries if "SELECT" in sql and "COUNT" in sql]


@pytest.mark.parametrize(
    "query,expect_short",
    [
        ("report", False),
        ("xylophonezzzz", True),
        ("zzzznoindexterm99999", True),
        ("report finance", False),
        ("report zzzzmissing", True),
    ],
)
def test_assess_plain_and_semantics(query, expect_short):
    cursor = _WordCursor({"report", "finance", "alpha"})
    intent = {"has_operators": False, "has_quotes": False}
    meta = assess_indexed_vocabulary_short_circuit(
        query=query,
        intent=intent,
        case_sensitive=False,
        whole_word=False,
        expansion_terms=[],
        cursor=cursor,
    )
    if expect_short:
        assert meta and meta.get("short_circuit") is True
    else:
        assert meta is None


def test_assess_boolean_and_or_semantics():
    cursor = _WordCursor({"report", "finance"})

    def assess(q: str):
        return assess_indexed_vocabulary_short_circuit(
            query=q,
            intent={"has_operators": True, "has_quotes": False},
            case_sensitive=False,
            whole_word=False,
            expansion_terms=[],
            cursor=cursor,
        )

    assert assess("report AND finance") is None
    assert assess("report AND missingtoken") is not None
    assert assess("missingtoken AND report") is not None
    assert assess("missingtoken AND othertoken") is not None
    assert assess("report OR missingtoken") is None
    assert assess("missingtoken OR report") is None
    assert assess("missingtoken OR othertoken") is not None


class _PathHitCursor(_WordCursor):
    """Vocabulary without the term, but a path the term matches (a file name)."""

    def fetchall(self):
        sql = self.queries[-1][0]
        if "word = ANY" in sql:
            return super().fetchall()
        if "FROM paths p" in sql:
            return [(1,)]  # the bounded existence probe found a match
        return []


@pytest.mark.parametrize("query,intent", [
    ("e04c07fc", {"has_operators": False, "has_quotes": False}),
    ("alpha-report-e04c07fc.txt", {"has_operators": False, "has_quotes": False}),
    ("report AND e04c07fc", {"has_operators": True, "has_quotes": False}),
])
def test_a_term_matching_a_file_name_is_never_short_circuited(query, intent):
    """A vocabulary miss is not proof that nothing matches.

    Interactive search also matches file names, file types, source and side
    names, the stored raw text and the token table. A file named
    ``alpha-report-e04c07fc.txt`` matched the query ``e04c07fc`` plainly, yet
    the short-circuit answered zero results without running a query - the
    existence probe is what makes the empty answer provable.
    """
    cursor = _PathHitCursor({"report"})
    assert assess_indexed_vocabulary_short_circuit(
        query=query,
        intent=intent,
        case_sensitive=False,
        whole_word=False,
        expansion_terms=[],
        cursor=cursor,
    ) is None


def test_regex_not_short_circuited():
    assert looks_like_regex_pattern("rep[a-z]{2,4}")
    cursor = _WordCursor(set())
    meta = assess_indexed_vocabulary_short_circuit(
        query="rep[a-z]{2,4}",
        intent={"has_operators": False, "has_quotes": False},
        case_sensitive=False,
        whole_word=False,
        expansion_terms=[],
        cursor=cursor,
    )
    assert meta is None


def test_advanced_search_skips_candidate_sql_for_missing_plain_term(monkeypatch):
    from Api.services import search_service

    connection = _SearchConnection({"report", "finance"})
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

    results, total = search_service.SearchService.advanced_search(
        "zzzznoindexterm99999",
        limit=10,
        offset=0,
        use_bm25=False,
        use_expansion=False,
        use_fuzzy=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="exact",
    )
    assert results == []
    assert total == 0
    assert _candidate_sql_calls(connection) == []
    assert _count_sql_calls(connection) == []
    assert search_service.SearchService._last_short_circuit is not None


def test_advanced_search_boolean_and_missing_skips_heavy_sql(monkeypatch):
    from Api.services import search_service

    connection = _SearchConnection({"report", "finance"})
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

    results, total = search_service.SearchService.advanced_search(
        "report AND zzzzmissing",
        limit=10,
        use_bm25=False,
        use_expansion=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="exact",
    )
    assert results == [] and total == 0
    assert _candidate_sql_calls(connection) == []
    assert _count_sql_calls(connection) == []


def test_advanced_search_boolean_or_partial_still_runs_sql(monkeypatch):
    from Api.services import search_service

    class Cursor(_WordCursor):
        def fetchone(self):
            if "COUNT" in self.queries[-1][0]:
                return (0,)
            return (1,)

        def fetchall(self):
            sql = self.queries[-1][0]
            if "word = ANY" in sql:
                return super().fetchall()
            if "FROM paths p" in sql:
                return []
            return []

    connection = _SearchConnection({"report"})
    connection.cursor_instance = Cursor({"report"})
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

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
    assert _count_sql_calls(connection) or _candidate_sql_calls(connection)


def test_indexed_expression_provably_empty_tree():
    id_map = {"report": 1}
    assert indexed_expression_provably_empty(("term", "missing"), id_map)
    assert not indexed_expression_provably_empty(("term", "report"), id_map)
    assert indexed_expression_provably_empty(
        ("and", ("term", "report"), ("term", "missing")), id_map
    )
    assert not indexed_expression_provably_empty(
        ("or", ("term", "report"), ("term", "missing")), id_map
    )
    assert indexed_expression_provably_empty(
        ("or", ("term", "a"), ("term", "b")), {}
    )
