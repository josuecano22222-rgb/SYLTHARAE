"""Block-level content controls must not swallow the paragraphs they hold.

Word wraps a block in ``w:sdt`` (a structured document tag) when the author
inserts a content control: a date picker, a repeating section, a form field, a
whole reusable snippet. ``python-docx``'s ``document.iter_inner_content()``
yields only direct ``w:p`` / ``w:tbl`` children of the body, so everything
inside such a control is skipped - not blanked, never visited.

That is not a rare corner: ``test-doc/Documents/testWORD_template.docx`` is a
CV built entirely from content controls and stored 13% of its document tokens;
``testWORD_sdtInTextBox.docx`` lost the words "rich text content control"
themselves.

The reader now walks the body itself and descends through ``w:sdtContent``, so
a controlled paragraph keeps its place in the reading order.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("docx", reason="python-docx not installed")

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
)


def _read(tmp_path, build):
    from docx import Document

    from reader_file.readers.read_office import OfficeFileReader

    doc = Document()
    build(doc)
    path = tmp_path / "sdt.docx"
    doc.save(str(path))

    result = OfficeFileReader().read_file(
        {"path": str(path), "effective_extension": ".docx"})
    assert not result.get("error"), result.get("error")
    return result


def _texts(result):
    return [el.get("text") or "" for el in result["elements"]
            if el.get("type") == "paragraph"]


def _wrap_in_content_control(paragraph, alias="Control"):
    """Re-wrap an existing body paragraph in a block-level w:sdt.

    ``python-docx`` cannot create content controls, so the paragraph element is
    moved into hand-built ``w:sdt`` markup - which is exactly what Word writes.
    """
    from docx.oxml import parse_xml

    sdt = parse_xml(
        '<w:sdt %s><w:sdtPr><w:alias w:val="%s"/></w:sdtPr>'
        '<w:sdtContent/></w:sdt>' % (NS, alias))
    body = paragraph._p.getparent()
    paragraph._p.addprevious(sdt)
    content = sdt.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sdtContent")
    content.append(paragraph._p)
    return sdt


def test_paragraph_inside_content_control_is_read(tmp_path):
    def build(doc):
        doc.add_paragraph("BeforeControl")
        controlled = doc.add_paragraph("InsideControl")
        _wrap_in_content_control(controlled)
        doc.add_paragraph("AfterControl")

    texts = _texts(_read(tmp_path, build))
    assert "InsideControl" in texts, texts


def test_content_control_keeps_reading_order(tmp_path):
    def build(doc):
        doc.add_paragraph("BeforeControl")
        controlled = doc.add_paragraph("InsideControl")
        _wrap_in_content_control(controlled)
        doc.add_paragraph("AfterControl")

    texts = _texts(_read(tmp_path, build))
    assert (texts.index("BeforeControl") < texts.index("InsideControl")
            < texts.index("AfterControl")), texts


def test_table_inside_content_control_is_read(tmp_path):
    def build(doc):
        marker = doc.add_paragraph("Marker")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "CellLeft"
        table.cell(0, 1).text = "CellRight"
        # a table is a body child too, so wrap the paragraph just before it and
        # move the table into the same control
        sdt = _wrap_in_content_control(marker)
        content = sdt.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sdtContent")
        content.append(table._tbl)

    result = _read(tmp_path, build)
    tables = [el for el in result["elements"] if el.get("type") == "table"]
    assert tables, result["elements"]
    assert tables[0]["rows"] == [["CellLeft", "CellRight"]], tables[0]["rows"]


def test_nested_content_controls_are_read(tmp_path):
    def build(doc):
        paragraph = doc.add_paragraph("NestedControlText")
        inner = _wrap_in_content_control(paragraph, alias="Inner")
        outer_sdt = _wrap_in_content_control(paragraph, alias="Outer")
        assert inner is not None and outer_sdt is not None

    texts = _texts(_read(tmp_path, build))
    assert "NestedControlText" in texts, texts
    assert texts.count("NestedControlText") == 1, texts
