"""Decide what a byte string says, when its label does not.

Every text format in this pipeline arrives as bytes and a claim. The claim is
wrong often enough to matter: the corpus contains a page that declares UTF-8
and is windows-1256 Arabic (its whole point), a Russian page in windows-1251
whose bytes are read as UTF-8 with replacement characters, and a cp866 text file
that decodes "successfully" as cp1252 into printable nonsense.

A decode that does not raise is not a decode that is right. The order below is
the one that survives those three cases:

1. a byte-order mark, which is evidence rather than a claim;
2. a caller-declared charset, *only if* it decodes cleanly;
3. a detector (``charset-normalizer``) when one is installed, unless it is
   merely guessing a single-byte code page from a short sample;
4. a candidate chain, preferring encodings that can represent every byte
   without replacement characters.

The result always says which encoding was used and how it was chosen, because
"the text looks odd" is otherwise unfalsifiable.
"""
from __future__ import annotations

from typing import Optional, Tuple

#: Byte-order marks, longest first so UTF-32 wins over UTF-16.
BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

#: Tried after the declared charset. The single-byte code pages are last
#: because they can decode almost anything: cp1252 "succeeds" on cp866 Cyrillic
#: and produces text that is wrong in a way no exception reports.
CANDIDATES = (
    "utf-8", "utf-16", "utf-16-le", "utf-16-be",
    "cp1256", "cp1251", "cp1252", "koi8-r", "koi8-u", "cp866",
    "iso-8859-1", "iso-8859-2", "iso-8859-5", "iso-8859-7", "iso-8859-15",
    "shift_jis", "euc-jp", "gb18030", "big5", "euc-kr",
)


def _replacement_ratio(text: str) -> float:
    return text.count("\ufffd") / max(1, len(text))


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return good / len(text)


def decode_bytes(raw: bytes, declared: Optional[str] = None) -> Tuple[str, str]:
    """Decode ``raw`` as text. Returns ``(text, encoding_used)``.

    ``declared`` is what the file (or its caller) claims. It is used when it
    decodes, and skipped when it does not - a declared UTF-8 on windows-1256
    bytes raises, and a declared cp1252 on cp866 bytes does not, which is why
    the detector is consulted before the single-byte chain.
    """
    if not raw:
        return "", "empty"

    for bom, encoding in BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(encoding), encoding
            except UnicodeDecodeError:
                break

    if declared:
        try:
            text = raw.decode(declared)
            if _replacement_ratio(text) == 0:
                return text, declared
        except (UnicodeDecodeError, LookupError):
            pass

    try:  # optional, and only a suggestion
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None and best.encoding:
            text = str(best)
            if _replacement_ratio(text) == 0 and _printable_ratio(text) >= 0.9:
                return text, str(best.encoding)
    except Exception:
        pass

    fallback = None
    for encoding in CANDIDATES:
        if declared and encoding == declared:
            continue
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if _replacement_ratio(text) != 0:
            continue
        if fallback is None:
            fallback = (text, encoding)
        if _printable_ratio(text) >= 0.99:
            return text, encoding

    if fallback is not None:
        return fallback
    return raw.decode("utf-8", "replace"), "utf-8 (replaced)"
