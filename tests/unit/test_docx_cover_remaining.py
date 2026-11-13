"""DOCX: the parts python-docx does not model are still parts of the document.

python-docx exposes paragraphs, runs and tables. It does not expose
``headerN``/``footerN``, ``footnotes``, ``endnotes`` or ``comments``; it refuses
packages whose main part is not ``document.main+xml`` (``.docm``, ``.dotx``);
and it silently ignores ``mc:AlternateContent`` blocks unless the reader looks
for them. A document can therefore be read completely, by the library's
definition, and still be missing a third of its words.

The fix is a cover pass: after the structured read, walk the package XML part by
part, and append anything the structured read did not already produce, tagged
``source: xml_backfill`` and ``location: <part>`` so a reader of the result can
tell recovered text from parsed text. These tests pin that behaviour.
"""

import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

docx = pytest.importorskip("docx", reason="python-docx not installed")

from reader_file.readers.read_office import OfficeFileReader  # noqa: E402


def _content(result):
    if isinstance(result.get("content"), dict) and "elements" not in result:
        return result["content"]
    return result


def _all_text(content) -> str:
    """Every stored string, whatever shape the reader used for it."""
    elements = content.get("elements") or []
    if elements:  # the element list is the superset; paragraphs mirrors it
        parts = [element.get("text") or "" for element in elements]
    else:
        parts = [paragraph.get("text") if isinstance(paragraph, dict) else str(paragraph)
                 for paragraph in content.get("paragraphs") or []]
    return " ".join(part for part in parts if part)


def _document_with_furniture(tmp_path, name="furniture.docx"):
    document = docx.Document()
    document.add_paragraph("Body paragraph one")
    document.add_paragraph("Body paragraph two")
    document.sections[0].header.paragraphs[0].text = "Confidential draft"
    document.sections[0].footer.paragraphs[0].text = "Page 1 of 1"
    path = tmp_path / name
    document.save(str(path))
    return path


def test_header_and_footer_are_recovered_with_provenance(tmp_path):
    content = _content(OfficeFileReader().read_file(
        {"path": str(_document_with_furniture(tmp_path)), "extension": ".docx"}))
    texts = [element.get("text") for element in content["elements"]]
    assert any("Body paragraph one" in (text or "") for text in texts)

    recovered = [element for element in content["elements"]
                 if element.get("source") == "xml_backfill"]
    recovered_text = " ".join(element.get("text") or "" for element in recovered)
    assert "Confidential draft" in recovered_text
    assert "Page 1 of 1" in recovered_text
    locations = {element.get("location") for element in recovered}
    assert any("header" in str(location) for location in locations)
    assert any("footer" in str(location) for location in locations)
    assert content.get("xml_backfill", {}).get("added", 0) >= 2


def test_body_text_is_not_duplicated_by_the_cover_pass(tmp_path):
    content = _content(OfficeFileReader().read_file(
        {"path": str(_document_with_furniture(tmp_path)), "extension": ".docx"}))
    body_hits = [element for element in content["elements"]
                 if (element.get("text") or "").strip() == "Body paragraph one"]
    assert len(body_hits) == 1
    assert body_hits[0].get("source") != "xml_backfill"


def test_a_macroless_but_misnamed_package_still_reads(tmp_path):
    """python-docx rejects a main part that is not word/document.xml.

    ``.docm`` and ``.dotx`` are exactly that shape. The read must not collapse:
    it records ``python_docx_error`` and the cover pass supplies the text.
    """
    source = _document_with_furniture(tmp_path)
    target = tmp_path / "macro-template.docx"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for name in original.namelist():
            data = original.read(name)
            if name == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument."
                    b"wordprocessingml.document.main+xml",
                    b"application/vnd.ms-word.template.macroEnabledTemplate.main+xml")
            copy.writestr(name, data)

    content = _content(OfficeFileReader().read_file(
        {"path": str(target), "extension": ".docx"}))
    assert not content.get("error")
    assert "Body paragraph one" in _all_text(content)
    assert content.get("xml_backfill", {}).get("parts")


def test_alternate_content_fallback_duplicates_are_not_counted_twice(tmp_path):
    """``mc:AlternateContent`` carries the same text twice (Choice + Fallback)."""
    source = _document_with_furniture(tmp_path)
    target = tmp_path / "alternate.docx"
    body = (
        '<w:p><w:r><w:t>Choice only text</w:t></w:r></w:p>'
        '<mc:AlternateContent><mc:Choice Requires="wps"><w:p><w:r><w:t>Boxed '
        'text</w:t></w:r></w:p></mc:Choice><mc:Fallback><w:p><w:r><w:t>Boxed '
        'text</w:t></w:r></w:p></mc:Fallback></mc:AlternateContent>')
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for name in original.namelist():
            data = original.read(name)
            if name == "word/document.xml":
                xml = data.decode("utf-8")
                xml = xml.replace("</w:body>", body + "</w:body>")
                data = xml.encode("utf-8")
            copy.writestr(name, data)

    content = _content(OfficeFileReader().read_file(
        {"path": str(target), "extension": ".docx"}))
    assert _all_text(content).count("Boxed text") == 1
