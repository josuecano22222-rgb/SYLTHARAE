"""Place-name normalisation shared by the seed build, the loader and the detector.

``match_key`` is the one function that turns a place name *or* a stretch of
document text into the form used for matching, so a name and its occurrence
in text can only meet if both normalise identically. It is deliberately
conservative:

* Unicode NFKC (folds Arabic presentation forms such as ``ﺣَﻠَﺐ``);
* drops invisible marks: Arabic harakat/tatweel, Hebrew niqqud/cantillation,
  bidi controls (``core.detection.textspan.dropped``), and ZWNJ (Persian
  half-space is written inconsistently as ZWNJ, nothing, or space);
* Arabic-script letter variants: alef forms -> ا, alef maqsura/Persian yeh
  -> ي, keheh -> ك, teh marbuta / heh goal -> ه;
* Hebrew geresh/gershayim and typographic apostrophes -> ASCII ``'``/``"``;
  maqaf and dashes -> ``-``;
* whitespace runs -> one space.

Latin case is **preserved**: ``Split`` (city) and ``split`` (verb) must not
meet. Latin diacritics are preserved too (``Šibenik`` is not ``Sibenik``);
diacritic-less spellings are separate curated ``variant`` names.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Optional, Tuple

from core.detection.textspan import dropped

SCRIPTS = ("Latn", "Arab", "Hebr")
LANGUAGES = ("en", "ar", "he", "fa", "hr")
#: The script each supported language's names are written in.
LANGUAGE_SCRIPT = {"en": "Latn", "hr": "Latn", "ar": "Arab", "fa": "Arab", "he": "Hebr"}

_CHAR_MAP = {
    "\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627", "\u0671": "\u0627",
    "\u0649": "\u064A", "\u06CC": "\u064A",   # alef maqsura, Persian yeh -> yeh
    "\u06A9": "\u0643",                       # keheh -> kaf
    "\u0629": "\u0647", "\u06C0": "\u0647", "\u06C1": "\u0647",  # teh marbuta, heh forms -> heh
    "\u05F3": "'", "\u2019": "'", "\u2018": "'", "\u02BC": "'", "\u00B4": "'",
    "\u05F4": '"', "\u201C": '"', "\u201D": '"',
    "\u05BE": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
}
_REMOVED = {"\u200C", "\u200D"}  # ZWNJ, ZWJ


_FOLD_CACHE: dict = {}


def _fold(raw: str) -> str:
    """Normalised replacement for one input character ('' when dropped).
    Pure, so memoised - normalisation is the per-character hot path."""
    out = _FOLD_CACHE.get(raw)
    if out is None:
        if dropped(raw) or raw in _REMOVED:
            out = ""
        else:
            out = "".join(_CHAR_MAP.get(ch, ch)
                          for ch in unicodedata.normalize("NFKC", raw)
                          if not (dropped(ch) or ch in _REMOVED))
            out = "".join(" " if ch.isspace() else ch for ch in out)
        if len(_FOLD_CACHE) < 65536:
            _FOLD_CACHE[raw] = out
    return out


def normalize_with_index(text: str) -> Tuple[str, List[int]]:
    """Normalise ``text``; return it with a map from each output char to its
    offset in the input (so matches report offsets into the original)."""
    out: List[str] = []
    index: List[int] = []
    prev_space = False
    fold = _fold
    for pos, raw in enumerate(text):
        folded = fold(raw)
        for ch in folded:
            if ch == " ":
                if prev_space:
                    continue
                prev_space = True
            else:
                prev_space = False
            out.append(ch)
            index.append(pos)
    return "".join(out), index


def match_key(name: str) -> str:
    """The normalised form of a place name used for matching."""
    return normalize_with_index(name)[0].strip()


def comparison_key(name: str) -> str:
    """Case-insensitive key used only to compare a label with the native
    (endonym) labels when classifying a name - never for matching text."""
    return match_key(name).casefold()


def script_of(name: str) -> Optional[str]:
    """ISO 15924 script of ``name`` (Latn/Arab/Hebr), or None when mixed/other."""
    found = set()
    for ch in name:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if 0x0590 <= cp <= 0x05FF or 0xFB1D <= cp <= 0xFB4F:
            found.add("Hebr")
        elif (0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F
              or 0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF):
            found.add("Arab")
        elif ch.isascii() or 0x00C0 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF:
            found.add("Latn")
        else:
            found.add("other")
    return found.pop() if len(found) == 1 and "other" not in found else None


def tokens(norm: str) -> List[Tuple[int, int]]:
    """Word token spans in normalised text: runs of letters/digits, with an
    apostrophe kept inside a word when a letter follows it (``ג'דה``,
    ``People's``)."""
    spans: List[Tuple[int, int]] = []
    i, n = 0, len(norm)
    while i < n:
        if not norm[i].isalnum():
            i += 1
            continue
        start = i
        while i < n and (norm[i].isalnum()
                         or (norm[i] == "'" and i + 1 < n and norm[i + 1].isalpha()
                             and i > start)):
            i += 1
        # A trailing geresh is part of Hebrew transliterations (פרת').
        if i < n and norm[i] == "'" and "\u05D0" <= norm[i - 1] <= "\u05EA":
            i += 1
        spans.append((start, i))
    return spans


def name_tokens(name: str) -> Tuple[str, ...]:
    key = match_key(name)
    return tuple(key[s:e] for s, e in tokens(key))


def is_separator(gap: str) -> bool:
    """Whether the text between two tokens of one multi-word name is only
    whitespace/hyphens (so "Tel Aviv-Yafo" matches across the hyphen, but a
    comma or full stop breaks a name)."""
    return bool(gap) and all(ch in " -" for ch in gap)


def sorted_unique(values: Iterable[str]) -> List[str]:
    return sorted(set(values))
