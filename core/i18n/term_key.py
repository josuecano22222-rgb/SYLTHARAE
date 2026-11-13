"""One normalisation for term names, whatever language they are written in.

Terms, category names and keyword words all live in ``words`` as a single
canonical string. Their translations live in ``term_names`` (see
``docs/plans/CATEGORY_TERM_I18N.md``). Search has to answer the same question in
two directions - "the analyst typed سياسي, which term is that?" and "the analyst
typed political, does the Arabic name match?" - so both the stored name and the
typed query go through **one** function here.

The folding rules are not re-implemented. They are ``core.geo.names``'s rules,
which already normalise Arabic harakat and tatweel, Persian yeh and keheh,
Hebrew niqqud and geresh, and ZWNJ, and which the gazetteer relies on for place
names. A second copy of that table would drift, so this module imports it.

Two deliberate differences from place names
===========================================

1. **Case is folded.** ``core.geo.names.match_key`` preserves Latin case on
   purpose ("Split" the city is not "split" the verb). A category called
   "Political" and a query for "political" are the same category, so
   :func:`term_key` casefolds.
2. **The Arabic definite article is optional.** ``السياسي`` and ``سياسي`` are the
   same term to a reader and different strings to a computer. Queries and stored
   names therefore expand to a small set of variants rather than one key; the
   expansion only ever *adds* candidates, so it cannot lose a match.

Scripts
=======

:func:`script_ok` mirrors the ``CHECK`` on ``term_names.script`` so a name can be
rejected with a readable message before PostgreSQL rejects it, and so a test can
assert the rule without a database.
"""
from __future__ import annotations

from typing import Tuple

from core.geo.names import LANGUAGE_SCRIPT, match_key, script_of

#: The Arabic definite article, as it survives normalisation.
ARABIC_ARTICLE = "ال"

__all__ = [
    "ARABIC_ARTICLE",
    "LANGUAGE_SCRIPT",
    "key_variants",
    "script_of",
    "script_ok",
    "term_key",
]


def term_key(text: str) -> str:
    """The comparison form of a term name or a query.

    Normalises with the place-name rules and casefolds the result. Empty input
    gives an empty key; callers must treat that as "no key", never as a match.
    """
    if not text:
        return ""
    return match_key(text).casefold()


def _strip_article(key: str) -> str:
    # A bare article is not a term, and stripping it from a two-letter stem
    # would turn "الة" into "ة".
    if key.startswith(ARABIC_ARTICLE) and len(key) - len(ARABIC_ARTICLE) >= 2:
        return key[len(ARABIC_ARTICLE):]
    return ""


def key_variants(text: str) -> Tuple[str, ...]:
    """Every key a query (or a stored name) may legitimately match under.

    Order is deterministic and the first element is always :func:`term_key`, so
    a caller that wants a single key uses ``key_variants(text)[0]`` and a caller
    that wants recall uses the whole tuple.

    * the key itself;
    * the key without a leading ``ال``;
    * the key with a leading ``ال`` added, so a query for ``سياسي`` also finds a
      name stored as ``السياسي``.

    Duplicates and empty strings are removed: ``term_names.match_key`` is unique
    per ``(word, language, name_type)``, and an empty key must never be queried.
    """
    key = term_key(text)
    if not key:
        return ()
    variants = [key, _strip_article(key), ARABIC_ARTICLE + key]
    seen = set()
    ordered = []
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            ordered.append(variant)
    return tuple(ordered)


def script_ok(language: str, script: str, name_type: str) -> bool:
    """Whether a name of this language, script and type is allowed.

    Mirrors ``term_names.script``'s CHECK exactly:

    * a ``transliteration`` is Latin by definition;
    * otherwise the script must be the one the language is written in;
    * an unknown language is refused rather than silently accepted.
    """
    if not script:
        return False
    if name_type == "transliteration":
        return script == "Latn"
    expected = LANGUAGE_SCRIPT.get(language)
    return expected is not None and script == expected
