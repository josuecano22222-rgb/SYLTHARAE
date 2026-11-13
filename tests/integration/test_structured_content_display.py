"""Integration: structured per-type content display (raw text store).

The word-ID content store lowercases and space-joins words on
reconstruction, destroying the structure the extractors produced
(worksheet names and boundaries, slide numbers, page headers,
tab-delimited rows, original casing, line breaks). This phase adds the
``contents_raw`` store (baseline migration m0001): the extractor's combined
text is
kept verbatim at ingest and preferred on read, so every content interface
can render each file type with the presentation best suited to it -
spreadsheet sheets with navigation, slide decks, paginated PDFs, rendered
Markdown, line-numbered logs.

Proven here end to end on the real pipeline + database:

1. The baseline migration (m0001) creates ``contents_raw``.
2. Real .xlsx/.pptx/.docx/.md/.log/.csv files ingest, and
   ``load_text_content`` returns the STRUCTURED text (original case,
   newlines, tabs, sheet/slide markers) instead of the word soup.
3. PowerPoint slides carry explicit "Slide N" boundary markers (new
   element format emitted none before).
4. Markdown and log sources are preserved verbatim (rendering is
   client-side; fidelity of the source is the server's contract).
5. Files stored with word IDs only (no ``contents_raw`` row) still display via the
   word-join fallback.
6. The in-document search endpoint reports offsets against the SAME
   structured text the pages display.
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


# ---------------------------------------------------------------------------
# Real fixture files
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """A corpus with one file per display-relevant type."""
    import openpyxl
    from docx import Document
    from pptx import Presentation
    from pptx.util import Inches

    root = tmp_path_factory.mktemp("structured_corpus")

    # --- Spreadsheet: three worksheets -------------------------------
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Region", "Q1", "Q2"])
    ws.append(["EMEA", "1200", "1350"])
    ws.append(["APAC", "800", "940"])
    ws2 = wb.create_sheet("Forecast")
    ws2.append(["Scenario", "Growth"])
    ws2.append(["Base", "5%"])
    ws2.append(["High", "12%"])
    ws3 = wb.create_sheet("Notes")
    ws3.append(["Author", "Remark"])
    ws3.append(["ops", "Reviewed ZEPHYR figures"])
    wb.save(root / f"ledger-{_UNIQUE}.xlsx")

    # --- Presentation: three slides, one with a table ------------------
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "Quarterly Overview"
    s1.placeholders[1].text = "Revenue grew strongly"
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "Regional Split"
    tbl = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(1.5)).table
    tbl.cell(0, 0).text = "Region"
    tbl.cell(0, 1).text = "Share"
    tbl.cell(1, 0).text = "EMEA"
    tbl.cell(1, 1).text = "60%"
    s3 = prs.slides.add_slide(prs.slide_layouts[0])
    s3.shapes.title.text = "Outlook"
    s3.placeholders[1].text = "ZEPHYR expansion planned"
    prs.save(root / f"deck-{_UNIQUE}.pptx")

    # --- Word document: headings + table ------------------------------
    doc = Document()
    doc.add_heading("Annual Report", level=1)
    doc.add_paragraph("The year in review was stable.")
    doc.add_heading("Details", level=2)
    table = doc.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Uptime"
    table.cell(1, 1).text = "99.9%"
    doc.add_paragraph("End of report.")
    doc.save(root / f"report-{_UNIQUE}.docx")

    # --- Markdown -----------------------------------------------------
    (root / f"readme-{_UNIQUE}.md").write_text(
        "# Project ZEPHYR\n\n"
        "A **demo** README with `inline code`.\n\n"
        "## Features\n\n"
        "- Fast ingestion\n"
        "- Structured display\n\n"
        "```python\n"
        "print('hello')\n"
        "```\n\n"
        "| Col A | Col B |\n"
        "|-------|-------|\n"
        "| 1     | 2     |\n",
        encoding="utf-8",
    )

    # --- Log ----------------------------------------------------------
    (root / f"server-{_UNIQUE}.log").write_text(
        "2026-04-01T10:00:00Z INFO startup complete\n"
        "2026-04-01T10:00:05Z WARN disk usage at 81%\n"
        "2026-04-01T10:01:00Z ERROR connection reset by peer\n",
        encoding="utf-8",
    )

    # --- CSV ----------------------------------------------------------
    (root / f"regions-{_UNIQUE}.csv").write_text(
        "region,product,units\nEMEA,Widget,12\nAPAC,Gadget,7\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture(scope="module")
def ingested(pg_db, corpus):
    """Ingest the corpus through the real pipeline; return name->path_id."""
    tag = os.getpid()
    source_name, side_name = f"_struct01_src_{tag}_{_UNIQUE}", f"_struct01_side_{tag}"

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
            for suffix in ("xlsx", "pptx", "docx", "md", "log", "csv"):
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
# 1. Migration
# ---------------------------------------------------------------------------

def test_baseline_migration_created_contents_raw(pg_db):
    """contents_raw comes from the frozen chain, not from a later migration.

    The chain in this checkout is m0001..m0003 (database/migrations); there is
    no m0010, so asserting a recorded version >= "0010" asserted a migration
    history the repository never had. The table is created by the baseline
    migration, which is what this checks - against the versions the runner
    discovers, not against a number written here.
    """
    from database.migration_runner import discover_migrations

    chain = {m.version for m in discover_migrations()}
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_name = 'contents_raw'")
            assert cur.fetchone(), "contents_raw table missing"
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        assert chain <= applied, f"not every migration was applied: {sorted(chain - applied)}"
        assert min(chain) == "0001"
        assert "0010" not in chain
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Structured raw text per file type
# ---------------------------------------------------------------------------

def _content(path_id):
    from Api.utils.utils import load_text_content
    return load_text_content(path_id)


def test_xlsx_raw_text_preserves_sheets_and_tabs(ingested):
    text = _content(ingested["xlsx"])
    # All three worksheets present, in order, with their names
    assert "Sheet: Budget" in text
    assert "Sheet: Forecast" in text
    assert "Sheet: Notes" in text
    assert text.index("Sheet: Budget") < text.index("Sheet: Forecast") < text.index("Sheet: Notes")
    # Original casing preserved (word-join would lowercase everything)
    assert "EMEA" in text and "APAC" in text
    # Tab-delimited rows preserved (the Excel table parser needs them)
    assert "Region\tQ1\tQ2" in text
    assert "EMEA\t1200\t1350" in text


def test_pptx_raw_text_has_slide_boundary_markers(ingested):
    text = _content(ingested["pptx"])
    # Explicit slide markers for every slide (new element format emitted
    # none before this change - slides were indistinguishable)
    assert "Slide 1" in text
    assert "Slide 2" in text
    assert "Slide 3" in text
    # Slide content and casing survive
    assert "Quarterly Overview" in text
    assert "Regional Split" in text
    assert "ZEPHYR expansion planned" in text
    # The table on slide 2 is tab-delimited
    assert "Region\tShare" in text


def test_docx_raw_text_preserves_styles_and_tables(ingested):
    text = _content(ingested["docx"])
    assert "[Style: Heading 1] Annual Report" in text
    assert "[Style: Heading 2] Details" in text
    assert "The year in review was stable." in text
    assert "Metric\tValue" in text
    assert "Uptime\t99.9%" in text
    # The table marker is what the display layer uses to open the table: the
    # rows alone would come back as tab-separated prose (the reported defect).
    assert "Table 1" in text
    # One row per line, cells tab-separated: two rows, not one paragraph.
    table_lines = [line for line in text.splitlines()
                   if line.startswith(("Metric\t", "Uptime\t"))]
    assert table_lines == ["Metric\tValue", "Uptime\t99.9%"], table_lines


def test_markdown_source_is_verbatim(ingested):
    text = _content(ingested["md"])
    assert "# Project ZEPHYR" in text
    assert "A **demo** README with `inline code`." in text
    assert "```python" in text
    assert "print('hello')" in text
    assert "| Col A | Col B |" in text
    # No extractor statistics header polluting the document
    assert "Encoding:" not in text
    assert "Non-empty Lines:" not in text


def test_log_source_is_verbatim_with_lines(ingested):
    text = _content(ingested["log"])
    assert "2026-04-01T10:00:00Z INFO startup complete" in text
    assert "2026-04-01T10:01:00Z ERROR connection reset by peer" in text
    assert "\n" in text  # line structure preserved
    # Content stored exactly once (the reader returns both 'content' and
    # 'lines'; both used to be stored, duplicating every text file)
    assert text.count("startup complete") == 1
    assert text.count("connection reset by peer") == 1


def test_csv_raw_text_is_tab_delimited(ingested):
    text = _content(ingested["csv"])
    assert "region\tproduct\tunits" in text
    assert "EMEA\tWidget\t12" in text


# ---------------------------------------------------------------------------
# 3. Fallback for pre-migration content (word IDs only)
# ---------------------------------------------------------------------------

def test_word_join_fallback_when_no_raw(pg_db):
    from database.services.contents_db_service import ContentDBService
    from Api.utils.utils import load_text_content

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id",
                (f"fallback-side-{_UNIQUE}",),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 't', 1.0, 'NL', CURRENT_DATE) RETURNING id",
                (f"fallback-source-{_UNIQUE}",),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash)"
                " VALUES (%s) RETURNING id",
                (f"fallback-hash-{_UNIQUE}",),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO paths (file_name, file_path, file_size, file_type,
                                   file_status, file_date, date_creation, context_id)
                VALUES (%s, %s, 10, 'txt', 'Read', CURRENT_DATE, CURRENT_DATE, %s)
                RETURNING id
                """,
                (f"fallback-{_UNIQUE}.txt", f"/fallback/fallback-{_UNIQUE}.txt", context_id),
            )
            path_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    # Store word-ID content ONLY (no raw) - the shape a file ingested before
    # contents_raw existed would have.
    words = ["Legacy", "storage", "without", "raw", "text"]
    svc = ContentDBService()
    svc.words_repo.bulk_insert_words(words)
    id_map = svc.words_repo.resolve_word_ids_batch(words)
    hash_id = svc.resolve_hash_id(path_id)
    assert hash_id is not None, "the occurrence must resolve to its canonical content"
    svc.contents_repo.store_text_content(
        [id_map[w] for w in words], date.today(), hash_id)

    # Word-join reconstruction still works.
    content = load_text_content(path_id)
    assert content == " ".join(words)


# ---------------------------------------------------------------------------
# 4. Search offsets are against the displayed (raw) text
# ---------------------------------------------------------------------------

def test_search_offsets_match_displayed_structured_text(admin_client, ingested):
    xlsx_id = ingested["xlsx"]
    displayed = _content(xlsx_id)

    resp = admin_client.get(f"/file/{xlsx_id}/search?q=EMEA&case_sensitive=true")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_matches"] >= 1
    for match in body["matches"]:
        assert displayed[match["start"]:match["end"]] == "EMEA"

    # Total chars equal the displayed text length
    assert body["total_chars"] == len(displayed)


def test_pages_serve_structured_content(admin_client, ingested):
    for suffix, expected in (
        ("xlsx", "Sheet: Budget"),
        ("pptx", "Slide 1"),
        ("md", "# Project ZEPHYR"),
    ):
        path_id = ingested[suffix]
        detail = admin_client.get(f"/file/{path_id}")
        assert detail.status_code == 200
        reader = admin_client.get(f"/file/{path_id}/full-content")
        assert reader.status_code == 200
        # The content API (reader data source) serves the structured text
        api = admin_client.get(f"/file/{path_id}/content?offset=0&limit=100000")
        assert api.status_code == 200
        assert expected in (api.get_json().get("content") or "")


def test_pdf_marker_search_offsets(app, admin_client, ingested, monkeypatch):
    """PDF display data: page markers carry offsets consistent with the
    displayed text (PDF ingestion needs pymupdf, unavailable here, so the
    marker text is synthesized for the API-level contract)."""
    import Api.blueprints.files as files_bp

    synthetic = (
        "Page 1 | Method: text | Length: 20 chars\n"
        "first page needle here\n"
        "Page 2 | Method: text | Length: 20 chars\n"
        "second page needle there\n"
    )
    monkeypatch.setattr(files_bp, "load_text_content", lambda _fid: synthetic)
    try:
        resp = admin_client.get(f"/file/{ingested['md']}/search?q=needle")
        assert resp.status_code == 200
        matches = resp.get_json()["matches"]
        assert [m["line"] for m in matches] == [2, 4]
        for m in matches:
            assert synthetic[m["start"]:m["end"]] == "needle"
    finally:
        monkeypatch.undo()
