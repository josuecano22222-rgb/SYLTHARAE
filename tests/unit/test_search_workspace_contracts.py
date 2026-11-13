from __future__ import annotations

from Api.services import search_export, search_service


def test_match_annotations_distinguish_filename_and_content_hits():
    assert search_service.SearchService._matching_fields(
        "Annual Report.pdf", "The annual report is attached.", '"annual report"') == {
            "file_name": True,
            "content": True,
            "metadata": False,
        }
    assert search_service.SearchService._matching_fields(
        "Report.pdf", "No matching body term.", "invoice") == {
            "file_name": False,
            "content": False,
            "metadata": False,
        }


def test_context_lines_highlight_positive_terms_for_operator_queries():
    matches = search_service.SearchService._find_matching_lines(
        1,
        "alpha OR beta NOT excluded",
        max_matches=3,
        content="First alpha passage.\nA beta passage.\nexcluded only.",
    )

    assert [item["line_number"] for item in matches] == [1, 2]
    assert "<mark>alpha</mark>" in matches[0]["highlighted_line"]
    assert "<mark>beta</mark>" in matches[1]["highlighted_line"]


def test_duplicate_suppression_is_an_explicit_search_export_option():
    request = search_export.parse({
        "query": "agreement",
        "export_scope": "filtered",
        "format": "csv",
        "hide_duplicates": "true",
    })

    assert request.hide_duplicates is True
    assert request.definition["hide_duplicates"] is True


def test_full_text_search_accepts_duplicate_suppression_without_losing_query_terms(monkeypatch):
    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, params))

        def fetchone(self):
            return (0,)

        def fetchall(self):
            return []

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

    connection = Connection()
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)
    monkeypatch.setattr(search_service, "return_connection", lambda _connection: None)

    results, total = search_service.SearchService.full_text_search(
        query='"exact phrase"',
        limit=10,
        offset=0,
        hide_duplicates=True,
    )

    assert results == []
    assert total == 0
    count_sql = connection.cursor_instance.queries[0][0]
    result_sql, result_params = connection.cursor_instance.queries[1]
    assert "COUNT(DISTINCT COALESCE(h.hash::text" in count_sql
    assert "PARTITION BY COALESCE(content_hash, 'path:' || id::text)" in result_sql
    assert "WHERE content_rank = 1" in result_sql
    assert result_params[0] == "exact phrase"


def test_advanced_search_counts_and_limits_by_exact_content_hash(monkeypatch):
    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, params))

        def fetchone(self):
            return (0,)

        def fetchall(self):
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

    connection = Connection()
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

    results, total = search_service.SearchService.advanced_search(
        query='"exact phrase"',
        limit=10,
        offset=0,
        use_bm25=False,
        use_expansion=False,
        use_fuzzy=False,
        analyst_scope="all",
        hide_duplicates=True,
        include_snippets=False,
        count_mode="exact",
    )

    assert results == []
    assert total == 0
    count_sql = next(sql for sql, _ in connection.cursor_instance.queries if "SELECT COUNT" in sql)
    candidate_sql, candidate_params = next(
        (sql, params) for sql, params in connection.cursor_instance.queries
        if "duplicate_rank = 1" in sql
    )
    assert "COUNT(DISTINCT COALESCE(h.hash::text" in count_sql
    assert "PARTITION BY COALESCE(h.hash::text" in candidate_sql
    assert "duplicate_rank = 1" in candidate_sql
    assert "contents_raw" in candidate_sql
    assert "cr.hash_id = h.id" in candidate_sql
    assert candidate_params[0] == "%exact phrase%"


def test_match_annotations_respect_case_whole_word_and_metadata_sources():
    fields = search_service.SearchService._matching_fields(
        "Alpha.pdf",
        "alphabet alpha Alpha",
        "Alpha",
        case_sensitive=True,
        whole_word=True,
        metadata_text="Side Alpha",
    )
    assert fields == {
        "file_name": True,
        "content": True,
        "metadata": True,
    }

    lowercase_only = search_service.SearchService._matching_fields(
        "ALPHA.pdf", "ALPHABET", "alpha",
        case_sensitive=True, whole_word=True,
    )
    assert lowercase_only == {
        "file_name": False,
        "content": False,
        "metadata": False,
    }


def test_context_navigation_uses_all_positive_terms_but_not_exclusions():
    matches = search_service.SearchService._find_matching_lines(
        1,
        "Alpha OR beta NOT omitted",
        max_matches=5,
        content="Alpha and alphabet.\nbeta stands alone.\nomitted term.",
        case_sensitive=True,
        whole_word=True,
    )
    assert [item["line_number"] for item in matches] == [1, 2]
    assert "<mark>Alpha</mark>" in matches[0]["highlighted_line"]
    assert "<mark>beta</mark>" in matches[1]["highlighted_line"]


def test_advanced_search_sql_applies_case_and_word_flags_to_text_and_metadata(monkeypatch):
    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, tuple(params)))

        def fetchone(self):
            return (0,)

        def fetchall(self):
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

    connection = Connection()
    monkeypatch.setattr(search_service, "get_connection", lambda: connection)

    results, total = search_service.SearchService.advanced_search(
        "Alpha Beta",
        limit=10,
        offset=0,
        use_bm25=False,
        use_expansion=False,
        use_fuzzy=False,
        analyst_scope="all",
        case_sensitive=True,
        whole_word=True,
        include_snippets=False,
        count_mode="exact",
    )

    assert results == []
    assert total == 0
    count_sql, params = next(
        (sql, params) for sql, params in connection.cursor_instance.queries if "SELECT COUNT" in sql
    )
    assert "p.file_name ~ %s" in count_sql
    assert "p.file_type ~ %s" in count_sql
    assert "_search_source.name ~ %s" in count_sql
    assert "_search_side.name ~ %s" in count_sql
    assert "cr.content ~ %s" in count_sql
    assert "words_hashs" not in count_sql  # token tables cannot preserve case
    assert params[0] == r"(^|[^[:alnum:]_])Alpha([^[:alnum:]_]|$)"
    assert params[5] == r"(^|[^[:alnum:]_])Beta([^[:alnum:]_]|$)"
    assert count_sql.count("%s") == len(params)


def test_search_export_definition_preserves_exact_match_options():
    request = search_export.parse({
        "query": "\"Alpha Beta\"",
        "export_scope": "filtered",
        "format": "csv",
        "case_sensitive": True,
        "whole_word": "true",
    })
    assert request.case_sensitive is True
    assert request.whole_word is True
    assert request.definition["case_sensitive"] is True
    assert request.definition["whole_word"] is True


def test_search_results_do_not_expose_private_server_filesystem_paths(monkeypatch):
    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params=()):
            self.queries.append((query, tuple(params)))

        def fetchone(self):
            return (1,)

        def fetchall(self):
            sql = self.queries[-1][0] if self.queries else ""
            if "word = ANY" in sql:
                return [("alpha", 42)]
            return [(7, "evidence.txt", "/srv/private/evidence.txt", "txt",
                     12, None, "Read", None, "Source", "Side", 1, 2)]

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
    monkeypatch.setattr(
        search_service,
        "batch_load_text_windows",
        lambda path_ids, **kwargs: {int(pid): "alpha evidence" for pid in path_ids},
    )

    results, total = search_service.SearchService.advanced_search(
        "alpha",
        limit=10,
        offset=0,
        use_bm25=False,
        use_expansion=False,
        use_fuzzy=False,
        analyst_scope="all",
        count_mode="exact",
    )

    assert total == 1
    assert results[0]["file_name"] == "evidence.txt"
    assert "file_path" not in results[0]


def test_boolean_query_parser_preserves_quoted_phrase_operator_precedence():
    assert search_service._parse_boolean_search_expression(
        '"annual report" OR memo AND NOT draft') == (
            "or",
            ("term", "annual report"),
            ("and", ("term", "memo"), ("not", ("term", "draft"))),
        )


def test_boolean_query_parser_treats_adjacent_terms_as_and():
    assert search_service._parse_boolean_search_expression("alpha beta") == (
        "and", ("term", "alpha"), ("term", "beta"))
