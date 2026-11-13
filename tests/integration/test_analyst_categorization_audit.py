"""Audit-phase regression tests: targeted coverage for each defect found and
fixed during the PR #11 acceptance audit.

D1  - query-cache staleness after analyst mutations (assign/remove/category
      CRUD must invalidate the cached scoped search results)
D2  - reflected XSS via category names in onclick strings / attributes
D3  - CSV formula injection in exports + UTF-8 BOM for Excel
D5  - "Select all" semantics are page-scoped in the UI; the backend only
      ever categorizes the explicit path_ids it receives
D6  - case-insensitive category-name uniqueness, including concurrent creates
D7  - ILIKE wildcard escaping in analyst-view filters

Run: pytest tests/integration/test_analyst_categorization_audit.py -v
"""

import csv
import io
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]

_ALPHA = f"alpha-report-{_UNIQUE}.txt"  # stays analyst-uncategorized in this file
_BETA = f"beta-report-{_UNIQUE}.txt"    # the file acted upon by the tests


@pytest.fixture(scope="module")
def seeded_files(pg_db):
    """Two files with distinctive names; mirrors the seeding pattern of
    test_analyst_categorization.py (alpha also gets a SMART category to
    prove analyst/smart separation, FR-2.4)."""
    import psycopg2

    cfg = pg_db
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, %s, CURRENT_DATE) RETURNING id",
                (f"audit-side-{_UNIQUE}", 1.0),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, %s, %s, %s, CURRENT_DATE) RETURNING id",
                (f"audit-source-{_UNIQUE}", "test", 1.0, "NL"),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (f"audit-hash-{_UNIQUE}",),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            path_ids = {}
            for name in (_ALPHA, _BETA):
                cur.execute(
                    """
                    INSERT INTO paths (file_name, file_path, file_size, file_type,
                                       file_status, file_date, date_creation, context_id)
                    VALUES (%s, %s, 100, 'txt', 'Read', CURRENT_DATE, CURRENT_DATE, %s)
                    RETURNING id
                    """,
                    (name, f"/audit-test/{name}", context_id),
                )
                path_ids[name] = cur.fetchone()[0]
            # Smart taxonomy entry linked to alpha only (FR-2.4 falsification).
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                        (f"smartword{_UNIQUE}",))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (word_id,))
            smart_category_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO words_categorys (word_id, category_id) VALUES (%s, %s)",
                (word_id, smart_category_id),
            )
            cur.execute(
                "INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)"
                " VALUES (%s, %s, 1, ''::bytea)",
                (hash_id, word_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "alpha_id": path_ids[_ALPHA],
        "beta_id": path_ids[_BETA],
        "smart_category_id": smart_category_id,
    }


# ---------------------------------------------------------------------------
# D1 — cache invalidation
# ---------------------------------------------------------------------------

def test_query_cache_invalidated_on_assign_and_remove(admin_client, seeded_files):
    """The cached search surface (POST /search/advanced ->
    get_optimized_search_results -> execute_query(use_cache=True)) must
    reflect analyst mutations immediately: prime the cache, assign one
    matching file, re-request the same URL, then remove and re-request."""
    def ids():
        # Same request every time -> identical SQL text+params -> one cache key.
        resp = admin_client.post("/search/advanced", json={"query": _UNIQUE})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return [r["id"] for r in resp.get_json()]

    # Prime the cache: the beta file is uncategorized and must be visible.
    assert seeded_files["beta_id"] in ids()

    # Prove the query cache is actually engaged on this path (otherwise the
    # assertions below would pass trivially with no caching at all).
    # NB: must import via the package, exactly like the application
    # does - Api.utils loads utils.py as 'Api.utils_module', and a
    # direct 'Api.utils.utils' import would create a SECOND module
    # instance with its own cache singleton.
    from Api.utils import get_query_cache
    assert get_query_cache().get_stats()["size"] > 0, "search path did not populate the query cache"

    resp = admin_client.post("/api/analyst/categories", json={"name": f"cache-{_UNIQUE}"})
    category_id = resp.get_json()["category_id"]

    # The beta file is assigned: the SAME url must stop returning it at once
    # (before the fix the 5-minute query cache served the stale list).
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["beta_id"]],
        "category_id": category_id,
        "source_query": f"cache {_UNIQUE}",
    })
    assert resp.get_json()["assigned"] == 1
    assert seeded_files["beta_id"] not in ids(), "stale cache served after assign"

    # Removal must invalidate too.
    resp = admin_client.post("/api/analyst/remove", json={"path_ids": [seeded_files["beta_id"]]})
    assert resp.get_json()["removed_assignments"] == 1
    assert seeded_files["beta_id"] in ids(), "stale cache served after remove"


# ---------------------------------------------------------------------------
# D2 — XSS in category rendering
# ---------------------------------------------------------------------------

def test_category_names_are_never_inlined_into_event_handlers(admin_client):
    """Category names are attacker-controlled. They must never appear raw
    inside onclick JS-string contexts, and must be HTML-escaped in text and
    attribute positions."""
    payload_name = "<script>alert(1)</script>"
    quote_name = '");alert(1);//'
    resp = admin_client.post("/api/analyst/categories", json={"name": payload_name})
    assert resp.get_json()["success"] is True
    resp = admin_client.post("/api/analyst/categories", json={"name": quote_name})
    assert resp.get_json()["success"] is True

    resp = admin_client.get("/analyst/categorization")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # No raw script tag from user data may survive into the document.
    assert "<script>alert(1)</script>" not in html
    # The escaped form must be present (Jinja autoescape).
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # No unescaped quote sequence may appear (the quote-laden name inlined
    # into an onclick JS-string would break out as raw '");alert(1);//').
    assert '");alert(1);//' not in html
    assert "'+\"alert" not in html
    # The delete affordance must use the element-reference pattern, not an
    # inlined id/name string.
    assert "deleteAnalystCategory(this)" in html
    # Category names ride along in data attributes, HTML-escaped.
    assert "data-category-name=" in html
    assert "data-category-name=\"<script>" not in html


# ---------------------------------------------------------------------------
# D3 — CSV injection + BOM
# ---------------------------------------------------------------------------

def test_export_escapes_formula_cells_and_emits_bom(admin_client, seeded_files):
    resp = admin_client.post("/api/analyst/categories",
                             json={"name": f"=CMD(\"calc\")-{_UNIQUE}"})
    category_id = resp.get_json()["category_id"]
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["beta_id"]],
        "category_id": category_id,
        "source_query": "+2+3|cmd",  # dangerous first char in query column
    })
    assert resp.get_json()["assigned"] == 1

    resp = admin_client.get("/api/analyst/export")
    assert resp.status_code == 200
    raw = resp.get_data()
    # UTF-8 BOM so Excel detects Unicode.
    assert raw[:3] == b"\xef\xbb\xbf", "export missing UTF-8 BOM"

    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    qi = header.index("originating_search_query")
    ci = header.index("analyst_category")
    dangerous = ("=", "+", "-", "@", "\t", "\r")

    data_rows = [r for r in rows[1:] if r]
    assert data_rows, "export unexpectedly empty"
    for row in data_rows:
        for cell in row:
            assert not cell.startswith(dangerous), f"unescaped CSV cell: {cell!r}"
    # The payload category must be present exactly in its neutralized form.
    names = [r[ci] for r in data_rows]
    assert f"'=CMD(\"calc\")-{_UNIQUE}" in names
    # The dangerous query must be neutralized too.
    queries = [r[qi] for r in data_rows]
    assert "'+2+3|cmd" in queries


# ---------------------------------------------------------------------------
# D5 — select-all semantics
# ---------------------------------------------------------------------------

def test_assign_only_applies_to_explicit_path_ids(admin_client, seeded_files):
    """The UI select-all is page-scoped; the backend contract is that ONLY
    the explicit path_ids in the request body are categorized. No
    'everything matching the query' expansion may exist."""
    resp = admin_client.post("/api/analyst/categories", json={"name": f"explicit-{_UNIQUE}"})
    category_id = resp.get_json()["category_id"]

    # Both seeded files match the query term, but only one id is sent.
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["beta_id"]],
        "category_id": category_id,
        "source_query": _UNIQUE,
    })
    body = resp.get_json()
    assert body["assigned"] == 1
    assert body["requested"] == 1

    # alpha (also matching the term) must remain untouched.
    resp = admin_client.get("/api/analyst/assignments",
                            query_string={"file_query": f"alpha-report-{_UNIQUE}"})
    assert resp.get_json()["total"] == 0


def test_select_all_label_states_page_scope(admin_client):
    """The select-all control must say it applies to the current page, not
    to every result matched by the query."""
    admin_client.get("/set_language/en")  # hermetic: system language is global
    resp = admin_client.get("/search/advanced")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Select all results on this page" in html
    assert ">Select all results<" not in html  # old ambiguous wording gone

    # Redesign structure invariants (search command card consolidation):
    # - ONE command surface carries query + scope
    # - the duplicate "Search Within" source/side block is gone (the
    #   Advanced Filters panel is the single home for source/side scoping)
    # - the dead voice-search button (never wired to any handler) is gone
    assert 'id="search-command-card"' not in html  # class, not id
    assert 'class="search-command-card"' in html
    assert "searchWithinSource" not in html
    assert "searchWithinSide" not in html
    assert "voiceSearchBtn" not in html
    assert 'id="mainSearchInput"' in html
    assert 'id="analystScopeSelector"' in html
    assert 'id="clearSearchBtn"' in html


# ---------------------------------------------------------------------------
# D6 — case-insensitive uniqueness (incl. race)
# ---------------------------------------------------------------------------

def test_category_name_uniqueness_is_case_insensitive(admin_client):
    resp = admin_client.post("/api/analyst/categories", json={"name": f"CaseCat-{_UNIQUE}"})
    assert resp.get_json()["success"] is True
    resp = admin_client.post("/api/analyst/categories",
                             json={"name": f"casecat-{_UNIQUE.upper()}"})
    assert resp.status_code == 400  # clean validation error, never a 500
    body = resp.get_json()
    assert body["success"] is False
    assert "already exists" in body["error"]


def test_concurrent_category_creation_yields_single_winner(app, admin_credentials):
    """8 threads race to create the same name: exactly one may win. The
    case-insensitive unique index turns the would-be 500 into a 409."""
    name = f"RaceCat-{_UNIQUE}"
    results = []

    def create(_):
        with app.test_client() as client:
            client.post("/auth/login", json={
                "username": admin_credentials[0],
                "password": admin_credentials[1],
            })
            resp = client.post("/api/analyst/categories", json={"name": name})
            return resp.status_code, resp.get_json().get("success", False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))

    successes = [ok for _, ok in results if ok]
    assert len(successes) == 1, f"race outcome: {results}"
    # Every loser got a clean validation error, never a 500 crash.
    for status, ok in results:
        if not ok:
            assert status == 400, (status, ok)


# ---------------------------------------------------------------------------
# D7 — ILIKE wildcard escaping
# ---------------------------------------------------------------------------

def test_view_filters_treat_wildcards_literally(admin_client, seeded_files):
    resp = admin_client.post("/api/analyst/categories", json={"name": f"like-{_UNIQUE}"})
    category_id = resp.get_json()["category_id"]
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["beta_id"]],
        "category_id": category_id,
        "source_query": f"100% certain {_UNIQUE}",
    })
    assert resp.get_json()["assigned"] == 1

    # A bare % must match ONLY assignments whose query literally contains %,
    # never act as a match-anything wildcard.
    resp = admin_client.get("/api/analyst/assignments", query_string={"q": "%"})
    body = resp.get_json()
    assert body["total"] >= 1
    for a in body["assignments"]:
        assert "%" in a["source_query"], f"wildcard % matched non-literal query: {a['source_query']!r}"

    # Underscore must be literal. The seeded file is "beta-report-<id>"
    # (hyphen): if '_' were an unescaped single-char wildcard, the pattern
    # "beta_report-<id>" WOULD match it; escaped, it must match nothing.
    resp = admin_client.get("/api/analyst/assignments",
                            query_string={"file_query": f"beta_report-{_UNIQUE}"})
    assert resp.get_json()["total"] == 0, "underscore wildcard not escaped"
    # Control: the real (hyphenated) name matches (>=1 because earlier tests
    # legitimately assigned several analyst categories to this same file).
    resp = admin_client.get("/api/analyst/assignments",
                            query_string={"file_query": f"beta-report-{_UNIQUE}"})
    assert resp.get_json()["total"] >= 1
