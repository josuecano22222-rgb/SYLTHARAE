"""Routing: an ODF package names itself, and text that is text stays text.

Two files in the corpus were sent to readers that cannot read them, and both
were stored as zero characters with no error:

``test-doc/Documents/testODTStyles3.odt``
    A valid ODT whose ``mimetype`` member is not the first entry in the ZIP.
    The signature sniffer can only tell "this is a ZIP", so the file went to the
    archive reader, which lists members and stores none of their text. ODF
    compliance is a convention, not a guarantee, and LibreOffice's own test
    files break it.

``test-doc/Text/testBMPfp.txt``
    A text fixture whose first two bytes happen to be ``BM``. The two-byte
    signature table says "bitmap", content beat the name, and the image reader
    dutifully failed on a file made of prose.

Both rules are narrow on purpose: a real bitmap called ``.png`` still loses to
its own bytes, and only extensions that are text by definition can claim the
text veto.
"""

import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.services.file_reader_service import FileReaderService  # noqa: E402

ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"


def _write_odf(path: Path, mimetype: str, body: str, mimetype_first: bool):
    """A real ODF package built by odfpy, then repacked in a chosen member order.

    Building it with odfpy matters: a hand-written minimal ``content.xml`` is not
    a document the reader's parser accepts, and a fixture the reader cannot read
    would test the fixture instead of the routing.
    """
    odf = pytest.importorskip("odf.opendocument", reason="odfpy not installed")
    from odf.text import P

    document = odf.OpenDocumentText()
    document.text.addElement(P(text=body))
    built = path.with_suffix(".src")
    document.save(str(built))

    with zipfile.ZipFile(built) as source:
        entries = [(name, source.read(name)) for name in source.namelist()]
    built.unlink()

    mimetype_entry = [e for e in entries if e[0] == "mimetype"]
    others = [e for e in entries if e[0] != "mimetype"]
    ordered = mimetype_entry + others if mimetype_first else others + mimetype_entry
    if mimetype_entry:  # the mimetype the caller asked for wins
        ordered = ([(mimetype_entry[0][0], mimetype.encode("ascii"))] +
                   [e for e in ordered if e[0] != "mimetype"])
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in ordered:
            archive.writestr(name, data)
    return path


@pytest.fixture()
def service():
    return FileReaderService()


def _route(service, path, extension):
    reader, decision = service.resolve_reader_for_file(str(path), extension)
    return (reader.__class__.__name__ if reader else None), decision


def test_odf_with_non_compliant_member_order_reaches_the_office_reader(tmp_path, service):
    path = _write_odf(tmp_path / "non-compliant.odt", ODT_MIMETYPE,
                      "Odysseus wandered", mimetype_first=False)
    reader, decision = _route(service, path, ".odt")
    assert reader == "OfficeFileReader", decision
    assert decision["effective_extension"] == ".odt"
    assert decision["detection_method"] == "odf-mimetype"

    result = service.read_file({"path": str(path), "extension": ".odt"})
    content = (result.get("content")
               if isinstance(result.get("content"), dict) and "elements" not in result
               else result)
    assert not content.get("error"), content.get("error")
    assert "Odysseus" in str(content.get("paragraphs"))


def test_odf_spreadsheet_mimetype_is_read_by_name(tmp_path, service):
    path = _write_odf(tmp_path / "book.ods", ODS_MIMETYPE, "cell text",
                      mimetype_first=False)
    reader, decision = _route(service, path, ".ods")
    assert decision["effective_extension"] == ".ods", decision
    assert reader == "OfficeFileReader"


def test_a_plain_zip_is_still_an_archive(tmp_path, service):
    path = tmp_path / "plain.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("notes.txt", "not a document")
    reader, decision = _route(service, path, ".zip")
    assert reader == "ArchiveFileReader", decision


def test_text_starting_with_a_bitmap_signature_stays_text(tmp_path, service):
    path = tmp_path / "looks-like-a-bitmap.txt"
    path.write_bytes(b"BM is how this sentence starts, and it is prose.\n" * 4)
    reader, decision = _route(service, path, ".txt")
    assert reader == "RemainingFileReader", decision
    assert decision["effective_extension"] == ".txt"
    assert "retained over .bmp" in (decision["detection_note"] or "")


def test_a_real_bitmap_is_still_an_image(tmp_path, service):
    pytest.importorskip("PIL", reason="Pillow not installed")
    from PIL import Image

    path = tmp_path / "real.bmp"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path)
    reader, decision = _route(service, path, ".bmp")
    assert reader == "ImageFileReader", decision


def test_binary_content_does_not_earn_the_text_veto(tmp_path, service):
    """The veto needs the bytes to actually be text; it is not applied blindly."""
    path = tmp_path / "actually-binary.txt"
    path.write_bytes(bytes(range(256)) * 8)
    _, decision = _route(service, path, ".txt")
    assert "decodes as text" not in (decision.get("detection_note") or ""), decision
