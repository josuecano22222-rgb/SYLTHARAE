"""PDF: the reading model must not lose text, and must report the truth.

Two defects found by comparing the stored page text of the corpus against
MuPDF's own reading of the same page:

``testPDF_childAttachments.pdf`` page 1
    Stored: 1 566 characters. Page: 2 503 characters. Every word of the missing
    900 was on the page - the OCR fallback replaced a perfectly good text layer
    with a partial recognition of the rendered page. The fallback exists for
    pages whose layer is recognition residue, and it was firing on a page whose
    layer was fine because the page is a table of contents: dot leaders
    (". . . . .") are tokens, and a token with no letters counts as a bad token,
    so 40 % of the page's tokens looked like garbage.

``testPageNumber.pdf``
    Same cause, smaller page: 26 characters of real text replaced by 19
    characters of OCR in a different order.

A third defect is a reported value, not text loss: ``block["column"]`` is
documented as the column a block belongs to, and it was set to the block's
position in the reading order, so a two-column page reported columns 0..30.

The rule these tests fix is simple: OCR may *fill* a page, never shrink one, and
never replace a layer that reads as language.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF not installed")

from reader_file.readers.pdf_layout import page_model, text_quality  # noqa: E402
from reader_file.readers.read_pdf import choose_reading  # noqa: E402


def two_column_page():
    """A page with a header band and two columns of prose."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "Quarterly report on extraction coverage", fontsize=12)
    left = "\n".join(f"Left column line {index + 1} with several words."
                     for index in range(8))
    right = "\n".join(f"Right column line {index + 1} with several words."
                      for index in range(8))
    page.insert_textbox(pymupdf.Rect(60, 100, 280, 500), left, fontsize=10)
    page.insert_textbox(pymupdf.Rect(320, 100, 540, 500), right, fontsize=10)
    page.insert_text((60, 800), "Page 1", fontsize=9)
    return doc, page


class TestColumns:
    def test_two_columns_are_reported_as_two(self):
        _doc, page = two_column_page()
        model = page_model(page, want_tables=False)
        assert model["columns"] == 2

    def test_block_column_is_a_column_index_not_a_reading_position(self):
        _doc, page = two_column_page()
        model = page_model(page, want_tables=False)
        columns = {block["column"] for block in model["blocks"]}
        assert columns <= {0, 1}, columns
        assert columns == {0, 1}

    def test_reading_order_reads_the_left_column_before_the_right(self):
        _doc, page = two_column_page()
        text = page_model(page, want_tables=False)["text"]
        left_last = text.rindex("Left column line 8")
        right_first = text.index("Right column line 1")
        assert left_last < right_first


class TestQuality:
    def test_dot_leaders_do_not_make_a_page_look_like_garbage(self):
        toc = "What's New\n. . . . . . . . . . . . . . . 1\nReminders\n. . . . . 2\n"
        assert text_quality(toc) > 0.9

    def test_recognition_residue_is_still_low_quality(self):
        residue = "\ufffd \ufffd \ufffd a \ufffd \u25a1 b \ufffd \ufffd"
        assert text_quality(residue) < 0.5


class TestOcrAcceptance:
    def test_ocr_fills_an_empty_page(self):
        choice = choose_reading("", "Happy New Year 2003!", 0.0)
        assert choice["source"] == "ocr"
        assert choice["text"] == "Happy New Year 2003!"

    def test_ocr_never_replaces_a_good_text_layer(self):
        native = "Publication 15\nCat. No. 10000W\nEmployer's Tax Guide\n" * 10
        ocr = "Publication 15 Cat. No. 10000W"  # much shorter, as a render loses text
        choice = choose_reading(native, ocr, text_quality(native))
        assert choice["source"] == "text_layer"
        assert choice["text"].strip() == native.strip()
        assert choice["reason"]

    def test_ocr_never_shrinks_a_page_even_when_its_quality_score_is_higher(self):
        native = "a1 b2 c3 d4 e5 f6 g7 h8\n" * 20   # poor lexical quality, real content
        ocr = "a1 b2 c3"
        choice = choose_reading(native, ocr, 0.1)
        assert choice["source"] == "text_layer"

    def test_ocr_replaces_a_residue_layer(self):
        residue = "\ufffd\ufffd \ufffd a \ufffd\ufffd \u25a1"
        ocr = "This page was scanned, and the text was recovered."
        choice = choose_reading(residue, ocr, text_quality(residue))
        assert choice["source"] == "ocr"

    def test_no_ocr_keeps_the_layer(self):
        choice = choose_reading("Native text here", None, 0.8)
        assert choice["source"] == "text_layer"
        assert choice["text"] == "Native text here"

    def test_a_page_with_no_text_and_no_ocr_stays_empty(self):
        choice = choose_reading("", None, 0.0)
        assert choice["text"] == ""
        assert choice["source"] == "none"
