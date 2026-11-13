"""Legacy .ppt OLE text path (LibreOffice-free)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_office import OfficeFileReader

FIXTURE = PROJECT_ROOT / "test-doc" / "testPPT.ppt"


@pytest.mark.skipif(not FIXTURE.is_file(), reason="test-doc/testPPT.ppt missing")
def test_legacy_ppt_ole_extraction_finds_slide_text():
    result = OfficeFileReader().read_ppt_file(str(FIXTURE))
    assert isinstance(result, dict)
    assert not result.get("error"), result.get("error")
    texts = []
    for slide in result.get("slides") or []:
        texts.extend(slide.get("texts") or [])
    joined = "\n".join(texts)
    assert "Sample Powerpoint Slide" in joined
