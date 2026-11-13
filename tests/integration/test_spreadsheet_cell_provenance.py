"""Integration: every spreadsheet cell keeps the address it has in the source.

The reported defect: a value that lives in ``Sheet1!B4`` was cited as
``Sheet1 - B3``. The cause is not the address arithmetic - the row map counts
blank lines correctly - it is that the reader *drops* rows that are entirely
empty (``if not row: continue``), and nothing puts the gap back into the stored
text. The line that carries the value is therefore one line too early, and
every row below it inherits the error.

The tests below do not assert one hand-checked address. They rebuild every
cell from the stored text and compare the result with the workbook opened
independently through openpyxl, so any solution that shifts a single row or
column fails:

* rows that are blank before the first datum, in the middle, and at the end;
* a non-default start cell (content begins at C3);
* a merged range (the value belongs to its top-left cell only);
* a hidden row (still content, still addressable);
* two sheets, so a location without its sheet name is a failure too.

The round-trip is deliberately written against the public text API
(``load_text_content``) and the public row map (``_spreadsheet_row_map``):
those are what the viewer and the search results read.
"""

from __future__ import annotations

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
_MARKER = f"PROVENANCE{_UNIQUE}"

# What the workbook really contains, independent of the reader.
# (sheet, coordinate) -> value. Rows 1-2 and columns A-B are blank on
# purpose, so a reader that starts at A1 reports C3 as A1.
EXPECTED = {
    ("Sheet1", "C3"): "Region",
    ("Sheet1", "D3"): "Revenue",
    ("Sheet1", "C4"): "EMEA",
    ("Sheet1", "D4"): "4400",
    # Row 5 is entirely blank, row 6 has the value in column D only.
    ("Sheet1", "D6"): f"{_MARKER} value",
    ("Sheet1", "C7"): "APAC",          # row 7 is hidden in the sheet
    ("Sheet1", "D7"): "800",
    ("Sheet1", "C1"): "Budget title",  # merged C1:D1 - the value belongs to C1
    ("Notes", "A1"): "Author",
    ("Notes", "B2"): f"{_MARKER} note",
}


def _build_workbook(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["C1"] = "Budget title"
    ws.merge_cells("C1:D1")
    ws["C3"] = "Region"
    ws["D3"] = "Revenue"
    ws["C4"] = "EMEA"
    ws["D4"] = 4400
    # rows 5 is left entirely empty
    ws["D6"] = f"{_MARKER} value"
    ws["C7"] = "APAC"
    ws["D7"] = 800
    ws.row_dimensions[7].hidden = True

    notes = wb.create_sheet("Notes")
    notes["A1"] = "Author"
    notes["B2"] = f"{_MARKER} note"
    wb.save(path)


def _build_ods(path: Path) -> None:
    """An .ods whose blank rows and empty columns are written as repeats.

    ODF stores "two blank rows" as one row with number-rows-repeated="2" and
    "two empty cells" as one cell with number-columns-repeated="2". A reader
    that ignores the attributes collapses them, so every address below the
    repeat is short by the number of repeats.
    """
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Budget")

    def cell(text=None, repeat_cols=1):
        node = TableCell()
        if repeat_cols > 1:
            node.setAttribute("numbercolumnsrepeated", str(repeat_cols))
        if text is not None:
            node.addElement(P(text=text))
        return node

    def row(cells, repeat_rows=1):
        node = TableRow()
        if repeat_rows > 1:
            node.setAttribute("numberrowsrepeated", str(repeat_rows))
        for item in cells:
            node.addElement(item)
        return node

    table.addElement(row([cell("Region"), cell("Revenue")]))
    table.addElement(row([cell()], repeat_rows=2))       # rows 2 and 3 blank
    table.addElement(row([cell("EMEA"), cell(f"{_MARKER} ods")]))  # row 4
    table.addElement(row([cell(repeat_cols=3), cell("4000")]))     # row 5: A-C empty, D
    doc.spreadsheet.addElement(table)
    doc.save(str(path))


def _build_csv(path: Path) -> None:
    path.write_text(
        "kind,amount\n"
        "\n"
        "alpha,10\n"
        f"beta,{_MARKER}\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("cell_provenance")
    _build_workbook(root / f"ledger-{_UNIQUE}.xlsx")
    _build_ods(root / f"budget-{_UNIQUE}.ods")
    _build_csv(root / f"table-{_UNIQUE}.csv")
    return root


@pytest.fixture(scope="module")
def ingested(pg_db, corpus):
    tag = os.getpid()
    source_name = f"_prov_src_{tag}_{_UNIQUE}"
    side_name = f"_prov_side_{tag}"

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
        max_workers=1, enable_storage=True,
        storage_source=source_name, storage_side=side_name,
    )
    assert reader.process_folder(str(corpus)), "nothing was processed"

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    ids = {}
    try:
        with conn.cursor() as cur:
            for suffix in ("xlsx", "ods", "csv"):
                cur.execute(
                    "SELECT id FROM paths WHERE file_name LIKE %s ORDER BY id DESC LIMIT 1",
                    (f"%-{_UNIQUE}.{suffix}",),
                )
                row = cur.fetchone()
                assert row, f"no stored path for .{suffix}"
                ids[suffix] = row[0]
    finally:
        conn.close()
    return ids


def _cells_from_stored_text(path_id: int):
    """Rebuild {(sheet, coordinate): value} from the public text + row map."""
    from Api.services.search_service import _column_letter, _spreadsheet_row_map
    from Api.utils.utils import load_text_content

    text = load_text_content(path_id)
    lines = text.split("\n")
    row_map = _spreadsheet_row_map(lines)
    cells = {}
    for index, (sheet, row) in row_map.items():
        for offset, value in enumerate(lines[index].split("\t"), start=1):
            if str(value).strip() == "":
                continue
            cells[(sheet, f"{_column_letter(offset)}{row}")] = str(value).strip()
    return cells


def _workbook_cells(path: Path):
    """The same map, straight from the file with openpyxl."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    cells = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None or str(cell.value).strip() == "":
                    continue
                cells[(ws.title, cell.coordinate)] = str(cell.value).strip()
    return cells


def test_stored_text_reproduces_every_cell_address(ingested, corpus):
    stored = _cells_from_stored_text(ingested["xlsx"])
    for (sheet, coordinate), value in EXPECTED.items():
        assert stored.get((sheet, coordinate)) == value, (
            f"{sheet}!{coordinate} should hold {value!r}, "
            f"stored map has {stored.get((sheet, coordinate))!r}")
    # The whole sheet, not only the cells this file names.
    assert stored == _workbook_cells(corpus / f"ledger-{_UNIQUE}.xlsx")


def test_round_trip_against_an_independent_reader(ingested, corpus):
    """Every emitted (location, value) must exist in the source workbook."""
    truth = _workbook_cells(corpus / f"ledger-{_UNIQUE}.xlsx")
    for (sheet, coordinate), value in _cells_from_stored_text(ingested["xlsx"]).items():
        assert truth.get((sheet, coordinate)) == value, (
            f"emitted {sheet}!{coordinate}={value!r} but the workbook holds "
            f"{truth.get((sheet, coordinate))!r}")


def test_search_cites_the_real_cell(ingested):
    from Api.services.search_service import SearchService

    results, _ = SearchService.full_text_search(query=_MARKER, limit=10)
    hit = next(r for r in results if r["id"] == ingested["xlsx"])
    locations = [m.get("location") for m in hit.get("line_matches") or []]
    assert "Sheet1 - D6" in locations, f"expected Sheet1 - D6, got {locations}"
    assert "Sheet1 - B3" not in locations, "the value is in D6, not B3"


def test_csv_keeps_blank_rows_and_leading_empty_cells(ingested):
    from Api.utils.utils import load_text_content

    lines = load_text_content(ingested["csv"]).strip("\n").split("\n")
    assert lines[0] == "kind\tamount"
    assert lines[1] == ""              # blank row kept
    assert lines[2] == "alpha\t10"
    assert lines[3] == f"beta\t{_MARKER}"


# ---------------------------------------------------------------------------
# ODF stores runs of identical rows and cells as one node with a repeat count.
# ---------------------------------------------------------------------------

def test_ods_repeated_rows_and_cells_keep_their_addresses(ingested):
    from Api.services.search_service import _column_letter, _spreadsheet_row_map
    from Api.utils.utils import load_text_content

    text = load_text_content(ingested["ods"])
    lines = text.split("\n")
    sheet_start = next(i for i, line in enumerate(lines) if line.startswith("Sheet: Budget"))
    body = lines[sheet_start + 3:]
    # Two repeated blank rows are rows 2 and 3, so EMEA is on row 4 - not 3.
    assert body[0] == "Region\tRevenue"
    assert body[1] == "" and body[2] == "", f"repeated blank rows collapsed: {body[:4]!r}"
    assert body[3] == f"EMEA\t{_MARKER} ods", f"row 4 wrong: {body[3]!r}"
    # Three repeated empty cells occupy A-C, so 4000 is in column D.
    assert body[4] == "\t\t\t4000", f"repeated empty cells collapsed: {body[4]!r}"

    row_map = _spreadsheet_row_map(lines)
    addresses = {(sheet, f"{_column_letter(j + 1)}{row}"): value
                 for index, (sheet, row) in row_map.items()
                 for j, value in enumerate(lines[index].split("\t"))
                 if value.strip()}
    assert addresses.get(("Budget", "B4")) == f"{_MARKER} ods"
    assert addresses.get(("Budget", "D5")) == "4000"
