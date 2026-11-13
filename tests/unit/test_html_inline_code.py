"""Unit: HTML extraction accounts for the whole document.

The question this file answers came from a real ingest log: an ``index.html``
of about 77 KB produced 539 characters of content. Nothing in the log said
where the rest went, so "thin extraction" and "correct extraction of a
script-heavy page" were indistinguishable.

``BeautifulSoup.get_text()`` silently omits ``<script>``, ``<style>`` and
comments. A standalone ``.js``/``.css`` file *is* an indexed text format in this
pipeline (READER-02), so the same bytes were treated differently depending on
which file they arrived in - and for a single-page application most of the
document's bytes are exactly that code.

What must hold, and is asserted here:

* visible text is unchanged (the reader's documented contract);
* inline script/style is returned and published through ``forensic_text``, the
  FORENSIC-01 channel for evidence outside the visible body, so it is indexed
  and attributable;
* ``extraction_info`` records the character split - visible, script, style,
  comments - so the log/report can explain a small visible-text count;
* output is bounded, and truncation is stated rather than silent.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.readers.read_remaining import RemainingFileReader  # noqa: E402

PAGE = """<!doctype html>
<html><head><title>Status App</title>
<style>.chart{color:red} .axis{font-size:9px}</style>
<script type="application/json">{"series":[1,2,3],"label":"revenue q3 2024"}</script>
<script>function boot(){return fetch('/api/data');}</script>
</head>
<body><h1>Dashboard</h1><p>Quarterly revenue.</p>
<!-- internal comment: build 8842 -->
</body></html>
"""


@pytest.fixture
def reader():
    return RemainingFileReader()


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestVisibleTextIsUnchanged:
    def test_document_text_still_comes_from_get_text(self, reader, tmp_path):
        result = reader.read_html_file(str(_write(tmp_path, "index.html", PAGE)))
        assert "Dashboard" in result["text_content"]
        assert "Quarterly revenue." in result["text_content"]
        # Script bodies are not visible text and must not be smuggled into it.
        assert "fetch(" not in result["text_content"]
        assert ".chart{" not in result["text_content"]

    def test_structure_keys_are_still_present(self, reader, tmp_path):
        result = reader.read_html_file(str(_write(tmp_path, "index.html", PAGE)))
        assert result["title"] == "Status App"
        assert result["headings"]["h1"] == ["Dashboard"]
        assert result["link_count"] == 0
        assert result["image_count"] == 0


class TestInlineCodeIsKeptAndLabelled:
    def test_inline_script_and_style_are_returned(self, reader, tmp_path):
        result = reader.read_html_file(str(_write(tmp_path, "index.html", PAGE)))
        assert '"label":"revenue q3 2024"' in result["script_text"]
        assert "function boot()" in result["script_text"]
        assert ".chart{color:red}" in result["style_text"]

    def test_inline_code_is_published_for_indexing(self, reader, tmp_path):
        """forensic_text is the existing channel for non-body evidence."""
        result = reader.read_html_file(str(_write(tmp_path, "index.html", PAGE)))
        forensic = result["forensic_text"]
        assert "--- Inline script ---" in forensic
        assert "--- Inline style ---" in forensic
        assert "function boot()" in forensic

    def test_a_page_without_inline_code_adds_nothing(self, reader, tmp_path):
        page = "<html><body><p>Plain document</p></body></html>"
        result = reader.read_html_file(str(_write(tmp_path, "plain.html", page)))
        assert result["script_text"] == ""
        assert result["style_text"] == ""
        assert result["forensic_text"] == ""


class TestTheCharacterSplitIsReported:
    def test_every_part_of_the_document_is_accounted_for(self, reader, tmp_path):
        result = reader.read_html_file(str(_write(tmp_path, "index.html", PAGE)))
        info = result["extraction_info"]
        assert info["visible_text_chars"] == len(result["text_content"])
        assert info["script_chars"] == len(result["script_text"])
        assert info["style_chars"] == len(result["style_text"])
        assert info["comment_chars_excluded"] > 0
        assert info["inline_code_truncated"] == []

    def test_a_thin_visible_text_is_explained_by_the_split(self, reader, tmp_path):
        """The reported case: 539 chars of text out of a much larger file."""
        page = (
            "<html><head><script>"
            + "var payload = '" + "x" * 5000 + "';"
            + "</script></head><body><p>Thin</p></body></html>"
        )
        result = reader.read_html_file(str(_write(tmp_path, "spa.html", page)))
        info = result["extraction_info"]
        assert result["text_content"].strip() == "Thin"
        assert info["visible_text_chars"] < 10
        assert info["script_chars"] > 5000

    def test_oversized_inline_code_is_bounded_and_says_so(self, reader, tmp_path):
        limit = RemainingFileReader.HTML_INLINE_CODE_LIMIT
        page = "<html><head><script>" + "y" * (limit + 1000) + "</script></head></html>"
        result = reader.read_html_file(str(_write(tmp_path, "big.html", page)))
        assert len(result["script_text"]) == limit
        assert result["extraction_info"]["inline_code_truncated"] == ["script"]
        assert result["extraction_info"]["script_chars"] == limit
