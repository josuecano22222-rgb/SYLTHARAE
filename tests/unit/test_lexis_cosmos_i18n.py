"""LEXIS Cosmos ships translated: template, page script and registry entry.

``scripts/fill_lexis_cosmos_i18n.py`` is the source of the strings and of their
translations; this module is what makes running it mandatory rather than
optional. It re-derives the msgids from the template and the page script (the
same way the script does) and insists that

* every string the page asks for is in all five catalogs with a real, unfuzzy
  translation - an untranslated canvase caption is a half-English screen;
* nothing the page introduced is left equal to its English source outside
  English, except pure format words (``ID``, ``CSV``, ``LEXIS``);
* the compiled ``.mo`` - what gettext actually loads - carries them too;
* the interface registry entry (the sidebar label and its tooltip) is
  translated as well, and the routing/registry script knows about it;
* the locale packs and the server catalog do not disagree about a string, which
  would make a JS-rendered label flip between two translations.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fill_lexis_cosmos_i18n import (  # noqa: E402
    LANGS,
    MAINTAINED,
    ROWS,
    REGISTRY_ROWS,
    lexis_msgids,
)

pytestmark = pytest.mark.unit

PACKS_DIR = PROJECT_ROOT / "static" / "js" / "i18n" / "locales"
TRANSLATIONS_DIR = PROJECT_ROOT / "translations"

#: Words that stay the same in every catalog: format names, brand lettering and
#: bare identifiers. Anything else must differ from the English source.
LITERAL_ALLOWED = {"CSV", "ID", "JSON", "LEXIS", "Markdown", "TSV", "SYLTHARAE System"}


def _catalog(lang: str):
    from babel.messages.pofile import read_po

    with open(TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.po", "rb") as handle:
        return read_po(handle, locale=lang)


def _mo_catalog(lang: str):
    from babel.messages.mofile import read_mo

    with open(TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.mo", "rb") as handle:
        return read_mo(handle)


def _pack(lang: str) -> dict[str, str]:
    text = (PACKS_DIR / f"{lang}.js").read_text(encoding="utf-8")
    match = re.search(r"window\.I18N_UI_PACKS\[['\"]\w+['\"]\]\s*=\s*", text)
    if not match:
        return {}
    body = text[match.end():].strip()
    return json.loads(body[: body.rindex("}") + 1])


def _is_literal(msgid: str) -> bool:
    return msgid in LITERAL_ALLOWED or not any(ch.islower() for ch in msgid)


def test_the_page_has_strings_to_check():
    """Guard: the rest is worthless if the extraction finds nothing."""
    msgids = lexis_msgids()
    assert len(msgids) > 200
    template = (PROJECT_ROOT / "templates" / "Lexis" / "cosmos.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "static" / "js" / "pages" / "lexis-cosmos-page.js").read_text(encoding="utf-8")
    assert "{{ _(" in template or "_('" in template
    assert "t('" in script


@pytest.mark.parametrize("lang", MAINTAINED)
def test_every_page_string_is_translated(lang):
    catalog = _catalog(lang)
    missing, untranslated = [], []
    for msgid in lexis_msgids():
        message = catalog.get(msgid)
        if message is None:
            missing.append(msgid)
        elif not message.string or message.fuzzy:
            untranslated.append(msgid)
        elif message.string == msgid and not _is_literal(msgid):
            untranslated.append(msgid)
    assert not missing, f"[{lang}] {len(missing)} page strings absent: {missing[:5]}"
    assert not untranslated, (
        f"[{lang}] {len(untranslated)} page strings left in English: {untranslated[:5]}")


@pytest.mark.parametrize("lang", MAINTAINED)
def test_the_compiled_catalog_carries_the_page_strings(lang):
    mo = _mo_catalog(lang)
    missing = [msgid for msgid in lexis_msgids() if mo.get(msgid) is None]
    assert not missing, f"[{lang}] .mo needs recompiling: {missing[:5]}"


@pytest.mark.parametrize("lang", ("ar", "he", "fa"))
def test_the_registry_entry_is_translated(lang):
    from core.interfaces import get_interface

    interface = get_interface("lexis_cosmos")
    catalog = _catalog(lang)
    for msgid in (interface.name, interface.description):
        message = catalog.get(msgid)
        assert message is not None and message.string, f"[{lang}] registry string missing: {msgid[:40]}"
        assert message.string != msgid, f"[{lang}] registry string not translated: {msgid[:40]}"


def test_the_translation_rows_cover_every_string_the_page_introduces():
    """The script's rows are the translations: an unlisted string is a bug."""
    rows = {row[0] for row in ROWS + REGISTRY_ROWS}
    unlisted = [msgid for msgid in lexis_msgids()
                if msgid not in rows and not _is_literal(msgid)]
    # Strings the rest of the interface already translates are covered by the
    # existing catalogs, which the third test above verifies one by one.
    untranslated = [msgid for msgid in unlisted
                    if any((_catalog(lang).get(msgid) is None
                            or not _catalog(lang).get(msgid).string
                            or _catalog(lang).get(msgid).string == msgid)
                           for lang in MAINTAINED)]
    assert not untranslated, f"strings with no translation row: {untranslated[:6]}"


@pytest.mark.parametrize("lang", ("en", "ar", "he", "fa"))
def test_pack_and_catalog_agree_for_the_page_strings(lang):
    """Croatian is maintained but not offered, so it has no locale pack."""
    pack = _pack(lang)
    catalog = _catalog(lang)
    conflicts = []
    for msgid in lexis_msgids():
        value = pack.get(msgid)
        message = catalog.get(msgid)
        if value and message is not None and message.string and message.string != value:
            conflicts.append((msgid[:40], value, message.string))
    assert not conflicts, f"[{lang}] pack/catalog disagreements: {conflicts[:4]}"


def test_the_page_is_registered_as_a_translatable_interface():
    from core.interfaces import get_interface
    from core.interfaces.registry import REGISTRY

    interface = get_interface("lexis_cosmos")
    assert interface.kind.value == "PAGE"
    # The page endpoint is owned by this interface and by no other.
    owners = [i.interface_id for i in REGISTRY
              if interface.route in i.endpoints]
    assert owners == ["lexis_cosmos"]
    assert interface.help_topic == "classify/lexis-cosmos"
    names = {row[0] for row in REGISTRY_ROWS}
    assert interface.name in names
    assert interface.description in names
    assert LANGS == ("en", "ar", "he", "fa", "hr")
