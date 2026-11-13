"""Phase 0 against PostgreSQL: one criteria language, one result set.

The compiler (reports/monitoring/export/dashboards) and interactive search
must agree on *which records match* the same conditions. Ranking is
deliberately search-only; set membership is not allowed to drift. These tests
seed a small corpus through the real tables and compare the two paths.
"""

import datetime
import uuid

import pytest

from core.criteria import AccessScope, compile_criteria, from_dict

from _seed import connect, document, side, source

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def corpus(pg_db, app):
    tag = uuid.uuid4().hex[:8]
    alpha, beta = f"zqa{tag}", f"zqb{tag}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s1, s2 = source(cur), source(cur)
        d1, d2 = side(cur), side(cur)
        ids = {}
        ids["a_only"] = document(cur, source_id=s1, side_id=d1, text=f"report {alpha} here",
                                 file_type="pdf", file_date=datetime.date(2026, 2, 1))[0]
        ids["a_and_b"] = document(cur, source_id=s1, side_id=d2,
                                  text=f"{alpha} and {beta} together", file_type="txt",
                                  file_date=datetime.date(2026, 3, 1))[0]
        ids["b_only"] = document(cur, source_id=s2, side_id=d1, text=f"just {beta}",
                                 file_type="pdf", file_date=datetime.date(2026, 4, 1))[0]
        ids["upper"] = document(cur, source_id=s2, side_id=d2,
                                text=f"UPPER {alpha.upper()} case", file_type="docx",
                                file_date=datetime.date(2026, 5, 1))[0]
        ids["phrase"] = document(cur, source_id=s2, side_id=d1,
                                 text=f"the {alpha} gamma phrase", file_type="txt",
                                 file_date=datetime.date(2026, 6, 1))[0]
        # Same content seen twice (duplicate occurrence of one hash).
        dup_path, dup_hash, _ = document(cur, source_id=s1, side_id=d1,
                                         text=f"dup {alpha} content", file_type="txt")
        ids["dup1"] = dup_path
        ids["dup2"] = document(cur, source_id=s2, side_id=d2, text="(ignored)",
                               hash_id=dup_hash, file_type="txt")[0]
    conn.close()
    return {"alpha": alpha, "beta": beta, "s1": s1, "s2": s2, "d1": d1, "d2": d2,
            "ids": ids, "dup_hash": dup_hash}


def _compiled_ids(pg_db, criteria, scope=None):
    q = compile_criteria(criteria, scope or AccessScope.unrestricted())
    sql, params = q.ids_sql(10_000, 0)
    count_sql, count_params = q.count_sql()
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [r[0] for r in cur.fetchall()]
            cur.execute(count_sql, count_params)
            total = cur.fetchone()[0]
    finally:
        conn.close()
    assert total == len(rows), "count and id list disagree"
    return rows


def _search_ids(**kwargs):
    from Api.services.search_service import SearchService

    base = dict(limit=10_000, offset=0, use_bm25=False, use_expansion=False,
                use_fuzzy=False, analyst_scope="all")
    base.update(kwargs)
    results, total = SearchService.advanced_search(**base)
    ids = sorted(int(r.get("path_id") or r["id"]) for r in results)
    assert total == len(ids)
    return ids


PARITY_CASES = [
    ("single term", lambda c: {"text": c["alpha"]}, lambda c: {"query": c["alpha"]}),
    ("boolean NOT", lambda c: {"text": f"{c['alpha']} NOT {c['beta']}"},
     lambda c: {"query": f"{c['alpha']} NOT {c['beta']}"}),
    ("boolean OR", lambda c: {"text": f"{c['alpha']} OR {c['beta']}"},
     lambda c: {"query": f"{c['alpha']} OR {c['beta']}"}),
    ("phrase", lambda c: {"text": f'"{c["alpha"]} gamma"'},
     lambda c: {"query": f'"{c["alpha"]} gamma"'}),
    ("case sensitive", lambda c: {"text": c["alpha"], "case_sensitive": True},
     lambda c: {"query": c["alpha"], "case_sensitive": True}),
    ("source filter", lambda c: {"text": c["alpha"], "sources": [c["s2"]]},
     lambda c: {"query": c["alpha"], "source_ids": [c["s2"]]}),
    ("side + type", lambda c: {"text": f"{c['alpha']} OR {c['beta']}", "sides": [c["d1"]],
                               "file_types": ["pdf"]},
     lambda c: {"query": f"{c['alpha']} OR {c['beta']}", "side_ids": [c["d1"]],
                "file_type": ["pdf"]}),
    ("date range", lambda c: {"text": f"{c['alpha']} OR {c['beta']}",
                              "date_from": "2026-02-15", "date_to": "2026-05-01"},
     lambda c: {"query": f"{c['alpha']} OR {c['beta']}", "date_from": "2026-02-15",
                "date_to": "2026-05-01"}),
    ("filter only", lambda c: {"sources": [c["s1"]], "file_types": ["pdf"]},
     lambda c: {"query": "", "source_ids": [c["s1"]], "file_type": ["pdf"]}),
]


@pytest.mark.parametrize("label, criteria_of, search_of", PARITY_CASES,
                         ids=[c[0] for c in PARITY_CASES])
def test_compiler_and_interactive_search_select_the_same_records(
        pg_db, corpus, label, criteria_of, search_of):
    compiled = sorted(_compiled_ids(pg_db, from_dict(criteria_of(corpus))))
    searched = _search_ids(**search_of(corpus))
    assert compiled == searched, label
    assert compiled or label == "boolean NOT", f"{label}: vacuous parity (no rows)"


def test_expected_membership_is_what_the_corpus_says(pg_db, corpus):
    ids = corpus["ids"]
    got = set(_compiled_ids(pg_db, from_dict({"text": f"{corpus['alpha']} NOT {corpus['beta']}"})))
    assert got == {ids["a_only"], ids["upper"], ids["phrase"], ids["dup1"], ids["dup2"]}
    cs = set(_compiled_ids(pg_db, from_dict({"text": corpus["alpha"], "case_sensitive": True})))
    assert ids["upper"] not in cs and ids["a_only"] in cs


def test_access_scope_is_enforced_in_the_query(pg_db, corpus):
    crit = from_dict({"text": f"{corpus['alpha']} OR {corpus['beta']}"})
    everything = set(_compiled_ids(pg_db, crit))
    only_s1 = set(_compiled_ids(pg_db, crit, AccessScope(role="analyst",
                                                         allowed_source_ids=(corpus["s1"],))))
    none = _compiled_ids(pg_db, crit, AccessScope(role="viewer", allowed_source_ids=()))
    assert only_s1 and only_s1 < everything
    assert none == []
    ids = corpus["ids"]
    assert ids["b_only"] not in only_s1 and ids["a_only"] in only_s1


def test_ordering_is_deterministic_and_total(pg_db, corpus):
    crit = from_dict({"text": f"{corpus['alpha']} OR {corpus['beta']}",
                      "sort": [{"field": "type", "direction": "asc"}]})
    first = _compiled_ids(pg_db, crit)
    assert first == _compiled_ids(pg_db, crit)
    # Ties on file_type are broken by path id ascending.
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT id, file_type FROM paths WHERE id = ANY(%s)", (first,))
        types = dict(cur.fetchall())
    conn.close()
    keys = [(types[i], i) for i in first]
    assert keys == sorted(keys)


def test_hash_unit_counts_content_once(pg_db, corpus):
    by_path = _compiled_ids(pg_db, from_dict({"text": f"dup {corpus['alpha']}"}))
    by_hash = _compiled_ids(pg_db, from_dict({"text": f"dup {corpus['alpha']}", "unit": "hash"}))
    assert sorted(by_path) == sorted([corpus["ids"]["dup1"], corpus["ids"]["dup2"]])
    assert by_hash == [corpus["dup_hash"]]


def test_hostile_values_are_data_not_sql(pg_db, corpus):
    hostile = "x'); DELETE FROM paths; --"
    assert _compiled_ids(pg_db, from_dict({"text": f'"{hostile}"', "file_types": [hostile]})) == []
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM paths WHERE id = %s", (corpus["ids"]["a_only"],))
        assert cur.fetchone()[0] == 1
    conn.close()
