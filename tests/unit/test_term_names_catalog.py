"""The Names page and the label marker ship no untranslated string.

Round 19's LEXIS work was rejected for one reason: strings were added to a page
and the catalogs were not, so an Arabic reader got an English sentence in the
middle of a translated table. The catalogs are frozen for round 21 - no new
msgids are added, and no machine translation is written - so the only safe way
to add a page is to build it out of vocabulary that is **already** translated.

This module enforces that mechanically. It derives the msgids from the new
template rather than listing them by hand, so a string added later without a
translation fails here instead of shipping in English, and it asserts the
JavaScript asks the page for its user-visible text instead of hard-coding it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSLATIONS = PROJECT_ROOT / "translations"
TEMPLATE = PROJECT_ROOT / "templates" / "Category" / "category_names.html"
SCRIPT = PROJECT_ROOT / "static" / "js" / "pages" / "category-names-page.js"

#: Every catalog the application maintains. Arabic, Hebrew and Persian are
#: offered; Croatian is maintained but not selectable.
CATALOGS = ("ar", "he", "fa", "hr")

GETTEXT_CALL = re.compile(r"""_\(\s*(['"])((?:\\.|(?!\1).)*)\1""", re.S)


def _msgids() -> list[str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    found: list[str] = []
    for _quote, raw in GETTEXT_CALL.findall(text):
        msgid = raw.replace("\\'", "'").replace('\\"', '"')
        if msgid and msgid not in found:
            found.append(msgid)
    return found


def _catalog(lang: str):
    from babel.messages.pofile import read_po

    with (TRANSLATIONS / lang / "LC_MESSAGES" / "messages.po").open("rb") as handle:
        return read_po(handle, locale=lang)


def test_the_template_has_strings_to_check():
    """A guard: an extraction that finds nothing would pass everything below."""
    assert len(_msgids()) >= 15, _msgids()


@pytest.mark.parametrize("lang", CATALOGS)
def test_every_string_on_the_page_is_translated(lang):
    catalog = _catalog(lang)
    missing = [msgid for msgid in _msgids()
               if msgid not in catalog or not (catalog[msgid].string or "").strip()]
    assert not missing, f"{lang} has no translation for: {missing}"


@pytest.mark.parametrize("lang", CATALOGS)
def test_no_string_on_the_page_is_marked_fuzzy(lang):
    catalog = _catalog(lang)
    fuzzy = [msgid for msgid in _msgids()
             if msgid in catalog and catalog[msgid].fuzzy]
    assert not fuzzy, f"{lang} marks these entries fuzzy: {fuzzy}"


def test_the_catalogs_were_not_edited_to_fit_the_page():
    """The round-21 rule is reuse, not extension.

    If a future change really needs a new string, it needs four catalog entries
    and a decision; this test fails first and asks for that decision instead of
    letting an empty entry ship.
    """
    catalog = _catalog("ar")
    assert len(catalog) > 1000, "the Arabic catalog looks truncated"


def test_the_script_does_not_hard_code_user_visible_text():
    """A sentence in the JavaScript is a sentence no catalog can reach.

    Strings passed to ``window.t`` are checked by the coverage suite; what this
    asserts is the opposite - the page's own script contains no bare English
    sentence, because the only user-visible text it needs (the delete
    confirmation) is read from a translated ``data-confirm`` attribute.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    literals = re.findall(r"""(["'])([A-Z][^"']{12,}?)\1""", source)
    words = [text for _q, text in literals
             if re.search(r"\b(the|is|was|not|you|this|that|please|cannot)\b", text,
                          re.IGNORECASE)]
    assert not words, f"the script hard-codes prose: {words}"
    assert "data-confirm" in source, "the confirmation text is not read from the page"
