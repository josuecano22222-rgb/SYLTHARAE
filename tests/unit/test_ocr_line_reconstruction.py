"""Recognised blocks must be assembled by position, not by engine order.

An OCR engine answers with boxes and strings; the string list alone does not
say where anything was. Joining blocks with a newline turns two fragments of
one visual line into two lines - the corpus sample
``Documents/testPDF_AdobeIllustrator.pdf`` reads "TheR" and "evere" on a
curve and the reader stored ``TheR\\n\\nGolf Club`` - and it inherits whatever
order the engine happened to return.

These tests pin the reconstruction rules on synthetic boxes with known
coordinates, so they do not need an OCR engine to run.
"""
from core.ocr.layout import reconstruct_lines
from core.ocr.engines import OcrBlock


def block(text, x0, y0, x1, y1, confidence=0.9):
    return OcrBlock(text=text, confidence=confidence, bbox=(x0, y0, x1, y1))


def test_two_boxes_on_one_line_are_joined_with_a_space():
    # Same baseline, a clear word-sized gap between them.
    blocks = [block("Hello", 10, 10, 90, 30), block("World", 110, 10, 190, 30)]
    assert reconstruct_lines(blocks) == "Hello World"


def test_a_gap_too_small_for_a_word_space_joins_without_one():
    # An engine that cut one word in two around a glyph: the boxes abut.
    blocks = [block("Revere", 10, 10, 70, 30), block("Club", 70, 10, 130, 30)]
    assert reconstruct_lines(blocks) == "RevereClub"


def test_blocks_out_of_engine_order_are_sorted_by_position():
    blocks = [
        block("second", 10, 60, 90, 80),
        block("first", 10, 10, 90, 30),
    ]
    assert reconstruct_lines(blocks) == "first\nsecond"


def test_baseline_jitter_stays_on_the_same_line():
    # A superscript, or a box the engine clipped a few pixels off.
    blocks = [block("cost", 10, 10, 70, 30), block("2", 80, 4, 90, 18)]
    assert reconstruct_lines(blocks) == "cost 2"


def test_a_tall_gap_between_lines_is_kept_as_a_paragraph_break():
    blocks = [
        block("para one", 10, 10, 120, 30),
        block("para two", 10, 120, 120, 140),
    ]
    assert reconstruct_lines(blocks) == "para one\n\npara two"


def test_blocks_without_boxes_are_not_dropped():
    blocks = [
        block("kept", 10, 10, 70, 30),
        OcrBlock(text="no box", confidence=0.5, bbox=None),
    ]
    text = reconstruct_lines(blocks)
    assert "kept" in text
    assert "no box" in text


def test_empty_input_is_empty_text():
    assert reconstruct_lines([]) == ""


def test_a_curved_line_is_not_split_into_two_lines():
    # The measured Adobe Illustrator case: two fragments of one word lying on
    # a curve, each box tilted so they overlap vertically and horizontally.
    blocks = [
        block("TheR", 592, 1156, 1132, 1340),
        block("evere", 897, 1290, 1209, 1378),
    ]
    assert reconstruct_lines(blocks) == "TheRevere"
