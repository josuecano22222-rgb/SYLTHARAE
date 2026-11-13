"""Unit: structural marker stripping (core/content_markers.py).

The storage pipeline embeds display markers (sheet names, slide numbers,
page headers, styles, email headers, ...) into stored text. These tests pin
the separation contract:

* whole-line structural markers (counts, boundaries, ids, paths, software
  labels) are removed entirely from the index view;
* label prefixes keep their VALUE (email senders/subjects, document
  titles, notes) - the label word itself is dropped;
* ordinary content passes through untouched;
* line count is preserved so index line N == display line N.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.content_markers import (
    is_structural_marker_line,
    strip_line_markers,
    strip_structural_markers,
)


# ---------------------------------------------------------------------------
# Whole-line structural markers -> ''
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    # Spreadsheets
    "Sheet: Sheet1",
    "Sheet: Budget 2026",
    "Rows: 40",
    "Columns: 12",
    "Columns: id, name, created_at",
    # Presentations
    "Slide 1",
    "Slide 42",
    "Total Slides: 10",
    "Table 3",
    "[Image: image1.png]",
    # PDF
    "Page 1",
    "Page 7 | Method: text | Length: 120 chars",
    "Page 3 | Error: conversion_failed",
    "Total Pages: 12",
    "OCR Used: Yes",
    "Creator: Microsoft Word",
    "Producer: iText 7",
    # Email structure
    "Message #3",
    "--- Message Content ---",
    "Attachments: 2",
    "Attachments Folder: /tmp/extract_9",
    "Source: /inbox/thread-77.eml",
    # HTML extraction
    "Image: logo -> https://example.com/x.png",
    "Links (14 total):",
    "Images (3 total):",
    "--- Content ---",
    # E-books / databases
    "Chapter ID: ch5",
    "Chapters: 9",
    "Total Words: 51000",
    "Length: 3200 chars",
    "Words: 812",
    "SQLite Version: 3.40.1",
    "Tables: 7",
    "Table: users",
    "Sample Data:",
])
def test_whole_line_markers_are_removed(line):
    assert strip_line_markers(line) == ""
    assert is_structural_marker_line(line) is True


# ---------------------------------------------------------------------------
# Label prefixes -> value kept, label dropped
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("From: alice@example.com", "alice@example.com"),
    ("To: Bob <bob@example.com>", "Bob <bob@example.com>"),
    ("CC: carol@example.com", "carol@example.com"),
    ("Subject: Quarterly review", "Quarterly review"),
    ("Date: Mon, 13 Sep 2026", "Mon, 13 Sep 2026"),
    ("Message-ID: <abc@def>", "<abc@def>"),
    ("Title: Annual Report", "Annual Report"),
    ("Notes: speaker notes text", "speaker notes text"),
    ("Caption: revenue by region", "revenue by region"),
    ("Author: Jane Doe", "Jane Doe"),
    ("Event: Standup", "Standup"),
    ("Start: 2026-09-13", "2026-09-13"),
    ("Status: CONFIRMED", "CONFIRMED"),
    ("H1: Overview", "Overview"),
    # Style marker: label dropped, paragraph text kept
    ("[Style: Heading 1] Annual Report", "Annual Report"),
    ("[Style: Intense Quote] something quoted", "something quoted"),
])
def test_label_prefixes_keep_value(line, expected):
    assert strip_line_markers(line) == expected
    assert is_structural_marker_line(line) is False


# ---------------------------------------------------------------------------
# Ordinary content is untouched
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "The quarterly revenue grew by 12%.",
    "EMEA\t1200\t1350",
    "See Table 3 for details",        # marker regex is whole-line only
    "Slide 4 covers the outlook",     # not a bare boundary marker
    "Page numbers are shown in print",
    "Sheet metal demand rose",        # not a worksheet header
    "From here on, everything changed",
])
def test_ordinary_content_untouched(line):
    assert strip_line_markers(line) == line
    assert is_structural_marker_line(line) is False


# ---------------------------------------------------------------------------
# Document-level behaviour
# ---------------------------------------------------------------------------

def test_document_strip_preserves_line_count_and_values():
    display = (
        "Sheet: Sheet1\n"
        "Rows: 2\n"
        "Columns: 2\n"
        "Region\tRevenue\n"
        "EMEA\t1200\n"
        "\n"
        "Sheet: Notes\n"
        "Author\tRemark\n"
        "ops\tReviewed the ledger"
    )
    index = strip_structural_markers(display)
    index_lines = index.split("\n")
    assert len(index_lines) == len(display.split("\n"))
    # Structural lines became empty; content lines unchanged
    assert index_lines[0] == ""
    assert index_lines[3] == "Region\tRevenue"
    assert index_lines[4] == "EMEA\t1200"
    assert index_lines[8] == "ops\tReviewed the ledger"
    # The searchable words contain the data but no labels
    words = set(index.lower().split())
    assert "sheet1" not in words
    assert "rows" not in words
    assert "region" in words
    assert "emea" in words


def test_email_labels_do_not_match_but_values_do():
    display = (
        "From: alice@example.com\n"
        "To: bob@example.com\n"
        "Subject: ZEPHYR milestone\n"
        "--- Message Content ---\n"
        "The ZEPHYR rollout completed on schedule."
    )
    index = strip_structural_markers(display)
    assert "From" not in index
    assert "Subject:" not in index
    assert "alice@example.com" in index
    assert "ZEPHYR milestone" in index
    assert "The ZEPHYR rollout completed on schedule." in index


def test_empty_and_none_inputs():
    assert strip_structural_markers("") == ""
    assert strip_structural_markers(None) is None
    assert strip_line_markers("") == ""
