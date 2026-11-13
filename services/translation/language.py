"""Which language is this, and who decides.

The corpus is English, Arabic, Hebrew, Persian and Croatian, and the analyst
usually selects a sentence or two. Detection is therefore a *script and
stop-word* heuristic, not a statistical model:

* a run of Hebrew letters is Hebrew;
* a run of Arabic-script letters is Persian when it carries the letters Persian
  has and Arabic does not (پ چ ژ گ) and Arabic otherwise - the two share almost
  every letter, so script alone cannot separate them;
* Latin script is separated into Croatian and English by function words, which
  is the only reliable signal available on two sentences.

Detection never has the last word. A selection like ``"42"`` or ``"Yes."``
carries no evidence at all, and a short sentence in a related language can be
misread; the analyst can always declare the source language, and when they do,
:func:`resolve_languages` reports that the declaration was used *and* what
detection thought, so the interface can say "you said Croatian, this looks
English, translate anyway?".
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Optional, Tuple

__all__ = [
    "RTL_LANGUAGES",
    "TRANSLATION_LANGUAGES",
    "detect_language",
    "resolve_languages",
]

#: Content languages this feature offers, independent of the interface
#: languages in ``settings.languages`` (Croatian is in the corpus and is not a
#: UI language).
TRANSLATION_LANGUAGES = OrderedDict([
    ("en", "English"),
    ("ar", "العربية"),
    ("he", "עברית"),
    ("fa", "فارسی"),
    ("hr", "Hrvatski"),
])

RTL_LANGUAGES = frozenset({"ar", "he", "fa"})

_HEBREW = re.compile(r"[\u0590-\u05FF]")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_PERSIAN_LETTERS = re.compile(r"[\u067E\u0686\u0698\u06AF\u06CC\u06A9]")
_LATIN = re.compile(r"[A-Za-z\u00C0-\u024F]")

_CROATIAN_WORDS = frozenset("""
    je su sam smo ste nije nisu koji koja koje da se za na u iz od do po
    molim vas kao što prije poslije izvješće izvješća datoteka strana izvor
    danas jučer sutra biti ima nemaju ovo ono te ili ali zato jer kada gdje
    dobar dan jutro večer hvala ne zdravo vrlo mnogo također pošaljete
    moguće dolje gore ovdje tamo
""".split())

_ENGLISH_WORDS = frozenset("""
    the and of to in is are was were this that with for from not have has
    you we they it he she his her their our your please report file door open
    delivered yesterday today morning close send
""".split())

#: Minimum script letters before a script-based answer is called confident.
_SCRIPT_EVIDENCE = 3


def _latin_scores(word: str) -> Tuple[int, int]:
    lowered = word.lower()
    return (1 if lowered in _ENGLISH_WORDS else 0,
            1 if lowered in _CROATIAN_WORDS else 0)


def detect_language(text: str, with_confidence: bool = False):
    """Return the detected language code (``None`` when unknown).

    With ``with_confidence=True``, returns ``(language, confident)``.
    """
    sample = text or ""
    hebrew = len(_HEBREW.findall(sample))
    arabic = len(_ARABIC.findall(sample))
    persian = len(_PERSIAN_LETTERS.findall(sample))
    latin = len(_LATIN.findall(sample))

    language: Optional[str] = None
    confident = False

    if hebrew >= _SCRIPT_EVIDENCE and hebrew >= arabic:
        language, confident = "he", True
    elif arabic >= _SCRIPT_EVIDENCE:
        language = "fa" if persian else "ar"
        confident = True
    elif latin >= _SCRIPT_EVIDENCE:
        words = [word for word in re.split(r"[^A-Za-z\u00C0-\u024F]+", sample) if word]
        english = sum(_latin_scores(word)[0] for word in words)
        croatian = sum(_latin_scores(word)[1] for word in words)
        if english == croatian == 0:
            # Letters but no function word either way (a name, a code, a
            # single word): guess English only when it is asked of us, and do
            # not call it confident.
            language, confident = "en", False
        elif croatian > english:
            language, confident = "hr", True
        else:
            language, confident = "en", True

    return (language, confident) if with_confidence else language


def resolve_languages(text: str, target_lang: str,
                      source_lang: Optional[str] = None) -> Tuple[str, Optional[str], bool]:
    """Return ``(source, detected, overridden)`` for a translation request.

    A declared source language always wins; ``detected`` still reports what
    the text looked like, so the caller can warn about a mismatch.
    """
    detected = detect_language(text)
    if source_lang:
        return source_lang, detected, True
    return (detected or "en"), detected, False
