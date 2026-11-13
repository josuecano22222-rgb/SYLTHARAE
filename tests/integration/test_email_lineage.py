"""Integration: email and message containers, end to end.

Inventory first, because the brief requires it. reader_file/readers/read_email.py
declares .eml .msg .mbox .pst. What each actually does, measured here:

    .eml   VERIFIED. Headers, plain body, HTML body and attachments extracted;
           attachments stored as their own rows and linked to the message.
    .mbox  VERIFIED. Multiple messages parsed; attachments stored and linked.
           The whole mailbox is one paths row - individual messages are not
           separate objects. Recorded as a limitation, not claimed otherwise.
    .msg   NOT VERIFIED. extract_msg is installed and declared, but it only
           READS; no tool here can author a .msg, so there is no valid fixture
           to extract. Only the failure path is tested.
    .pst   NOT SUPPORTED AT RUNTIME. Declared, but it needs pypff, which is
           neither installed nor a declared dependency. Asserted below so the
           claim cannot outlive the capability silently.

Two defects fixed by this suite are pinned by regression tests:

EMAIL-01  The message -> attachment relationship was never persisted. STEP 1 of
          the router stored the message and produced message_path_id, then
          nothing used it, so every attachment was stored as an unrelated
          top-level file. Measured before the fix:

              message.eml   parent=NULL
              report.pdf    parent=NULL   <- should be the message
              bundle.zip    parent=NULL

EMAIL-02  mbox attachments were silently discarded. extract_mbox returned
          total_attachments but not the has_attachments key the router reads,
          so the attachment branch was skipped. Attachments were written to disk
          and counted, then never stored, indexed or linked - with no error.
"""

import datetime
import io
import itertools
import mailbox
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

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_SEQ = itertools.count()

EML_PDF_MARKER = "EMAILPDF 5523"
EML_NESTED_MARKER = "EMAILDEEP 7712"
EML_TXT_MARKER = "EMAILTXTATTACH 1122"
EML_BODY_MARKER = "EMAILBODY 4401"
EML_SUBJECT_MARKER = "EMAILSUBJECT 8890"


def rasterise(text):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", (1000, 240), "white")
    ImageDraw.Draw(img).text((40, 90), text,
                             font=ImageFont.truetype(FONT, 36), fill="black")
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


@pytest.fixture(scope="module")
def engine_or_skip():
    from core.ocr import get_ocr_engine

    if get_ocr_engine() is None:
        pytest.skip("no OCR engine installed")


def _ingest(pg_db, tmp_root, side_tag, source_tag):
    """Shared ingest helper; returns rows keyed by file_name."""
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
            (side_tag, today),
        )
        cur.execute(
            "INSERT INTO sources (name, job, importance, country, date_creation)"
            " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name)"
            " DO UPDATE SET name = EXCLUDED.name",
            (source_tag, today),
        )
    conn.commit()
    IntegratedFileReader(
        max_workers=1, enable_storage=True,
        storage_source=source_tag, storage_side=side_tag,
    ).process_folder(str(tmp_root))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,"
                " p.processing_status, p.extraction_provenance"
                " FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN sides s ON s.id = hc.side_id WHERE s.name = %s",
                (side_tag,),
            )
            rows = {}
            for pid, name, parent, hier, status, prov in cur.fetchall():
                rows.setdefault(name, []).append({
                    "id": pid, "parent_path_id": parent, "hierarchy_path": hier,
                    "processing_status": status, "provenance": prov or {},
                })
    finally:
        conn.close()
    return rows


# ------------------------------------------------------------------ eml
@pytest.fixture(scope="module")
def eml(pg_db, tmp_path_factory, engine_or_skip):
    """Ingest one .eml carrying a PDF, a nested archive and a text attachment."""
    tag = f"_eml_{next(_SEQ)}"
    root = tmp_path_factory.mktemp("eml_corpus")

    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("deep_report.pdf", scanned_pdf(EML_NESTED_MARKER))

    msg = EmailMessage()
    msg["From"] = "Alice Sender <alice@example.org>"
    msg["To"] = "bob@example.org, carol@example.org"
    msg["Cc"] = "dave@example.org"
    msg["Bcc"] = "eve@example.org"
    msg["Subject"] = f"Marker {EML_SUBJECT_MARKER} quarterly report"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="example.org")
    msg.set_content(f"Marker {EML_BODY_MARKER} plain text part of the message.")
    msg.add_attachment(scanned_pdf(EML_PDF_MARKER), maintype="application",
                       subtype="pdf", filename="report.pdf")
    msg.add_attachment(nested.getvalue(), maintype="application",
                       subtype="zip", filename="bundle.zip")
    msg.add_attachment(f"Marker {EML_TXT_MARKER}\n".encode(), maintype="text",
                       subtype="plain", filename="notes.txt")
    (root / "message.eml").write_bytes(bytes(msg))

    rows = _ingest(pg_db, root, f"{tag}_side", f"{tag}_src")
    return {"tag": tag, "rows": rows, "pg_db": pg_db}


def one(bundle, name):
    matches = bundle["rows"].get(name)
    assert matches, f"{name} was not stored; have {sorted(bundle['rows'])}"
    assert len(matches) == 1, f"{name} stored {len(matches)} times"
    return matches[0]


def test_message_and_all_attachments_were_stored(eml):
    for expected in ("message.eml", "report.pdf", "bundle.zip", "notes.txt"):
        assert expected in eml["rows"], sorted(eml["rows"])


def test_message_is_the_root(eml):
    assert one(eml, "message.eml")["parent_path_id"] is None


def test_every_attachment_points_at_its_message(eml):
    """EMAIL-01 regression: attachments must not be orphaned top-level files."""
    message = one(eml, "message.eml")
    for name in ("report.pdf", "bundle.zip", "notes.txt"):
        assert one(eml, name)["parent_path_id"] == message["id"], name


def test_attachment_hierarchy_names_the_message(eml):
    assert one(eml, "report.pdf")["hierarchy_path"] == "message.eml::report.pdf"
    assert one(eml, "bundle.zip")["hierarchy_path"] == "message.eml::bundle.zip"


def test_email_attachment_archive_document_chain_is_complete(eml):
    """email -> attachment -> archive -> document, with OCR provenance."""
    assert one(eml, "deep_report.pdf")["hierarchy_path"] == (
        "message.eml::bundle.zip::deep_report.pdf"
    )
    chain = one(eml, "deep_report.pdf")
    assert chain["parent_path_id"] == one(eml, "bundle.zip")["id"]
    assert chain["provenance"].get("ocr", {}).get("derived") is True


def test_chain_is_walkable_from_the_message_row(eml):
    cfg = eml["pg_db"]
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE tree AS (
                    SELECT id, file_name, 0 AS depth FROM paths WHERE id = %s
                    UNION ALL
                    SELECT p.id, p.file_name, t.depth + 1
                    FROM paths p JOIN tree t ON p.parent_path_id = t.id
                    WHERE t.depth < 10
                )
                SELECT depth, file_name FROM tree ORDER BY depth, file_name
                """,
                (one(eml, "message.eml")["id"],),
            )
            levels = {}
            for depth, name in cur.fetchall():
                levels.setdefault(depth, []).append(name)
    finally:
        conn.close()
    assert levels[0] == ["message.eml"]
    assert sorted(levels[1]) == ["bundle.zip", "notes.txt", "report.pdf"], levels
    assert levels[2] == ["deep_report.pdf"], levels


def test_pdf_attachment_is_marked_ocr_derived(eml):
    assert one(eml, "report.pdf")["provenance"].get("ocr", {}).get("derived") is True


def test_the_message_itself_is_not_marked_ocr(eml):
    assert "ocr" not in one(eml, "message.eml")["provenance"]


def test_attachments_have_their_own_hashes(eml):
    """Each attachment is its own object with its own content hash."""
    cfg = eml["pg_db"]
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.file_name, h.hash FROM paths p"
                " JOIN hash_contexts hc ON hc.id = p.context_id"
                " JOIN hashs h ON h.id = hc.hash_id"
                " JOIN sides s ON s.id = hc.side_id"
                " WHERE s.name = %s AND p.file_name IN"
                " ('report.pdf','bundle.zip','notes.txt')",
                (f"{eml['tag']}_side",),
            )
            hashes = {name: h for name, h in cur.fetchall()}
    finally:
        conn.close()
    assert len(hashes) == 3, hashes
    assert len(set(hashes.values())) == 3, "attachments share a hash"
    assert all(len(h) == 64 for h in hashes.values()), hashes


@pytest.mark.parametrize("marker,expected", [
    (EML_BODY_MARKER, "message.eml"),
    (EML_SUBJECT_MARKER, "message.eml"),
    (EML_PDF_MARKER, "report.pdf"),
    (EML_TXT_MARKER, "notes.txt"),
    (EML_NESTED_MARKER, "deep_report.pdf"),
])
def test_email_content_is_searchable_and_traces_to_its_object(eml, marker, expected):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=marker, limit=20)
    assert total >= 1, f"{marker} was not searchable"
    assert expected in [r.get("file_name") for r in results], marker


def test_file_details_walks_email_to_nested_ocr(admin_client, eml):
    resp = admin_client.get(f"/api/file/{one(eml, 'deep_report.pdf')['id']}/details")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    d = resp.get_json()["details"]
    assert [a["name"] for a in d["lineage"]["ancestors"]] == [
        "message.eml", "bundle.zip"
    ], d["lineage"]["ancestors"]
    assert d["lineage"]["root"]["name"] == "message.eml"
    assert d["extraction_provenance"]["ocr"]["derived"] is True


def test_file_details_on_the_message_lists_its_attachments(admin_client, eml):
    resp = admin_client.get(f"/api/file/{one(eml, 'message.eml')['id']}/details")
    assert resp.status_code == 200
    d = resp.get_json()["details"]
    names = sorted(x["name"] for x in d["lineage"]["descendants"])
    assert names == ["bundle.zip", "deep_report.pdf", "notes.txt", "report.pdf"], names


# ----------------------------------------------------------------- mbox
@pytest.fixture(scope="module")
def mbox(pg_db, tmp_path_factory):
    """Ingest a two-message mbox, each carrying one attachment."""
    tag = f"_mbox_{next(_SEQ)}"
    root = tmp_path_factory.mktemp("mbox_corpus")
    path = root / "mailbox.mbox"
    box = mailbox.mbox(str(path))
    box.lock()
    for i in (1, 2):
        m = EmailMessage()
        m["From"] = f"sender{i}@example.org"
        m["To"] = f"rcpt{i}@example.org"
        m["Subject"] = f"Marker MBOXSUBJECT{i}"
        m["Cc"] = f"cc{i}@example.org"
        m["Date"] = formatdate(localtime=True)
        m["Message-ID"] = make_msgid(domain="example.org")
        m.set_content(f"Marker MBOXBODY{i} body of message {i}")
        m.add_attachment(f"Marker MBOXATTACH{i}\n".encode(), maintype="text",
                         subtype="plain", filename=f"att{i}.txt")
        box.add(m)
    box.flush()
    box.unlock()
    box.close()
    rows = _ingest(pg_db, root, f"{tag}_side", f"{tag}_src")
    return {"tag": tag, "rows": rows}


def test_mbox_and_both_attachments_were_stored(mbox):
    """EMAIL-02 regression: mbox attachments used to be dropped entirely."""
    for expected in ("mailbox.mbox", "att1.txt", "att2.txt"):
        assert expected in mbox["rows"], sorted(mbox["rows"])


def test_mbox_attachments_are_linked_to_the_mailbox(mbox):
    parent = one(mbox, "mailbox.mbox")
    for name in ("att1.txt", "att2.txt"):
        assert one(mbox, name)["parent_path_id"] == parent["id"], name
        assert one(mbox, name)["hierarchy_path"] == f"mailbox.mbox::{name}"


@pytest.mark.parametrize("marker,expected", [
    ("MBOXBODY1", "mailbox.mbox"),
    ("MBOXBODY2", "mailbox.mbox"),
    ("MBOXATTACH1", "att1.txt"),
    ("MBOXATTACH2", "att2.txt"),
])
def test_mbox_content_is_searchable(mbox, marker, expected):
    from Api.services.search_service import SearchService

    results, total = SearchService.full_text_search(query=marker, limit=20)
    assert total >= 1, f"{marker} was not searchable"
    assert expected in [r.get("file_name") for r in results]


def test_mbox_reader_reports_the_contract_the_router_needs(tmp_path):
    """EMAIL-02 at the reader level: has_attachments must be present.

    tmp_path, not /tmp: mailbox.mbox() opens an existing file and appends to it,
    so a fixed path leaks messages between runs and the counts drift.
    """
    from reader_file.readers.read_email import EmailFileReader

    tmp = tmp_path / "contract.mbox"
    box = mailbox.mbox(str(tmp))
    box.lock()
    m = EmailMessage()
    m["From"] = "a@b.c"
    m["Subject"] = "contract"
    m.set_content("body")
    m.add_attachment(b"payload\n", maintype="text", subtype="plain",
                     filename="x.txt")
    box.add(m)
    box.flush()
    box.unlock()
    box.close()
    res = EmailFileReader().read_file({"path": str(tmp), "extension": ".mbox"})
    assert res.get("error") is None, res
    assert "has_attachments" in res, sorted(res)
    assert res["has_attachments"] is True
    assert res["attachment_count"] == 1
    assert res["total_attachments"] == 1


# ------------------------------------------- formats that are NOT supported
def test_msg_extraction_cannot_be_verified_here(tmp_path):
    """Documents the boundary rather than claiming .msg support.

    extract_msg is installed and declared, but it only reads; nothing here can
    author a .msg, so there is no valid fixture and content extraction is
    UNVERIFIED. What is verified is that a file claiming to be a .msg fails
    safely instead of crashing the ingest.
    """
    from reader_file.readers.read_email import EmailFileReader

    path = tmp_path / "not_a_real.msg"
    path.write_bytes(b"\xd0\xcf\x11\xe0 not really a compound file")
    res = EmailFileReader().read_file({"path": str(path), "extension": ".msg"})
    assert res is not None
    # Either an error is recorded, or an empty result - never an exception.
    assert res.get("error") or not res.get("message")


def test_pst_is_declared_but_undecodable_here():
    """.pst is advertised but cannot work: pypff is not a declared dependency."""
    from reader_file.readers.read_email import EmailFileReader

    assert ".pst" in EmailFileReader().get_supported_extensions()
    try:
        import pypff  # noqa: F401
        pytest.skip("pypff is installed; this boundary does not apply")
    except ImportError:
        pass
    # So the claim and the capability disagree. Pin that so it is a visible
    # gap rather than something a reader of get_supported_extensions() trusts.
    res = EmailFileReader().read_file(
        {"path": "/nonexistent/fake.pst", "extension": ".pst"}
    )
    assert res.get("error"), "a .pst read reported no error without pypff"


def test_unsupported_email_extension_is_rejected(tmp_path):
    from reader_file.readers.read_email import EmailFileReader

    path = tmp_path / "notes.emlx"
    path.write_text("not a recognised email container")
    res = EmailFileReader().read_file({"path": str(path), "extension": ".emlx"})
    assert "unsupported" in (res.get("error") or "").lower(), res
