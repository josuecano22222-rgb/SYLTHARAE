"""HTML: adjacent elements are separate words, not one welded token.

`BeautifulSoup.get_text()` with no separator concatenates the text of every
element, so a page built out of `<p>`, `<td>` or `<option>` cells produces
tokens that exist nowhere in the document:

    <td>м. Тверская</td><td>10 минут пешком от метро</td>
    -> "м. Тверская10 минут пешком от метро"

The word "Тверская10" is unsearchable, and the two words it hides ("Тверская",
"10") are lost from the index even though every character is present. Measured
on `Programming/big-preamble.html`: 15 oracle tokens missing, all of them at a
block boundary - "МКАД2100", "Тверская10", "метроул.".

A newline is inserted after block-level elements (and at `<br>`) before the text
is taken, which is what a browser does at those boundaries. Inline elements are
left alone: `<b>Конти</b>нент` is one word and must stay one word.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytest = __import__("pytest")
pytest.importorskip("bs4", reason="beautifulsoup4 not installed")

from reader_file.readers.read_remaining import RemainingFileReader  # noqa: E402

CORPUS = PROJECT_ROOT / "test-doc" / "Programming"


def _read(tmp_path, html, name="page.html"):
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    result = RemainingFileReader().read_file({"path": str(path), "extension": ".html"})
    content = result.get("content") if isinstance(result.get("content"), dict) else result
    return content


def test_adjacent_table_cells_do_not_weld(tmp_path):
    content = _read(tmp_path, (
        "<html><body><table><tr>"
        "<td>м. Тверская</td><td>10 минут пешком</td>"
        "</tr></table></body></html>"))
    text = content["text_content"]
    assert "Тверская10" not in text
    assert "Тверская" in text and "10 минут" in text


def test_adjacent_paragraphs_do_not_weld(tmp_path):
    content = _read(tmp_path, "<body><p>МКАД</p><p>2100 - 2500</p></body>")
    assert "МКАД2100" not in content["text_content"]
    assert "2100" in content["text_content"].split()


def test_a_line_break_separates_words(tmp_path):
    content = _read(tmp_path, "<body><p>first<br>second</p></body>")
    assert "firstsecond" not in content["text_content"]
    assert {"first", "second"} <= set(content["text_content"].split())


def test_a_styled_fragment_inside_a_word_stays_one_word(tmp_path):
    """`<b>` is inline: `Конти` + `нент` is the word `Континент`."""
    content = _read(tmp_path, "<body><p>Группа <b>Конти</b>нент</p></body>")
    assert "Континент" in content["text_content"].replace("\n", " ").split()


def test_option_text_is_visible_content(tmp_path):
    content = _read(tmp_path, (
        "<body><select><option>2100</option><option>2500</option></select></body>"))
    assert "2100" in content["text_content"].split()
    assert "2500" in content["text_content"].split()


@pytest.mark.skipif(not (CORPUS / "big-preamble.html").exists(),
                    reason="corpus fixture not present")
def test_the_corpus_page_no_longer_welds_block_text():
    result = RemainingFileReader().read_file(
        {"path": str(CORPUS / "big-preamble.html"), "extension": ".html"})
    content = result.get("content") if isinstance(result.get("content"), dict) else result
    text = content["text_content"]
    for welded in ("МКАД2100", "Тверская10", "метроул."):
        assert welded not in text, welded
    assert {"МКАД", "2100", "Тверская"} <= set(text.split())
