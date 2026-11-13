"""Reading comments must not walk the whole sheet a second time.

Measured on `Spreadsheets/testRecordSizeExceeded.xlsx`: after the capped value
pass had already extracted 10,000,085 characters, the comment pass iterated
every row of the sheet again and raised `MemoryError`, which the reader
returned as a total failure - so a file that had been read successfully was
recorded as ERROR and its text was thrown away.

openpyxl already keeps the comments in `worksheet._comments`; walking cells to
find them is redundant work proportional to the sheet, on the one code path
where the sheet is known to be too big. The test makes `iter_rows` explode: a
reader that still walks the grid fails here, one that reads the comment store
does not notice.
"""
import os

import pytest

openpyxl = pytest.importorskip("openpyxl")

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _workbook_with_comment(tmp_path):
    from openpyxl.comments import Comment

    path = tmp_path / "commented.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "value"
    sheet["B2"] = "annotated"
    sheet["B2"].comment = Comment("Check this figure", "Auditor")
    workbook.save(path)
    return path


def test_comments_are_read_without_iterating_the_grid(tmp_path, monkeypatch):
    from openpyxl.worksheet.worksheet import Worksheet

    from reader_file.readers.read_office import OfficeFileReader

    path = _workbook_with_comment(tmp_path)

    original = Worksheet.iter_rows
    calls = {"n": 0}

    def bounded(self, *args, **kwargs):
        # The value pass is allowed to walk the grid once. A second walk is the
        # defect: on a large workbook it is where the extraction died, after
        # the text had already been collected.
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("iter_rows must not be used to find comments")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Worksheet, "iter_rows", bounded)
    result = OfficeFileReader().read_file({"path": str(path), "extension": ".xlsx"})
    content = result.get("content", result) if isinstance(result, dict) else {}

    assert not content.get("error"), content.get("error")
    sheets = content.get("sheets") or {}
    comments = [note for sheet in sheets.values() for note in (sheet.get("comments") or [])]
    assert [note["text"] for note in comments] == ["Check this figure"]
    assert comments[0]["author"] == "Auditor"
    assert "Check this figure" in (content.get("forensic_text") or "")


def test_the_corpus_comment_workbook_still_yields_its_comments():
    from reader_file.readers.read_office import OfficeFileReader

    path = os.path.join(CORPUS, "Spreadsheets", "testComment.xlsx")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    result = OfficeFileReader().read_file({"path": path, "extension": ".xlsx"})
    content = result.get("content", result) if isinstance(result, dict) else {}
    assert content.get("comment_count", 0) > 0
