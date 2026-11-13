"""The docx reader's table grid: one cell of text per merged region.

python-docx describes the table grid the way Word lays it out: a merged cell
is the same ``tc`` element at every grid position it covers, and it hands back
its text at each of those positions. Storing every position verbatim therefore
duplicated the text - a heading merged across two columns appeared twice, and
a vertically merged cell reappeared on every row it spanned. The reader keeps
the text once (at the first position it covers) and leaves the covered
positions empty: the grid keeps its shape and the reading order is unchanged.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("docx", reason="python-docx not installed")


def _table_rows(path):
    from reader_file.readers.read_office import OfficeFileReader

    result = OfficeFileReader().read_file(
        {"path": str(path), "effective_extension": ".docx"})
    assert not result.get("error"), result.get("error")
    tables = [el for el in result["elements"] if el.get("type") == "table"]
    assert tables, "no table element extracted"
    return tables[0]["rows"]


def _write(tmp_path, build):
    from docx import Document

    doc = Document()
    build(doc)
    path = tmp_path / "table.docx"
    doc.save(str(path))
    return path


def test_horizontal_merge_text_appears_once(tmp_path):
    def build(doc):
        table = doc.add_table(rows=2, cols=3)
        table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged header"
        table.cell(1, 0).text = "A1"
        table.cell(1, 1).text = "B1"
        table.cell(1, 2).text = "C1"

    rows = _table_rows(_write(tmp_path, build))
    assert rows[0][0] == "Merged header"
    assert rows[0][1] == "", rows
    assert rows[1] == ["A1", "B1", "C1"], rows


def test_vertical_merge_text_appears_once(tmp_path):
    def build(doc):
        table = doc.add_table(rows=3, cols=2)
        table.cell(1, 0).merge(table.cell(2, 0)).text = "Spans two rows"
        table.cell(0, 0).text = "Top"
        table.cell(0, 1).text = "Right 0"
        table.cell(1, 1).text = "Right 1"
        table.cell(2, 1).text = "Right 2"

    rows = _table_rows(_write(tmp_path, build))
    assert rows[1][0] == "Spans two rows", rows
    assert rows[2][0] == "", rows
    assert rows[2][1] == "Right 2"


def test_repeated_values_are_not_merges(tmp_path):
    """The same text in two cells is two cells, not a merge."""
    def build(doc):
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Same"
        table.cell(0, 1).text = "Same"
        table.cell(1, 0).text = "Same"
        table.cell(1, 1).text = "Other"

    rows = _table_rows(_write(tmp_path, build))
    assert rows == [["Same", "Same"], ["Same", "Other"]], rows


def test_plain_table_grid_is_untouched(tmp_path):
    def build(doc):
        table = doc.add_table(rows=2, cols=3)
        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "Value"
        table.cell(0, 2).text = "Notes"
        table.cell(1, 0).text = "Uptime"
        table.cell(1, 1).text = "99.9%"
        table.cell(1, 2).text = "Production"

    rows = _table_rows(_write(tmp_path, build))
    assert rows == [["Metric", "Value", "Notes"],
                    ["Uptime", "99.9%", "Production"]], rows


def test_multi_paragraph_cell_keeps_its_words(tmp_path):
    def build(doc):
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Description"
        table.cell(0, 1).text = "Line one\nLine two"

    rows = _table_rows(_write(tmp_path, build))
    assert rows[0][1] == "Line one\nLine two"
