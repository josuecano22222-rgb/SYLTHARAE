from __future__ import annotations

from Api.services import search_service


def test_fast_mode_uses_sql_limit_offset(monkeypatch):
    """Fast mode emits SQL LIMIT/OFFSET for an ordinary two-word search.

    The double answers the vocabulary lookup, so ``alpha`` and ``beta`` are
    indexed words in a corpus that may hold them - the case this test is about
    (the SQL shape). A corpus where a term is absent from the vocabulary *and*
    matches no path at all is a provably empty search, and the short-circuit is
    entitled to answer it without SQL: that case is
    ``tests/unit/test_search_zero_shortcircuit.py``.
    """

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, params))

        def fetchone(self):
            return (0,)

        def fetchall(self):
            sql = self.queries[-1][0]
            if "word = ANY" not in sql:
                return []
            keys = self.queries[-1][1][0]
            return [(key, index + 1) for index, key in enumerate(keys)]

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

    connection = Connection()
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

    search_service.SearchService.advanced_search(
        "alpha beta",
        limit=20,
        offset=40,
        use_bm25=False,
        use_expansion=False,
        use_fuzzy=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="exact",
    )

    candidate_sql = next(
        sql for sql, _ in connection.cursor_instance.queries
        if "LIMIT %s OFFSET %s" in sql
    )
    assert "relevance_score" in candidate_sql
    limit_params = next(
        params for sql, params in connection.cursor_instance.queries
        if "LIMIT %s OFFSET %s" in sql
    )[-2:]
    assert limit_params == (20, 40)
