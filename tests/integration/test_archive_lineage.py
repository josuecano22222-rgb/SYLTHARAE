"""Integration: archive lineage end to end (task 2, §9, PARENT-01).

Proves the chain the brief asks for:

    archive -> child -> nested child -> OCR derivative

Before this, hierarchy_path was accepted by the pipeline and built by the CLI
but appeared nowhere in database/, and parent_path_id's only consumer was dead
code. Verified on a real database, container.zip -> child.pdf produced:

    titles_content: (1,'Main',None,1), (2,'Main',None,2)
    rows with parent lineage: 0

Extracted members are stored while their container is still being processed,
so no parent id exists at that moment. StoragePipeline._link_extracted_children
therefore runs once the container's own row exists, walking extracted_files
recursively.
"""

import datetime
import io
import itertools
import sys
import zipfile
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_SEQ = itertools.count()


def text_png(marker):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(img).text((50, 120), marker,
                             font=ImageFont.truetype(FONT, 44), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def engine_or_skip():
    from core.ocr import get_ocr_engine

    if get_ocr_engine() is None:
        pytest.skip("no OCR engine installed")


@pytest.fixture
def lineage(pg_db, tmp_path, engine_or_skip):
    """Ingest outer.zip -> inner.zip -> scan.png and return the lineage rows."""
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_lin_{next(_SEQ)}"
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

    root = tmp_path / "corpus"
    root.mkdir()
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w", zipfile.ZIP_DEFLATED) as inner:
        inner.writestr("scan.png", text_png("NESTEDLINEAGE 7788"))
    with zipfile.ZipFile(root / "outer.zip", "w", zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("inner.zip", inner_buf.getvalue())

    reader = IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    )
    reader.process_folder(str(root))
    live_containers = reader.progress_ledger.live_containers()
    assert not live_containers, (
        "completed sequential extraction left stale container work: "
        f"{live_containers}"
    )

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,"
                " p.extraction_provenance FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {
                r[1]: {"id": r[0], "parent_path_id": r[2],
                       "hierarchy_path": r[3], "provenance": r[4]}
                for r in cur.fetchall()
            }
    finally:
        conn.close()
    return rows


def test_all_three_levels_were_stored(lineage):
    assert sorted(lineage) == ["inner.zip", "outer.zip", "scan.png"], sorted(lineage)


def test_top_level_archive_has_no_parent(lineage):
    assert lineage["outer.zip"]["parent_path_id"] is None


def test_child_points_at_its_container(lineage):
    assert lineage["inner.zip"]["parent_path_id"] == lineage["outer.zip"]["id"]
    assert lineage["scan.png"]["parent_path_id"] == lineage["inner.zip"]["id"]


def test_hierarchy_path_records_the_full_chain(lineage):
    assert lineage["inner.zip"]["hierarchy_path"] == "outer.zip::inner.zip"
    assert lineage["scan.png"]["hierarchy_path"] == (
        "outer.zip::inner.zip::scan.png"
    )


def test_chain_is_walkable_with_a_recursive_query(pg_db, lineage):
    """Lineage must be queryable, not merely stored."""
    outer_id = lineage["outer.zip"]["id"]
    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "WITH RECURSIVE chain AS ("
                "  SELECT id, file_name, 1 AS lvl FROM paths WHERE id = %s"
                "  UNION ALL"
                "  SELECT p.id, p.file_name, c.lvl + 1 FROM paths p"
                "  JOIN chain c ON p.parent_path_id = c.id"
                ") SELECT lvl, file_name FROM chain ORDER BY lvl",
                (outer_id,),
            )
            walked = cur.fetchall()
    finally:
        conn.close()
    assert walked == [
        (1, "outer.zip"),
        (2, "inner.zip"),
        (3, "scan.png"),
    ], walked


def test_ocr_derivative_keeps_its_provenance(lineage):
    """The leaf of the chain is an OCR derivative and must say so."""
    ocr = lineage["scan.png"]["provenance"]["ocr"]
    assert ocr["derived"] is True
    assert ocr["engine"] in ("tesseract", "rapidocr")
    assert ocr["engine_version"]


def test_ocr_text_from_the_nested_leaf_is_searchable(pg_db, lineage):
    """The whole point: a file three levels deep is still findable."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(
        query="NESTEDLINEAGE", limit=5
    )
    assert total >= 1, "OCR text from the nested leaf was not indexed"
    assert "scan.png" in [r.get("file_name") for r in results]


def test_container_itself_is_not_marked_as_ocr(lineage):
    """An archive must not inherit its child's OCR provenance."""
    for name in ("outer.zip", "inner.zip"):
        prov = lineage[name]["provenance"] or {}
        assert "ocr" not in prov, f"{name} wrongly claims OCR: {prov}"
