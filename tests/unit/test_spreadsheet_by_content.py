"""Spreadsheets are opened by content, not by the name they arrived with.

A real corpus contained an xlsx workbook named ``attachment_00520.docx`` (the
name was inherited from the container it was extracted out of). The router had
already identified the bytes as xlsx, but openpyxl validates the *path* by
extension before it looks at the content:

    openpyxl does not support .docx file format, please check you can open it
    with Excel first. Supported formats are: .xlsx,.xlsm,.xltx,.xltm

so the file was stored with no text at all, and previewing it showed an error.
Both the ingestion reader and the preview service now open such a file as a
stream (the extension check does not apply to file-like objects). This was fixed
twice, in two places, and neither had a test; these tests cover the shared rule
and both callers so a third copy cannot appear unnoticed.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

openpyxl = pytest.importorskip("openpyxl")

from core.file_utils import (  # noqa: E402
    OPENPYXL_PATH_SUFFIXES,
    load_spreadsheet_workbook,
)


@pytest.fixture()
def workbook_bytes(tmp_path):
    """A real workbook, and its bytes, so we can rename it freely."""
    path = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["region", "units"])
    ws.append(["EMEA", 12])
    wb.save(path)
    wb.close()
    return path.read_bytes()


def _write(tmp_path, name, data):
    target = tmp_path / name
    target.write_bytes(data)
    return target


def test_openpyxl_really_does_refuse_a_misnamed_path(tmp_path, workbook_bytes):
    """If openpyxl ever stops refusing, this rule can be simplified - not silently wrong."""
    misnamed = _write(tmp_path, "attachment_00520.docx", workbook_bytes)
    with pytest.raises(Exception) as exc:
        openpyxl.load_workbook(str(misnamed))
    assert "does not support" in str(exc.value)


def test_helper_opens_a_misnamed_workbook_by_content(tmp_path, workbook_bytes):
    misnamed = _write(tmp_path, "attachment_00520.docx", workbook_bytes)
    wb = load_spreadsheet_workbook(str(misnamed), data_only=True)
    try:
        rows = list(wb[wb.sheetnames[0]].iter_rows(values_only=True))
        assert rows[0] == ("region", "units")
        assert rows[1] == ("EMEA", 12)
    finally:
        wb.close()


def test_helper_keeps_the_path_for_supported_suffixes(tmp_path, workbook_bytes):
    """The normal case must not change (files keep the exact code path they had)."""
    for suffix in OPENPYXL_PATH_SUFFIXES:
        named = _write(tmp_path, f"book{suffix}", workbook_bytes)
        wb = load_spreadsheet_workbook(str(named), read_only=True)
        try:
            assert wb.sheetnames
        finally:
            wb.close()


def test_declared_suffixes_match_openpyxl_reported_set(tmp_path, workbook_bytes):
    """The tuple is openpyxl's rule; it is asserted against openpyxl itself."""
    misnamed = _write(tmp_path, "book.doc", workbook_bytes)
    with pytest.raises(Exception) as exc:
        openpyxl.load_workbook(str(misnamed))
    message = str(exc.value)
    for suffix in OPENPYXL_PATH_SUFFIXES:
        assert suffix in message, (suffix, message)


def test_office_reader_extracts_a_misnamed_workbook(tmp_path, workbook_bytes):
    """The ingestion path: content wins over the name."""
    from reader_file.readers.read_office import OfficeFileReader

    misnamed = _write(tmp_path, "attachment_00520.docx", workbook_bytes)
    result = OfficeFileReader().read_file(
        {"path": str(misnamed), "effective_extension": ".xlsx"}
    )
    assert result is not None
    assert not result.get("error"), result.get("error")
    sheet = result["sheets"]["Sheet"]
    assert sheet["data"][0] == ["region", "units"]
    assert sheet["data"][1] == ["EMEA", 12]

    # And the pipeline renders that into the text it stores and indexes.
    from pipeline.storage_pipeline import StoragePipeline

    text = StoragePipeline._extract_text_from_content(
        StoragePipeline.__new__(StoragePipeline), result)
    assert "EMEA" in text, text[:300]


def test_preview_service_previews_a_misnamed_workbook(tmp_path, workbook_bytes):
    from Api.services.file_preview import FilePreviewService

    misnamed = _write(tmp_path, "attachment_00520.docx", workbook_bytes)
    preview = FilePreviewService._preview_xlsx(str(misnamed))
    assert preview.get("preview_type") != "error", preview
    assert any("EMEA" in cell for row in preview["data"] for cell in row)
