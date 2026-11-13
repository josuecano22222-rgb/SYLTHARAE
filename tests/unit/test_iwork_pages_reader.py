"""Apple Pages/Numbers must not route as plain ZIP archives."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.services.file_reader_service import FileReaderService


PAGES_FIXTURE = PROJECT_ROOT / "test-doc" / "testPagesComments.pages"
NUMBERS_FIXTURE = PROJECT_ROOT / "test-doc" / "testNumbers.numbers"


@pytest.mark.skipif(not PAGES_FIXTURE.is_file(), reason="test-doc fixture missing")
def test_pages_routes_to_office_not_archive():
    service = FileReaderService()
    decision = service.resolve_type_for_file(str(PAGES_FIXTURE))
    assert decision["effective_extension"] == ".pages"
    reader = service.get_reader_for_extension(".pages")
    assert reader.__class__.__name__ == "OfficeFileReader"


@pytest.mark.skipif(not PAGES_FIXTURE.is_file(), reason="test-doc fixture missing")
def test_pages_index_xml_yields_storage_text():
    service = FileReaderService()
    result = service.read_file(
        {"path": str(PAGES_FIXTURE), "extension": "pages"}
    )
    assert isinstance(result, dict)
    assert not result.get("error")
    body = str(result.get("text") or "")
    assert len(body) > 500
    assert "Lorem ipsum" in body
    assert result.get("extraction_method") == "index.xml"


@pytest.mark.skipif(not NUMBERS_FIXTURE.is_file(), reason="test-doc fixture missing")
def test_numbers_routes_to_office_reader():
    service = FileReaderService()
    decision = service.resolve_type_for_file(str(NUMBERS_FIXTURE))
    assert decision["effective_extension"] == ".numbers"
    reader = service.get_reader_for_extension(".numbers")
    assert reader.__class__.__name__ == "OfficeFileReader"
