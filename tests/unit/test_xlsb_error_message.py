"""The .xlsb failure message must name the step that actually failed.

pyxlsb is installed on the machine that reported ``testEXCEL.xlsb`` as failed,
but the old message still said "Install pyxlsb", which sent the user the wrong
way. The real pyxlsb error and the missing LibreOffice step are now reported.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from reader_file.readers.read_office import OfficeFileReader

ROOT = Path(__file__).resolve().parents[2]
BAD_XLSB = ROOT / "test-doc" / "testEXCEL.xlsb"


@pytest.mark.skipif(not BAD_XLSB.exists(), reason="sample workbook not in checkout")
def test_pyxlsb_failure_is_reported_with_its_cause(monkeypatch):
    monkeypatch.setattr(OfficeFileReader, "_convert_with_libreoffice",
                        lambda self, path, ext: None)
    message = OfficeFileReader().read_xlsb_file(str(BAD_XLSB))["error"]
    assert "pyxlsb failed" in message
    assert "LibreOffice" in message
    assert "Install pyxlsb" not in message


def test_missing_pyxlsb_still_says_to_install_it(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pyxlsb", None)  # makes "import pyxlsb" fail
    monkeypatch.setattr(OfficeFileReader, "_convert_with_libreoffice",
                        lambda self, path, ext: None)
    target = tmp_path / "x.xlsb"
    target.write_bytes(b"not a workbook")
    message = OfficeFileReader().read_xlsb_file(str(target))["error"]
    assert "Install pyxlsb" in message
