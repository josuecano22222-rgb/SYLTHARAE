"""Text bytes: the decode that succeeds is not always the decode that is right.

Three failures from the corpus, all of them silent:

* ``Programming/testArabicMisleadingCharset.html`` declares UTF-8 and holds
  windows-1256 Arabic. UTF-8 raises, the old chain fell through to cp1252, and
  the page was stored as Latin-1 mojibake: every character present, nothing
  searchable, no error.
* ``Text/russian.cp866.txt`` is cp866 Cyrillic. cp1252 decodes those bytes
  "successfully" into printable nonsense, so the file looked read.
* a UTF-16 file with a BOM must not be guessed from its contents when the BOM
  already says what it is.

`core.text_encoding.decode_bytes` states the order: BOM, declared charset when
it decodes, a detector, then a candidate chain. It always reports which
encoding it used.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.text_encoding import decode_bytes  # noqa: E402

ARABIC = "اللغة العربية هي واحدة من أكثر اللغات انتشارا في العالم"
RUSSIAN = "Съешь же ещё этих мягких французских булок, да выпей чаю"


def test_bom_wins_over_contents():
    text, encoding = decode_bytes("Привет мир".encode("utf-16"))
    assert encoding.startswith("utf-16")
    assert text.strip("\ufeff") == "Привет мир"


def test_declared_charset_is_used_when_it_decodes():
    text, encoding = decode_bytes(RUSSIAN.encode("cp1251"), declared="cp1251")
    assert encoding == "cp1251"
    assert text == RUSSIAN


def test_a_wrong_declaration_is_not_obeyed_when_it_raises():
    text, encoding = decode_bytes(ARABIC.encode("cp1256"), declared="utf-8")
    assert text == ARABIC, (encoding, text[:40])


def test_a_single_byte_code_page_is_not_accepted_just_because_it_decodes():
    """cp866 bytes decode as cp1252 without error - and produce nonsense."""
    text, encoding = decode_bytes(RUSSIAN.encode("cp866"))
    assert text == RUSSIAN, (encoding, text[:40])


def test_utf8_survives_unchanged():
    text, encoding = decode_bytes("naïve café — ok".encode("utf-8"))
    assert text == "naïve café — ok"
    assert encoding in ("utf-8", "utf_8")


def test_empty_input_is_not_an_error():
    assert decode_bytes(b"") == ("", "empty")


def test_binary_bytes_degrade_visibly_rather_than_raising():
    text, encoding = decode_bytes(bytes(range(256)) * 4)
    assert isinstance(text, str)
    assert encoding
