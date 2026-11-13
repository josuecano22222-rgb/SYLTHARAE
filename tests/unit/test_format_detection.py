"""Format identification: content decides, declarations are preserved.

These tests pin the properties the ingestion pipeline depends on:

* identification is driven by content, not by the filename;
* variants that change what must be extracted (macro-enabled, template,
  slideshow, binary workbook) are distinguished;
* anything the catalogue names can actually be read - identification and
  processing are looked up from the same table, so they cannot drift;
* an unreadable or unrecognisable artifact yields a recorded state with a
  reason, never an exception and never a silent guess.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.formats import (  # noqa: E402
    CATALOG,
    FormatFamily,
    READER_BY_FAMILY,
    alias_extensions,
    canonical_extension,
    catalogue_extensions,
    identify,
    identify_bytes,
    lookup_by_extension,
    lookup_format,
    reader_extensions,
)

from _cfb import build_compound_file  # noqa: E402  (test-only CFB writer)
from core.formats.detection import (  # noqa: E402
    DISC_BINARY_TEXT,
    DISC_ENCRYPTED,
    DISC_EXTENSION_ABSENT,
    DISC_EXTENSION_UNKNOWN,
    DISC_FORMAT_MISMATCH,
    DISC_VARIANT_MISMATCH,
    SEVERITY_HIGH,
)

WORD_MAIN = ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document.main+xml")
WORD_TEMPLATE = ("application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.template.main+xml")
WORD_MACRO = "application/vnd.ms-word.document.macroEnabled.main+xml"
WORD_TEMPLATE_MACRO = "application/vnd.ms-word.template.macroEnabled.main+xml"
EXCEL_MAIN = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.sheet.main+xml")
EXCEL_MACRO = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
PPT_MAIN = ("application/vnd.openxmlformats-officedocument."
            "presentationml.presentation.main+xml")
PPT_SLIDESHOW = ("application/vnd.openxmlformats-officedocument."
                 "presentationml.slideshow.main+xml")
PPT_SLIDESHOW_MACRO = "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml"


def ooxml(content_type: str, parts: dict, extra: dict | None = None) -> bytes:
    """Build an OOXML-shaped ZIP with a content type for its main part."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'content-types"><Override PartName="/main" '
            f'ContentType="{content_type}"/></Types>')
        for name, payload in parts.items():
            package.writestr(name, payload)
        for name, payload in (extra or {}).items():
            package.writestr(name, payload)
    return buffer.getvalue()


def odf(mimetype: str, extra: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        package.writestr("content.xml", "<office/>")
        for name, payload in (extra or {}).items():
            package.writestr(name, payload)
    return buffer.getvalue()


class TestOoxmlVariants:
    """A macro-enabled document is a different artifact from a plain one."""

    def test_plain_word_document(self):
        result = identify_bytes(ooxml(WORD_MAIN, {"word/document.xml": "x"}),
                                declared_name="report.docx")
        assert result.format_id == "ooxml.doc"
        assert result.extension == ".docx"
        assert result.family is FormatFamily.OFFICE_OOXML
        assert result.reader == "read_office"
        assert result.macro_present is False
        assert result.discrepancies == []

    def test_word_template_is_not_reported_as_a_document(self):
        result = identify_bytes(ooxml(WORD_TEMPLATE, {"word/document.xml": "x"}),
                                declared_name="letter.dotx")
        assert result.format_id == "ooxml.doc.template"
        assert result.extension == ".dotx"

    @pytest.mark.parametrize("part", ["word/vbaProject.bin", "word/VBAproject.BIN"])
    def test_macro_project_part_makes_it_macro_enabled(self, part):
        data = ooxml(WORD_MAIN, {"word/document.xml": "x"}, {part: "MZ"})
        result = identify_bytes(data, declared_name="report.docm")
        assert result.format_id == "ooxml.doc.macro"
        assert result.extension == ".docm"
        assert result.macro_present is True
        assert result.features["has_macros"] is True

    def test_macro_content_type_without_a_part_is_still_macro_enabled(self):
        result = identify_bytes(ooxml(WORD_MACRO, {"word/document.xml": "x"}),
                                declared_name="report.docm")
        assert result.format_id == "ooxml.doc.macro"
        assert result.features["macro_evidence"] == "macroEnabled content type"

    def test_macro_enabled_document_named_as_a_plain_one_is_a_high_severity_discrepancy(self):
        """The filename must never decide whether macro content is extracted."""
        data = ooxml(WORD_MACRO, {"word/document.xml": "x"},
                     {"word/vbaProject.bin": "MZ"})
        result = identify_bytes(data, declared_name="quarterly.doc")
        assert result.format_id == "ooxml.doc.macro"
        kinds = {d.kind for d in result.discrepancies}
        assert DISC_VARIANT_MISMATCH in kinds
        assert result.high_severity_discrepancies
        assert any(d.severity == SEVERITY_HIGH for d in result.discrepancies)

    def test_macro_enabled_template(self):
        result = identify_bytes(ooxml(WORD_TEMPLATE_MACRO, {"word/document.xml": "x"}),
                                declared_name="template.dotm")
        assert result.format_id == "ooxml.doc.template.macro"
        assert result.extension == ".dotm"

    def test_excel_macro_workbook_and_binary_workbook(self):
        macro = identify_bytes(ooxml(EXCEL_MACRO, {"xl/workbook.xml": "x"},
                                     {"xl/vbaProject.bin": "MZ"}),
                               declared_name="book.xlsm")
        assert macro.format_id == "ooxml.xls.macro"
        binary = identify_bytes(ooxml(EXCEL_MAIN, {"xl/workbook.bin": "BIFF12"}),
                                declared_name="book.xlsb")
        assert binary.format_id == "ooxml.xls.binary"

    def test_slideshow_and_macro_slideshow(self):
        plain = identify_bytes(ooxml(PPT_SLIDESHOW, {"ppt/presentation.xml": "x"}),
                               declared_name="show.ppsx")
        assert plain.format_id == "ooxml.ppt.slideshow"
        macro = identify_bytes(ooxml(PPT_SLIDESHOW_MACRO, {"ppt/presentation.xml": "x"}),
                               declared_name="show.ppsm")
        assert macro.format_id == "ooxml.ppt.slideshow.macro"
        assert macro.macro_present is True

    def test_signature_and_embedded_media_are_recorded_as_features(self):
        data = ooxml(WORD_MAIN, {"word/document.xml": "x", "word/media/image1.png": "P"},
                     {"_xmlsignatures/sig1.xml": "<Signature/>"})
        result = identify_bytes(data, declared_name="signed.docx")
        assert result.features["has_xml_signature"] is True
        assert result.features["entry_count"] >= 3


class TestOdfAndEbook:
    @pytest.mark.parametrize("mimetype,format_id,extension", [
        ("application/vnd.oasis.opendocument.text", "odf.text", ".odt"),
        ("application/vnd.oasis.opendocument.spreadsheet", "odf.spreadsheet", ".ods"),
        ("application/vnd.oasis.opendocument.presentation", "odf.presentation", ".odp"),
        ("application/vnd.oasis.opendocument.text-template", "odf.text.template", ".ott"),
        ("application/epub+zip", "ebook.epub", ".epub"),
    ])
    def test_odf_family_is_identified_from_the_mimetype_part(self, mimetype, format_id, extension):
        result = identify_bytes(odf(mimetype), declared_name=f"doc{extension}")
        assert result.format_id == format_id
        assert result.extension == extension
        assert result.features["mimetype_entry"] == mimetype

    def test_encrypted_odf_is_reported_as_encrypted_not_as_plain(self):
        manifest = ('<manifest><encryption-data><algorithm/></encryption-data>'
                    '</manifest>')
        data = odf("application/vnd.oasis.opendocument.text",
                   {"META-INF/manifest.xml": manifest})
        result = identify_bytes(data, declared_name="secret.odt")
        assert result.encrypted is True
        assert any(d.kind == DISC_ENCRYPTED for d in result.discrepancies)


class TestArchives:
    def test_plain_zip_reports_its_inventory(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("notes.txt", "hello")
            archive.writestr("nested.zip", "PK\x03\x04")
            archive.writestr("report.docx", "x" * 50)
        result = identify_bytes(buffer.getvalue(), declared_name="bundle.zip")
        assert result.format_id == "archive.zip"
        assert result.features["entry_count"] == 3
        assert result.features["nested_archives"] == 1
        assert result.features["nested_documents"] == 1
        assert result.features["uncompressed_bytes"] > 0

    def test_plain_zip_holding_a_docx_is_not_mistaken_for_the_document(self):
        """The bug this guards: the outer archive must be expanded, not read as
        the nested document, or every member disappears from the workload."""
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as package:
            package.writestr("[Content_Types].xml", "<Types/>")
            package.writestr("word/document.xml", "x" * 800)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("poisoned.docx", inner.getvalue())
            archive.writestr("readme.txt", "hello")
        result = identify_bytes(buffer.getvalue(), declared_name="bundle.zip")
        assert result.format_id == "archive.zip", result.as_dict()
        assert result.features["nested_documents"] == 1

    @pytest.mark.parametrize("payload,format_id", [
        (b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32, "archive.rar"),
        (b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32, "archive.7z"),
        (b"\x1f\x8b\x08" + b"\x00" * 32, "archive.gz"),
        (b"BZh9" + b"\x00" * 32, "archive.bz2"),
        (b"\xfd7zXZ\x00" + b"\x00" * 32, "archive.xz"),
        (b"MSCF" + b"\x00" * 32, "archive.cab"),
    ])
    def test_archive_streams_are_identified_by_signature(self, payload, format_id):
        result = identify_bytes(payload, declared_name="payload.dat")
        assert result.format_id == format_id
        assert result.family is FormatFamily.ARCHIVE

    def test_truncated_zip_is_recorded_rather_than_silently_accepted(self):
        data = b"PK\x03\x04" + b"\x00" * 200  # no central directory
        result = identify_bytes(data, declared_name="broken.zip")
        assert result.format_id == "archive.zip"
        assert any(d.kind == "unsupported_format" for d in result.discrepancies)


class TestPdf:
    def test_version_and_features_are_read_from_the_body(self):
        pdf = (b"%PDF-1.6\n"
               b"1 0 obj << /Type /Catalog /JavaScript 2 0 R /AcroForm 3 0 R >> endobj\n"
               b"6 0 obj << /Type /Filespec /EF << /F 7 0 R >> >> endobj\n"
               b"7 0 obj << /Type /EmbeddedFile >> endobj\n"
               b"trailer << /Root 1 0 R >>\n%%EOF\n"
               b"%PDF-1.6\n8 0 obj << /Linearized 1 >> endobj\n%%EOF\n")
        result = identify_bytes(pdf, declared_name="report.pdf")
        assert result.format_id == "pdf"
        assert result.version == "PDF-1.6"
        assert result.features["javascript"] is True
        assert result.features["embedded_files"] is True
        assert result.features["acroform"] is True
        assert result.features["linearized"] is True
        assert result.features["incremental_updates"] == 1

    def test_encrypted_pdf_is_recorded_as_encrypted(self):
        pdf = b"%PDF-1.7\n5 0 obj << /Filter /Standard >> endobj\ntrailer << /Encrypt 5 0 R >>\n%%EOF"
        result = identify_bytes(pdf, declared_name="locked.pdf")
        assert result.encrypted is True
        assert any(d.kind == DISC_ENCRYPTED for d in result.discrepancies)

    def test_pdf_named_as_an_image_is_a_high_severity_discrepancy(self):
        result = identify_bytes(b"%PDF-1.4\n%%EOF", declared_name="scan.jpg")
        assert result.format_id == "pdf"
        assert any(d.kind == DISC_FORMAT_MISMATCH and d.severity == SEVERITY_HIGH
                   for d in result.discrepancies)


class TestEmail:
    def test_rfc822_message_without_an_extension(self):
        message = (b"Received: from a.example by b.example; Mon, 1 Jan 2024 00:00:00 +0000\n"
                   b"From: sender@example.com\nTo: recipient@example.com\n"
                   b"Subject: evidence\nMIME-Version: 1.0\nContent-Type: text/plain\n\nbody\n")
        result = identify_bytes(message, declared_name="message")
        assert result.format_id == "email.eml"
        assert result.confidence == "weak"
        assert len(result.features["email_headers"]) >= 5
        assert any(d.kind == DISC_EXTENSION_ABSENT for d in result.discrepancies)

    def test_mbox_separator_line(self):
        message = b"From sender@example.com Mon Jan  1 00:00:00 2024\nReceived: from a by b\nSubject: s\n"
        result = identify_bytes(message, declared_name="mail.mbox")
        assert result.format_id == "email.mbox"

    def test_pst_and_ost_share_a_signature_so_the_declared_name_is_recorded_as_the_source(self):
        payload = b"!BDN" + b"\x00" * 200
        pst = identify_bytes(payload, declared_name="mail.pst")
        assert pst.format_id == "email.pst"
        ost = identify_bytes(payload, declared_name="mail.ost")
        assert ost.format_id == "email.ost"
        assert "declared extension" in ost.features["store_variant_source"]

    def test_msg_is_recognised_from_its_mapi_streams(self, tmp_path):
        path = str(tmp_path / "message.msg")
        build_compound_file(path, {
            "__properties_version1.0": b"\x00" * 32,
            "__substg1.0_0037001E": "subject".encode("utf-16-le"),
        })
        result = identify(path, declared_name="message.msg")
        assert result.format_id in ("ole.msg", "email.msg")
        assert result.features["mapi_streams"] >= 2


class TestLegacyOffice:
    """Legacy OLE Office: the container is identical, the payload decides."""

    @staticmethod
    def _word_fib(*, template: bool = False, encrypted: bool = False) -> bytes:
        fib = bytearray(0x400)
        fib[0:2] = b"\xec\xa5"                      # wIdent
        flags = 0
        if template:
            flags |= 0x0001                          # fDot
        if encrypted:
            flags |= 0x0100                          # fEncrypted
        fib[10:12] = flags.to_bytes(2, "little")
        return bytes(fib)

    @staticmethod
    def _biff_bof(document_type: int) -> bytes:
        return b"\x09\x08" + document_type.to_bytes(2, "little") + b"\x00" * 12

    def test_word_document_and_template_are_distinguished_by_the_fib(self, tmp_path):
        document_path = str(tmp_path / "report.doc")
        build_compound_file(document_path, {"WordDocument": self._word_fib()})
        document = identify(document_path, declared_name="report.doc")
        assert document.format_id == "ole.doc"
        assert document.features["fdot"] is False

        template_path = str(tmp_path / "letter.dot")
        build_compound_file(template_path, {"WordDocument": self._word_fib(template=True)})
        template = identify(template_path, declared_name="letter.dot")
        assert template.format_id == "ole.doc.template"
        assert template.features["fdot"] is True

    def test_word_document_with_a_vba_program_is_macro_capable(self, tmp_path):
        path = str(tmp_path / "macro_doc.doc")
        build_compound_file(path, {
            "WordDocument": self._word_fib(),
            "_VBA_PROJECT_CUR/VBA/dir": b"\x01\x00",
            "PROJECT": b"Module=Module1",
        })
        result = identify(path, declared_name="macro_doc.doc")
        assert result.format_id == "ole.doc.macro"
        assert result.macro_present is True
        assert any("vba" in part.lower() for part in result.features["macro_streams"])

    def test_encrypted_legacy_document_is_reported_as_encrypted(self, tmp_path):
        path = str(tmp_path / "locked.doc")
        build_compound_file(path, {"WordDocument": self._word_fib(encrypted=True)})
        result = identify(path, declared_name="locked.doc")
        assert result.encrypted is True
        assert any(d.kind == DISC_ENCRYPTED for d in result.discrepancies)

    def test_excel_workbook_and_template_are_distinguished_by_the_biff_bof(self, tmp_path):
        workbook_path = str(tmp_path / "book.xls")
        build_compound_file(workbook_path, {"Workbook": self._biff_bof(0x0010)})
        assert identify(workbook_path, declared_name="book.xls").format_id == "ole.xls"

        template_path = str(tmp_path / "book.xlt")
        build_compound_file(template_path, {"Workbook": self._biff_bof(0x0020)})
        template = identify(template_path, declared_name="book.xlt")
        assert template.format_id == "ole.xls.template"
        assert template.features["biff_document_type"] == 0x20

    def test_powerpoint_variant_is_taken_from_the_declared_name_and_said_so(self, tmp_path):
        path = str(tmp_path / "deck.pps")
        build_compound_file(path, {"PowerPoint Document": b"\x0f\x00\xe8\x03" + b"\x00" * 64})
        result = identify(path, declared_name="deck.pps")
        assert result.format_id == "ole.ppt.slideshow"
        assert "declared extension" in result.features["variant_source"]

    def test_expected_pdf_inside_a_password_protected_package(self, tmp_path):
        """An encrypted OOXML file is a CFB with EncryptionInfo; it must not be
        reported as a readable document."""
        path = str(tmp_path / "locked.docx")
        build_compound_file(path, {
            "EncryptionInfo": b"\x04\x00\x04\x00",
            "EncryptedPackage": b"\x00" * 64,
        })
        result = identify(path, declared_name="locked.docx")
        assert result.encrypted is True
        assert result.format_id == "office.encrypted"
        assert result.family is FormatFamily.OFFICE_OOXML

    def test_unrecognised_ole_container_is_not_guessed(self, tmp_path):
        path = str(tmp_path / "blob.bin")
        build_compound_file(path, {"RandomStream": b"payload"})
        result = identify(path, declared_name="blob.bin")
        assert result.format_id == "ole.unknown"
        assert any(d.kind == DISC_FORMAT_MISMATCH for d in result.discrepancies)


class TestTextAndBinary:
    def test_text_without_a_known_extension_is_identified_as_text(self):
        result = identify_bytes(b"line one\nline two\n" * 40, declared_name="notes.xyz")
        assert result.family is FormatFamily.TEXT
        assert result.confidence == "weak"
        assert any(d.kind == DISC_EXTENSION_UNKNOWN for d in result.discrepancies)

    def test_binary_declared_as_text_is_reported_as_binary(self):
        result = identify_bytes(b"\x00\x01\x02\x03" * 100, declared_name="notes.txt")
        assert result.family is FormatFamily.BINARY
        assert any(d.kind == DISC_BINARY_TEXT and d.severity == SEVERITY_HIGH
                   for d in result.discrepancies)

    def test_empty_file_is_unknown_with_a_reason(self):
        result = identify_bytes(b"", declared_name="empty.pdf")
        assert result.format_id == "binary.unknown"
        assert result.confidence == "none"
        assert any(d.kind == "unreadable" for d in result.discrepancies)

    def test_text_declared_as_an_image_is_read_as_text(self):
        """Reported from a real ingest: a text file named ``fake-png.png``.

        The declared type was kept, so the file went to the image reader and
        failed with "cannot identify image file" - an ERROR in the log for a
        file whose bytes are perfectly readable text. The content decides, and
        the reader that follows from the content is the text reader.
        """
        result = identify_bytes(b"this is plain text pretending to be a png\n" * 20,
                                declared_name="fake-png.png")
        assert result.format_id == "text.plain"
        assert result.family is FormatFamily.TEXT
        assert result.reader == "read_remaining"
        assert result.features.get("declared_type_rejected_for_content") == "image.png"
        assert any(d.kind == DISC_FORMAT_MISMATCH and d.severity == SEVERITY_HIGH
                   for d in result.discrepancies)

    def test_text_declared_as_an_archive_is_read_as_text(self):
        result = identify_bytes(b"not an archive at all, just words\n" * 20,
                                declared_name="evidence.zip")
        assert result.family is FormatFamily.TEXT
        assert result.reader == "read_remaining"
        assert any(d.kind == DISC_FORMAT_MISMATCH for d in result.discrepancies)

    def test_a_text_capable_declared_type_is_still_honoured(self):
        """SVG is XML text but its declared type names the right reader.

        Rejecting every image-family declaration for text content would take
        SVG away from the SVG reader, which is the opposite of the fix.
        """
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text>x</text></svg>'
        result = identify_bytes(svg, declared_name="chart.svg")
        assert result.format_id == "image.svg"
        assert result.reader == "read_img_fast"

    def test_text_capable_declarations_keep_their_own_format(self):
        """A .js or .json file is not "unspecific plain text"."""
        script = identify_bytes(b"function f() { return 1; }\n" * 10,
                                declared_name="app.js")
        assert script.format_id == "text.script"
        assert not script.high_severity_discrepancies

        data = identify_bytes(b'{"evidence": true}\n' * 10, declared_name="doc.json")
        assert data.format_id == "text.json"
        assert not data.high_severity_discrepancies

    def test_jpeg_alias_is_not_reported_as_a_contradiction(self):
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 100
        result = identify_bytes(jpeg, declared_name="photo.jpeg")
        assert result.extension in (".jpg", ".jpeg")
        assert all(d.kind != DISC_FORMAT_MISMATCH for d in result.discrepancies)
        assert not result.high_severity_discrepancies


class TestCatalogueIntegrity:
    """Identification and processing must not drift apart."""

    def test_every_catalogued_format_has_a_reader_family(self):
        for spec in CATALOG.values():
            assert spec.family in READER_BY_FAMILY, spec.format_id
            if not spec.identified_only:
                assert READER_BY_FAMILY[spec.family], spec.format_id

    def test_every_reader_extension_is_accepted_by_its_reader(self):
        """A format the catalogue routes must be read by the reader it routes
        to. Otherwise identification silently sends files to a reader that
        refuses them, which is how artifacts disappear from a run."""
        import importlib

        instances = {}
        mismatches = []
        for extension, format_id in reader_extensions().items():
            spec = lookup_format(format_id)
            assert spec is not None
            module_name = f"reader_file.readers.{READER_BY_FAMILY[spec.family]}"
            instance = instances.get(module_name)
            if instance is None:
                module = importlib.import_module(module_name)
                reader_class = next(
                    obj for name, obj in vars(module).items()
                    if name.endswith("FileReader") and name != "BaseReader")
                instance = instances[module_name] = reader_class()
            supported = {e.lower() for e in instance.get_supported_extensions()}
            if extension not in supported:
                mismatches.append((extension, format_id, module_name))
        assert not mismatches, (
            "catalogue extensions with no implementation in the reader they route to: "
            f"{mismatches}")

    def test_identified_only_formats_reach_an_explicit_unsupported_outcome(self, tmp_path):
        """An identified-only format must be *recorded* as unsupported.

        Identification that routes a file to a reader that refuses it, or to no
        reader at all without the ledger noticing, is how artifacts disappear
        from a run. This checks the whole path: catalogue -> router -> service
        -> ledger classification.
        """
        from pipeline.progress_ledger import OUTCOME_UNSUPPORTED, classify_result
        from reader_file.services.file_reader_service import FileReaderService

        # 8BPS = Photoshop; the catalogue declares it identified-only.
        target = tmp_path / "mask.psd"
        target.write_bytes(b"8BPS" + b"\x00\x01" + b"\x00" * 64)
        service = FileReaderService()
        result = service.read_file({"path": str(target), "extension": ".psd"})
        assert result is not None
        assert "unsupported file type" in str(result.get("error", "")).lower(), result
        assert classify_result(result) == OUTCOME_UNSUPPORTED
        # The identity survives even though the payload is not parsed.
        decision = result.get("type_detection") or {}
        assert decision.get("format_id"), decision
        assert decision.get("mime_type"), decision

    def test_a_content_contradiction_wins_over_the_declared_extension(self, tmp_path):
        """A .txt holding a PDF must be read as a PDF, with the mismatch recorded."""
        from reader_file.services.file_reader_service import FileReaderService

        target = tmp_path / "not_really_text.txt"
        target.write_bytes(b"%PDF-1.4\n" + b"\x00" * 64)
        service = FileReaderService()
        decision = service.resolve_type_for_file(str(target), ".txt")
        assert decision["effective_extension"] == ".pdf", decision
        assert decision["extension_mismatch"] is True, decision
        assert decision["format_identification"]["detected_extension"] == ".pdf"
        assert any(d["kind"] == "format_mismatch"
                   for d in decision["format_discrepancies"]), decision

    def test_formats_without_a_parser_say_so_and_keep_their_identity(self):
        """Identified-but-unparsable is a recorded outcome, not a silent skip."""
        identified_only = [spec for spec in CATALOG.values() if spec.identified_only]
        assert identified_only, "the identified-only list must not be empty"
        for spec in identified_only:
            assert spec.extensions, spec.format_id
            assert spec.identified_only_reason, (
                f"{spec.format_id} is marked identified-only without a reason")
            assert spec.mime and spec.description, spec.format_id

    def test_aliases_resolve_to_the_canonical_extension_and_are_declared(self):
        assert canonical_extension(".jpeg") == ".jpg"
        assert canonical_extension(".tif") == ".tiff"
        assert canonical_extension(".tgz") == ".gz"
        assert canonical_extension(".uncharted") == ".uncharted"
        for alias, canonical in alias_extensions().items():
            assert alias.startswith(".") and canonical.startswith(".")
            assert lookup_by_extension(alias) is lookup_by_extension(canonical)

    def test_every_spelling_in_the_catalogue_is_looked_up(self):
        for extension in catalogue_extensions():
            assert lookup_by_extension(extension) is not None, extension

    def test_macro_capable_formats_are_declared_as_such(self):
        for format_id in ("ooxml.doc.macro", "ooxml.xls.macro", "ooxml.ppt.macro",
                          "ooxml.doc.template.macro", "ooxml.ppt.slideshow.macro"):
            assert lookup_format(format_id).macro_capable is True
        assert lookup_format("ooxml.doc").macro_capable is False

    def test_declared_extensions_from_the_catalogue(self):
        spec = lookup_by_extension("DOCM")
        assert spec is not None and spec.format_id == "ooxml.doc.macro"
        assert lookup_by_extension("") is None
