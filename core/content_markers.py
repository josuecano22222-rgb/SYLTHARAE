"""Structural marker handling for stored content text.

The storage pipeline embeds structural markers into the combined text so
content display can reconstruct each file type's presentation (worksheet
names and boundaries, slide numbers, page headers, paragraph styles, email
headers, ...). Those markers are DISPLAY scaffolding, not document content:
labels such as "Sheet 1", "Rows: 40", "Slide 2", "[Style: Heading 1]" or
"Method: text" must never influence search, classification or
categorization - a query for "sheet" should not return every spreadsheet,
and the word-frequency analysis must not count label words.

This module separates the two views of the same text:

* the DISPLAY text (verbatim, with markers) - stored in ``contents_raw``
  and served to the content pages; and
* the INDEX text (markers removed, values of labelled fields kept) -
  used for word indexing/classification, search snippet matching and
  ranking.

Marker rules (line-based, mirroring exactly what
``StoragePipeline._extract_text_from_content`` injects):

* WHOLE-LINE markers are pure structure: counts (``Rows: 3``,
  ``Total Slides: 10``), boundaries (``Slide 4``, ``Page 2 | Method: ...``),
  identifiers (``Message #3``, ``Chapter ID: 7``), file paths
  (``Source: /inbox/a.eml``), separators (``--- Message Content ---``) and
  software labels (``OCR Used: Yes``). They are removed entirely.
* LABEL-PREFIX markers carry a real value after the label
  (``From: a@b.com``, ``Subject: hello``, ``Title: Annual Report``,
  ``[Style: Heading 1] text``). Only the label is removed; the VALUE is
  document content and stays searchable (email senders, subjects, document
  titles, ...).

Line count is preserved: a removed line becomes an empty line, so line N
of the index text always corresponds to line N of the display text.
"""

import re

# ---------------------------------------------------------------------------
# Whole-line structural markers (removed completely)
# ---------------------------------------------------------------------------

_WHOLE_LINE_MARKERS = [
    # PDF metadata + page headers
    r"Total Pages:\s*\d+",
    r"Encrypted:\s*Yes",
    r"OCR Used:\s*(?:Yes|No)",
    r"OCR Languages:\s*.*",
    r"Creator:\s*.*",
    r"Producer:\s*.*",
    r"Page\s+\d+(?:\s*\|.*)?",
    # Spreadsheets (worksheet headers + dimensions)
    r"Sheet:\s*.*",
    r"Rows:\s*\d+",
    r"Columns:\s*.*",
    # Word / PPT / DB table markers
    r"Table\s+\d+",
    # Presentations
    r"Total Slides:\s*\d+",
    r"Slide\s+\d+",
    r"\[Image:\s*[^\]]*\]",
    # Email structure (counts, folders, source paths, separators)
    r"Message\s*#\s*\d+",
    r"--- Message Content ---",
    r"Attachments:\s*\d+",
    r"Attachments Folder:\s*.*",
    r"Source:\s*.*",
    # E-books (counts, ids)
    r"Chapter ID:\s*.*",
    r"Chapters:\s*\d+",
    r"Total Words:\s*\d+",
    r"Length:\s*\d+\s*chars",
    r"Words:\s*\d+",
    # HTML extraction listings + separators
    r"Image:\s*.*->.*",
    r"Links\s*\(\s*\d+\s*total\s*\):?",
    r"Images\s*\(\s*\d+\s*total\s*\):?",
    r"--- Content ---",
    # Database (SQLite) schema labels
    r"SQLite Version:\s*.*",
    r"Tables:\s*\d+",
    r"Table:\s*.*",
    r"Sample Data:?",
]

_WHOLE_LINE_RE = re.compile(
    r"^(?:" + "|".join(_WHOLE_LINE_MARKERS) + r")\s*$", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Label prefixes whose VALUE is document content (label removed, value kept)
# ---------------------------------------------------------------------------

_LABEL_PREFIX_RE = re.compile(
    r"^(?:From|To|CC|BCC|Date|Subject|Message-ID|Title|Notes|Caption|Name|"
    r"Author|Publisher|Language|Description|Event|Location|Start|End|"
    r"Organizer|Attendees|Todo|Due|Status|Journal|H1|H2|H3)\s*:\s*",
    re.IGNORECASE,
)

# Paragraph style marker: "[Style: Heading 1] actual text" -> "actual text"
_STYLE_PREFIX_RE = re.compile(r"^\[Style:\s*[^\]]*\]\s*")


def strip_line_markers(line: str) -> str:
    """Return the searchable content of a single line.

    * '' for whole-line structural markers (counts, boundaries, paths, ...).
    * the value (label stripped) for ``Label: value`` lines and
      ``[Style: X] value`` paragraphs.
    * the line unchanged for ordinary content.
    """
    if not line:
        return line
    if _WHOLE_LINE_RE.match(line):
        return ""
    stripped = _STYLE_PREFIX_RE.sub("", line, count=1)
    stripped = _LABEL_PREFIX_RE.sub("", stripped, count=1)
    return stripped


def strip_structural_markers(text: str) -> str:
    """Remove structural markers from stored content text.

    Returns the index/rank view of the text: labels gone, labelled values
    kept, line count preserved (marker lines become empty lines) so line
    numbers stay aligned with the display text.
    """
    if not text:
        return text
    return "\n".join(strip_line_markers(line) for line in text.split("\n"))


def is_structural_marker_line(line: str) -> bool:
    """True when a line is pure structure (no searchable content)."""
    return bool(line) and _WHOLE_LINE_RE.match(line) is not None
