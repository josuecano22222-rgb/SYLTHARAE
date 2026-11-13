"""Phase 0: the canonical Criteria object and the single query compiler.

Pure tests (no database). The PostgreSQL behaviour - that compiled criteria
return the same rows as interactive search - is pinned in
tests/integration/test_criteria_spine_pg.py.
"""

import re

import pytest

from core.criteria import (
    AccessScope,
    Criteria,
    CriteriaError,
    SearchOptions,
    Sort,
    compile_criteria,
    from_dict,
    from_legacy_search,
    legacy_unmapped_keys,
)
from core.criteria.sql import filter_predicates, parse_boolean_expression

# --------------------------------------------------------------------------
# Canonical form & fingerprint
# --------------------------------------------------------------------------


def test_equivalent_payloads_share_one_fingerprint():
    a = from_dict({"sources": [3, 1, 3], "file_types": [" pdf", "docx"],
                   "text": "  alpha  ", "keyword_logic": "or", "keywords": [9]})
    b = from_dict({"file_types": ["docx", "pdf"], "sources": ["1", 3],
                   "text": "alpha", "keyword_logic": "OR", "keywords": ["9"]})
    assert a == b
    assert a.canonical_json() == b.canonical_json()
    assert a.fingerprint() == b.fingerprint()
    assert re.fullmatch(r"[0-9a-f]{64}", a.fingerprint())


def test_any_semantic_change_changes_the_fingerprint():
    base = from_dict({"text": "alpha", "sources": [1]})
    variants = [
        {"text": "alpha", "sources": [2]},
        {"text": "beta", "sources": [1]},
        {"text": "alpha", "sources": [1], "case_sensitive": True},
        {"text": "alpha", "sources": [1], "unit": "hash"},
        {"text": "alpha", "sources": [1], "file_statuses": []},
        {"text": "alpha", "sources": [1], "date_from": "2026-01-01"},
    ]
    prints = {from_dict(v).fingerprint() for v in variants}
    assert base.fingerprint() not in prints
    assert len(prints) == len(variants)


def test_round_trip_is_lossless():
    c = Criteria(text="a OR b", phrases=("x y",), keywords=(4, 5), keyword_logic="NOT",
                 categories=(2,), analyst_categories=(7,), analyst_scope="categorized",
                 sources=(1,), sides=(2,), file_types=("pdf",), file_statuses=("Read",),
                 date_field="context_date", date_from="2026-01-01", date_to="2026-02-01",
                 unit="hash", hide_duplicates=True, sort=(Sort("date", "asc"),),
                 options=SearchOptions(use_fuzzy=False, similarity_threshold=0.4))
    assert from_dict(c.to_dict()) == c
    assert from_dict(c.to_dict()).fingerprint() == c.fingerprint()


def test_empty_status_list_is_distinct_from_unfiltered():
    """None = unfiltered; () = explicitly neither (matches nothing)."""
    assert from_dict({}).file_statuses is None
    assert from_dict({"file_statuses": []}).file_statuses == ()
    assert from_dict({}).fingerprint() != from_dict({"file_statuses": []}).fingerprint()


# --------------------------------------------------------------------------
# Strictness
# --------------------------------------------------------------------------


@pytest.mark.parametrize("payload, fragment", [
    ({"rows": [1, 2]}, "unknown criteria fields"),
    ({"sources": ["x"]}, "not an id"),
    ({"sources": [0]}, "positive"),
    ({"sources": [True]}, "not an id"),
    ({"date_from": "27/09/2026"}, "YYYY-MM-DD"),
    ({"date_from": "2026-02-30"}, "not a real date"),
    ({"date_from": "2026-03-01", "date_to": "2026-01-01"}, "after"),
    ({"unit": "row"}, "unit"),
    ({"keyword_logic": "XOR"}, "keyword_logic"),
    ({"include_child_categories": True}, "no parent/child"),
    ({"file_statuses": ["Deleted"]}, "file_statuses"),
    ({"sort": [{"field": "rank"}]}, "sort"),
    ({"options": {"turbo": True}}, "unknown options"),
    ({"schema_version": 99}, "schema_version"),
])
def test_invalid_payloads_are_refused_with_a_reason(payload, fragment):
    with pytest.raises(CriteriaError) as info:
        from_dict(payload)
    assert fragment in str(info.value)


# --------------------------------------------------------------------------
# Legacy saved-search mapping
# --------------------------------------------------------------------------


def test_legacy_advanced_search_definition_maps_every_known_key():
    filters = {
        "scope": "categorized", "sort_by": "date", "sort_order": "asc",
        "similarity_threshold": 0.5,
        "options": {"case_sensitive": True, "whole_word": True, "use_fuzzy": False},
        "hide_duplicates": True, "file_type": ["pdf"], "category_id": ["2"],
        "analyst_category_id": [7], "source_id": [1, 3], "side_id": "4",
        "date_from": "2026-01-01", "date_to": "2026-06-30", "status": ["Read", "Unread"],
    }
    c = from_legacy_search("alpha", filters)
    assert c.text == "alpha"
    assert c.analyst_scope == "categorized"
    assert c.sort == (Sort("date", "asc"),)
    assert c.case_sensitive and c.whole_word and c.hide_duplicates
    assert c.options.use_fuzzy is False and c.options.similarity_threshold == 0.5
    assert c.file_types == ("pdf",) and c.categories == (2,)
    assert c.analyst_categories == (7,) and c.sources == (1, 3) and c.sides == (4,)
    assert c.file_statuses == ("Read", "Unread")
    assert legacy_unmapped_keys(filters) == []


@pytest.mark.parametrize("filters, expected", [
    ({}, "uncategorized"),            # the page replays f.scope || 'uncategorized'
    ({"scope": ""}, "uncategorized"),
    ({"scope": "bogus"}, "uncategorized"),  # never widened to "all"
    ({"scope": "ALL"}, "all"),
    ({"scope": "categorised"}, "categorized"),
])
def test_legacy_scope_maps_to_what_the_search_actually_ran_with(filters, expected):
    assert from_legacy_search("q", filters).analyst_scope == expected


def test_one_scope_normaliser_for_search_and_criteria():
    from Api.services.analyst_categories import normalize_scope
    from core.criteria import normalize_analyst_scope

    for raw in (None, "", "all", "Categorized", "analyst-categorized", "junk", "uncategorised"):
        assert normalize_scope(raw) == normalize_analyst_scope(raw), raw


def test_unknown_legacy_keys_are_reported_not_hidden():
    assert legacy_unmapped_keys({"source_id": [1], "mystery": 1}) == ["mystery"]


# --------------------------------------------------------------------------
# Compiler
# --------------------------------------------------------------------------

HOSTILE = "x'); DROP TABLE users; --"


def _placeholders(sql: str) -> int:
    return sql.count("%s")


def test_compiled_sql_never_contains_user_values():
    c = from_dict({"text": f'"{HOSTILE}" OR beta', "phrases": [HOSTILE],
                   "file_types": [HOSTILE], "sources": [1], "date_from": "2026-01-01"})
    q = compile_criteria(c, AccessScope.unrestricted())
    count_sql, params = q.count_sql()
    assert HOSTILE not in count_sql and "DROP TABLE" not in count_sql
    assert _placeholders(count_sql) == len(params)
    ids_sql, ids_params = q.ids_sql(50, 100)
    assert _placeholders(ids_sql) == len(ids_params)
    assert ids_params[-2:] == (50, 100)
    assert any(HOSTILE in str(p) for p in params)


def test_access_scope_is_compiled_into_the_where_clause():
    c = from_dict({"text": "alpha"})
    restricted = compile_criteria(c, AccessScope(role="analyst", allowed_source_ids=(4, 9)))
    assert "hc.source_id IN (%s,%s)" in restricted.where_sql
    assert restricted.params[-2:] == (4, 9)
    nothing = compile_criteria(c, AccessScope(role="viewer", allowed_source_ids=()))
    assert "1=0" in nothing.where_sql
    with pytest.raises(CriteriaError):
        compile_criteria(c, None)


def test_ordering_is_deterministic_and_declared():
    q = compile_criteria(from_dict({}), AccessScope.unrestricted())
    assert q.order_sql.endswith("p.id ASC")
    assert any("Relevance ranking exists only in interactive search" in n for n in q.notes)
    q2 = compile_criteria(from_dict({"sort": [{"field": "size", "direction": "desc"}]}),
                          AccessScope.unrestricted())
    assert q2.order_sql == "p.file_size DESC, p.id ASC"


def test_malformed_expression_is_refused_not_widened():
    with pytest.raises(CriteriaError):
        compile_criteria(from_dict({"text": "(alpha OR"}), AccessScope.unrestricted())


def test_page_bounds_are_enforced():
    q = compile_criteria(from_dict({}), AccessScope.unrestricted())
    for bad in ((0, 0), (10_001, 0), (10, -1)):
        with pytest.raises(CriteriaError):
            q.ids_sql(*bad)


def test_keyword_logic_compiles_three_ways():
    base = {"keywords": [3, 5]}
    and_q = compile_criteria(from_dict({**base, "keyword_logic": "AND"}), AccessScope.unrestricted())
    or_q = compile_criteria(from_dict({**base, "keyword_logic": "OR"}), AccessScope.unrestricted())
    not_q = compile_criteria(from_dict({**base, "keyword_logic": "NOT"}), AccessScope.unrestricted())
    assert and_q.where_sql.count("keywords_hashs") == 2
    assert "IN (%s,%s)" in or_q.where_sql and "NOT EXISTS" not in or_q.where_sql
    assert "NOT EXISTS" in not_q.where_sql


def test_path_ids_select_exact_files_and_explain():
    empty = from_dict({})
    selected = from_dict({"path_ids": [9, 3, 3, 1]})
    assert selected.path_ids == (1, 3, 9)
    # Empty path_ids stay out of the canonical form so old fingerprints hold.
    assert "path_ids" not in empty.to_dict()
    assert empty.fingerprint() == from_dict({}).fingerprint()
    assert selected.fingerprint() != empty.fingerprint()
    assert selected.to_dict()["path_ids"] == [1, 3, 9]
    assert from_dict(selected.to_dict()) == selected
    assert "Exact file selection: 3 path id(s)" in "\n".join(selected.explain())
    q = compile_criteria(selected, AccessScope.unrestricted())
    assert "p.id IN (%s,%s,%s)" in q.where_sql
    assert q.params[:3] == (1, 3, 9)


def test_filter_predicates_accept_path_ids():
    conditions, params = filter_predicates(path_ids=(4, 2))
    assert any("p.id IN (%s,%s)" in c for c in conditions)
    assert params == [4, 2]


def test_explain_states_every_condition():
    c = from_dict({"text": "alpha", "sources": [1], "unit": "hash",
                   "date_from": "2026-01-01"})
    q = compile_criteria(c, AccessScope.unrestricted())
    text = "\n".join(q.explain(c))
    for fragment in ("alpha", "Sources", "Unit of analysis: hash", "2026-01-01",
                     c.fingerprint(), "interactive search only"):
        assert fragment in text


# --------------------------------------------------------------------------
# Shared vocabulary: search uses the same builders
# --------------------------------------------------------------------------


def test_search_service_uses_the_shared_vocabulary():
    from Api.services import search_service
    from core.criteria import sql as shared

    assert search_service._parse_boolean_search_expression is shared.parse_boolean_expression
    assert search_service._text_match_clause is shared.text_match_clause
    assert search_service._escape_like is shared.escape_like
    assert search_service._filter_predicates is shared.filter_predicates


def test_filter_predicates_empty_status_list_matches_nothing():
    conditions, params = filter_predicates(file_statuses=[])
    assert conditions == ["1=0"] and params == []
    with pytest.raises(ValueError):
        filter_predicates(file_statuses=["Nope"])


def test_boolean_parser_contract_unchanged():
    assert parse_boolean_expression("alpha NOT beta") == (
        "and", ("term", "alpha"), ("not", ("term", "beta")))
    assert parse_boolean_expression('"a b" OR c') == (
        "or", ("term", "a b"), ("term", "c"))
    assert parse_boolean_expression("") is None
