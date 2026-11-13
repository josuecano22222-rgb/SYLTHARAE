"""Reports: one statement over the stored paths, read through HTTP.

The report surface has no table of its own. These tests create a small world
through the real ingestion path (``process_full_document``), then assert that
the JSON report, the two pages and the printable dossier describe exactly the
rows that exist - totals, per-file-type success/failure, the classifications,
the category terms and the keywords - and that the filters narrow all of them
together.

They also pin the shape the surface promises: the same numbers from the page
and from the API, and no dependency on any table the frozen schema does not
have.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def world(pg_db):
    """Two sources, two sides, four stored occurrences with known statuses."""
    from database.services.contents_db_service import ContentDBService

    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation)"
                    " VALUES (%s, 0.5, CURRENT_DATE) RETURNING id", (f"rp_side_{_U}",))
        side_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                    (f"rp_src_{_U}",))
        source_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id",
                    (f"rp_other_{_U}",))
        other_source_id = cur.fetchone()[0]
    conn.close()

    service = ContentDBService()

    def store(name, text, ext, source):
        return service.process_full_document(
            hash_value=hashlib.sha256(f"{_U}{name}".encode()).hexdigest(),
            source_id=source, side_id=side_id, file_name=f"{name}.{ext}",
            file_path=f"/tmp/rp/{_U}/{name}.{ext}", file_size=10, file_type=ext,
            file_status="Read", file_date=date(2026, 1, 1),
            content_words=["report", "entry", name],
            raw_text=text, attempts=1)["hash_id"]

    ids = {
        "source": source_id, "other_source": other_source_id, "side": side_id,
        "hash_a": store("alpha", "The treaty will expire on 1 June 2027. Contact a@b.co.",
                        "txt", source_id),
        "hash_b": store("beta", "The audit will take place on 20 October 2026.",
                        "txt", source_id),
        "hash_c": store("gamma", "Delegates flew in from Zagreb.", "pdf", other_source_id),
    }

    # A stored occurrence points at a status; set them explicitly so the
    # success/failure split is a fact of this world, not of the detector.
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        marker = f"rp_{_U}"
        cur.execute("SELECT id, file_path FROM paths WHERE file_path LIKE %s",
                    (f"%{marker}%",))
        rows = cur.fetchall()
        assert len(rows) == 3, f"expected the three stored occurrences, got {rows}"
        wanted = {}
        for path_id, file_path in rows:
            wanted[file_path.rsplit("/", 1)[-1].split(".")[0]] = path_id
        statuses = {"alpha": ("processed", None),
                    "beta": ("failed", "reader refused the archive"),
                    "gamma": ("partially_processed", "2 of 3 pages decoded")}
        for key, (status, detail) in statuses.items():
            cur.execute("UPDATE paths SET processing_status = %s, status_detail = %s"
                        " WHERE id = %s", (status, detail, wanted[key]))

        # A classification and a keyword on the first document, so the report's
        # taxonomy facets have something to read: category term = a word, and a
        # keyword = a packed phrase (the encoding the schema uses).
        from core.serialization import pack_int_list

        def word(text):
            cur.execute("SELECT id FROM words WHERE word = %s", (text,))
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (text,))
            return cur.fetchone()[0]

        term_id = word(f"treaty{_U}")
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (term_id,))
        category_id = cur.fetchone()[0]
        cur.execute("INSERT INTO words_categorys (word_id, category_id) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING", (term_id, category_id))
        cur.execute("INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)"
                    " VALUES (%s, %s, 3, %s) ON CONFLICT DO NOTHING",
                    (ids["hash_a"], term_id, pack_int_list([1, 2, 3])))
        phrase = [word(f"expire{_U}"), word(f"treaty{_U}")]
        cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)"
                    " RETURNING id", (pack_int_list(phrase), category_id))
        keyword_id = cur.fetchone()[0]
        cur.execute("INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)"
                    " VALUES (%s, %s, 2) ON CONFLICT DO NOTHING", (ids["hash_a"], keyword_id))
        ids["category_term"] = f"treaty{_U}"
        ids["keyword_id"] = keyword_id
    conn.close()
    return ids


def _query(client, url, **params):
    resp = client.get(url, query_string=params)
    return resp.status_code, resp


def test_overview_counts_agree_with_the_rows(pg_db, world, viewer_client):
    status, resp = _query(viewer_client, "/api/reports/overview",
                          source_id=world["source"])
    assert status == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_json()
    totals = body["totals"]
    assert totals["total"] == 2
    assert totals["success"] == 1 and totals["failed"] == 1
    assert totals["success_pct"] == 50.0

    by_type = {row["file_type"]: row for row in body["by_file_type"]}
    assert by_type["txt"]["total"] == 2
    assert by_type["txt"]["failed"] == 1 and by_type["txt"]["success"] == 1
    # Classifications, category terms and keywords all come from the same
    # filtered population. The keyword is a phrase decoded from its packed
    # word-id list, not a byte blob.
    assert [row["category_term"] for row in body["classifications"]] == [world["category_term"]]
    keywords = {row["keyword"]: row["paths"] for row in body["keywords"]}
    assert keywords == {f"expire{_U} treaty{_U}": 1}, keywords
    assert {row["source_name"] for row in body["sources"]} == {f"rp_src_{_U}"}


def test_filters_narrow_every_part_of_the_report(pg_db, world, viewer_client):
    # The session database holds every other module's rows too, so the
    # unfiltered count is asserted as "at least this world", and each filter
    # is asserted against a source only this module created.
    _, all_rows = _query(viewer_client, "/api/reports/overview")
    assert all_rows.get_json()["totals"]["total"] >= 3
    _, one_source = _query(viewer_client, "/api/reports/overview",
                           source_id=world["other_source"])
    body = one_source.get_json()
    assert body["totals"]["total"] == 1
    assert body["by_file_type"][0]["file_type"] == "pdf"
    assert {row["source_name"] for row in body["sources"]} == {f"rp_other_{_U}"}

    # A date range that excludes the rows returns measured zeros, not an error.
    _, empty = _query(viewer_client, "/api/reports/overview",
                      date_from="2099-01-01")
    assert empty.get_json()["totals"]["total"] == 0
    assert empty.get_json()["classifications"] == []


def test_an_invalid_filter_is_a_stated_400(viewer_client):
    status, resp = _query(viewer_client, "/api/reports/overview",
                          date_from="01/10/2026")
    assert status == 400
    assert "date_from" in resp.get_json()["error"]["message"]
    status, _ = _query(viewer_client, "/api/reports/overview", source_id="x")
    assert status == 400


def test_the_page_renders_the_same_numbers(pg_db, world, viewer_client):
    status, resp = _query(viewer_client, "/reports", source_id=world["source"])
    assert status == 200
    body = resp.get_data(as_text=True)
    assert 'id="reportTotal"' in body
    # The totals paragraph/table is rendered server-side by the same statement.
    assert f">{2}<" in body
    assert 'id="reportByType"' in body and "txt" in body
    assert 'id="reportKeywords"' in body


def test_dashboard_reads_the_same_statement(pg_db, world, viewer_client):
    status, resp = _query(viewer_client, "/api/reports/dashboard",
                          source_id=world["source"])
    assert status == 200
    body = resp.get_json()
    assert body["totals"]["total"] == 2
    assert body["languages"], "the language list the report dialog offers"
    status, page = _query(viewer_client, "/reports/dashboard",
                          source_id=world["source"])
    assert status == 200 and 'id="reportsDashboardPage"' in page.get_data(as_text=True)


def test_the_dossier_prints_the_entries_and_their_detail(pg_db, world, viewer_client):
    status, resp = _query(viewer_client, "/reports/entries/print",
                          source_id=world["source"])
    assert status == 200
    body = resp.get_data(as_text=True)
    assert "alpha.txt" in body and "beta.txt" in body
    assert "reader refused the archive" in body, "the status reason is printed"
    assert "gamma.txt" not in body, "the filter applies to the dossier too"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"

    # Narrowing to one entry by id.
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM paths WHERE file_path LIKE %s",
                    (f"%{_U}/alpha.txt",))
        alpha_path_id = cur.fetchone()[0]
    conn.close()
    status, one = _query(viewer_client, "/reports/entries/print",
                         source_id=world["source"], ids=str(alpha_path_id))
    assert status == 200
    narrowed = one.get_data(as_text=True)
    assert "alpha.txt" in narrowed and "beta.txt" not in narrowed
    status, bad = _query(viewer_client, "/reports/entries/print", ids="1,x")
    assert status == 400 and "ids" in bad.get_json()["error"]["message"]


def test_the_report_needs_no_table_of_its_own(pg_db):
    """The frozen schema is the source: report_* tables were removed on purpose."""
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public'")
            tables = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    assert not {t for t in tables if t.startswith("report_")}


def test_the_batch_report_page_does_not_depend_on_a_missing_module(app, viewer_client):
    """``/operations/input/reports/<job_id>`` imported a package that did not exist.

    The route is the printable per-batch report. Its handler imports
    ``services.reporting.ingestion_batch``; before that package existed the
    import ran inside the route and every request - valid id, missing id,
    even a non-numeric one - answered 500. An id that names no job is a 404.
    """
    resp = viewer_client.get("/operations/input/reports/no-such-job")
    assert resp.status_code == 404, (resp.status_code, resp.get_data(as_text=True)[:200])
    resp = viewer_client.get("/operations/input/reports/999999999")
    assert resp.status_code == 404, (resp.status_code, resp.get_data(as_text=True)[:200])
