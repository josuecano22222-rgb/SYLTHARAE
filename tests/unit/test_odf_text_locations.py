"""ODF text: where it hides, and why the obvious loop misses it.

Four defects, one family. Each was found by comparing a stored artifact against
an independent reading of ``content.xml`` and each is a *location* problem
rather than a parsing problem:

``testODP_NPE.odp`` (recall 0.031)
    Slide text lives in ``draw:custom-shape`` as often as in ``draw:frame``; the
    reader recognised only frames and tables. And a run is a ``text:span`` with
    a style, so ``para.childNodes[0].data`` - the "text of the paragraph" - is
    ``None``. Every bullet in the deck was skipped twice over.

``testODFwithOOo3.odt`` (recall 0.717)
    odfpy refuses the package (MathML external DTD), so the fallback walks
    ``content.xml``. It emitted ``text:p``, ``text:h`` *and* ``text:span`` with
    ``"".join(itertext())`` and no separators between siblings, welding
    ``dc:creator`` and ``dc:date`` into ``Hanssens2009-10-05T19:10:26`` and
    losing the field boundary. It also discarded comments, notes and tracked
    changes.

``test-columnar.ods`` (recall 0.863)
    Same ``childNodes[0].data`` idiom in the cell loop.

``testODP_NPE.odp`` footers
    ``presentation:footer-decl`` holds the copyright line - visible furniture,
    stored nowhere.
"""

import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_office import OfficeFileReader  # noqa: E402

pytest.importorskip("odf", reason="odfpy not installed")

ODF_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document-content '
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'office:version="1.2">'
)


def _content(result):
    if isinstance(result.get("content"), dict):
        return result["content"]
    return result


def _odt_zip(tmp_path, body_xml, name="fallback.odt"):
    """An ODT whose content.xml is given verbatim (odfpy will not load it)."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr(
            "content.xml",
            ODF_HEAD + "<office:body><office:text>" + body_xml +
            "</office:text></office:body></office:document-content>")
    return path


class TestOdtContentXmlFallback:
    """The structured walk used when odfpy refuses the package."""

    def test_sibling_fields_are_separated_not_welded(self, tmp_path):
        body = (
            "<text:h><text:bookmark-start text:name='x'/>Report Title"
            "<office:annotation><dc:creator>Bart</dc:creator>"
            "<dc:date>2009-10-05T19:10:26</dc:date>"
            "<text:p>a note</text:p></office:annotation></text:h>")
        content = _content(OfficeFileReader().read_file(
            {"path": str(_odt_zip(tmp_path, body)), "extension": ".odt"}))
        heading = content["paragraphs"][0]["text"]
        assert heading.startswith("Report Title")
        assert "TitleBart" not in heading
        # Round 18 moved the annotation out of the heading and onto its own
        # labelled line: a comment is metadata about the paragraph, and leaving
        # it inline is what welded "Rolf" to "2017-01-31T00:00:00" in
        # testODTStyles3.odt. The attribution is still stored, whole.
        comment = next(p["text"] for p in content["paragraphs"]
                       if p.get("kind") == "comment")
        assert "[comment: Bart on 2009-10-05T19:10:26] a note" in comment

    def test_a_paragraph_is_emitted_once_even_with_styled_spans(self, tmp_path):
        body = ("<text:p><text:span>first</text:span> <text:span>second</text:span>"
                " tail</text:p>")
        content = _content(OfficeFileReader().read_file(
            {"path": str(_odt_zip(tmp_path, body)), "extension": ".odt"}))
        body_lines = [p["text"] for p in content["paragraphs"]]
        assert body_lines == ["first second tail"]

    def test_text_s_repeat_count_becomes_spaces(self, tmp_path):
        body = "<text:p>a<text:s text:c='4'/>b<text:tab/>c</text:p>"
        content = _content(OfficeFileReader().read_file(
            {"path": str(_odt_zip(tmp_path, body)), "extension": ".odt"}))
        assert content["paragraphs"][0]["text"] == "a b c"

    def test_comment_note_and_tracked_change_are_stored_with_provenance(self, tmp_path):
        body = (
            "<text:p>body text</text:p>"
            "<office:annotation><dc:creator>Bart</dc:creator>"
            "<text:p>reviewer remark</text:p></office:annotation>"
            "<text:p>a claim<text:note text:note-class='footnote'>"
            "<text:note-body><text:p>the source</text:p></text:note-body>"
            "</text:note></text:p>"
            "<text:tracked-changes><text:changed-region><text:deletion>"
            "<office:change-info><dc:creator>Bart</dc:creator>"
            "<dc:date>2009-10-05T19:09:00</dc:date></office:change-info>"
            "<text:p>removed words</text:p></text:deletion></text:changed-region>"
            "</text:tracked-changes>")
        content = _content(OfficeFileReader().read_file(
            {"path": str(_odt_zip(tmp_path, body)), "extension": ".odt"}))
        by_kind = {}
        for paragraph in content["paragraphs"]:
            by_kind.setdefault(paragraph.get("kind"), []).append(paragraph)

        assert any("reviewer remark" in p["text"] for p in by_kind["comment"])
        assert by_kind["comment"][0]["location"] == "office:annotation"
        assert any("the source" in p["text"] for p in by_kind["footnote"])
        change = by_kind["tracked_change"][0]
        assert "removed words" in change["text"]
        assert "Bart" in change["text"] and "2009-10-05" in change["text"]
        assert change["location"] == "text:deletion"

    def test_table_cells_are_stored_as_rows(self, tmp_path):
        body = ("<text:p>before</text:p><table:table>"
                "<table:table-row><table:table-cell><text:p>A1</text:p>"
                "</table:table-cell><table:table-cell><text:p>B1</text:p>"
                "</table:table-cell></table:table-row></table:table>")
        content = _content(OfficeFileReader().read_file(
            {"path": str(_odt_zip(tmp_path, body)), "extension": ".odt"}))
        assert content["tables"][0]["rows"] == [["A1", "B1"]]
        # the cell text is not double-counted as a paragraph
        assert [p["text"] for p in content["paragraphs"]] == ["before"]


class TestOdpSlideText:
    def _deck(self, tmp_path):
        from odf.draw import CustomShape, Frame, Page, TextBox
        from odf.opendocument import OpenDocumentPresentation as Presentation
        from odf.presentation import FooterDecl
        from odf.text import P, Span

        document = Presentation()
        page = Page(name="Slide One", masterpagename="Default")
        # A draw:custom-shape holds text:list/text:p directly - that is the
        # shape the real deck uses, and the one the reader skipped.
        shape = CustomShape()
        paragraph = P()
        paragraph.addElement(Span(text="The Medicalization of Society"))
        shape.addElement(paragraph)
        page.addElement(shape)

        frame = Frame()
        frame_box = TextBox()
        frame_paragraph = P()
        frame_paragraph.addText("text inside a plain frame")
        frame_box.addElement(frame_paragraph)
        frame.addElement(frame_box)
        page.addElement(frame)
        document.presentation.addElement(page)
        document.presentation.addElement(
            FooterDecl(name="ftr1", text="Copyright 2009 Alan S Berger"))
        path = tmp_path / "deck.odp"
        document.save(str(path))
        return path

    def test_text_inside_a_custom_shape_is_read(self, tmp_path):
        content = _content(OfficeFileReader().read_file(
            {"path": str(self._deck(tmp_path)), "extension": ".odp"}))
        texts = content["slides"][0]["texts"]
        assert "The Medicalization of Society" in texts

    def test_styled_run_inside_a_frame_is_read(self, tmp_path):
        content = _content(OfficeFileReader().read_file(
            {"path": str(self._deck(tmp_path)), "extension": ".odp"}))
        assert "text inside a plain frame" in content["slides"][0]["texts"]

    def test_footer_declaration_is_stored_as_forensic_text(self, tmp_path):
        content = _content(OfficeFileReader().read_file(
            {"path": str(self._deck(tmp_path)), "extension": ".odp"}))
        assert "Copyright 2009 Alan S Berger" in (content.get("forensic_text") or "")


class TestOdsCellText:
    def test_cell_text_wrapped_in_a_styled_span_is_read(self, tmp_path):
        from odf.opendocument import OpenDocumentSpreadsheet as Spreadsheet
        from odf.table import Table, TableCell, TableRow
        from odf.text import P, Span

        document = Spreadsheet()
        table = Table(name="Sheet1")
        row = TableRow()
        for value in ("Record Number", "Square of the Record Number"):
            cell = TableCell(valuetype="string")
            paragraph = P()
            paragraph.addElement(Span(text=value))
            cell.addElement(paragraph)
            row.addElement(cell)
        table.addElement(row)
        document.spreadsheet.addElement(table)
        path = tmp_path / "book.ods"
        document.save(str(path))

        content = _content(OfficeFileReader().read_file(
            {"path": str(path), "extension": ".ods"}))
        rows = content["sheets"]["Sheet1"]["data"]
        assert rows == [["Record Number", "Square of the Record Number"]]


class TestEncryptedOdf:
    """A protected document is unavailable, and says so."""

    def _encrypted_package(self, tmp_path):
        path = tmp_path / "locked.odt"
        manifest = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<manifest:manifest '
            'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
            '<manifest:file-entry manifest:full-path="/" manifest:media-type='
            '"application/vnd.oasis.opendocument.text"/>'
            '<manifest:file-entry manifest:full-path="content.xml" '
            'manifest:media-type="text/xml">'
            '<manifest:encryption-data manifest:checksum-type="SHA1"/>'
            '</manifest:file-entry></manifest:manifest>')
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr("META-INF/manifest.xml", manifest)
            archive.writestr("content.xml", bytes([0x85, 0x2a, 0xff, 0x00, 0x91]))
        return path

    def test_reader_reports_encryption_instead_of_a_codec_error(self, tmp_path):
        content = _content(OfficeFileReader().read_file(
            {"path": str(self._encrypted_package(tmp_path)), "extension": ".odt"}))
        assert content.get("encrypted") is True
        assert "Encrypted ODF package" in content["error"]
        assert "codec" not in content["error"]

    def test_oracle_refuses_to_score_ciphertext(self, tmp_path):
        sys.path.insert(0, str(PROJECT_ROOT / "tools" / "audit"))
        from archive_extraction import _oracle_text

        path = self._encrypted_package(tmp_path)
        assert _oracle_text(path, ".odt") == ""
