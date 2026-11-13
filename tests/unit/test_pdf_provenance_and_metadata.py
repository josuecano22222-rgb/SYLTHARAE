"""Round 18, task 1: what a PDF's revision block actually is, and where it goes.

Round 17 recorded F-26 as "PDF revision names and descriptions are text and are
dropped at the storage boundary". That claim was never checked against the
reader's output, and it is wrong: the PDF reader's ``revisions`` key is
``count_incremental_updates()``, a dict of *counts*
(``incremental_updates``, ``eof_markers``, ``scan_bytes``, ``scan_truncated``).
PDF has no named revisions to read; the text that "revision names" would have
described does not exist in the file.

Checking it did find a real loss next door: the PDF reader reports metadata the
storage text builder never reads - ``keywords``, ``creationDate``, ``modDate``,
``format`` and ``trapped``. Keywords are words an analyst searches for, and the
dates are content. Those are the text loss; the revision counts are structure.

So the fix has two halves, and this file pins both:

* the revision record is persisted as structure in
  ``extraction_provenance["forensics"]["revisions"]``, because a count is not a
  sentence and putting "3" in the body would be noise;
* the unread metadata fields reach the stored text, because they are text.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from pipeline.storage_pipeline import StoragePipeline  # noqa: E402

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _pipeline():
    return object.__new__(StoragePipeline)


def stored_text(content):
    return _pipeline()._extract_text_from_content(content) or ""


def provenance(content):
    return _pipeline()._build_extraction_provenance(content) or {}


# --------------------------------------------------------------------------- #
# The correction
# --------------------------------------------------------------------------- #
def test_the_pdf_revision_block_is_counts_not_names():
    """The premise of F-26, measured against the corpus file."""
    path = os.path.join(CORPUS, "Documents", "testPDF_incrementalUpdates.pdf")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    from reader_file.readers.read_pdf import PDFFileReader

    result = PDFFileReader().read_file({"path": path, "extension": ".pdf"})
    content = result.get("content", result) if isinstance(result, dict) else {}
    revisions = content.get("revisions") or {}

    assert revisions, "the reader reports no revision block for this file"
    assert set(revisions) <= {"incremental_updates", "eof_markers", "scan_bytes",
                              "scan_truncated"}, set(revisions)
    assert isinstance(revisions["incremental_updates"], int)
    # This is the point: there is no name and no description to store.
    for value in revisions.values():
        assert not isinstance(value, str) or value.isdigit() or len(value) < 40


# --------------------------------------------------------------------------- #
# The fix, part 1: structure goes to provenance
# --------------------------------------------------------------------------- #
def test_pdf_revisions_are_persisted_as_structure():
    content = {"pages": [{"page_number": 1, "text": "page one"}],
               "num_pages": 1,
               "revisions": {"incremental_updates": 3, "eof_markers": 4,
                             "scan_bytes": 64872, "scan_truncated": False}}
    forensics = provenance(content).get("forensics") or {}
    assert forensics.get("revisions") == {
        "incremental_updates": 3, "eof_markers": 4,
        "scan_bytes": 64872, "scan_truncated": False}


def test_a_pdf_without_a_revision_block_gets_no_empty_forensics_key():
    content = {"pages": [{"page_number": 1, "text": "page one"}], "num_pages": 1}
    assert "forensics" not in provenance(content)


def test_a_truncated_eof_scan_is_visible_as_such():
    """A scan that stopped early must not read as a complete count."""
    content = {"pages": [{"page_number": 1, "text": "x"}],
               "revisions": {"incremental_updates": 2, "eof_markers": 3,
                             "scan_bytes": 1 << 20, "scan_truncated": True}}
    forensics = provenance(content).get("forensics") or {}
    assert forensics["revisions"]["scan_truncated"] is True


# --------------------------------------------------------------------------- #
# The fix, part 2: the metadata that is text reaches the text
# --------------------------------------------------------------------------- #
def test_pdf_metadata_fields_the_builder_ignored_are_stored():
    content = {
        "pages": [{"page_number": 1, "text": "body"}],
        "num_pages": 1,
        "metadata": {
            "title": "Quarterly review",
            "keywords": "acquisition, escrow, closing",
            "creationDate": "D:20230327163701Z",
            "modDate": "D:20230410104217-06'00'",
            "format": "PDF 1.3",
            "trapped": "False",
        },
    }
    text = stored_text(content)
    assert "acquisition, escrow, closing" in text
    assert "2023" in text and "1637" in text or "D:20230327163701Z" in text


def test_metadata_fields_are_labelled_so_they_are_not_mistaken_for_body_text():
    content = {
        "pages": [{"page_number": 1, "text": "body"}],
        "num_pages": 1,
        "metadata": {"keywords": "one, two", "modDate": "D:20230410104217Z"},
    }
    text = stored_text(content)
    for line in text.splitlines():
        if "one, two" in line:
            assert line.lower().startswith("keywords"), line
            break
    else:  # pragma: no cover - guarded by the test above
        raise AssertionError("keywords line missing")


def test_absent_metadata_keys_add_no_empty_lines():
    content = {"pages": [{"page_number": 1, "text": "body"}], "num_pages": 1,
               "metadata": {"title": "", "keywords": "", "trapped": ""}}
    text = stored_text(content)
    assert "Keywords:" not in text
    assert "Trapped:" not in text


def test_an_empty_metadata_list_value_does_not_crash_the_builder():
    content = {"pages": [{"page_number": 1, "text": "body"}], "num_pages": 1,
               "metadata": {"keywords": ["a", "b"], "format": None}}
    text = stored_text(content)
    assert "a" in text
