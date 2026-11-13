"""Integration: unified file-content display — search + classification.

Covers the acceptance criteria of the unified content-display phase:

1. Literal search semantics on /file/<id>/search — regex metacharacters
   are treated as plain text (no 500 on "(", no wildcard behavior of ".*").
2. Precise location: absolute offsets, 1-based line numbers, page grouping
   that mirrors the viewer's page size.
3. "?q=" is accepted as an alias for "?search=" on BOTH content pages
   (File Detail and Full Content Reader) so links from search results can
   carry the originating query.
4. The analyst classification card is present on BOTH content pages
   (write controls for analyst/admin, read-only otherwise) — FR-1.4 keeps
   the manual namespace separate from the smart taxonomy.
5. /api/analyst/assignments supports an exact file_id filter (the data
   source the classification card refreshes from).
"""

import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_ALPHA = f"content-search-alpha-{_UNIQUE}.txt"
_BETA = f"content-search-beta-{_UNIQUE}.txt"

# Reconstructed content for the alpha file (words joined with spaces):
#   "alpha needle beta .* needle gamma NEEDLE"
_ALPHA_WORDS = ["alpha", "needle", "beta", ".*", "needle", "gamma", "NEEDLE"]
_BETA_WORDS = ["plain", "document", "text"]


@pytest.fixture(scope="module")
def seeded(pg_db):
    """Two files whose content is stored through the real repository so the
    search endpoint reads exactly what the pipeline would produce."""
    cfg = pg_db
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id",
                (f"content-search-side-{_UNIQUE}",),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 'test', 1.0, 'NL', CURRENT_DATE) RETURNING id",
                (f"content-search-source-{_UNIQUE}",),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (f"content-search-hash-{_UNIQUE}",),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            # Beta carries different text, i.e. a different canonical
            # content: its own hash and context in the same (source, side).
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (f"content-search-hash-b-{_UNIQUE}",),
            )
            beta_hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (beta_hash_id, source_id, side_id),
            )
            beta_context_id = cur.fetchone()[0]

            path_ids = {}
            for name, ctx in ((_ALPHA, context_id), (_BETA, beta_context_id)):
                cur.execute(
                    """
                    INSERT INTO paths (file_name, file_path, file_size, file_type,
                                       file_status, file_date, date_creation, context_id)
                    VALUES (%s, %s, 100, 'txt', 'Read', CURRENT_DATE, CURRENT_DATE, %s)
                    RETURNING id
                    """,
                    (name, f"/content-search-test/{name}", ctx),
                )
                path_ids[name] = cur.fetchone()[0]

            # Words (upsert, then resolve ids)
            all_words = sorted(set(_ALPHA_WORDS + _BETA_WORDS))
            cur.executemany(
                "INSERT INTO words (word) VALUES (%s) ON CONFLICT (word) DO NOTHING",
                [(w,) for w in all_words],
            )
            cur.execute(
                "SELECT word, id FROM words WHERE word = ANY(%s)", (all_words,)
            )
            word_ids = dict(cur.fetchall())
        conn.commit()
    finally:
        conn.close()

    # Store the content through the real repository (pickled symbol pairs).
    from database.services.contents_db_service import ContentDBService

    svc = ContentDBService()
    svc.contents_repo.store_text_content(
        [word_ids[w] for w in _ALPHA_WORDS], date.today(), hash_id)
    svc.contents_repo.store_text_content(
        [word_ids[w] for w in _BETA_WORDS], date.today(), beta_hash_id)

    return {
        "alpha_id": path_ids[_ALPHA],
        "beta_id": path_ids[_BETA],
        "alpha_text": " ".join(_ALPHA_WORDS),
    }


# ---------------------------------------------------------------------------
# /file/<id>/search — literal semantics + precise location
# ---------------------------------------------------------------------------

def test_search_regex_metacharacters_do_not_500(admin_client, seeded):
    """A bare "(" is an invalid regex but a perfectly fine literal query."""
    resp = admin_client.get(f"/file/{seeded['alpha_id']}/search?q=(")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["total_matches"] == 0
    assert body["matches"] == []


def test_search_dot_star_matches_only_the_literal(admin_client, seeded):
    """'.*' must match the literal text '.*' — exactly once — never act as
    the regex wildcard that would match the entire document."""
    resp = admin_client.get(f"/file/{seeded['alpha_id']}/search?q=.*")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_matches"] == 1
    assert body["matches"][0]["text"] == ".*"
    # Absolute offset points at the literal inside the reconstructed text
    assert body["matches"][0]["start"] == seeded["alpha_text"].index(".*")


def test_search_case_and_whole_word_options(admin_client, seeded):
    alpha = seeded["alpha_id"]

    # Case-insensitive (default): 'needle' matches needle + needle + NEEDLE
    resp = admin_client.get(f"/file/{alpha}/search?q=needle")
    body = resp.get_json()
    assert body["total_matches"] == 3
    assert body["search_options"] == {"case_sensitive": False, "whole_word": False}

    # Case-sensitive: only the lowercase occurrences
    resp = admin_client.get(f"/file/{alpha}/search?q=needle&case_sensitive=true")
    assert resp.get_json()["total_matches"] == 2

    # Case-sensitive uppercase spelling
    resp = admin_client.get(f"/file/{alpha}/search?q=NEEDLE&case_sensitive=true")
    assert resp.get_json()["total_matches"] == 1

    # Whole word: 'need' is NOT a whole-word match
    resp = admin_client.get(f"/file/{alpha}/search?q=need&whole_word=true")
    assert resp.get_json()["total_matches"] == 0


def test_search_reports_offsets_lines_and_page_grouping(admin_client, seeded):
    alpha = seeded["alpha_id"]
    text = seeded["alpha_text"]

    resp = admin_client.get(f"/file/{alpha}/search?q=needle")
    assert resp.status_code == 200
    body = resp.get_json()

    # Every match carries absolute offsets and a 1-based line number
    starts = [m["start"] for m in body["matches"]]
    assert starts == sorted(starts)
    for m in body["matches"]:
        assert text[m["start"]:m["end"]] == m["text"]
        assert m["length"] == len(m["text"])
        assert isinstance(m["line"], int) and m["line"] >= 1

    # Page grouping mirrors the viewer slicing (per_page param is honored)
    assert body["total_chars"] == len(text)
    assert set(body["matches_by_page"].keys()) == {'1'}
    page_matches = body["matches_by_page"]["1"]
    assert [m["global_start"] for m in page_matches] == starts
    assert page_matches[0]["start"] == starts[0]  # page-relative == global on page 1


def test_search_line_numbers_across_lines(app, admin_client, seeded, monkeypatch):
    """Line numbers are 1-based and exact for multi-line content."""
    import Api.blueprints.files as files_bp

    synthetic = "first line\nsecond needle line\n\nneedle at start of fourth"
    monkeypatch.setattr(files_bp, "load_text_content", lambda _fid: synthetic)
    try:
        resp = admin_client.get(f"/file/{seeded['alpha_id']}/search?q=needle")
        assert resp.status_code == 200
        matches = resp.get_json()["matches"]
        assert [m["line"] for m in matches] == [2, 4]
        assert matches[1]["start"] == synthetic.rindex("needle")
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# "?q=" alias + query carry on both content pages
# ---------------------------------------------------------------------------

def test_file_detail_accepts_q_alias(admin_client, seeded):
    resp = admin_client.get(f"/file/{seeded['alpha_id']}?q=needle")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert '"searchQuery": "needle"' in html
    # The search control is pre-filled from the URL parameter
    assert 'id="searchInput"' in html


def test_file_detail_accepts_search_param(admin_client, seeded):
    resp = admin_client.get(f"/file/{seeded['alpha_id']}?search=needle")
    assert resp.status_code == 200
    assert '"searchQuery": "needle"' in resp.get_data(as_text=True)


def test_full_content_accepts_q_alias(admin_client, seeded):
    resp = admin_client.get(f"/file/{seeded['alpha_id']}/full-content?q=needle")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert '"searchQuery": "needle"' in html


def test_full_content_accepts_search_param(admin_client, seeded):
    resp = admin_client.get(f"/file/{seeded['alpha_id']}/full-content?search=needle")
    assert resp.status_code == 200
    assert '"searchQuery": "needle"' in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Analyst classification card on every content display interface
# ---------------------------------------------------------------------------

def test_classify_card_on_file_detail_for_admin(admin_client, seeded):
    resp = admin_client.get(f"/file/{seeded['alpha_id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="analystClassifyCard"' in html
    assert 'analyst-classify-page-data' in html
    assert 'js/modules/analyst-classify.js' in html
    # Write controls are rendered for an analyst/admin
    assert 'id="analystClassifyCard-select"' in html
    assert 'data-analyst-assign' in html
    assert '"canCategorize": true' in html


def test_classify_card_on_file_detail_is_read_only_for_viewer(viewer_client, seeded):
    resp = viewer_client.get(f"/file/{seeded['alpha_id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="analystClassifyCard"' in html
    # No write controls for a viewer
    assert 'id="analystClassifyCard-select"' not in html
    assert 'data-analyst-assign' not in html
    assert '"canCategorize": false' in html


def test_classify_card_on_full_content_reader(admin_client, seeded):
    resp = admin_client.get(f"/file/{seeded['alpha_id']}/full-content")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="analystClassifyCard"' in html
    assert 'analyst-classify-page-data' in html
    assert 'js/modules/analyst-classify.js' in html
    assert 'analyst-classify-compact' in html
    assert '"canCategorize": true' in html


# ---------------------------------------------------------------------------
# /api/analyst/assignments file_id filter (the card's refresh source)
# ---------------------------------------------------------------------------

def test_assignments_file_id_filter(admin_client, seeded):
    alpha, beta = seeded["alpha_id"], seeded["beta_id"]
    tag = uuid.uuid4().hex[:6]

    # Two categories on alpha, one on beta
    ids = []
    for i in (1, 2):
        resp = admin_client.post(
            "/api/analyst/categories", json={"name": f"{tag}-cat{i}"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        ids.append(resp.get_json()["category_id"])

    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [alpha], "category_id": ids[0], "source_query": "content view"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [alpha], "category_id": ids[1], "source_query": "content view"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = admin_client.post("/api/analyst/assign", json={
        "path_ids": [beta], "category_id": ids[1], "source_query": "content view"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Unfiltered: both files
    resp = admin_client.get("/api/analyst/assignments?per_page=100")
    all_rows = resp.get_json()["assignments"]
    assert {r["path_id"] for r in all_rows} >= {alpha, beta}

    # file_id filter: ONLY alpha's assignments, both categories
    resp = admin_client.get(f"/api/analyst/assignments?file_id={alpha}&per_page=100")
    rows = resp.get_json()["assignments"]
    assert rows, "expected assignments for the filtered file"
    assert {r["path_id"] for r in rows} == {alpha}
    names = {r["category_name"] for r in rows}
    assert names == {f"{tag}-cat1", f"{tag}-cat2"}

    # Cleanup: remove analyst categories from both files (bulk per file)
    for fid in (alpha, beta):
        resp = admin_client.post("/api/analyst/remove", json={"path_ids": [fid]})
        assert resp.status_code == 200, resp.get_data(as_text=True)
