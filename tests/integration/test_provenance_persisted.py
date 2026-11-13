"""Integration: extraction provenance reaches the database (task 1, §11).

Migration 0007 added paths.extraction_provenance, but a column that nothing
writes is not provenance. These tests run the real pipeline against a real
PostgreSQL instance and read the column back, proving the chain

    reader -> StoragePipeline._build_extraction_provenance
           -> ContentDBService.process_full_document
           -> PathsRepository.insert_info_paths
           -> paths.extraction_provenance

The point of the column is that recognised text must be distinguishable from
authored text, so the assertions focus on ocr.derived and the engine identity
rather than merely on the column being non-null.
"""

import datetime
import io
import itertools
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_SEQ = itertools.count()


def text_image(marker):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(img).text((50, 120), marker,
                             font=ImageFont.truetype(FONT, 44), fill="black")
    return img


def scanned_pdf(marker):
    pymupdf = pytest.importorskip("pymupdf")
    png = io.BytesIO()
    text_image(marker).save(png, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 595, 150), stream=png.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def engine_or_skip():
    from core.ocr import get_ocr_engine

    if get_ocr_engine() is None:
        pytest.skip("no OCR engine installed")


@pytest.fixture
def ingested(pg_db, tmp_path, engine_or_skip):
    """Ingest an OCR'd image, a scanned PDF and a plain CSV."""
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_prov_{next(_SEQ)}"
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5, %s)"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
            (f"{tag}_side", today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
            " DO UPDATE SET name = EXCLUDED.name",
            (f"{tag}_src", today),
        )
    conn.commit()
    conn.close()

    root = tmp_path / "corpus"
    root.mkdir()
    text_image("PROVENANCEMARKER 4417").save(root / "scan.png")
    (root / "scanned.pdf").write_bytes(scanned_pdf("SCANPAGE 9931"))
    (root / "plain.csv").write_text("region,units\nEMEA,12\nAPAC,7\n", encoding="utf-8")

    IntegratedFileReader(
        max_workers=2, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))
    return tag


@pytest.fixture
def provenance(pg_db, ingested):
    """{file_name: extraction_provenance} for the ingested corpus."""
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name, p.extraction_provenance FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{ingested}_side",),
            )
            return dict(cur.fetchall())
    finally:
        conn.close()


def test_all_three_files_were_stored(provenance):
    assert sorted(provenance) == ["plain.csv", "scan.png", "scanned.pdf"]


def test_ocr_image_records_engine_and_derived(provenance):
    ocr = provenance["scan.png"]["ocr"]
    assert ocr["derived"] is True, "recognised text must be marked derived"
    assert ocr["attempted"] is True
    assert ocr["successful"] is True
    assert ocr["engine"] in ("tesseract", "rapidocr")
    assert ocr["engine_version"]
    assert 0.0 < ocr["confidence"] <= 1.0
    assert ocr["input_variant"] in ("preprocessed", "original")


def test_scanned_pdf_records_page_level_provenance(provenance):
    ocr = provenance["scanned.pdf"]["ocr"]
    assert ocr["derived"] is True
    assert ocr["ocr_pages"] == 1
    assert ocr["total_pages"] == 1
    assert ocr["engines"] == ["rapidocr"] or ocr["engines"] == ["tesseract"]


def test_non_ocr_file_does_not_claim_ocr(provenance):
    """A CSV must not carry an ocr block - that would be a false claim."""
    assert "ocr" not in provenance["plain.csv"]
    # But it still records how it was identified.
    assert provenance["plain.csv"]["detection"]["declared_extension"] == ".csv"


def test_detection_provenance_is_recorded(provenance):
    for name in ("scan.png", "scanned.pdf"):
        detection = provenance[name]["detection"]
        assert detection["detection_method"] == "magic-bytes", detection
        assert detection["extension_mismatch"] is False


def test_provenance_is_queryable_not_just_stored(pg_db, ingested):
    """§12: it must be usable by search and display, not only written.

    Scoped by side - the database is shared across tests in a session, so an
    unscoped query returns other tests' rows as well.
    """
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id"
                " WHERE s.name = %s"
                " AND p.extraction_provenance->'ocr'->>'derived' = 'true'"
                " ORDER BY p.file_name",
                (f"{ingested}_side",),
            )
            derived = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    assert derived == ["scan.png", "scanned.pdf"]


def test_ocr_text_and_its_provenance_agree(pg_db, ingested):
    """The searchable text and the provenance must describe the same file."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(
        query="PROVENANCEMARKER", limit=5
    )
    assert total >= 1, "OCR text was not searchable"
    assert "scan.png" in [r.get("file_name") for r in results]


def test_provenance_row_matches_the_file_it_describes(pg_db, ingested):
    """The provenance must belong to this file, not any file of that name.

    Scoped by side: the database is shared across tests in a session and
    several of them ingest a file called scan.png, so an unscoped lookup by
    file_name is ambiguous and can return another test's row.
    """
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.extraction_provenance->'ocr'->>'derived',"
                " p.extraction_provenance->'ocr'->>'engine'"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id"
                " WHERE s.name = %s AND p.file_name = 'scan.png'",
                (f"{ingested}_side",),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    assert rows, "no scan.png row for this corpus"
    assert all(r[0] == "true" for r in rows), rows
    assert all(r[1] in ("tesseract", "rapidocr") for r in rows), rows
