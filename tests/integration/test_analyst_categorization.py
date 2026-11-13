"""Integration: Analyst-Driven Manual Categorization with Scoped Search
Control (FRS).

Proves the core guarantees of the specification end to end on the real
application + database:

* FR-1.3  - category assignment from a search selection (incl. creating a
            new category at the point of assignment)
* FR-1.4  - hard separation between analyst categories and the system
            ("smart") taxonomy: separate tables, separate API namespace,
            separate fields in exports; assigning an analyst category never
            touches the smart taxonomy and vice versa
* FR-1.5  - audit trail with analyst identity, timestamp, originating search
            query, category and affected file ids
* FR-2.1  - default search scope excludes analyst-categorized files
* FR-2.2  - explicit scope selector values ('all', 'categorized',
            'uncategorized')
* FR-2.3  - scope selection persists in the session
* FR-2.4  - scope operates on analyst status only (a smart-categorized file
            is still "uncategorized" for scope purposes)
* FR-4    - dedicated Analyst Categorization View with filters
* NFR-3   - removal returns files to uncategorized scope
* NFR-5   - role enforcement (viewer cannot categorize)
"""

import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_ALPHA = f"alpha-report-{_UNIQUE}.txt"     # will be analyst-categorized
_BETA = f"beta-report-{_UNIQUE}.txt"       # stays uncategorized


@pytest.fixture(scope="module")
def seeded_files(pg_db):
    """Two files with distinctive names: one to categorize, one to leave
    uncategorized. A smart (system) category + word link is also created for
    the alpha file so FR-2.4 (scope independence from smart categorization)
    can be proven."""
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
                (f"analyst-test-side-{_UNIQUE}", 1.0),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, %s, %s, %s, CURRENT_DATE) RETURNING id",
                (f"analyst-test-source-{_UNIQUE}", "test", 1.0, "NL"),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (f"analyst-test-hash-alpha-{_UNIQUE}",),
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
                    (name, f"/analyst-test/{name}", context_id),
                )
                path_ids[name] = cur.fetchone()[0]

            # Smart (system) taxonomy entry linked to the alpha file: the
            # word + categorys + words_categorys chain. The alpha file is
            # SMART-categorized but ANALYST-uncategorized (FR-2.4).
            smart_word = f"smartword{_UNIQUE}"
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (smart_word,))
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
        "path_ids": path_ids,
        "smart_category_id": smart_category_id,
        "alpha_id": path_ids[_ALPHA],
        "beta_id": path_ids[_BETA],
    }


# ---------------------------------------------------------------------------
# Service-level scope helpers (pure functions, no DB)
# ---------------------------------------------------------------------------

def test_normalize_scope_defaults_to_uncategorized():
    from Api.services.analyst_categories import normalize_scope
    assert normalize_scope(None) == "uncategorized"
    assert normalize_scope("") == "uncategorized"
    assert normalize_scope("garbage") == "uncategorized"
    assert normalize_scope("all") == "all"
    assert normalize_scope("categorized") == "categorized"
    assert normalize_scope("uncategorized") == "uncategorized"
    assert normalize_scope("UNCATEGORIZED") == "uncategorized"


def test_scope_condition_sql_references_only_analyst_table():
    from Api.services.analyst_categories import scope_condition
    unc = scope_condition("uncategorized")
    assert unc is not None and "analyst_file_categories" in unc and "NOT EXISTS" in unc
    cat = scope_condition("categorized")
    assert cat is not None and "analyst_file_categories" in cat and "NOT EXISTS" not in cat
    # No condition for "all files"
    assert scope_condition("all") is None
    # Never references the smart taxonomy
    for fragment in (unc, cat):
        assert "categorys" not in fragment.replace("analyst_file_categories", "")


# ---------------------------------------------------------------------------
# API + database behavior
# ---------------------------------------------------------------------------

def test_analyst_category_crud_and_assignment(admin_client, seeded_files):
    # Create an analyst category
    resp = admin_client.post("/api/analyst/categories", json={"name": f"reviewed-{_UNIQUE}"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    category_id = body["category_id"]

    # The analyst category list must NOT contain smart categories and the
    # smart category list must NOT contain analyst categories (FR-1.4)
    resp = admin_client.get("/api/analyst/categories")
    analyst_names = [c["name"] for c in resp.get_json()]
    assert f"reviewed-{_UNIQUE}" in analyst_names
    resp = admin_client.get("/api/categories")
    smart_names = [c["name"] for c in resp.get_json()]
    assert f"reviewed-{_UNIQUE}" not in smart_names
    assert f"smartword{_UNIQUE}" in smart_names
    assert f"smartword{_UNIQUE}" not in analyst_names

    # Assign from a "search selection" with the originating query (FR-1.3/1.5)
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["alpha_id"]],
        "category_id": category_id,
        "source_query": f"alpha-report {_UNIQUE}",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True and body["assigned"] == 1

    # Audit log carries analyst identity, query, category and file ids (FR-1.5)
    resp = admin_client.get("/api/analyst/log")
    entries = resp.get_json()["entries"]
    match = [e for e in entries
             if e["category_name"] == f"reviewed-{_UNIQUE}" and e["action"] == "assign"]
    assert match, "assign action not audit-logged"
    entry = match[0]
    assert entry["analyst_username"] == "testadmin"
    assert entry["source_query"] == f"alpha-report {_UNIQUE}"
    assert seeded_files["alpha_id"] in entry["path_ids"]
    assert entry["created_at"] is not None


def test_assign_creates_new_category_at_point_of_assignment(admin_client, seeded_files):
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["beta_id"]],
        "category_name": f"brand-new-{_UNIQUE}",
        "create_category": True,
        "source_query": "beta query",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["category_name"] == f"brand-new-{_UNIQUE}"

    # Clean up so the beta file goes back to uncategorized for the scope
    # tests (NFR-3 also proves removal works).
    resp = admin_client.post("/api/analyst/remove", json={
        "path_ids": [seeded_files["beta_id"]],
    })
    assert resp.status_code == 200
    assert resp.get_json()["removed_assignments"] >= 1


def test_search_scope_default_excludes_analyst_categorized(admin_client, seeded_files):
    """FR-2.1: default scope is uncategorized-only. The alpha file (analyst
    categorized AND smart categorized) must be excluded; the beta file
    (uncategorized) must be returned. This also proves FR-2.4: alpha's smart
    categorization does NOT make it 'categorized' for scope purposes.
    """
    # Default (no scope parameter)
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "per_page": 50,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    ids = [r["id"] for r in data["results"]]
    assert seeded_files["beta_id"] in ids
    assert seeded_files["alpha_id"] not in ids
    assert data["filters"]["analyst_scope"] == "uncategorized"

    # Explicit uncategorized
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "scope": "uncategorized", "per_page": 50,
    })
    ids = [r["id"] for r in resp.get_json()["results"]]
    assert seeded_files["beta_id"] in ids
    assert seeded_files["alpha_id"] not in ids

    # All files
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "scope": "all", "per_page": 50,
    })
    ids = [r["id"] for r in resp.get_json()["results"]]
    assert seeded_files["beta_id"] in ids
    assert seeded_files["alpha_id"] in ids

    # Analyst-categorized only
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "scope": "categorized", "per_page": 50,
    })
    ids = [r["id"] for r in resp.get_json()["results"]]
    assert seeded_files["alpha_id"] in ids
    assert seeded_files["beta_id"] not in ids


def test_scope_selection_persists_in_session(admin_client, seeded_files):
    """FR-2.3: the last-selected scope is remembered for the session."""
    # Choose 'all' explicitly
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "scope": "all", "per_page": 50,
    })
    assert resp.get_json()["filters"]["analyst_scope"] == "all"

    # Subsequent search WITHOUT a scope parameter reuses 'all'
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "per_page": 50,
    })
    data = resp.get_json()
    assert data["filters"]["analyst_scope"] == "all"
    assert seeded_files["alpha_id"] in [r["id"] for r in data["results"]]


def test_results_carry_analyst_categories_as_separate_field(admin_client, seeded_files):
    """FR-1.4: reporting/filtering/export can distinguish Smart Category vs
    Analyst Category as separate fields."""
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "scope": "all", "per_page": 50,
    })
    results = {r["id"]: r for r in resp.get_json()["results"]}
    alpha = results[seeded_files["alpha_id"]]
    assert alpha["analyst_categories"] == [f"reviewed-{_UNIQUE}"]
    # The analyst category never leaks into the smart categories field
    assert f"reviewed-{_UNIQUE}" not in (alpha.get("categories") or [])


def test_analyst_assignment_never_touches_smart_taxonomy(admin_client, app, pg_db, seeded_files):
    """NFR-1/NFR-2: assigning and removing analyst categories must not change
    any smart-taxonomy table."""
    cfg = pg_db

    def smart_state():
        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"], user=cfg["user"],
            password=cfg["password"], dbname=cfg["database"],
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM categorys")
                cats = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM words_categorys")
                wcs = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM words_hashs"
                    " WHERE hash_id = ("
                    "   SELECT c.hash_id FROM paths p"
                    "   JOIN hash_contexts c ON c.id = p.context_id"
                    "   WHERE p.id = %s)",
                    (seeded_files["alpha_id"],),
                )
                wp = cur.fetchone()[0]
            return (cats, wcs, wp)
        finally:
            conn.close()

    before = smart_state()

    # Assign another analyst category, then remove both
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["alpha_id"]],
        "category_name": f"second-label-{_UNIQUE}",
        "create_category": True,
        "source_query": "separation check",
    })
    assert resp.status_code == 200
    resp = admin_client.post("/api/analyst/remove", json={
        "path_ids": [seeded_files["alpha_id"]],
    })
    assert resp.status_code == 200

    assert smart_state() == before


def test_remove_returns_file_to_uncategorized_scope(admin_client, seeded_files):
    """NFR-3: after removal the file is searchable under the default scope
    again."""
    # Guarantee the file is analyst-categorized at the start (earlier tests
    # may have cleaned assignments away) - self-sufficient test state.
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["alpha_id"]],
        "category_name": f"removal-check-{_UNIQUE}",
        "create_category": True,
        "source_query": "removal check",
    })
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    # Alpha is categorized -> excluded by default
    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "per_page": 50,
    })
    assert seeded_files["alpha_id"] not in [r["id"] for r in resp.get_json()["results"]]

    # Remove all analyst categories from alpha
    resp = admin_client.post("/api/analyst/remove", json={
        "path_ids": [seeded_files["alpha_id"]],
    })
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    resp = admin_client.get("/api/search", query_string={
        "query": _UNIQUE, "per_page": 50,
    })
    assert seeded_files["alpha_id"] in [r["id"] for r in resp.get_json()["results"]]


def test_viewer_cannot_categorize(viewer_client, seeded_files):
    """NFR-5: role enforcement - viewers may read the view but not mutate."""
    resp = viewer_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["alpha_id"]],
        "category_name": "nope",
        "create_category": True,
    })
    assert resp.status_code == 403

    resp = viewer_client.post("/api/analyst/categories", json={"name": "nope"})
    assert resp.status_code == 403

    # Reads remain allowed
    assert viewer_client.get("/api/analyst/categories").status_code == 200
    assert viewer_client.get("/api/analyst/assignments").status_code == 200


def test_analyst_categorization_view_page(admin_client):
    """FR-4: the dedicated view renders with its distinct identity and
    exposes the FR-4.2 filters."""
    resp = admin_client.get("/analyst/categorization")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Analyst Categorization" in html
    # FR-4.2 filters present
    assert "filterCategory" in html
    assert "filterAnalyst" in html
    assert "filterDateFrom" in html
    assert "filterQuery" in html
    # Structurally distinct from smart classification pages
    assert "analyst-banner" in html


def test_analyst_view_filters_and_traceability(admin_client, seeded_files):
    """FR-4.2: filter by category / analyst / date and see the originating
    query (traceability back to FR-1.5)."""
    # Re-assign so there is data to filter
    resp = admin_client.get("/api/analyst/categories")
    category_id = next(c["id"] for c in resp.get_json() if c["name"] == f"reviewed-{_UNIQUE}")
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [seeded_files["alpha_id"]],
        "category_id": category_id,
        "source_query": f"traceability-query-{_UNIQUE}",
    })
    assert resp.status_code == 200

    resp = admin_client.get("/api/analyst/assignments", query_string={
        "category_id": category_id,
    })
    data = resp.get_json()
    assert data["total"] >= 1
    row = next(a for a in data["assignments"] if a["path_id"] == seeded_files["alpha_id"])
    assert row["category_name"] == f"reviewed-{_UNIQUE}"
    assert row["assigned_by_username"] == "testadmin"
    assert row["source_query"] == f"traceability-query-{_UNIQUE}"
    assert row["assigned_at"] is not None

    # Date-range + query-text filters
    resp = admin_client.get("/api/analyst/assignments", query_string={
        "q": f"traceability-query-{_UNIQUE}",
        "date_from": "2000-01-01",
        "date_to": "2999-01-01",
    })
    assert resp.get_json()["total"] >= 1
    resp = admin_client.get("/api/analyst/assignments", query_string={
        "q": "definitely-not-a-real-query-xyz",
    })
    assert resp.get_json()["total"] == 0


def test_analyst_export_csv_has_separate_fields(admin_client, seeded_files):
    """FR-1.4: exports expose 'Analyst Category' as its own, separate field."""
    resp = admin_client.get("/api/analyst/export")
    assert resp.status_code == 200
    csv_text = resp.get_data(as_text=True)
    header = csv_text.splitlines()[0]
    assert "analyst_category" in header
    assert "assigned_by" in header
    assert "originating_search_query" in header
    # A data row for the seeded assignment includes the analyst label
    assert f"reviewed-{_UNIQUE}" in csv_text


def test_basic_search_page_honors_scope(admin_client, seeded_files):
    """The basic /search page applies the same scope semantics (FR-2.x)."""
    resp = admin_client.get("/search", query_string={"q": _UNIQUE})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Search Scope" in html
    # Alpha is currently analyst-categorized -> excluded by default scope
    assert _ALPHA not in html
    assert _BETA in html

    resp = admin_client.get("/search", query_string={
        "q": _UNIQUE, "scope": "categorized",
    })
    html = resp.get_data(as_text=True)
    assert _ALPHA in html
    assert _BETA not in html


def test_advanced_and_enhanced_search_pages_render_scope_controls(admin_client):
    # Hermetic: /set_language persists system.language globally; other test
    # modules legitimately exercise non-English locales.
    admin_client.get("/set_language/en")
    """FR-3.2: the Advanced Search interface carries the scope control as a
    first-class, visible input; the enhanced page exposes it too."""
    resp = admin_client.get("/search/advanced")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "analystScopeSelector" in html           # first-class scope control
    assert "Uncategorized Files Only" in html
    assert "Analyst-Categorized Files Only" in html
    assert "All Files" in html
    assert "analystCategorizationBar" in html        # FR-1.3 assignment bar
    assert "selectAllResults" in html                # FR-1.2 select-all
    assert "analystCategoriesFilter" in html         # separate analyst filter

    resp = admin_client.get("/search/enhanced")
    assert resp.status_code == 200
    assert "analystScopeSelect" in resp.get_data(as_text=True)


def test_file_detail_page_shows_analyst_categories_separately(admin_client, seeded_files):
    """FR-1.4 (display): the file detail page shows analyst categories in
    their own card, separate from the smart classification panel."""
    resp = admin_client.get(f"/file/{seeded_files['alpha_id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Analyst Categories" in html
    assert "analyst-category-badge" in html
