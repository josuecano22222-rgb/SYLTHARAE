"""Integration: OCR text must reach the index and be searchable (PHASE 2A).

§2A requires OCR output to become searchable, not merely extracted. Both files
in this corpus have no text of their own:

* ``scanned_receipt.png`` - a bitmap whose only content is rendered pixels.
* ``scanned_doc.pdf``     - a PDF whose single page is a rasterised image. Its
                            native text layer is asserted empty, so any text
                            that surfaces came from OCR.

Before Phase 2A neither produced anything on a host without the tesseract
binary, and a scanned PDF has no text layer to fall back to - the document's
content was lost entirely.

This test is the storage->index->search half of the chain; the unit tests in
``tests/unit/test_ocr_engines.py`` cover the engine itself.
"""

import datetime
import io
import itertools
import os
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
IMG_MARKER = "SCANNEDINVOICEEIGHTYFOUR"
PDF_MARKER = "RECEIPTNUMBERSEVENSEVEN"


def text_image(marker: str):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (1400, 320), "white")
    ImageDraw.Draw(img).text((60, 130), marker,
                             font=ImageFont.truetype(FONT, 48), fill="black")
    return img


def build_scanned_pdf(marker: str) -> bytes:
    """A PDF page that is pure image - no text layer whatsoever."""
    pymupdf = pytest.importorskip("pymupdf")
    png = io.BytesIO()
    text_image(marker).save(png, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 595, 136), stream=png.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def engine_present():
    """These tests prove the OCR chain, so an engine must be installed."""
    from core.ocr import get_ocr_engine

    engine = get_ocr_engine()
    if engine is None:
        pytest.skip(
            "no OCR engine installed; install tesseract or "
            "rapidocr-onnxruntime to exercise the OCR chain"
        )
    return engine


@pytest.fixture
def corpus(pg_db, tmp_path, engine_present):
    root = tmp_path / "corpus"
    root.mkdir()
    text_image(IMG_MARKER).save(root / "scanned_receipt.png")
    (root / "scanned_doc.pdf").write_bytes(build_scanned_pdf(PDF_MARKER))
    return root


@pytest.fixture
def pre_ingest_hashes(corpus):
    """Hash every corpus file before anything touches it."""
    from core.hashing import hash_file

    return {p.name: hash_file(str(p)) for p in sorted(corpus.iterdir())}


@pytest.fixture
def ingested(pg_db, corpus):
    # Unique per test invocation: the database is shared across tests in a
    # session, and identity is (content_hash, source_id, side_id). Reusing the
    # same source/side would make the second ingest resolve to the first
    # test's records via deduplication instead of writing new ones.
    tag = f"{os.getpid()}_{next(_INGEST_SEQ)}"
    source_name, side_name = f"_ocr2a_src_{tag}", f"_ocr2a_side_{tag}"

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

    results = IntegratedFileReader(
        max_workers=2, enable_storage=True,
        storage_source=source_name, storage_side=side_name,
    ).process_folder(str(corpus))
    assert results, "nothing was processed"
    return results, side_name


_INGEST_SEQ = itertools.count()


def test_scanned_pdf_really_has_no_text_layer(corpus):
    """Guard the premise: if this gains a text layer the test proves nothing."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open(str(corpus / "scanned_doc.pdf"))
    try:
        assert doc[0].get_text("text").strip() == ""
    finally:
        doc.close()


@pytest.mark.parametrize(
    "term,expected_file",
    [
        (IMG_MARKER, "scanned_receipt.png"),
        (PDF_MARKER, "scanned_doc.pdf"),
    ],
)
def test_ocr_text_is_searchable(ingested, term, expected_file):
    results, _side = ingested
    from Api.services.search_service import SearchService

    found, total = SearchService.full_text_search(query=term, limit=5)
    assert total >= 1, f"{term} was not found in the index"
    names = [r.get("file_name") for r in found]
    assert expected_file in names, f"{term} matched {names}, expected {expected_file}"


def test_both_files_are_indexed_as_words(ingested, pg_db):
    """OCR text must be tokenised into the index, not just stored as a blob.

    Scoped by side: the database is shared across tests in a session, so an
    unscoped SELECT would return other tests' rows too.
    """
    _results, side_name = ingested
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name, p.file_status, COUNT(wp.word_id) "
                "FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id "
                "JOIN sides s ON s.id = hc.side_id "
                "LEFT JOIN words_hashs wp ON wp.hash_id = (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id) "
                "WHERE s.name = %s GROUP BY p.id ORDER BY p.file_name",
                (side_name,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    assert [r[0] for r in rows] == ["scanned_doc.pdf", "scanned_receipt.png"], rows
    assert all(r[1] == "Read" for r in rows), rows
    assert all(r[2] >= 1 for r in rows), f"OCR text did not reach the word index: {rows}"


def test_reader_reports_ocr_provenance_for_both(ingested):
    """The readers must state how each file's text was derived."""
    results, _side = ingested
    for item in results:
        content = item["Content"]
        pages = content.get("pages")
        if pages:  # PDF
            page = pages[0]
            assert page["method"].startswith("ocr_"), page["method"]
            assert page["ocr_derived"] is True
            engine = page["ocr_engine"]
            version = page["ocr_engine_version"]
        else:  # image
            assert content["ocr_successful"] is True
            assert content["ocr_derived"] is True
            engine = content["ocr_engine"]
            version = content["ocr_engine_version"]
        assert engine in ("tesseract", "rapidocr"), engine
        assert version and version != "unknown", version


def test_source_files_unmodified(pre_ingest_hashes, ingested, corpus):
    """OCR must never overwrite the original file.

    Hashes are captured before ingest, so this compares against a value taken
    independently of the pipeline rather than the pipeline's own record.
    """
    from core.hashing import hash_file

    _results, _side = ingested
    for name, before in pre_ingest_hashes.items():
        assert hash_file(str(corpus / name)) == before, f"{name} was modified"
