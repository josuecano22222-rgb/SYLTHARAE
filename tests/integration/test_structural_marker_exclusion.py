"""Integration: structural markers must not leak into search or
classification (label exclusion).

The storage pipeline embeds display markers into stored text (worksheet
headers "Sheet: Sheet1", dimensions "Rows: 40", slide boundaries, page
headers, paragraph styles, email header labels, file paths, ...). These are
PRESENTATIONAL scaffolding, not document content: a query for "sheet" must
not return every spreadsheet, and the word-frequency classification must
not count label words.

Since the display phase, the verbatim text (with markers) is stored in
``contents_raw`` for the content viewers. This phase splits the views:

* DISPLAY text (unchanged) - markers kept, viewers and in-document search
  work on exactly what the user sees;
* INDEX text (new) - markers stripped before word tokenization, so
  words_hashs (search + classification + analysis) sees only real content;
* search snippet matching skips pure structural lines and matches labelled
  lines (From:/Subject:) on their VALUE.

Proven here end to end on the real pipeline + database:

1. Marker words (sheet, rows, columns, slide, style, ...) are absent from
   the word index (words_hashs) of ingested files.
2. Full-text search for label words does not return the files; real content
   (cell values, slide text, email subject/sender) still does.
3. Search line snippets never come from structural marker lines.
4. The DISPLAY text is unchanged: markers still present, in-document
   search still locates them (WYSIWYG contract of the content pages).
"""

import os
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
_MARKER = f"CORALBELL{_UNIQUE}"  # unique real-content probe


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    import openpyxl
    from docx import Document
    from pptx import Presentation

    root = tmp_path_factory.mktemp("marker_corpus")

    # Spreadsheet: default-style sheet name + custom name; real data cells.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"  # the exact software default label from the request
    ws.append(["Region", "Revenue"])
    ws.append(["EMEA", f"{_MARKER} earnings 4400"])
    ws2 = wb.create_sheet("Data")
    ws2.append(["Note"])
    ws2.append(["figures verified"])
    wb.save(root / f"ledger-{_UNIQUE}.xlsx")

    # Presentation: two slides with real text (slide boundaries are markers).
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = f"{_MARKER} overview"
    s1.placeholders[1].text = "growth held steady"
    s2 = prs.slides.add_slide(prs.slide_layouts[0])
    s2.shapes.title.text = "Outlook"
    s2.placeholders[1].text = "no material changes"
    prs.save(root / f"deck-{_UNIQUE}.pptx")

    # Word document: a Heading-styled paragraph (style name is a label).
    doc = Document()
    doc.add_heading(f"{_MARKER} summary", level=2)
    doc.add_paragraph("The operational picture remained stable.")
    doc.save(root / f"memo-{_UNIQUE}.docx")

    # Email: header labels are markers, header VALUES are content.
    (root / f"message-{_UNIQUE}.eml").write_text(
        f"From: Sender <sender-{_UNIQUE}@example.org>\n"
        f"To: recipient@example.org\n"
        f"Subject: {_MARKER} quarterly digest\n"
        f"Date: Mon, 13 Apr 2026 10:00:00 +0000\n"
        f"Message-ID: <{_UNIQUE}@example.org>\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        f"The {_MARKER} report is attached to this message.\n",
        encoding="utf-8",
    )

    # Plain text whose first line uses a label-like prefix: the VALUE stays
    # searchable, the label word does not.
    (root / f"notes-{_UNIQUE}.txt").write_text(
        "Status: all systems nominal\n"
        f"{_MARKER} checkpoint reached\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture(scope="module")
def ingested(pg_db, corpus):
    tag = os.getpid()
    source_name, side_name = f"_marker01_src_{tag}_{_UNIQUE}", f"_marker01_side_{tag}"

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
            (side_name, today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 'test', 0.5, 'test', %s) ON CONFLICT (name) DO NOTHING",
            (source_name, today),
        )
    conn.commit()
    conn.close()

    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        max_workers=2, enable_storage=True,
        storage_source=source_name, storage_side=side_name,
    )
    results = reader.process_folder(str(corpus))
    assert results, "nothing was processed"

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    path_ids = {}
    try:
        with conn.cursor() as cur:
            for suffix in ("xlsx", "pptx", "docx", "eml", "txt"):
                cur.execute(
                    "SELECT id FROM paths WHERE file_name LIKE %s ORDER BY id DESC LIMIT 1",
                    (f"%-{_UNIQUE}.{suffix}",),
                )
                row = cur.fetchone()
                assert row, f"no stored path for .{suffix} file"
                path_ids[suffix] = row[0]
    finally:
        conn.close()
    return path_ids


# ---------------------------------------------------------------------------
# 1. Word index cleanliness (search + classification source)
# ---------------------------------------------------------------------------

_LABEL_WORDS = ("sheet", "rows", "columns", "slide", "style", "heading",
                "attachments", "method", "total", "producer")


def test_marker_words_absent_from_word_index(pg_db, ingested):
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            for suffix, path_id in ingested.items():
                cur.execute(
                    """
                    SELECT w.word FROM words_hashs wp
                    JOIN words w ON wp.word_id = w.id
                    WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s) AND w.word = ANY(%s)
                    """,
                    (path_id, list(_LABEL_WORDS)),
                )
                leaked = [r[0] for r in cur.fetchall()]
                assert not leaked, f"{suffix}: label words leaked into index: {leaked}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Full-text search: labels don't match, content does
# ---------------------------------------------------------------------------

def _search(query):
    from Api.services.search_service import SearchService
    return SearchService.full_text_search(query=query, limit=50)


def _result_ids(results):
    return {r["id"] for r in results}


@pytest.mark.parametrize("label_query", [
    "Sheet", "Sheet1", "Rows", "Columns", "Slide", "Style", "Heading",
    "Method", "Attachments", "Producer", "Total Slides",
])
def test_label_queries_do_not_match_corpus_files(ingested, label_query):
    results, _total = _search(label_query)
    matched = _result_ids(results)
    for suffix, path_id in ingested.items():
        assert path_id not in matched, (
            f"label query {label_query!r} matched {suffix} file via a structural marker")


def test_sheet_names_are_labels_not_content(ingested):
    """Default sheet names ("Sheet1") are software labels: not searchable.
    (Cell VALUES are - see the content tests below.)"""
    results, _ = _search("Sheet1")
    assert ingested["xlsx"] not in _result_ids(results)


def test_real_content_is_searchable(ingested):
    # Unique marker appears in every corpus file's real content
    results, total = _search(_MARKER)
    matched = _result_ids(results)
    for suffix, path_id in ingested.items():
        assert path_id in matched, f"{suffix} content not searchable"
    assert total >= len(ingested)


def test_email_header_values_are_searchable(ingested):
    for query in (f"sender-{_UNIQUE}@example.org", f"{_MARKER} quarterly digest"):
        results, _ = _search(query)
        assert ingested["eml"] in _result_ids(results), f"email value not searchable: {query}"


def test_spreadsheet_cell_values_are_searchable(ingested):
    results, _ = _search(f"{_MARKER} earnings")
    assert ingested["xlsx"] in _result_ids(results)


def test_labelled_text_value_is_searchable_without_label(ingested):
    # "Status: all systems nominal" -> value searchable, label word is not
    results, _ = _search("all systems nominal")
    assert ingested["txt"] in _result_ids(results)
    results, _ = _search("Status")
    assert ingested["txt"] not in _result_ids(results)


# ---------------------------------------------------------------------------
# 3. Search snippets never come from structural marker lines
# ---------------------------------------------------------------------------

def test_line_matches_exclude_structural_marker_lines(ingested):
    # The xlsx display text contains "Sheet: Sheet1" and "Sheet: Data" plus
    # a cell holding the word "Data" is NOT present - instead probe with the
    # sheet name word "Data" which appears ONLY in a marker line: the file
    # must not match at all, and no snippet may cite a marker line.
    results, _ = _search("figures verified")
    assert ingested["xlsx"] in _result_ids(results)
    for r in results:
        for m in r.get("line_matches", []):
            line = m["line_text"]
            assert not line.startswith("Sheet:"), f"marker line as snippet: {line!r}"
            assert not line.startswith("Rows:"), f"marker line as snippet: {line!r}"
            assert not line.startswith("Columns:"), f"marker line as snippet: {line!r}"


def test_find_matching_lines_skips_pure_markers(ingested):
    from Api.services.search_service import SearchService

    # A word appearing ONLY inside structural lines yields no line matches.
    xlsx_id = ingested["xlsx"]
    matches = SearchService._find_matching_lines(xlsx_id, "Sheet1")
    assert matches == []


# ---------------------------------------------------------------------------
# 4. Display unchanged (markers still render and stay in-document searchable)
# ---------------------------------------------------------------------------

def test_display_text_keeps_markers(ingested):
    from Api.utils.utils import load_text_content

    xlsx = load_text_content(ingested["xlsx"])
    assert "Sheet: Sheet1" in xlsx and "Sheet: Data" in xlsx
    assert "Region\tRevenue" in xlsx

    docx = load_text_content(ingested["docx"])
    assert "[Style: Heading 2]" in docx

    pptx = load_text_content(ingested["pptx"])
    assert "Slide 1" in pptx and "Slide 2" in pptx

    eml = load_text_content(ingested["eml"])
    assert "From:" in eml and "Subject:" in eml


def test_in_document_search_still_finds_displayed_markers(admin_client, ingested):
    """The content pages' own search is WYSIWYG: it searches exactly the
    displayed text, so markers remain locatable there (that is how a user
    finds worksheet boundaries while reading - different surface, different
    contract from the file-level search index)."""
    resp = admin_client.get(f"/file/{ingested['xlsx']}/search?q=Sheet1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_matches"] >= 1  # found in the displayed text


def test_word_count_reflects_content_only(pg_db, ingested):
    """Classification input (word_count per word) must not count markers."""
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(wp.word_count), 0) FROM words_hashs wp
                JOIN words w ON wp.word_id = w.id
                WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s) AND w.word IN ('rows', 'columns', 'sheet')
                """,
                (ingested["xlsx"],),
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()
