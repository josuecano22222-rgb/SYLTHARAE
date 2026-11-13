"""Integration: exact cell locations for spreadsheets + multi-recipient
email handling.

Two user-facing guarantees:

1. SPREADSHEET CELL LOCATIONS (Excel and similar, incl. CSV)
   Generic auto-generated labels ("Sheet1", "Sheet2", ...) say nothing
   about where content sits. The exact location of every cell is preserved
   by ROW and COLUMN:
   * blank rows keep their row numbers (a value in B5 stays in B5);
   * empty leading/trailing cells keep their column positions;
   * the viewer renders an address grid (column letters + row gutter +
     per-cell address), generated at render time so it never pollutes the
     searchable text;
   * search results cite the exact cell ("Budget - B4") for spreadsheet
     hits instead of a document-wide line number.

2. EMAIL: ONE SENDER, ONE OR MORE RECIPIENTS
   A message has a single sender but may have many recipients, spread over
   comma-separated lists and even repeated To/Cc/Bcc header lines. Every
   recipient must be captured, displayed and searchable.
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
_MARKER = f"AZUREHERON{_UNIQUE}"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    import openpyxl

    root = tmp_path_factory.mktemp("cells_corpus")

    # Spreadsheet with a blank row (row 3) and an empty leading cell in
    # row 4 (the value must land in column B, not A).
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"  # generic auto-generated name
    ws.append(["Region", "Revenue"])            # row 1
    ws.append(["EMEA", "4400"])                 # row 2
    ws.append([])                               # row 3 (blank)
    ws.append([None, f"{_MARKER} value"])       # row 4: A empty, B = value
    ws2 = wb.create_sheet("Data")
    ws2.append(["Note"])
    ws2.append(["second sheet row"])
    wb.save(root / f"cells-{_UNIQUE}.xlsx")

    # CSV with a blank row and an empty cell (positions must hold).
    (root / f"table-{_UNIQUE}.csv").write_text(
        "kind,amount\n"
        "alpha,10\n"
        "\n"
        f",20\n"          # row 3 blank; row 4: kind empty, amount 20
        f"beta,{_MARKER}\n",
        encoding="utf-8",
    )

    # Email: ONE sender, MULTIPLE recipients - comma list + repeated To
    # header lines + a CC list. Every recipient must survive.
    (root / f"broadcast-{_UNIQUE}.eml").write_text(
        f"From: Control <control-{_UNIQUE}@example.org>\n"
        f"To: Alice <alice-{_UNIQUE}@example.org>, Bob <bob-{_UNIQUE}@example.org>\n"
        f"To: Carol <carol-{_UNIQUE}@example.org>\n"
        f"Cc: Dave <dave-{_UNIQUE}@example.org>, Erin <erin-{_UNIQUE}@example.org>\n"
        f"Subject: {_MARKER} broadcast\n"
        "Date: Mon, 13 Apr 2026 11:00:00 +0000\n"
        "Message-ID: <broadcast-2026-04@example.org>\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        f"Broadcast body with {_MARKER} for all recipients.\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture(scope="module")
def ingested(pg_db, corpus):
    tag = os.getpid()
    source_name, side_name = f"_cells01_src_{tag}_{_UNIQUE}", f"_cells01_side_{tag}"

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
            for suffix in ("xlsx", "csv", "eml"):
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
# 1. Spreadsheet cell locations
# ---------------------------------------------------------------------------

def test_stored_rows_preserve_exact_positions(ingested):
    from Api.utils.utils import load_text_content

    text = load_text_content(ingested["xlsx"])
    lines = text.split("\n")
    # After "Sheet: Sheet1" + Rows/Columns metadata: rows 1..4 in order,
    # row 3 stored as an EMPTY line (position preserved), row 4 keeps its
    # leading empty cell (line starts with a tab).
    sheet_start = next(i for i, ln in enumerate(lines) if ln.startswith("Sheet: Sheet1"))
    data = lines[sheet_start + 3:sheet_start + 7]
    assert data[0] == "Region\tRevenue"            # row 1
    assert data[1] == "EMEA\t4400"                 # row 2
    # row 3 is blank (kept as a whitespace-only line: empty cells, no data)
    assert data[2].strip() == "" and "\t" not in data[2].strip()
    assert data[3].startswith("\t")                 # row 4: A empty -> leading tab
    assert data[3] == f"\t{_MARKER} value"          # value sits in column B


def test_csv_rows_preserve_exact_positions(ingested):
    from Api.utils.utils import load_text_content

    text = load_text_content(ingested["csv"])
    lines = text.strip("\n").split("\n")
    assert lines[0] == "kind\tamount"
    assert lines[1] == "alpha\t10"
    assert lines[2] == ""                            # blank row kept
    assert lines[3] == "\t20"                        # empty leading cell kept
    assert lines[4] == f"beta\t{_MARKER}"


def test_search_results_cite_exact_cell(ingested):
    from Api.services.search_service import SearchService

    # The marker sits in Sheet1!B4 (blank row 3 preserved, leading empty
    # cell preserved).
    results, _ = SearchService.full_text_search(query=_MARKER, limit=10)
    hit = next(r for r in results if r["id"] == ingested["xlsx"])
    assert hit.get("line_matches"), "no line matches for spreadsheet hit"
    loc = hit["line_matches"][0].get("location")
    assert loc == "Sheet1 - B4", f"wrong cell location: {loc!r}"

    # CSV: marker in row 5, column 2 -> "B5" (no sheet name)
    results, _ = SearchService.full_text_search(query=_MARKER, limit=10)
    hit = next(r for r in results if r["id"] == ingested["csv"])
    loc = hit["line_matches"][0].get("location")
    assert loc == "B5", f"wrong csv cell location: {loc!r}"


def test_row_map_handles_blank_rows_and_multiple_sheets():
    from Api.services.search_service import _spreadsheet_row_map, _column_letter

    text = (
        "Sheet: Budget\nRows: 3\nColumns: 3\nRegion\tQ1\tQ2\nEMEA\t1200\t1350\n"
        "\nAPAC\t800\t940\n\nSheet: Notes\nRows: 1\nColumns: 2\nAuthor\tRemark\nops\tok"
    )
    lines = text.split("\n")
    m = _spreadsheet_row_map(lines)
    assert m[3] == ("Budget", 1)     # Region row
    assert m[4] == ("Budget", 2)     # EMEA row
    assert m[6] == ("Budget", 4)     # blank line counted -> APAC lands on row 4
    assert m[11] == ("Notes", 1)
    assert m[12] == ("Notes", 2)
    assert _column_letter(1) == "A"
    assert _column_letter(26) == "Z"
    assert _column_letter(27) == "AA"
    assert _column_letter(52) == "AZ"
    assert _column_letter(703) == "AAA"


# ---------------------------------------------------------------------------
# 2. Email: one sender, multiple recipients
# ---------------------------------------------------------------------------

def test_all_recipients_captured_and_searchable(ingested):
    from Api.utils.utils import load_text_content

    text = load_text_content(ingested["eml"])

    # ONE sender
    assert f"From: Control <control-{_UNIQUE}@example.org>" in text

    # EVERY recipient: comma list, repeated To header, and the CC list
    for name in ("alice", "bob", "carol", "dave", "erin"):
        assert f"{name}-{_UNIQUE}@example.org" in text, f"recipient {name} lost"

    # The To value contains all three To recipients (not just the first)
    to_line = next(ln for ln in text.split("\n") if ln.startswith("To:"))
    for name in ("alice", "bob", "carol"):
        assert f"{name}-{_UNIQUE}@example.org" in to_line


def test_each_recipient_is_independently_searchable(ingested):
    from Api.services.search_service import SearchService

    for name in ("alice", "bob", "carol", "dave", "erin"):
        results, _ = SearchService.full_text_search(
            query=f"{name}-{_UNIQUE}@example.org", limit=10)
        assert ingested["eml"] in {r["id"] for r in results}, (
            f"recipient {name} not searchable")


def test_recipient_values_not_label_searchable(ingested):
    from Api.services.search_service import SearchService

    # The label word "To" is structural; the addresses are content.
    results, _ = SearchService.full_text_search(query="To", limit=10)
    assert ingested["eml"] not in {r["id"] for r in results}
