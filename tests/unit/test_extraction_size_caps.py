"""A file may be large; the text stored for it may not be unbounded.

``test-doc/Spreadsheets/testRecordSizeExceeded.xlsx`` is 12 MB on disk and
200 000 rows x 15 columns in memory: three million cells, which the reader
materialised whole and the pipeline joined into a **181 million character**
string for one artifact. Nothing in the format says where to stop, so the
ceiling has to be explicit - and, because a cut is a loss, it has to be
recorded in the result rather than applied quietly.

Two layers, deliberately:
* the reader stops collecting cells at a cap and marks the sheet ``truncated``
  with the number kept and the number the sheet holds;
* the pipeline caps the stored text and appends a marker naming how much was
  cut, so no consumer can mistake a truncated body for a complete one.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

openpyxl = pytest.importorskip("openpyxl", reason="openpyxl not installed")

from reader_file.readers import read_office  # noqa: E402
from reader_file.readers.read_office import OfficeFileReader  # noqa: E402
from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


def _content(result):
    if isinstance(result.get("content"), dict) and "elements" not in result:
        return result["content"]
    return result


def _big_sheet(tmp_path, rows=200, columns=20):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            sheet.cell(row=row, column=column, value=f"r{row}c{column}")
    path = tmp_path / "big.xlsx"
    workbook.save(str(path))
    return path


def test_reader_stops_at_the_cell_cap_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(read_office, "MAX_SHEET_CELLS", 100)
    monkeypatch.setattr(read_office, "MAX_WORKBOOK_CELLS", 1000)
    path = _big_sheet(tmp_path)

    content = _content(OfficeFileReader().read_file(
        {"path": str(path), "extension": ".xlsx"}))
    sheet = content["sheets"]["Sheet"]
    assert sheet["truncated"] is True
    assert sheet["cells_read"] <= 100
    assert sheet["rows_read"] < 200
    assert sheet["cells_available"] == 200 * 20
    assert content.get("truncated") is True


def test_a_small_sheet_is_not_truncated(tmp_path):
    path = _big_sheet(tmp_path, rows=5, columns=4)
    content = _content(OfficeFileReader().read_file(
        {"path": str(path), "extension": ".xlsx"}))
    sheet = content["sheets"]["Sheet"]
    assert not sheet.get("truncated")
    assert len(sheet["data"]) == 5
    assert "r1c1" in sheet["data"][0]


def test_pipeline_caps_stored_text_with_an_explicit_marker(monkeypatch):
    pipeline = object.__new__(StoragePipeline)
    monkeypatch.setattr(StoragePipeline, "MAX_CONTENT_TEXT_CHARS", 100)

    content = {"text": "A" * 500}
    text = pipeline._extract_text_from_content(content)
    assert text.startswith("A" * 100)
    assert "TRUNCATED" in text
    assert "400 characters" in text


def test_pipeline_leaves_text_under_the_cap_untouched(monkeypatch):
    pipeline = object.__new__(StoragePipeline)
    monkeypatch.setattr(StoragePipeline, "MAX_CONTENT_TEXT_CHARS", 1000)
    text = pipeline._extract_text_from_content({"text": "short body"})
    assert text.strip() == "short body"
    assert "TRUNCATED" not in text
