"""Integration: File Details exposes provenance, lineage and status (task 4).

Proves the chain the brief requires, end to end and over real HTTP:

    source -> container -> nested object -> extracted content/OCR
           -> stored record -> search -> File Details

Before this the endpoint at /api/file/<id>/details returned 18 fields and none
of them were the ones added by migration 0007: extraction_provenance,
parent_path_id, hierarchy_path and processing_status were persisted but never
surfaced, so the File Details view could not answer "where did this object come
from" or "how was it derived" for anything it displayed.

Lineage is read from parent_path_id by Api.services.lineage_service using a
recursive CTE, never inferred from hierarchy_path or from filenames. Two members
of different archives can share a name; only the stored ids identify which
object a derivative came from.
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
MARKER = "FILEDETAILSCHAIN 3391"


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
def chain(pg_db, tmp_path, engine_or_skip):
    """Ingest outer.zip -> inner.zip -> scan.png; return the stored row ids."""
    from pipeline.integrated_reader import IntegratedFileReader

    tag = f"_det_{next(_SEQ)}"
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
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scan.png", text_png(MARKER))
    with zipfile.ZipFile(root / "outer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("inner.zip", inner.getvalue())

    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            ids = dict((name, pid) for pid, name in cur.fetchall())
    finally:
        conn.close()
    ids["tag"] = tag
    return ids


@pytest.fixture
def details(admin_client, chain):
    """Fetch File Details for the OCR leaf over real HTTP."""
    resp = admin_client.get(f"/api/file/{chain['scan.png']}/details")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    return body["details"]


def test_endpoint_returns_the_new_fields(details):
    """These four columns existed in the database but not in the response."""
    for key in ("extraction_provenance", "parent_path_id", "hierarchy_path",
                "processing", "lineage"):
        assert key in details, sorted(details)


def test_leaf_reports_its_full_ancestry(details, chain):
    names = [a["name"] for a in details["lineage"]["ancestors"]]
    assert names == ["outer.zip", "inner.zip"], names
    assert details["lineage"]["root"]["name"] == "outer.zip"
    assert details["lineage"]["root"]["id"] == chain["outer.zip"]
    assert details["parent_path_id"] == chain["inner.zip"]


def test_ancestry_uses_ids_not_filenames(details, chain):
    """Every ancestor must be an actual stored row, addressable by id."""
    for ancestor in details["lineage"]["ancestors"]:
        assert ancestor["id"] in (chain["outer.zip"], chain["inner.zip"])
    assert details["lineage"]["depth_from_root"] == 2
    assert details["lineage"]["is_top_level"] is False


def test_hierarchy_path_matches_the_id_chain(details):
    """The text chain is a rendering of the ids, so the two must agree."""
    assert details["hierarchy_path"] == "outer.zip::inner.zip::scan.png"


def test_ocr_provenance_is_visible(details):
    ocr = (details["extraction_provenance"] or {}).get("ocr")
    assert ocr is not None, details["extraction_provenance"]
    assert ocr["derived"] is True
    assert ocr["engine"] in ("tesseract", "rapidocr")
    assert 0.0 < ocr["confidence"] <= 1.0


def test_processing_status_is_surfaced(details):
    assert details["processing"]["status"] == "processed"
    assert details["processing"]["attempts"] >= 1
    assert details["processing"]["updated_at"], "a status with no timestamp"


def test_root_is_top_level_and_lists_its_descendants(admin_client, chain):
    resp = admin_client.get(f"/api/file/{chain['outer.zip']}/details")
    assert resp.status_code == 200
    d = resp.get_json()["details"]
    assert d["lineage"]["is_top_level"] is True
    assert d["lineage"]["ancestors"] == []
    names = [x["name"] for x in d["lineage"]["descendants"]]
    assert sorted(names) == ["inner.zip", "scan.png"], names
    depths = {x["name"]: x["depth"] for x in d["lineage"]["descendants"]}
    assert depths == {"inner.zip": 1, "scan.png": 2}, depths


def test_container_does_not_inherit_the_childs_ocr(admin_client, chain):
    resp = admin_client.get(f"/api/file/{chain['inner.zip']}/details")
    assert resp.status_code == 200
    prov = resp.get_json()["details"]["extraction_provenance"] or {}
    assert "ocr" not in prov, prov


def test_search_result_is_traceable_to_the_exact_object(admin_client, chain):
    """SEARCH -> DISPLAY: the hit must name the row File Details can open."""
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=MARKER, limit=10)
    assert total >= 1, "OCR text was not searchable"
    assert "scan.png" in [r.get("file_name") for r in results]
    # and the File Details view for that row carries the same provenance
    assert details_for(admin_client, chain)["extraction_provenance"]["ocr"]["derived"] is True


def details_for(client, chain):
    resp = client.get(f"/api/file/{chain['scan.png']}/details")
    assert resp.status_code == 200
    return resp.get_json()["details"]


def test_unknown_file_is_a_clean_404_not_a_500(admin_client):
    resp = admin_client.get("/api/file/99999999/details")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_unauthenticated_request_is_rejected(app, chain):
    """The lineage walk must not be readable without a session.

    A fresh client, not the shared `client` fixture: admin_client is built on
    that same instance, so it carries the admin session cookie once any
    authenticated test has run.
    """
    resp = app.test_client().get(f"/api/file/{chain['scan.png']}/details")
    assert resp.status_code == 401
