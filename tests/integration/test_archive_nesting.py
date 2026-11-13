"""Integration: nested archives, documents, images and OCR keep full lineage.

Builds the exact chain the brief names and proves it is reconstructible from
the database alone, using stored ids and not filenames or filesystem paths:

    outer.zip -> inner.zip -> report.pdf  (scanned, so OCR-derived text)
                          \\-> scan.png    (rasterised, so OCR-derived text)
                          \\-> notes.txt   (plain document, direct text)
             \\-> duplicate_a.txt, duplicate_b.txt (identical bytes)

Every level is asserted through the real pipeline into a real database, then
read back by recursive query and through the File Details API.
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

PDF_MARKER = "NESTEDREPORT 8842"
PNG_MARKER = "NESTEDIMAGE 5517"
TXT_MARKER = "NESTEDTEXT 2231"
DUP_MARKER = "DUPLICATECHILD 6690"


def rasterise(text):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(img).text((50, 120), text,
                             font=ImageFont.truetype(FONT, 44), fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def scanned_pdf(text):
    """A PDF whose only text layer is a rasterised image, so OCR is required."""
    pymupdf = pytest.importorskip("pymupdf")
    png = rasterise(text)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=png)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="module")
def engine_or_skip():
    from core.ocr import get_ocr_engine

    if get_ocr_engine() is None:
        pytest.skip("no OCR engine installed")


@pytest.fixture(scope="module")
def corpus(pg_db, tmp_path_factory, engine_or_skip):
    """Ingest the nested corpus; return every stored row keyed by filename."""
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_nest_{next(_SEQ)}"
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

    root = tmp_path_factory.mktemp("nested_corpus")

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.pdf", scanned_pdf(PDF_MARKER))
        z.writestr("scan.png", rasterise(PNG_MARKER))
        z.writestr("notes.txt", f"Marker {TXT_MARKER} plain document inside two archives\n")
    with zipfile.ZipFile(root / "outer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("inner.zip", inner.getvalue())
        # Identical bytes under two names: exercises duplicate children.
        z.writestr("duplicate_a.txt", f"Marker {DUP_MARKER}\n")
        z.writestr("duplicate_b.txt", f"Marker {DUP_MARKER}\n")

    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,"
                " p.processing_status, p.extraction_provenance"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {}
            for pid, name, parent, hier, status, prov in cur.fetchall():
                rows.setdefault(name, []).append({
                    "id": pid, "parent_path_id": parent, "hierarchy_path": hier,
                    "processing_status": status, "provenance": prov or {},
                })
    finally:
        conn.close()
    return {"tag": tag, "rows": rows, "conn_info": pg_db}


def one(corpus, name):
    matches = corpus["rows"].get(name)
    assert matches, f"{name} was not stored; have {sorted(corpus['rows'])}"
    assert len(matches) == 1, f"{name} stored {len(matches)} times"
    return matches[0]


# ------------------------------------------------------------ structure
def test_every_object_in_the_chain_was_stored(corpus):
    stored = sorted(corpus["rows"])
    for expected in ("outer.zip", "inner.zip", "report.pdf", "scan.png", "notes.txt"):
        assert expected in stored, (expected, stored)


def test_outer_archive_is_the_root(corpus):
    assert one(corpus, "outer.zip")["parent_path_id"] is None


def test_each_level_points_at_its_real_parent(corpus):
    outer, inner = one(corpus, "outer.zip"), one(corpus, "inner.zip")
    assert inner["parent_path_id"] == outer["id"]
    for child in ("report.pdf", "scan.png", "notes.txt"):
        assert one(corpus, child)["parent_path_id"] == inner["id"], child


def test_hierarchy_path_records_the_whole_chain(corpus):
    assert one(corpus, "report.pdf")["hierarchy_path"] == (
        "outer.zip::inner.zip::report.pdf"
    )
    assert one(corpus, "scan.png")["hierarchy_path"] == (
        "outer.zip::inner.zip::scan.png"
    )


def test_chain_is_reconstructible_by_recursive_query(corpus):
    """From the root row alone, the full descendant tree must be recoverable."""
    cfg = corpus["conn_info"]
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE tree AS (
                    SELECT id, file_name, 0 AS depth FROM paths
                    WHERE id = %s
                    UNION ALL
                    SELECT p.id, p.file_name, t.depth + 1
                    FROM paths p JOIN tree t ON p.parent_path_id = t.id
                    WHERE t.depth < 10
                )
                SELECT depth, file_name FROM tree ORDER BY depth, file_name
                """,
                (one(corpus, "outer.zip")["id"],),
            )
            levels = {}
            for depth, name in cur.fetchall():
                levels.setdefault(depth, []).append(name)
    finally:
        conn.close()
    assert levels[0] == ["outer.zip"]
    # Two members with identical bytes but different names are two OCCURRENCES
    # (different in-container locations): both are kept, each with its own
    # provenance. Only a re-encounter of the same member collapses.
    assert sorted(levels[1]) == ["duplicate_a.txt", "duplicate_b.txt", "inner.zip"], levels
    assert sorted(levels[2]) == ["notes.txt", "report.pdf", "scan.png"], levels


# ------------------------------------------------------------ provenance
def test_nested_scanned_pdf_is_marked_ocr_derived(corpus):
    ocr = one(corpus, "report.pdf")["provenance"].get("ocr")
    assert ocr is not None, one(corpus, "report.pdf")["provenance"]
    assert ocr["derived"] is True
    assert ocr["engine"] in ("tesseract", "rapidocr")


def test_nested_image_is_marked_ocr_derived(corpus):
    ocr = one(corpus, "scan.png")["provenance"].get("ocr")
    assert ocr is not None
    assert ocr["derived"] is True


def test_plain_nested_document_is_not_marked_ocr(corpus):
    assert "ocr" not in one(corpus, "notes.txt")["provenance"]


def test_containers_do_not_inherit_their_childrens_ocr(corpus):
    for container in ("outer.zip", "inner.zip"):
        assert "ocr" not in one(corpus, container)["provenance"], container


def test_every_level_reports_a_terminal_processing_status(corpus):
    for name, matches in corpus["rows"].items():
        for row in matches:
            assert row["processing_status"] != "discovered", (name, row)
            assert row["processing_status"] in (
                "processed", "partially_processed", "failed", "unsupported", "skipped",
            ), (name, row["processing_status"])


# ----------------------------------------------------------- duplicates
def test_identical_bytes_under_two_names_are_two_occurrences(corpus):
    """One content, many occurrences: deduplicate content, preserve context.

    duplicate_a.txt and duplicate_b.txt hold identical bytes but occupy two
    different locations inside outer.zip. Identity is
    (content, context, occurrence): the shared bytes are ONE canonical content
    in ONE context, and each named member is a distinct occurrence with its own
    row and its own provenance. The archive held two members and the database
    records two members. Only a re-import of the *same* member (same chain)
    collapses onto the earlier row.

    The invariant that must hold either way: a row's file_name and its
    hierarchy_path must agree. Before this was fixed the linker wrote the
    archive member's name into whichever row the member resolved to, producing
    file_name='duplicate_a.txt' with
    hierarchy_path='outer.zip::duplicate_b.txt' - whichever member was linked
    last won, and the two columns contradicted each other.
    """
    outer = one(corpus, "outer.zip")
    for name in ("duplicate_a.txt", "duplicate_b.txt"):
        row = one(corpus, name)
        assert row["parent_path_id"] == outer["id"], (name, row)
        assert row["hierarchy_path"] == f"outer.zip::{name}", (name, row)


def test_no_stored_row_contradicts_itself(corpus):
    """Every row's hierarchy must end with its own stored name."""
    for name, matches in corpus["rows"].items():
        for row in matches:
            if row["hierarchy_path"]:
                assert row["hierarchy_path"].split("::")[-1] == name, (name, row)


# ------------------------------------------------------------ searchability
@pytest.mark.parametrize("marker,expected", [
    (PDF_MARKER, "report.pdf"),
    (PNG_MARKER, "scan.png"),
    (TXT_MARKER, "notes.txt"),
    (DUP_MARKER, "duplicate_a.txt"),
])
def test_nested_content_is_searchable_and_traces_to_its_object(corpus, marker, expected):
    """SEARCH -> the hit must name the exact row, not just the container."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=marker, limit=20)
    assert total >= 1, f"{marker} was not searchable"
    names = [r.get("file_name") for r in results]
    assert expected in names, (marker, names)


def test_ocr_text_from_three_levels_deep_is_searchable(corpus):
    from Api.services.search_service import SearchService

    _, total = SearchService.full_text_search(query=PNG_MARKER, limit=10)
    assert total >= 1
    # and its File Details record carries the chain back to the root
    row = one(corpus, "scan.png")
    assert row["hierarchy_path"].startswith("outer.zip::inner.zip::")


# ------------------------------------------------------------ File Details
def test_file_details_walks_the_chain_from_the_deepest_object(admin_client, corpus):
    resp = admin_client.get(f"/api/file/{one(corpus, 'report.pdf')['id']}/details")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()["details"]
    assert [a["name"] for a in d["lineage"]["ancestors"]] == ["outer.zip", "inner.zip"]
    assert d["lineage"]["root"]["name"] == "outer.zip"
    assert d["lineage"]["depth_from_root"] == 2
    assert d["extraction_provenance"]["ocr"]["derived"] is True
    assert d["processing"]["status"] == "processed"


def test_file_details_on_the_root_lists_the_whole_tree(admin_client, corpus):
    resp = admin_client.get(f"/api/file/{one(corpus, 'outer.zip')['id']}/details")
    assert resp.status_code == 200
    d = resp.get_json()["details"]
    names = sorted(x["name"] for x in d["lineage"]["descendants"])
    assert names == [
        "duplicate_a.txt", "duplicate_b.txt", "inner.zip",
        "notes.txt", "report.pdf", "scan.png",
    ], names
