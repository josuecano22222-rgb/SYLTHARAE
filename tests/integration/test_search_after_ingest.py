"""Integration: extracted content must be searchable (SEARCH-01).

Defect: the relevance-scoring CASE in ``Api/services/search_service.py`` (and
the copy in ``Api/utils/utils.py``) embedded literal ILIKE wildcards directly
in the SQL text:

    WHEN p.file_name ILIKE '%' || term || '%' THEN 2.0

The query is executed WITH bound parameters, so psycopg2 runs the string
through %-interpolation and every literal '%' must be written '%%'. Verified
in isolation:

    name ILIKE '%'  || %s || '%'   -> IndexError: tuple index out of range
    name ILIKE '%%' || %s || '%%'  -> ok

``full_text_search`` caught the IndexError and returned an empty result set,
so EVERY full-text search silently returned zero results even though content
was stored and indexed. Measured on a real database before the fix: 5 files
ingested, 48 words and 76 words_hashs rows written, and

    QUARTERLYLEDGER -> total=0    RENAMEDREPORT -> total=0
    ARCHIVECHILD    -> total=0    EMEA          -> total=0

After the fix each query returns exactly the file that contains it.
"""

import datetime
import os
import sys
import zipfile
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

# 1x1 PNG, deliberately misnamed .txt to exercise content-based identification.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6260000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def build_pdf(text: str) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def corpus(pg_db, tmp_path):
    """A corpus that exercises text PDF, misnamed PDF, CSV and a nested archive."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "honest.pdf").write_bytes(build_pdf("Marker QUARTERLYLEDGER inside an honest pdf"))
    (root / "misnamed.docx").write_bytes(build_pdf("Marker RENAMEDREPORT inside a pdf named docx"))
    (root / "picture.txt").write_bytes(PNG_BYTES)
    (root / "sales.csv").write_text(
        "region,product,units\nEMEA,Widget,12\nAPAC,Gadget,7\n", encoding="utf-8"
    )
    # Extensionless ZIP containing a PDF: exercises container recursion.
    with zipfile.ZipFile(root / "container", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("nested.pdf", build_pdf("Marker ARCHIVECHILD inside a zip"))
    return root


@pytest.fixture
def ingested(pg_db, corpus):
    """Ingest the corpus through the real pipeline and return (source, side)."""
    tag = os.getpid()
    source_name, side_name = f"_search01_src_{tag}", f"_search01_side_{tag}"

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
            (side_name, today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 'test', 0.5, 'test', %s) ON CONFLICT (name) DO NOTHING",
            (source_name, today),
        )
    conn.commit()
    conn.close()

    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        max_workers=2, enable_storage=True,
        storage_source=source_name, storage_side=side_name,
    )
    results = reader.process_folder(str(corpus))
    assert results, "nothing was processed"
    return source_name, side_name


@pytest.mark.parametrize(
    "term,expected_file",
    [
        ("QUARTERLYLEDGER", "honest.pdf"),
        ("RENAMEDREPORT", "misnamed.docx"),
        ("ARCHIVECHILD", "nested.pdf"),
        ("EMEA", "sales.csv"),
    ],
)
def test_extracted_content_is_searchable(ingested, term, expected_file):
    """The stored content must be findable, and map to the right file."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=term, limit=10)
    assert total >= 1, f"{term!r} returned no results - content is stored but unsearchable"
    names = {r["file_name"] for r in results}
    assert expected_file in names, f"{term!r} matched {names}, expected {expected_file}"


def test_filename_is_searchable(ingested):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query="honest.pdf", limit=10)
    assert total >= 1
    assert "honest.pdf" in {r["file_name"] for r in results}


def test_nested_archive_child_was_stored(ingested, pg_db):
    """The ZIP's child PDF must exist as its own record (container ancestry)."""
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM paths WHERE file_name = %s", ("nested.pdf",))
            assert cur.fetchone()[0] >= 1
    finally:
        conn.close()


def test_relevance_ranking_query_executes_without_error(ingested):
    """Guard the exact psycopg2 %-interpolation failure that caused SEARCH-01."""
    from Api.services.search_service import SearchService

    # sort_by='relevance' is the path that builds the literal-% CASE expression.
    results, total = SearchService.full_text_search(
        query="Widget", sort_by="relevance", limit=5
    )
    assert isinstance(results, list)
    assert total >= 1
