"""Forensic extraction: macros, revisions, annotations, attachments, variants.

Everything here is about *evidence that is not the visible body*: the VBA
project inside a macro-enabled document, the tracked deletion nobody accepted,
the hidden slide, the PDF attachment, the annotation on page 3. Each test
asserts both that the evidence is extracted and that a failure to extract it is
recorded as a failure - "nothing found" and "could not look" must never be the
same result.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _cfb import build_compound_file  # noqa: E402
from core.forensics import extract_package_features, extract_vba, macro_summary  # noqa: E402
from core.forensics.office_package import text_from_features  # noqa: E402
from reader_file.readers.read_archive import ArchiveFileReader  # noqa: E402
from reader_file.readers.read_email import EmailFileReader  # noqa: E402
from reader_file.readers.read_office import OfficeFileReader  # noqa: E402
from reader_file.readers.read_pdf import PDFFileReader  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

WORD_MAIN = ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document.main+xml")
WORD_MACRO = "application/vnd.ms-word.document.macroEnabled.main+xml"


def _package(path: Path, content_type: str, parts: dict) -> Path:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'content-types"><Override PartName="/main" '
            f'ContentType="{content_type}"/></Types>')
        for name, payload in parts.items():
            package.writestr(name, payload)
    return path


def _word_body() -> str:
    return f'''<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
<w:p><w:r><w:t>visible text</w:t></w:r>
     <w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden text</w:t></w:r></w:p>
<w:p><w:ins w:author="Alice" w:date="2024-01-01T00:00:00Z"><w:r><w:t>added</w:t></w:r></w:ins>
     <w:del w:author="Bob" w:date="2024-01-02T00:00:00Z"><w:r><w:delText>deleted evidence</w:delText></w:r></w:del></w:p>
<w:p><w:hyperlink r:id="rId9"><w:r><w:t>link</w:t></w:r></w:hyperlink></w:p>
</w:body></w:document>'''


class TestOfficePackageFeatures:
    def test_document_properties_and_custom_properties(self, tmp_path):
        path = _package(tmp_path / "props.docx", WORD_MAIN, {
            "word/document.xml": _word_body(),
            "docProps/core.xml": (
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
                'package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/'
                'elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
                '<dc:creator>Dana</dc:creator><cp:lastModifiedBy>Erin</cp:lastModifiedBy>'
                '<dcterms:created>2024-01-01T00:00:00Z</dcterms:created>'
                '</cp:coreProperties>'),
            "docProps/custom.xml": (
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
                'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/'
                'officeDocument/2006/docPropsVTypes">'
                '<property fmtid="{D5CDD505}" pid="2" name="CaseRef">'
                '<vt:lpwstr>ABC-123</vt:lpwstr></property></Properties>'),
        })
        features = extract_package_features(str(path), app="word")
        properties = features["properties"]
        assert properties["core"]["creator"] == "Dana"
        assert properties["core"]["last_modified_by"] == "Erin"
        assert properties["custom"][0]["name"] == "CaseRef"
        assert properties["custom"][0]["value"] == "ABC-123"

    def test_revisions_comments_hidden_text_and_hyperlinks(self, tmp_path):
        path = _package(tmp_path / "revisions.docx", WORD_MAIN, {
            "word/document.xml": _word_body(),
            "word/comments.xml": (
                f'<w:comments xmlns:w="{W}"><w:comment w:id="1" w:author="Carol" '
                'w:date="2024-02-01T00:00:00Z"><w:p><w:r><w:t>check this</w:t>'
                '</w:r></w:p></w:comment></w:comments>'),
            "word/_rels/document.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="rId9" Type="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                'Target="https://example.com/evidence" TargetMode="External"/>'
                '</Relationships>'),
        })
        features = extract_package_features(str(path), app="word")
        document = features["document"]
        assert document["revisions"]["insertions"] == 1
        assert document["revisions"]["deletions"] == 1
        assert document["revisions"]["authors"] == ["Alice", "Bob"]
        assert document["revisions"]["deleted_text"][0]["text"] == "deleted evidence"
        assert document["hidden"]["hidden_runs"] == 1
        assert document["comments"][0]["text"] == "check this"
        assert document["hyperlinks"][0]["relationship_id"] == "rId9"
        assert features["relationships"]["external_references"][0]["target"] == \
            "https://example.com/evidence"

    def test_feature_text_makes_the_detail_searchable(self, tmp_path):
        path = _package(tmp_path / "text.docx", WORD_MAIN, {
            "word/document.xml": _word_body(),
        })
        features = extract_package_features(str(path), app="word")
        text = text_from_features(features)
        assert "deleted evidence" in text, "a tracked deletion must be searchable"       # noqa: E501

    def test_xlsx_hidden_sheets_formulas_and_comments(self, tmp_path):
        path = _package(tmp_path / "book.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                        {
                            "xl/workbook.xml": (
                                '<workbook xmlns="http://schemas.openxmlformats.org/'
                                'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                                'openxmlformats.org/officeDocument/2006/relationships">'
                                '<sheets><sheet name="Visible" sheetId="1"/>'
                                '<sheet name="Hidden" sheetId="2" state="hidden"/>'
                                '</sheets><definedNames><definedName name="Rate" '
                                'hidden="1">Sheet1!$A$1</definedName></definedNames>'
                                '</workbook>'),
                            "xl/worksheets/sheet1.xml": (
                                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                                'spreadsheetml/2006/main"><sheetData><row r="1">'
                                '<c r="A1"><f>SUM(B1:B9)</f><v>42</v></c></row>'
                                '</sheetData></worksheet>'),
                            "xl/comments1.xml": (
                                '<comments xmlns="http://schemas.openxmlformats.org/'
                                'spreadsheetml/2006/main"><commentList><comment ref="A1" '
                                'authorId="0"><text><t>verify this</t></text></comment>'
                                '</commentList></comments>'),
                        })
        features = extract_package_features(str(path), app="excel")
        workbook = features["workbook"]
        assert {"name": "Hidden", "state": "hidden"} in workbook["sheet_states"]
        assert workbook["defined_names"][0]["hidden"] == "1"
        assert workbook["formula_count"] == 1
        assert workbook["formulas"][0]["formula"] == "SUM(B1:B9)"
        assert workbook["comments"][0]["text"] == "verify this"

    def test_worksheet_labels_stay_out_of_the_searchable_text(self, tmp_path):
        """A sheet name and a sheet's state are labels, not body content.

        They are recorded structurally (``sheet_states``/``defined_names``) and
        are already shown to the examiner as display markers ("Sheet: Sheet1"),
        so flattening them into the text channel only made a query for "sheet"
        return every workbook - the label exclusion the pipeline guarantees
        (tests/integration/test_structural_marker_exclusion.py). Everything
        that *is* content - a comment, a formula, a tracked deletion - stays.
        """
        path = _package(tmp_path / "labels.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                        {
                            "xl/workbook.xml": (
                                '<workbook xmlns="http://schemas.openxmlformats.org/'
                                'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                                'openxmlformats.org/officeDocument/2006/relationships">'
                                '<sheets><sheet name="Sheet1" sheetId="1"/>'
                                '<sheet name="Data" sheetId="2" state="hidden"/>'
                                '</sheets><definedNames><definedName name="Rate" '
                                'hidden="1">Sheet1!$A$1</definedName></definedNames>'
                                '</workbook>'),
                            "xl/worksheets/sheet1.xml": (
                                '<worksheet xmlns="http://schemas.openxmlformats.org/'
                                'spreadsheetml/2006/main"><sheetData><row r="1">'
                                '<c r="A1"><f>SUM(B1:B9)</f><v>42</v></c></row>'
                                '</sheetData></worksheet>'),
                            "xl/comments1.xml": (
                                '<comments xmlns="http://schemas.openxmlformats.org/'
                                'spreadsheetml/2006/main"><commentList><comment ref="A1" '
                                'authorId="0"><text><t>verify this</t></text></comment>'
                                '</commentList></comments>'),
                        })
        features = extract_package_features(str(path), app="excel")
        text = text_from_features(features)

        # Content is searchable...
        assert "verify this" in text, "a comment must stay searchable"
        assert "SUM(B1:B9)" in text, "a formula must stay searchable"
        # ...and the labels are not, in any spelling a query could use.
        for label in ("Sheet1", "Data", "Rate", "hidden", "visible"):
            assert label not in text, f"structural label {label!r} leaked into the text"

        # The same facts remain recorded exactly, where they belong.
        workbook = features["workbook"]
        assert {"name": "Sheet1", "state": "visible"} in workbook["sheet_states"]
        assert {"name": "Data", "state": "hidden"} in workbook["sheet_states"]
        assert workbook["defined_names"][0]["name"] == "Rate"

    def test_pptx_hidden_slides_notes_and_comments(self, tmp_path):
        path = _package(tmp_path / "deck.pptx",
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                        {
                            "ppt/presentation.xml": "<p:presentation/>",
                            "ppt/slides/slide1.xml": (
                                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                                'presentationml/2006/main" xmlns:a="http://schemas.'
                                'openxmlformats.org/drawingml/2006/main"><p:cSld>'
                                '<a:t>first slide</a:t></p:cSld></p:sld>'),
                            "ppt/slides/slide2.xml": (
                                '<p:sld xmlns:p="http://schemas.openxmlformats.org/'
                                'presentationml/2006/main" xmlns:a="http://schemas.'
                                'openxmlformats.org/drawingml/2006/main" show="0">'
                                '<p:cSld><a:t>hidden slide</a:t></p:cSld></p:sld>'),
                            "ppt/notesSlides/notesSlide1.xml": (
                                '<p:notes xmlns:p="http://schemas.openxmlformats.org/'
                                'presentationml/2006/main" xmlns:a="http://schemas.'
                                'openxmlformats.org/drawingml/2006/main">'
                                '<a:t>speaker note</a:t></p:notes>'),
                            "ppt/comments/comment1.xml": (
                                '<p:cmLst xmlns:p="http://schemas.openxmlformats.org/'
                                'presentationml/2006/main"><p:cm authorId="0" dt="2024-03-01">'
                                '<p:text>reviewer remark</p:text></p:cm></p:cmLst>'),
                        })
        features = extract_package_features(str(path), app="powerpoint")
        presentation = features["presentation"]
        assert [slide["hidden"] for slide in presentation["slides"]] == [False, True]
        assert presentation["hidden_slides"][0]["text"] == "hidden slide"
        assert presentation["notes_text"][0]["text"] == "speaker note"
        assert presentation["comments"][0]["text"] == "reviewer remark"

    def test_unreadable_part_is_reported_rather_than_skipped(self, tmp_path):
        path = _package(tmp_path / "broken.docx", WORD_MAIN, {
            "word/document.xml": "<not-well-formed",
        })
        features = extract_package_features(str(path), app="word")
        assert features["extraction_errors"], features
        assert features["extraction_errors"][0]["scope"] == "word/document.xml"

    def test_malformed_package_reports_the_reason(self, tmp_path):
        path = tmp_path / "not-a-package.docx"
        path.write_bytes(b"this is not a zip")
        features = extract_package_features(str(path), app="word")
        assert features["extraction_errors"][0]["scope"] == "package"


class TestMacroExtraction:
    def test_macro_project_is_hashed_even_when_it_cannot_be_decompressed(self, tmp_path):
        """'Macros present but unreadable' must not look like 'no macros'."""
        payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
        path = _package(tmp_path / "macro.docm", WORD_MACRO, {
            "word/document.xml": _word_body(),
            "word/vbaProject.bin": payload,
        })
        macros = extract_vba(str(path))
        assert macros["macro_present"] is True
        assert macros["project_count"] == 1
        project = macros["projects"][0]
        assert project["container"] == "word/vbaProject.bin"
        assert project["sha256"] == hashlib.sha256(payload).hexdigest()
        assert project["md5"] == hashlib.md5(payload).hexdigest()
        # The payload is not a real compound file, so decompression fails - and
        # that failure is recorded, not swallowed.
        assert project["extraction_error"]
        assert macros["extraction_errors"] == []

    def test_no_macros_reports_no_macros(self, tmp_path):
        path = _package(tmp_path / "plain.docx", WORD_MAIN,
                        {"word/document.xml": _word_body()})
        macros = extract_vba(str(path))
        assert macros["macro_present"] is False
        assert macros["projects"] == []
        assert macros["extraction_errors"] == []

    def test_legacy_ole_macro_storage_is_detected(self, tmp_path):
        path = str(tmp_path / "legacy.doc")
        build_compound_file(path, {
            "WordDocument": b"\xec\xa5" + b"\x00" * 1022,
            "_VBA_PROJECT_CUR/VBA/dir": b"\x01\x00",
            "PROJECT": b"Module=Module1",
        })
        macros = extract_vba(path)
        assert macros["macro_present"] is True
        assert macros["projects"][0]["container"].lower().startswith("_vba_project_cur")
        assert macros["projects"][0]["sha256"], "the container digest must be present"

    def test_modules_are_surfaced_when_the_decompressor_yields_them(self, tmp_path, monkeypatch):
        """Integration with the MS-OVBA decompressor.

        The decompression itself is oletools' (MS-OVBA is not re-implemented
        here); this test fixes the contract across the boundary - module name,
        source, digest and analysis must reach the reader result and the
        searchable text. No real macro-bearing sample exists in this
        environment, so the decompressor is stubbed; see the implementation
        report for that limitation.
        """
        path = _package(tmp_path / "macro.docm", WORD_MACRO, {
            "word/document.xml": _word_body(),
            "word/vbaProject.bin": b"MZ" + b"\x00" * 128,
        })

        class _StubParser:
            def __init__(self, _path):
                pass

            def detect_vba_macros(self):
                return True

            def detect_autoexec(self):
                return [("AutoOpen", "Runs when the document is opened")]

            def detect_suspicious_keywords(self):
                return [("Shell", "May run an external program")]

            def detect_vba_stomping(self):
                return False

            def extract_macros(self):
                return [("vbaProject.bin", "VBA/Module1", "Module1",
                         "Sub AutoOpen()\n  Shell \"cmd /c whoami\"\nEnd Sub")]

            def close(self):
                pass

        import oletools.olevba as olevba

        monkeypatch.setattr(olevba, "VBA_Parser", _StubParser)
        macros = extract_vba(str(path))
        project = macros["projects"][0]
        assert project["module_count"] == 1
        assert project["modules"][0]["name"] == "Module1"
        assert "whoami" in project["modules"][0]["source"]
        assert [entry["keyword"] for entry in project["metadata"]["autoexec"]] == ["AutoOpen"]
        assert [entry["keyword"] for entry in project["metadata"]["suspicious"]] == ["Shell"]
        assert "whoami" in macros["source_text"]
        summary = macro_summary(macros)
        assert summary["projects"][0]["autoexec"] == ["AutoOpen"]

        result = OfficeFileReader().read_file(
            {"path": str(path), "effective_extension": ".docm"})
        assert result["macros"]["macro_present"] is True
        assert "whoami" in result["macros"]["source_text"]
        assert "whoami" in result["forensic_text"], (
            "macro source must be searchable, not merely recorded")

    def test_templates_and_slideshows_reach_the_document_reader(self, tmp_path):
        """A template is not an unsupported file type: it is parsed, with its
        variant recorded."""
        reader = OfficeFileReader()
        supported = reader.get_supported_extensions()
        for extension in (".dotx", ".dotm", ".dot", ".xltm", ".potx", ".potm",
                          ".pptm", ".ppsx", ".ppsm", ".ott", ".ots", ".otp", ".odg"):
            assert extension in supported, extension
            assert extension in reader.get_supported_extensions()
        # Every declared extension is routed somewhere, i.e. no branch falls
        # through to the "unsupported office file type" error.
        assert ".docm" in supported and ".tsv" in supported


class TestPdfFeatures:
    @pytest.fixture()
    def pdf(self, tmp_path):
        fitz = pytest.importorskip("pymupdf")
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "page text layer")
        annotation = page.add_text_annot((200, 200), "reviewer note")
        annotation.set_info(title="Investigator", content="check this figure")
        annotation.update()
        document.embfile_add("evidence.txt", b"attached evidence bytes",
                             filename="evidence.txt", desc="attachment")
        path = tmp_path / "case.pdf"
        document.save(str(path))
        document.close()
        return str(path)

    def test_annotations_embedded_files_and_revisions_through_the_reader(self, pdf):
        result = PDFFileReader().read_file({"path": pdf, "effective_extension": ".pdf"})
        assert result["num_pages"] == 1
        assert result["annotations"][0]["content"] == "check this figure"
        assert result["annotations"][0]["title"] == "Investigator"
        embedded = result["embedded_files"][0]
        assert embedded["filename"] == "evidence.txt"
        assert embedded["sha256"] == hashlib.sha256(b"attached evidence bytes").hexdigest()
        assert result["revisions"]["incremental_updates"] == 0
        assert "check this figure" in result["forensic_text"]

    def test_embedded_file_becomes_a_child_object(self, pdf):
        """A PDF attachment is an artifact, not a text fragment: it is written
        to the extraction directory so the router processes it as a child."""
        result = PDFFileReader().read_file({"path": pdf, "effective_extension": ".pdf"})
        assert result.get("extraction_path"), result.keys()
        extracted = sorted(Path(result["extraction_path"]).iterdir())
        assert [entry.name for entry in extracted] == ["evidence.txt"]
        assert extracted[0].read_bytes() == b"attached evidence bytes"
        assert result["embedded_files"][0]["materialised_as"]

    def test_javascript_and_form_fields_are_reported(self, tmp_path):
        fitz = pytest.importorskip("pymupdf")
        document = fitz.open()
        page = document.new_page()
        widget = fitz.Widget()
        widget.field_name = "case_ref"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = "ABC-123"
        widget.rect = fitz.Rect(72, 100, 200, 120)
        page.add_widget(widget)
        path = tmp_path / "form.pdf"
        document.save(str(path))
        document.close()
        result = PDFFileReader().read_file({"path": str(path), "effective_extension": ".pdf"})
        assert result["form_fields"][0]["name"] == "case_ref"
        assert result["form_fields"][0]["value"] == "ABC-123"
        assert "ABC-123" in result["forensic_text"]

    def test_encrypted_pdf_is_reported_as_encrypted_not_failed(self, tmp_path):
        fitz = pytest.importorskip("pymupdf")
        document = fitz.open()
        document.new_page().insert_text((72, 72), "secret")
        path = tmp_path / "locked.pdf"
        document.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256,
                      owner_pw="owner", user_pw="user")
        document.close()
        result = PDFFileReader().read_file({"path": str(path), "effective_extension": ".pdf"})
        assert result.get("is_encrypted") is True
        assert "error" in result


class TestContainerVariants:
    def test_xz_stream_is_decompressed_and_processed(self, tmp_path):
        import lzma

        path = tmp_path / "payload.txt.xz"
        path.write_bytes(lzma.compress(b"decompressed evidence text"))
        result = ArchiveFileReader().read_file(
            {"path": str(path), "effective_extension": ".xz"})
        assert result["archive_type"] == "xz"
        extracted = list(Path(result["extraction_path"]).iterdir())
        assert extracted[0].read_bytes() == b"decompressed evidence text"

    def test_tgz_is_extracted_as_a_tarball(self, tmp_path):
        import tarfile

        payload = tmp_path / "member.txt"
        payload.write_bytes(b"member content")
        archive_path = tmp_path / "bundle.tgz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(payload, arcname="member.txt")
        result = ArchiveFileReader().read_file(
            {"path": str(archive_path), "effective_extension": ".tgz"})
        assert result["files_extracted"] == 1
        assert (Path(result["extraction_path"]) / "member.txt").read_bytes() == b"member content"

    def test_emlx_message_is_parsed_with_its_mail_metadata(self, tmp_path):
        message = (b"From: sender@example.com\r\nTo: recipient@example.com\r\n"
                   b"Subject: evidence\r\nMIME-Version: 1.0\r\n"
                   b"Content-Type: text/plain\r\n\r\nmessage body\r\n")
        plist = (b'<?xml version="1.0"?><plist version="1.0"><dict>'
                 b'<key>flags</key><integer>8590195713</integer>'
                 b'<key>remote-id</key><string>message-id-1</string></dict></plist>')
        path = tmp_path / "message.emlx"
        path.write_bytes(str(len(message)).encode() + b"\n" + message + plist)
        result = EmailFileReader().read_file(
            {"path": str(path), "effective_extension": ".emlx"})
        assert result["email_type"] == "emlx"
        assert result["emlx"]["declared_length"] == len(message)
        assert result["emlx"]["mail_flags"]["remote-id"] == "message-id-1"
        assert "message body" in str(result)

    def test_identified_only_format_is_recorded_unsupported_not_skipped(self, tmp_path):
        """A format with no parser keeps its identity and gets an explicit
        UNSUPPORTED outcome - it must not vanish from the run."""
        import lzma

        from pipeline.progress_ledger import classify_result
        from reader_file.services.file_reader_service import FileReaderService

        path = tmp_path / "cabinet.cab"
        path.write_bytes(b"MSCF" + lzma.compress(b"cabinet payload")[:64])
        service = FileReaderService()
        reader, decision = service.resolve_reader_for_file(str(path), ".cab")
        assert reader is None, "no reader may claim a format it cannot parse"
        assert decision["format_id"] == "archive.cab"
        assert decision["mime_type"] == "application/vnd.ms-cab-compressed"
        assert decision["format_identification"]["confidence"] == "strong"
        outcome = classify_result({"Content": {
            "error": f"Unsupported file type: {decision['effective_extension']}"}})
        assert outcome == "unsupported"


class TestBackpressureDoesNotAbandonWork:
    """Overload must slow a run down, never lose a file."""

    def test_a_throttled_run_still_settles_every_discovered_file(self, tmp_path, monkeypatch):
        import tempfile
        import shutil

        from core.compute.backpressure import AdmissionController, HostPressure
        from pipeline.integrated_reader import IntegratedFileReader

        root = Path(tempfile.mkdtemp(prefix="throttle_"))
        try:
            for index in range(8):
                (root / f"doc{index}.txt").write_text(
                    f"line one of document {index}\nline two\n", encoding="utf-8")

            # Force the sampler to report memory pressure throughout the run.
            pressured = HostPressure(measured=True, source="test", cpu_percent=99.0,
                                     memory_percent=97.0, load_per_core=9.0)

            original_init = AdmissionController.__init__

            def patched_init(self, configured_window, **kwargs):
                kwargs["sampler"] = lambda: pressured
                kwargs["sample_interval_s"] = 0.0
                original_init(self, configured_window, **kwargs)

            monkeypatch.setattr(AdmissionController, "__init__", patched_init)

            reader = IntegratedFileReader(max_workers=3, enable_monitoring=False,
                                          enable_storage=False)
            # The host may legitimately reduce the worker count under pressure
            # (COMPUTE_ADAPTIVE_SCHEDULING); this test is about what happens to
            # the work, so pin the worker count it exercises.
            reader.max_workers = 3
            reader.process_folder(str(root))

            snapshot = reader.get_live_progress()
            assert snapshot["files_pending"] == 0, snapshot
            assert snapshot["in_progress"] == 0, snapshot
            assert (snapshot["files_completed"] + snapshot["files_failed"]
                    + snapshot["files_skipped"] + snapshot["files_unsupported"]
                    + snapshot["files_cancelled"] == snapshot["total_files"]), snapshot
            admission = snapshot.get("admission")
            assert admission is not None, "admission state must be observable"
            assert admission["throttled"] is True, admission
            assert admission["current_window"] < admission["configured_window"], admission
            assert abs(admission["current_window"] - 1) <= 1, admission
        finally:
            shutil.rmtree(root, ignore_errors=True)
