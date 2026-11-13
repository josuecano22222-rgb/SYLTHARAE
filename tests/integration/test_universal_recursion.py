"""Integration: one recursive pipeline for every container, supported or not.

EMBED-02. The recursion gate in file_router_service was

    if extraction_path and reader is self.file_reader_service.archive_reader:

so only archives recursed. Embedded Office images were materialised and OCR'd but
never became objects, and any future reader that materialised children would have
been ignored too. The gate is now format-agnostic: any reader that materialises
children into a directory gets them run through the ordinary pipeline, and email
is checked first only because its message row must exist before its attachments
so they can be linked to it. Email still delegates to the same
_process_extracted_files - an ordering difference, not a second pipeline.

Proven here, from real fixtures through the real pipeline into a real database:

    email -> ZIP -> DOCX -> embedded image -> OCR
    ZIP   -> email -> attachment -> archive -> PDF -> OCR
    email -> ZIP -> unsupported.xyz        (retained, not discarded)

Every level must have its own row, hash, parent, hierarchy, status and
provenance, and be independently searchable.
"""

import datetime
import io
import itertools
import os
import sys
import zipfile
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def _find_font():
    """A TrueType font that exists on this platform.

    The rasterised fixtures used to hardcode a Debian font path, so on Windows
    every test that needed an image fixture errored with
    ``OSError: cannot open resource`` before it could check anything. Look for a
    real font in the usual places; if none is present, fall back to Pillow's
    bundled default so the fixture still renders text.
    """
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


FONT = _find_font()
_SEQ = itertools.count()

M_EMAIL_TO_ZIP_TO_DOCX_IMG = "CHAINADOCX 1148"
M_EMAIL_TO_ZIP_TO_DOCX_TXT = "CHAINADOCXTXT 5502"
M_ZIP_TO_EMAIL_PDF = "CHAINBPDF 7734"
M_ZIP_TO_EMAIL_TXT = "CHAINBTXT 3319"
M_UNSUPPORTED = "CHAINUNSUPPORTED 9907"


def rasterise(text):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (1000, 260), "white")
    if FONT is not None:
        font = ImageFont.truetype(FONT, 38)
    else:  # no system font: Pillow always ships a usable default
        try:
            font = ImageFont.load_default(size=38)
        except TypeError:  # Pillow < 9.2 has no size argument
            font = ImageFont.load_default()
    ImageDraw.Draw(img).text((40, 100), text, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def scanned_pdf(text):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=rasterise(text))
    data = doc.tobytes()
    doc.close()
    return data


def docx_with_image(paragraph_text, image_text):
    """A DOCX whose only readable image content is rasterised, so OCR is needed."""
    docx = pytest.importorskip("docx")
    shared = pytest.importorskip("docx.shared")
    buf = io.BytesIO()
    doc = docx.Document()
    doc.add_paragraph(paragraph_text)
    png = io.BytesIO(rasterise(image_text))
    doc.add_picture(png, width=shared.Inches(4))
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def engine_or_skip():
    from core.ocr import get_ocr_engine

    if get_ocr_engine() is None:
        pytest.skip("no OCR engine installed")


def ingest(pg_db, root, tag):
    from pipeline.integrated_reader import IntegratedFileReader

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
    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=f"{tag}_src", storage_side=f"{tag}_side",
    ).process_folder(str(root))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,"
                " p.processing_status, p.status_detail, p.file_size,"
                " p.extraction_provenance, h.hash"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN hashs h ON h.id = hc.hash_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (f"{tag}_side",),
            )
            rows = {}
            for (pid, name, parent, hier, status, detail, size, prov,
                 digest) in cur.fetchall():
                rows.setdefault(name, []).append({
                    "id": pid, "parent_path_id": parent, "hierarchy_path": hier,
                    "processing_status": status, "status_detail": detail,
                    "file_size": size, "provenance": prov or {}, "hash": digest,
                })
    finally:
        conn.close()
    return {"tag": tag, "rows": rows, "pg_db": pg_db}


def one(bundle, name):
    matches = bundle["rows"].get(name)
    assert matches, f"{name} was not stored; have {sorted(bundle['rows'])}"
    assert len(matches) == 1, f"{name} stored {len(matches)} times"
    return matches[0]


# ============================================== email -> zip -> docx -> image
@pytest.fixture(scope="module")
def chain_a(pg_db, tmp_path_factory, engine_or_skip):
    tag = f"_chaina_{next(_SEQ)}"
    root = tmp_path_factory.mktemp("chain_a")

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "report.docx",
            docx_with_image(f"Marker {M_EMAIL_TO_ZIP_TO_DOCX_TXT}",
                            f"Marker {M_EMAIL_TO_ZIP_TO_DOCX_IMG}"),
        )
        z.writestr("notes.txt", f"Marker {M_ZIP_TO_EMAIL_TXT}\n")

    msg = EmailMessage()
    msg["From"] = "alice@example.org"
    msg["To"] = "bob@example.org"
    msg["Subject"] = "Marker CHAINASUBJECT"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="example.org")
    msg.set_content("Marker CHAINABODY body of the outer message")
    msg.add_attachment(inner.getvalue(), maintype="application", subtype="zip",
                       filename="bundle.zip")
    (root / "message.eml").write_bytes(bytes(msg))

    return ingest(pg_db, root, tag)


def test_chain_a_every_level_was_stored(chain_a):
    for expected in ("message.eml", "bundle.zip", "report.docx", "image1.png"):
        assert expected in chain_a["rows"], sorted(chain_a["rows"])


def test_chain_a_embedded_image_is_a_first_class_child(chain_a):
    """The point of EMBED-02: an embedded image is an object, not folded text."""
    image = one(chain_a, "image1.png")
    doc = one(chain_a, "report.docx")
    assert image["parent_path_id"] == doc["id"], image
    # hierarchy_path is the FULL path from the ingestion root, not just the
    # immediate parent - so a leaf four levels deep records all four.
    assert image["hierarchy_path"] == (
        "message.eml::bundle.zip::report.docx::image1.png"
    ), image
    assert image["hash"] and len(image["hash"]) == 64, image
    assert image["file_size"] and image["file_size"] > 0, image
    assert image["processing_status"] == "processed", image


def test_chain_a_embedded_image_carries_ocr_provenance(chain_a):
    ocr = one(chain_a, "image1.png")["provenance"].get("ocr")
    assert ocr is not None, one(chain_a, "image1.png")["provenance"]
    assert ocr["derived"] is True
    assert ocr["engine"] in ("tesseract", "rapidocr")


def test_chain_a_full_lineage_is_reconstructible(chain_a):
    image = one(chain_a, "image1.png")
    by_id = {r["id"]: r for matches in chain_a["rows"].values() for r in matches}
    chain, cur = [], image
    while cur:
        chain.append(cur["id"])
        parent = cur["parent_path_id"]
        cur = by_id.get(parent) if parent else None
    assert chain == [
        image["id"],
        one(chain_a, "report.docx")["id"],
        one(chain_a, "bundle.zip")["id"],
        one(chain_a, "message.eml")["id"],
    ], chain


def test_chain_a_document_body_survived_alongside_its_children(chain_a):
    """The merge, not replace: a DOCX has content of its own."""
    from Api.services.search_service import SearchService

    _, total = SearchService.full_text_search(query=M_EMAIL_TO_ZIP_TO_DOCX_TXT, limit=10)
    assert total >= 1, "the DOCX paragraph text was lost when children were attached"


def test_chain_a_every_level_is_independently_searchable(chain_a):
    from Api.services.search_service import SearchService

    _, img_total = SearchService.full_text_search(query=M_EMAIL_TO_ZIP_TO_DOCX_IMG, limit=20)
    assert img_total >= 1
    results, _ = SearchService.full_text_search(query=M_EMAIL_TO_ZIP_TO_DOCX_IMG, limit=20)
    names = [r.get("file_name") for r in results]
    assert "image1.png" in names, (
        f"the OCR-bearing object must be identifiable, got {names}"
    )


def test_chain_a_file_details_walks_the_whole_graph(admin_client, chain_a):
    resp = admin_client.get(f"/api/file/{one(chain_a, 'image1.png')['id']}/details")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()["details"]
    assert [a["name"] for a in d["lineage"]["ancestors"]] == [
        "message.eml", "bundle.zip", "report.docx"
    ], d["lineage"]["ancestors"]
    assert d["lineage"]["root"]["name"] == "message.eml"
    assert d["lineage"]["depth_from_root"] == 3
    assert d["extraction_provenance"]["ocr"]["derived"] is True


# =================================== zip -> email -> attachment -> archive -> pdf
@pytest.fixture(scope="module")
def chain_b(pg_db, tmp_path_factory, engine_or_skip):
    tag = f"_chainb_{next(_SEQ)}"
    root = tmp_path_factory.mktemp("chain_b")

    innermost = io.BytesIO()
    with zipfile.ZipFile(innermost, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scan.pdf", scanned_pdf(f"Marker {M_ZIP_TO_EMAIL_PDF}"))

    msg = EmailMessage()
    msg["From"] = "carol@example.org"
    msg["To"] = "dave@example.org"
    msg["Subject"] = "Marker CHAINBSUBJECT"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="example.org")
    msg.set_content("Marker CHAINBBODY body text")
    msg.add_attachment(innermost.getvalue(), maintype="application", subtype="zip",
                       filename="inner.zip")
    msg.add_attachment(f"Marker {M_ZIP_TO_EMAIL_TXT}\n".encode(),
                       maintype="text", subtype="plain", filename="readme.txt")

    with zipfile.ZipFile(root / "outer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("message.eml", bytes(msg))

    return ingest(pg_db, root, tag)


def test_chain_b_every_level_was_stored(chain_b):
    for expected in ("outer.zip", "message.eml", "inner.zip", "readme.txt", "scan.pdf"):
        assert expected in chain_b["rows"], sorted(chain_b["rows"])


def test_chain_b_lineage_from_ocr_back_to_the_archive(chain_b):
    pdf = one(chain_b, "scan.pdf")
    assert pdf["hierarchy_path"] == "message.eml::inner.zip::scan.pdf", pdf
    assert pdf["parent_path_id"] == one(chain_b, "inner.zip")["id"]
    assert one(chain_b, "inner.zip")["parent_path_id"] == one(chain_b, "message.eml")["id"]
    assert one(chain_b, "message.eml")["parent_path_id"] == one(chain_b, "outer.zip")["id"]
    assert one(chain_b, "outer.zip")["parent_path_id"] is None


def test_chain_b_pdf_is_marked_ocr_derived(chain_b):
    ocr = one(chain_b, "scan.pdf")["provenance"].get("ocr")
    assert ocr is not None and ocr["derived"] is True, one(chain_b, "scan.pdf")["provenance"]


def test_chain_b_pdf_text_is_searchable_and_names_the_pdf(chain_b):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=M_ZIP_TO_EMAIL_PDF, limit=20)
    assert total >= 1
    assert "scan.pdf" in [r.get("file_name") for r in results]


def test_chain_b_email_attachment_is_linked_to_its_message(chain_b):
    assert one(chain_b, "readme.txt")["parent_path_id"] == one(chain_b, "message.eml")["id"]


def test_chain_b_file_details_reaches_the_root(admin_client, chain_b):
    resp = admin_client.get(f"/api/file/{one(chain_b, 'scan.pdf')['id']}/details")
    assert resp.status_code == 200
    d = resp.get_json()["details"]
    assert [a["name"] for a in d["lineage"]["ancestors"]] == [
        "outer.zip", "message.eml", "inner.zip"
    ], d["lineage"]["ancestors"]
    assert d["lineage"]["root"]["name"] == "outer.zip"


# ==================================================== unsupported descendants
@pytest.fixture(scope="module")
def chain_c(pg_db, tmp_path_factory):
    """email -> ZIP -> an unsupported member, alongside a supported one."""
    tag = f"_chainc_{next(_SEQ)}"
    root = tmp_path_factory.mktemp("chain_c")

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mystery.xyz", b"\x01\x02\x03 " + M_UNSUPPORTED.encode())
        z.writestr("known.txt", "Marker CHAINCKNOWN supported sibling\n")

    msg = EmailMessage()
    msg["From"] = "eve@example.org"
    msg["To"] = "frank@example.org"
    msg["Subject"] = "Marker CHAINCSUBJECT"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="example.org")
    msg.set_content("Marker CHAINCBODY body text")
    msg.add_attachment(inner.getvalue(), maintype="application", subtype="zip",
                       filename="bundle.zip")
    (root / "message.eml").write_bytes(bytes(msg))

    return ingest(pg_db, root, tag)


def test_chain_c_unsupported_member_is_retained(chain_c):
    """unsupported != invisible. It must not be dropped for being undecodable."""
    assert "mystery.xyz" in chain_c["rows"], sorted(chain_c["rows"])


def test_chain_c_unsupported_member_keeps_its_identity_and_lineage(chain_c):
    row = one(chain_c, "mystery.xyz")
    assert row["parent_path_id"] == one(chain_c, "bundle.zip")["id"], row
    assert row["hierarchy_path"] == "message.eml::bundle.zip::mystery.xyz", row
    assert row["file_size"] and row["file_size"] > 0, row
    assert row["hash"] and len(row["hash"]) == 64, row


def test_chain_c_unsupported_member_is_classified_not_silent(chain_c):
    row = one(chain_c, "mystery.xyz")
    assert row["processing_status"] == "unsupported", row
    assert row["status_detail"], "an unsupported object must say why"


def test_chain_c_supported_sibling_still_fully_processed(chain_c):
    row = one(chain_c, "known.txt")
    assert row["processing_status"] == "processed", row
    assert row["parent_path_id"] == one(chain_c, "bundle.zip")["id"]


def test_chain_c_unsupported_member_is_visible_in_file_details(admin_client, chain_c):
    resp = admin_client.get(f"/api/file/{one(chain_c, 'mystery.xyz')['id']}/details")
    assert resp.status_code == 200
    d = resp.get_json()["details"]
    assert d["processing"]["status"] == "unsupported", d["processing"]
    assert d["processing"]["detail"], d["processing"]
    assert [a["name"] for a in d["lineage"]["ancestors"]] == ["message.eml", "bundle.zip"]


def test_chain_c_the_container_lists_its_unsupported_child(admin_client, chain_c):
    resp = admin_client.get(f"/api/file/{one(chain_c, 'bundle.zip')['id']}/details")
    assert resp.status_code == 200
    names = sorted(x["name"] for x in resp.get_json()["details"]["lineage"]["descendants"])
    assert names == ["known.txt", "mystery.xyz"], names


# ==================================================== no row contradicts itself
@pytest.mark.parametrize("fixture_name", ["chain_a", "chain_b", "chain_c"])
def test_no_row_contradicts_itself(request, fixture_name):
    """hierarchy_path must end with the row's own stored file_name."""
    try:
        bundle = request.getfixturevalue(fixture_name)
    except pytest.skip.Exception:
        pytest.skip("fixture unavailable")
    for name, matches in bundle["rows"].items():
        for row in matches:
            if row["hierarchy_path"]:
                assert row["hierarchy_path"].split("::")[-1] == name, (name, row)


@pytest.mark.parametrize("fixture_name", ["chain_a", "chain_b", "chain_c"])
def test_every_row_reached_a_terminal_status(request, fixture_name):
    try:
        bundle = request.getfixturevalue(fixture_name)
    except pytest.skip.Exception:
        pytest.skip("fixture unavailable")
    for name, matches in bundle["rows"].items():
        for row in matches:
            assert row["processing_status"] in (
                "processed", "partially_processed", "failed", "unsupported", "skipped",
            ), (name, row["processing_status"])


# ============================================================ recursion bounds
@pytest.fixture(scope="module")
def chain_deep(pg_db, tmp_path_factory):
    """ZIP nested deeper than MAX_RECURSION_DEPTH, with a leaf beyond it."""
    tag = f"_deep_{next(_SEQ)}"
    root = tmp_path_factory.mktemp("chain_deep")

    from reader_file.services.file_router_service import MAX_RECURSION_DEPTH

    levels = MAX_RECURSION_DEPTH + 4
    payload = io.BytesIO(b"Marker CHAINDEEPLEAF the deepest content\n")
    # Build inside-out: the innermost archive holds the leaf file.
    for i in range(levels, 0, -1):
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
            if i == levels:
                z.writestr("leaf.txt", payload.getvalue())
            else:
                z.writestr(f"level{i + 1}.zip", payload.getvalue())
        payload = outer
    (root / "level1.zip").write_bytes(payload.getvalue())
    return ingest(pg_db, root, tag), levels


def test_deep_chain_stops_at_the_cap_and_terminates(chain_deep):
    """Beyond the cap the pipeline stops descending instead of looping forever."""
    bundle, levels = chain_deep
    from reader_file.services.file_router_service import MAX_RECURSION_DEPTH

    stored = sorted(bundle["rows"])
    # level1 is the ingested root, so levels 1..MAX+2 are reachable.
    assert len(stored) == MAX_RECURSION_DEPTH + 2, (stored, levels)
    assert stored == [f"level{i}.zip" for i in range(1, MAX_RECURSION_DEPTH + 3)], stored
    # The members below the cap were never opened, so they were never discovered.
    assert "leaf.txt" not in bundle["rows"], sorted(bundle["rows"])


def test_deep_chain_container_at_the_cap_is_retained_not_discarded(chain_deep):
    """The object that hit the cap must survive - the source never vanishes."""
    bundle, _ = chain_deep
    from reader_file.services.file_router_service import MAX_RECURSION_DEPTH

    capped = one(bundle, f"level{MAX_RECURSION_DEPTH + 2}.zip")
    assert capped["hash"] and len(capped["hash"]) == 64, capped
    assert capped["file_size"] and capped["file_size"] > 0, capped
    assert capped["parent_path_id"] == one(bundle, f"level{MAX_RECURSION_DEPTH + 1}.zip")["id"]


def test_deep_chain_container_at_the_cap_reports_why_it_was_not_read(chain_deep):
    """...and must not pretend to have been analysed successfully."""
    bundle, _ = chain_deep
    from reader_file.services.file_router_service import MAX_RECURSION_DEPTH

    capped = one(bundle, f"level{MAX_RECURSION_DEPTH + 2}.zip")
    assert capped["processing_status"] == "failed", capped
    assert "recursion depth" in (capped["status_detail"] or "").lower(), capped


def test_deep_chain_levels_above_the_cap_are_processed(chain_deep):
    """The cap limits descent only - everything inside it is fully analysed."""
    bundle, _ = chain_deep
    root = one(bundle, "level1.zip")
    assert root["processing_status"] == "processed", root
    # A top-level ingested object has no parent, so it carries no hierarchy_path.
    assert root["parent_path_id"] is None, root
    assert root["hierarchy_path"] is None, root
    import json as _json
    with open("/tmp/deep_rows.json", "w") as fh:
        _json.dump({k: v for k, v in bundle["rows"].items()}, fh, indent=1, default=str)
    assert one(bundle, "level2.zip")["hierarchy_path"] == "level1.zip::level2.zip"
    assert one(bundle, "level6.zip")["processing_status"] == "processed"


def test_deep_chain_capped_container_is_visible_in_file_details(admin_client, chain_deep):
    """A user inspecting the archive can see that reading stopped, and why."""
    bundle, _ = chain_deep
    from reader_file.services.file_router_service import MAX_RECURSION_DEPTH

    row = one(bundle, f"level{MAX_RECURSION_DEPTH + 2}.zip")
    resp = admin_client.get(f"/api/file/{row['id']}/details")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()["details"]
    assert d["processing"]["status"] == "failed", d["processing"]
    assert "recursion depth" in (d["processing"]["detail"] or "").lower(), d["processing"]
    assert [a["name"] for a in d["lineage"]["ancestors"]] == [
        f"level{i}.zip" for i in range(1, MAX_RECURSION_DEPTH + 2)
    ], d["lineage"]["ancestors"]


@pytest.fixture(scope="module")
def reader_contract_probe(tmp_path_factory):
    """Run the router directly (no DB) to inspect the shape it reports."""
    from reader_file.services.file_router_service import FileRouterService

    root = tmp_path_factory.mktemp("contract")
    router = FileRouterService()

    def content_of(path, ext):
        res = router.process_file(
            {"path": str(path), "extension": ext, "type": "FILE",
             "size": path.stat().st_size},
            collect=False, store_result=False,
        )
        assert res is not None, "router returned no result"
        return res["Content"]

    zpath = root / "container.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("inner.txt", "Marker CONTRACTZIPCHILD\n")

    docx = pytest.importorskip("docx")
    dpath = root / "contract.docx"
    d = docx.Document()
    d.add_paragraph("Marker CONTRACTDOCBODY the document's own text")
    png = io.BytesIO(rasterise("Marker CONTRACTDOCIMG"))
    d.add_picture(png, width=docx.shared.Inches(4))
    d.save(str(dpath))

    return {"zip": content_of(zpath, ".zip"), "docx": content_of(dpath, ".docx")}


# ==================================== the reported summary key is not renamed
def test_office_container_reports_embedded_info(reader_contract_probe, tmp_path):
    """A DOCX reports embedded_info - it is not an archive, and must not
    misreport itself as one just to reuse the archive summary shape."""
    content = reader_contract_probe["docx"]
    assert "embedded_info" in content, sorted(content)
    assert "archive_info" not in content, sorted(content)
    assert content["embedded_info"]["total_files"] >= 1


def test_archive_container_still_reports_archive_info(reader_contract_probe):
    """ARCHIVE-01 contract, preserved through the format-agnostic gate.

    Regression: making the gate reader-agnostic must not rename the summary.
    _process_extracted_files names it f"{extraction_type}_info", so passing a
    single hardcoded label silently renamed archive_info for every archive.
    """
    content = reader_contract_probe["zip"]
    assert "archive_info" in content, sorted(content)
    assert content["archive_info"]["total_files"] >= 1


def test_archive_reader_fields_survive_the_merge(reader_contract_probe):
    """The merge, not replace: the archive reader's own fields must survive.

    At HEAD content_data = extraction_result discarded archive_type,
    files_extracted, bytes_extracted and skipped_members from the reader.
    """
    content = reader_contract_probe["zip"]
    for key in ("archive_type", "files_extracted", "bytes_extracted",
                "skipped_members", "extraction_path"):
        assert key in content, f"{key} was lost; have {sorted(content)}"


def test_document_body_and_children_coexist(reader_contract_probe):
    """The reason for the merge: a DOCX has content of its own."""
    content = reader_contract_probe["docx"]
    assert "embedded_info" in content, sorted(content)
    text = str(content.get("full_text") or content.get("text") or content)
    assert "CONTRACTDOCBODY" in text, "the document body was replaced by its children"
