"""Identical paragraphs are separate paragraphs, not one paragraph repeated.

Measured on `Other/testWORD_template.dotx`: the package holds "Click here to
enter text" five times and the reader stored it once, so the file scored 0.839
and was recorded as a Word traversal gap. The text is inside block-level
content controls (`w:sdt`), which python-docx's body walk does not descend
into, so it is recovered by the XML backfill pass - and that pass skipped any
paragraph whose text was already somewhere in the document. In a résumé
template the placeholder lines *are* repeats of the same sentence; deduping by
text threw away real content and reported it as coverage.

The fixture is a real docx with three identical content-control paragraphs, so
python-docx itself cannot see them and the backfill pass is what has to keep
all three.
"""
import os
import zipfile

import pytest

from reader_file.readers.read_office import OfficeFileReader

TEXT = "Click here to enter text."

BODY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>anchor paragraph</w:t></w:r></w:p>
    {blocks}
    <w:sectPr/>
  </w:body>
</w:document>
"""

SDT = """<w:sdt><w:sdtPr><w:id w:val="1"/></w:sdtPr>
<w:sdtContent><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:sdtContent></w:sdt>"""


def _docx_with_identical_sdts(tmp_path, count=3):
    docx = pytest.importorskip("docx")
    path = tmp_path / "repeats.docx"
    document = docx.Document()
    document.add_paragraph("anchor paragraph")
    document.save(path)

    blocks = "\n".join(SDT.format(text=TEXT) for _ in range(count))
    body = BODY.format(blocks=blocks)
    # Rewrite the body part in place: the rest of the package (content types,
    # relationships, styles) stays exactly what python-docx wrote.
    with zipfile.ZipFile(path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    parts["word/document.xml"] = body.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in parts.items():
            target.writestr(name, data)
    return path


def test_every_identical_content_control_paragraph_is_kept(tmp_path):
    path = _docx_with_identical_sdts(tmp_path, count=3)
    result = OfficeFileReader().read_file({"path": str(path), "extension": ".docx"})
    content = result.get("content", result) if isinstance(result, dict) else {}

    text = "\n".join(str(element.get("text") or "")
                     for element in content.get("elements", [])
                     if isinstance(element, dict))
    assert not content.get("error"), content.get("error")
    assert text.count(TEXT) == 3, (
        f"expected the placeholder text 3 times, found {text.count(TEXT)}")


def test_the_corpus_template_keeps_its_repeated_placeholders():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc",
                        "Other", "testWORD_template.dotx")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    result = OfficeFileReader().read_file({"path": path, "extension": ".dotx"})
    content = result.get("content", result) if isinstance(result, dict) else {}
    text = "\n".join(str(element.get("text") or "")
                     for element in content.get("elements", [])
                     if isinstance(element, dict))
    # The package has the sentence five times (measured in word/document.xml:
    # twice with a full stop, three without). Every copy must survive; the
    # reader stored one before this rule existed.
    assert text.count(TEXT) == 2, text.count(TEXT)
    assert text.count(TEXT.rstrip(".")) == 5, text.count(TEXT.rstrip("."))
