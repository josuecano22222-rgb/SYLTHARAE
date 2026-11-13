"""Legacy Word (.doc) and Excel (.xls/.xlsb) reader completeness."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_office import OfficeFileReader  # noqa: E402
from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


def test_doc_fallback_promotes_elements_for_storage():
    reader = OfficeFileReader()
    raw = {
        "filepath": "/x/legacy.doc",
        "text": "Alpha paragraph.\n\nBeta paragraph.",
        "paragraphs": ["Alpha paragraph.", "Beta paragraph."],
        "method": "antiword",
    }
    promoted = reader._promote_doc_text_to_elements(raw)
    assert promoted["elements"]
    assert len(promoted["elements"]) == 2
    assert promoted["elements"][0]["type"] == "paragraph"
    assert promoted["elements"][0]["position"] == 0

    pipe = StoragePipeline.__new__(StoragePipeline)
    indexed = StoragePipeline._extract_text_from_content(pipe, promoted)
    assert "Alpha paragraph" in indexed
    assert "Beta paragraph" in indexed


def test_xls_full_grid_matches_excel_storage_shape(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    path = tmp_path / "book.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Data")
    sheet.write(0, 0, "region")
    sheet.write(0, 1, "units")
    sheet.write(1, 0, "EMEA")
    sheet.write(1, 1, 42)
    book.save(str(path))

    reader = OfficeFileReader()
    result = reader.read_file({"path": str(path), "effective_extension": ".xls"})
    assert result and not result.get("error"), result
    data_sheet = result["sheets"]["Data"]
    assert data_sheet["max_row"] == 2
    assert data_sheet["max_column"] == 2
    assert data_sheet["data"][1][0] == "EMEA"

    pipe = StoragePipeline.__new__(StoragePipeline)
    text = StoragePipeline._extract_text_from_content(pipe, result)
    assert "Sheet: Data" in text
    assert "EMEA" in text
    assert "42" in text


def test_xlsx_and_xls_normalize_to_same_sheet_keys(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    xlwt = pytest.importorskip("xlwt")
    xlsx_path = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "T1"
    ws.append(["one", "two"])
    wb.save(xlsx_path)
    wb.close()

    xls_path = tmp_path / "b.xls"
    book = xlwt.Workbook()
    sh = book.add_sheet("T1")
    sh.write(0, 0, "one")
    sh.write(0, 1, "two")
    book.save(str(xls_path))

    reader = OfficeFileReader()
    xlsx = reader.read_file({"path": str(xlsx_path), "effective_extension": ".xlsx"})
    xls = reader.read_file({"path": str(xls_path), "effective_extension": ".xls"})
    for result in (xlsx, xls):
        sheet = result["sheets"]["T1"]
        assert sheet["max_row"] >= 1
        assert sheet["max_column"] >= 2
        assert sheet["data"][0][0] == "one"


def test_xlsb_reader_is_wired():
    reader = OfficeFileReader()
    assert callable(getattr(reader, "read_xlsb_file", None))
