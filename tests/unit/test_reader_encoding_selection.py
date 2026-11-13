"""Readers must store text, not a decode that merely did not raise.

The corpus has three files whose whole point is a wrong label:

* ``Text/russian.cp866.txt`` - cp866 Cyrillic, which decodes "successfully"
  as cp1252 into printable nonsense, so ``errors="replace"`` and a
  latin-1 fallback both report success while losing the language;
* ``Programming/testArabicMisleadingCharset.html`` - windows-1256 Arabic
  behind ``<meta charset="UTF-8">``;
* ``Text/file10_permahang.txt`` - zero bytes, which is not a codec error.

These are reader-level assertions on the stored text, so they fail if the
readers go back to a single guessed encoding.
"""
import os

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")


def _text_of(result):
    """The stored text from a reader result, wherever the reader put it."""
    content = result.get("content") if isinstance(result.get("content"), dict) else result
    return content.get("text_content") or content.get("content") or ""


def _read(relative_path, extension):
    from reader_file.readers.read_remaining import RemainingFileReader

    path = os.path.join(CORPUS, relative_path)
    if not os.path.exists(path):
        pytest.skip(f"corpus file missing: {relative_path}")
    return RemainingFileReader().read_file({"path": path, "extension": extension}) or {}


def _content(result):
    return result.get("content") if isinstance(result.get("content"), dict) else result


def test_cp866_cyrillic_is_not_stored_as_cp1252():
    content = _content(_read("Text/russian.cp866.txt", ".txt"))
    assert not content.get("error"), content.get("error")
    text = content.get("content") or ""
    assert "Однажды" in text
    # cp1252 renders these bytes as "¦¤ ¤âã¤¥−ãî ..." - printable, wrong.
    assert "¤" not in text
    assert (content.get("encoding_used") or "").lower() not in ("utf-8", "cp1252")


def test_html_with_misleading_utf8_label_is_stored_in_its_real_script():
    content = _content(_read("Programming/testArabicMisleadingCharset.html", ".html"))
    assert not content.get("error"), content.get("error")
    text = content.get("text_content") or ""
    assert "في قرية" in text
    assert "\ufffd" not in text


def test_windows_1251_page_keeps_its_cyrillic():
    content = _content(_read("Programming/big-preamble.html", ".html"))
    text = content.get("text_content") or ""
    assert "\ufffd" not in text
    assert any("\u0400" <= ch <= "\u04ff" for ch in text)


def test_empty_text_file_is_read_without_error():
    content = _content(_read("Text/file10_permahang.txt", ".txt"))
    assert not content.get("error"), content.get("error")
    assert content.get("character_count") == 0


def test_plain_utf8_text_is_unchanged():
    content = _content(_read("Text/README.txt", ".txt"))
    assert content, "reader returned nothing"
    assert not content.get("error"), content.get("error")
    text = content.get("content") or ""
    assert text
    assert "\ufffd" not in text
    assert (content.get("encoding_used") or "").lower().replace("-", "_") in ("utf_8", "ascii")
