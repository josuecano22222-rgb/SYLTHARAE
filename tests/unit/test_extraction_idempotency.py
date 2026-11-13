"""Reading the same file twice must produce the same text, byte for byte.

Phase 6 asks for a double ingest. The database half cannot run here (no
PostgreSQL server in this image, recorded as an environment gap), but the half
that decides what would be written - reader output through
`StoragePipeline._extract_text_from_content` - can, and it is where
non-determinism would come from: dict iteration order, set ordering in a
reader, a timestamp or a temporary path leaking into the text.

A file whose stored text changes between two reads is a file whose index,
hashes and duplicates are unreliable, so this is a correctness test and not a
smoke test.
"""
import hashlib
import os

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")

#: One file per family whose extraction has the most moving parts.
SAMPLE = [
    "Documents/testWORD.docx",
    "Spreadsheets/testEXCEL.xlsx",
    "Presentations/testPPT.pptx",
    "Programming/big-preamble.html",
    "Text/russian.cp866.txt",
    "Documents/testPDF_childAttachments.pdf",
    "Archives/droste.zip",
]


def stored_text(relative_path):
    from pipeline.storage_pipeline import StoragePipeline
    from reader_file.services.file_reader_service import FileReaderService

    path = os.path.join(CORPUS, relative_path)
    if not os.path.exists(path):
        pytest.skip(f"corpus file missing: {relative_path}")
    result = FileReaderService().read_file(
        {"path": path, "extension": os.path.splitext(path)[1]}) or {}
    content = result.get("content") if isinstance(result.get("content"), dict) else result
    return StoragePipeline._extract_text_from_content.__get__(
        object.__new__(StoragePipeline))(content) or ""


@pytest.mark.parametrize("relative_path", SAMPLE)
def test_two_reads_produce_identical_text(relative_path):
    first = stored_text(relative_path)
    second = stored_text(relative_path)
    assert hashlib.sha256(first.encode()).hexdigest() == \
        hashlib.sha256(second.encode()).hexdigest(), (
            f"{relative_path}: stored text differs between two reads "
            f"({len(first)} vs {len(second)} chars)")
