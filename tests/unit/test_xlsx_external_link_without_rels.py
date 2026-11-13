"""A workbook whose DDE external link has no ``_rels`` part must still be read.

``test-doc/testDdeLink.xlsx`` declares ``xl/externalLinks/externalLink1.xml``
but the package has no ``xl/externalLinks/_rels/externalLink1.xml.rels``.
openpyxl used to raise ``There is no item named ... in the archive`` while
loading external links, so the file was stored Unread with no text at all.
Its cell values are intact, so extraction must succeed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import file_utils
from reader_file.readers.read_office import OfficeFileReader

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "test-doc" / "testDdeLink.xlsx"


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample workbook not in checkout")
def test_dde_link_workbook_without_link_rels_is_read():
    result = OfficeFileReader().read_xlsx_file(str(SAMPLE))
    assert not result.get("error"), result.get("error")
    assert result["total_sheets"] >= 1
    assert "Sheet1" in result["sheet_names"]


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample workbook not in checkout")
def test_default_loader_still_fails_on_the_same_package():
    # The fix is scoped to extraction: the default keep_links=True path is unchanged,
    # so callers that do need link parts still see the original behaviour.
    with pytest.raises(KeyError):
        wb = file_utils.load_spreadsheet_workbook(str(SAMPLE), data_only=True)
        wb.close()
