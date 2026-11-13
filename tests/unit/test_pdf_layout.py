"""The PDF page model: reading order, hyphenation, ligatures, tables, geometry.

A PDF page is glyphs at coordinates, not paragraphs. ``get_text("text")`` gives
MuPDF's own guess at the order, which is right for one column of prose and wrong
for the layouts PDFs are actually used for: two-column journals, sidebars,
headers and footers, tables, rotated inserts. Token recall cannot see that -
every word is present, in an order that makes the text useless.

These tests build the layouts rather than trusting a fixture: each one writes a
small PDF with a known arrangement and asserts what a reader would see.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

fitz = pytest.importorskip("pymupdf", reason="PyMuPDF not installed")

from reader_file.readers import pdf_layout  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _pdf(tmp_path, draw, name="page.pdf", size=(400, 500)):
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    draw(page)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


def _lines(page, x, y, lines, size=10, leading=14, **kwargs):
    for i, line in enumerate(lines):
        page.insert_text((x, y + i * leading), line, fontsize=size, **kwargs)


def _model(path, page_number=0):
    doc = fitz.open(path)
    model = pdf_layout.page_model(doc[page_number])
    doc.close()
    return model


# --------------------------------------------------------------------------- #
# text normalisation
# --------------------------------------------------------------------------- #
def test_ligatures_become_letters():
    text, stats = pdf_layout.normalize_text("The \ufb01le and the \ufb02oor")
    assert text == "The file and the floor"
    assert stats["ligatures"] == 2


def test_soft_hyphen_and_odd_spaces_disappear():
    text, stats = pdf_layout.normalize_text("Wo\u00adrd\u00a0with\u2009thin\u200bspace")
    assert text == "Word with thin space"
    assert stats["invisible"] == 1
    assert stats["spaces"] == 3


def test_zero_width_non_joiner_is_preserved():
    """U+200C separates letters in Persian and Arabic; dropping it corrupts words."""
    persian = "\u0645\u06cc\u200c\u0631\u0648\u0645"
    text, _ = pdf_layout.normalize_text(persian)
    assert "\u200c" in text
    assert text == persian


def test_line_break_hyphen_is_joined():
    text, joins = pdf_layout.dehyphenate("Inter-\nnational standards")
    assert text == "International standards"
    assert joins == 1


def test_compound_hyphen_survives_a_capitalised_continuation():
    text, joins = pdf_layout.dehyphenate("Büro-\nMünchen")
    assert text == "Büro-\nMünchen"
    assert joins == 0


def test_list_marker_hyphen_is_not_joined():
    text, joins = pdf_layout.dehyphenate("A-\nB")
    assert joins == 0 and "A-\n" in text


def test_text_quality_separates_language_from_glyph_soup():
    assert pdf_layout.text_quality("the quick brown fox jumps") > 0.9
    assert pdf_layout.text_quality("\ufffd \ufffd \ufffd 4 \ufffd") < 0.3
    assert pdf_layout.text_quality("") == 0.0


# --------------------------------------------------------------------------- #
# reading order
# --------------------------------------------------------------------------- #
def test_two_columns_are_read_column_by_column(tmp_path):
    def draw(page):
        _lines(page, 40, 60, ["Header spanning the whole width"], size=12)
        _lines(page, 40, 120, ["Left one", "Left two", "Left three"])
        _lines(page, 230, 120, ["Right one", "Right two", "Right three"])

    text = _model(_pdf(tmp_path, draw))["text"]
    assert text.index("Header") < text.index("Left one")
    assert text.index("Left three") < text.index("Right one"), text
    assert text.index("Right three") > text.index("Right two")


def test_three_columns_keep_their_order(tmp_path):
    def draw(page):
        for i, x in enumerate((40, 170, 300)):
            _lines(page, x, 100, [f"Col{i} line{a}" for a in range(4)])

    model = _model(_pdf(tmp_path, draw, size=(420, 500)))
    text = model["text"]
    assert text.index("Col0") < text.index("Col1") < text.index("Col2"), text
    assert model["columns"] >= 3, model["columns"]


def test_footer_comes_last_not_first(tmp_path):
    def draw(page):
        _lines(page, 40, 60, ["Body first line", "Body second line"])
        _lines(page, 40, 470, ["Page 1 of 9"], size=8)

    text = _model(_pdf(tmp_path, draw))["text"]
    assert text.index("Body first line") < text.index("Page 1 of 9"), text


def test_sidebar_is_read_after_the_body_column_it_sits_beside(tmp_path):
    def draw(page):
        # narrow right-hand sidebar and a wide body column, same vertical band
        _lines(page, 40, 100, ["Body about the topic"] + ["more body text"] * 6, size=9)
        _lines(page, 300, 100, ["Sidebar note"] + ["sidebar detail"] * 3, size=8)

    text = _model(_pdf(tmp_path, draw, size=(420, 500)))["text"]
    assert text.index("Body about the topic") < text.index("Sidebar note")


def test_rotated_text_is_ordered_along_its_baseline(tmp_path):
    """A landscape insert on a portrait page must not scramble the page."""
    def draw(page):
        page.insert_text((60, 100), "Portrait text", fontsize=11)
        # rotated 90 degrees: text runs bottom-to-top on the right edge
        page.insert_text((350, 400), "Rotated one", fontsize=11, rotate=90)
        page.insert_text((370, 400), "Rotated two", fontsize=11, rotate=90)

    model = _model(_pdf(tmp_path, draw))
    assert "Rotated one" in model["text"] and "Portrait text" in model["text"]
    directions = {b["dir"] for b in model["blocks"]}
    assert (0.0, -1.0) in directions or (0.0, 1.0) in directions, directions


def test_page_rotation_is_reported_and_text_still_extracted(tmp_path):
    path = _pdf(tmp_path, lambda page: _lines(page, 60, 100, ["Rotated page body"]))
    doc = fitz.open(path)
    doc[0].set_rotation(90)
    doc.save(str(tmp_path / "rotated.pdf"))
    doc.close()

    model = _model(tmp_path / "rotated.pdf")
    assert model["rotation"] == 90
    assert "Rotated page body" in model["text"]


def test_hyphenated_line_break_is_rejoined_in_page_text(tmp_path):
    def draw(page):
        page.insert_text((40, 100), "This is an Inter-", fontsize=11)
        page.insert_text((40, 114), "national standard.", fontsize=11)

    model = _model(_pdf(tmp_path, draw))
    assert "International" in model["text"], model["text"]
    assert model["stats"]["hyphen_joins"] >= 1


# --------------------------------------------------------------------------- #
# blocks, geometry, tables
# --------------------------------------------------------------------------- #
def test_blocks_carry_geometry_and_typography(tmp_path):
    def draw(page):
        page.insert_text((40, 100), "A paragraph of body text", fontsize=11)

    model = _model(_pdf(tmp_path, draw))
    block = model["blocks"][0]
    assert block["type"] == "text"
    x0, y0, x1, y1 = block["bbox"]
    assert 0 <= x0 < x1 <= model["width"] + 1
    assert 0 <= y0 < y1 <= model["height"] + 1
    assert block["font_size"] == pytest.approx(11, abs=0.6)
    assert block["lines"] >= 1
    assert block["column"] == 0


def test_image_block_is_recorded_not_invented_as_text(tmp_path):
    png = (PROJECT_ROOT / "tests" / "unit" / "_none.png")
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 0)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(20, 20, 120, 120), pixmap=pix)
    path = tmp_path / "image.pdf"
    doc.save(str(path))
    doc.close()

    model = _model(path)
    assert model["stats"]["image_blocks"] == 1
    assert model["text"].strip() == ""
    assert not png.exists()


def test_table_grid_is_recovered(tmp_path):
    def draw(page):
        rows = [["Name", "Qty"], ["Widget", "3"], ["Gadget", "7"]]
        top, left, width, height = 100, 40, 200, 20
        for r in range(len(rows) + 1):
            page.draw_line((left, top + r * height), (left + width, top + r * height))
        for c in range(3):
            page.draw_line((left + c * width / 2, top),
                           (left + c * width / 2, top + len(rows) * height))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                page.insert_text((left + c * width / 2 + 4, top + r * height + 14),
                                 cell, fontsize=9)

    model = _model(_pdf(tmp_path, draw))
    assert model["tables"], "no table found"
    table = model["tables"][0]
    grid = [[cell for cell in row if cell] for row in table["rows"]]
    assert ["Name", "Qty"] in grid, table["rows"]
    assert ["Widget", "3"] in grid, table["rows"]
    assert table["row_count"] == 3
    # the flow text still carries every cell, so nothing is lost if the grid
    # finder misses a table on another page
    for cell in ("Widget", "Gadget", "Qty"):
        assert cell in model["text"]


def test_no_tables_costs_nothing(tmp_path):
    model = _model(_pdf(tmp_path, lambda page: _lines(page, 40, 100, ["Plain prose only"])))
    assert model["tables"] == []
    assert "Plain prose only" in model["text"]
