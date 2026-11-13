"""The stored form of a Word/PowerPoint table must be reversible.

A table is stored as the marker line ``Table N`` followed by one line per
row with cells separated by tabs. That is the only structure the display
layer has to rebuild the grid from, so anything that makes the text
ambiguous comes back to the examiner looking like a different document:

* a cell whose value contains a newline (a table cell with two paragraphs)
  split one table row into two - the grid gained a row and lost a column;
* a row whose cells were all empty serialized to an empty line, which is
  the same thing as the blank line the extractor writes BETWEEN document
  elements - the display layer read it as the end of the table and printed
  the remaining rows as running text.

These tests pin the storage contract itself; the display side of the same
contract is covered by tests/js/content_formatter_smoke.mjs.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


def _text(rows):
    """Run the real table serializer (``self`` carries no state)."""
    return StoragePipeline._extract_table_text(
        StoragePipeline.__new__(StoragePipeline), rows)


def _grid(rows):
    """The stored text as a grid again - exactly what the display sees."""
    return [line.split('\t') for line in _text(rows).split('\n')]


def test_multi_paragraph_cell_stays_in_one_row():
    rows = [["Item", "Description"],
            ["Widget", "Line one\nLine two"],
            ["Gadget", "Single line"]]
    text = _text(rows)
    # Three rows, not four: the newline inside the cell did not split it.
    assert text.count('\n') == 2, text
    # The words survive; only the intra-cell break becomes a space.
    assert _grid(rows)[1] == ["Widget", "Line one Line two"]


def test_carriage_returns_and_tabs_in_a_cell_do_not_move_the_grid():
    rows = [["A", "x\r\ny\tz"]]
    # One folded run of breaks -> one space, and the tab is gone (a tab here
    # would read as a cell boundary on the way back out).
    assert _grid(rows) == [["A", "x y z"]]


def test_all_empty_row_is_a_row_not_a_block_separator():
    """A one-column blank row must not serialize to an empty line."""
    rows = [["Approved"], [""], ["Pending"]]
    text = _text(rows)
    # Three stored lines: a blank line here would end the table on display.
    assert text.split('\n') == ["Approved", "\t", "Pending"], repr(text)


def test_all_empty_row_of_a_wide_table_is_still_one_line():
    rows = [["A", "B"], ["", ""], ["C", "D"]]
    lines = _text(rows).split('\n')
    assert len(lines) == 3, repr(lines)
    assert lines[1].strip() == '' and '\t' in lines[1], repr(lines[1])


def test_empty_cells_keep_their_column_positions():
    rows = [["", "Owner", "Status"],
            ["Alpha", "", "Open"],
            ["Beta", "Ryder", ""]]
    assert _grid(rows) == [["", "Owner", "Status"],
                           ["Alpha", "", "Open"],
                           ["Beta", "Ryder", ""]]


def test_none_cells_are_empty_cells():
    assert _grid([["A", None], [None, "B"]]) == [["A", ""], ["", "B"]]


def test_no_rows_is_no_text():
    assert _text([]) == ""
    assert _text(None) == ""
