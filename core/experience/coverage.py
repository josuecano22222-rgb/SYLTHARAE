"""Translation coverage, measured from the catalogs that actually ship.

The rule this module exists to keep: a coverage figure is a measurement, never
a score. There is no ``92/100`` anywhere, because a number like that cannot be
acted on. What is reported instead is what is true of a catalog and a screen:

* **translated** - the key or its source string has a translation in that
  language;
* **missing** - it has none, so the reader sees English;
* **fallback** - the translation exists but is identical to the source string,
  which usually means "not really translated";
* **source** - the string that is shown when nothing else can be.

Two catalogs exist in this product and both are read, because the frontend
runtime reads both: the Babel catalogs under ``translations/<lang>`` for
server-rendered text, and the JavaScript UI packs under
``static/js/i18n/locales`` for strings that only exist in client code. A
language's coverage is the union - anything that measured only one of the two
would report a number the reader can see to be wrong.

The contract's semantic keys (``screen.files.title``) are looked up by key
first and by their English source string second, because the catalogs still key
by source string today. That second lookup is what makes the number useful now
rather than after a migration nobody has started; the gap between the two is
reported as ``keyed`` versus ``by_source`` so progress is visible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from core.experience.contract import contract, contracts, translation_index

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRANSLATIONS = PROJECT_ROOT / "translations"
JS_PACKS = PROJECT_ROOT / "static/js/i18n/locales"


@dataclass(frozen=True)
class Catalog:
    """One language's catalog, from both places it can live."""

    locale: str
    messages: Dict[str, str]
    source: Tuple[str, ...]     # which files it was read from

    def translation(self, key: str, source_text: str) -> Optional[str]:
        """The translation of a contract key, or of its source string."""
        if key in self.messages:
            return self.messages[key]
        if source_text in self.messages:
            return self.messages[source_text]
        return None

    @property
    def size(self) -> int:
        return len(self.messages)


def _catalog_locale(path: Path) -> Optional[str]:
    """The locale a PO file belongs to, or ``None`` for a file that has none.

    ``translations/<locale>/LC_MESSAGES/messages.po`` names its locale in the
    path. ``translations/messages.pot`` is the *template* and belongs to no
    locale: read as ``path.parent.parent.name`` it announced the checkout
    directory as a locale, so Babel raised ``ValueError: expected only letters,
    got 'syltharae-pro'`` and ``/api/experience/coverage`` answered 500 in any
    checkout whose directory is not named after a language.
    """
    parts = path.parts
    if len(parts) >= 3 and parts[-2] == "LC_MESSAGES":
        return parts[-3]
    return None


def _read_po(path: Path, keep_untranslated: bool = False) -> Dict[str, str]:
    """Read a catalog. With ``keep_untranslated``, an entry with no translation
    keeps its source string - which is what the message *template* is: the list
    of strings that exist, whether or not anyone has translated them yet."""
    if not path.exists():
        return {}
    from babel.messages.pofile import read_po

    messages: Dict[str, str] = {}
    with open(path, "rb") as handle:
        catalog = read_po(handle, locale=_catalog_locale(path))
        for message in catalog:
            if not message.id:
                continue
            # A plural entry is one message, looked up by its singular msgid.
            msgid = message.id[0] if isinstance(message.id, (list, tuple)) else message.id
            text = message.string
            if isinstance(text, (list, tuple)):
                text = next((part for part in text if part), "")
            if not text and keep_untranslated:
                text = msgid
            if text:
                messages[str(msgid)] = str(text)
    return messages


def _read_js_pack(path: Path) -> Dict[str, str]:
    """The JavaScript UI pack: a JSON object assigned to a global.

    Parsed as JSON rather than evaluated - nothing in a data file is executed
    to measure it.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    match = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in data.items() if value}


def available_locales() -> Tuple[str, ...]:
    """Locales that have a catalog in either place, in a stable order."""
    found = set()
    if TRANSLATIONS.is_dir():
        for child in TRANSLATIONS.iterdir():
            if (child / "LC_MESSAGES" / "messages.po").exists():
                found.add(child.name)
    if JS_PACKS.is_dir():
        for pack in JS_PACKS.glob("*.js"):
            if pack.stem != "en":
                found.add(pack.stem)
    return tuple(sorted(found))


def catalog(locale: str) -> Catalog:
    """Every translation the product ships for one language."""
    messages: Dict[str, str] = {}
    sources: List[str] = []
    po = TRANSLATIONS / locale / "LC_MESSAGES" / "messages.po"
    server = _read_po(po)
    if server:
        messages.update(server)
        sources.append(str(po.relative_to(PROJECT_ROOT)))
    pack = JS_PACKS / f"{locale}.js"
    client = _read_js_pack(pack)
    if client:
        # The JavaScript pack wins a tie: it is what the runtime uses for
        # client-rendered text, which is exactly where conflicts show up.
        messages.update(client)
        sources.append(str(pack.relative_to(PROJECT_ROOT)))
    return Catalog(locale=locale, messages=messages, source=tuple(sources))


def _catalogs() -> Dict[str, Catalog]:
    return {locale: catalog(locale) for locale in available_locales()}


def source_strings() -> Dict[str, str]:
    """The English source of every string the product can show.

    Taken from the message template - the file translators are given - so this
    is the list of strings that exist, not a list of strings somebody listed.
    """
    return _read_po(TRANSLATIONS / "messages.pot", keep_untranslated=True) or \
        _read_po(TRANSLATIONS / "en" / "LC_MESSAGES" / "messages.po",
                 keep_untranslated=True)


def language_coverage() -> List[Dict[str, object]]:
    """Per language: how much of the product it actually translates."""
    source = source_strings()
    rows = []
    for locale, cat in _catalogs().items():
        if locale == "en":
            continue
        translated = fallback = missing = 0
        for message_id, text in source.items():
            value = cat.messages.get(message_id)
            if not value:
                missing += 1
            elif value.strip() == text.strip():
                fallback += 1
            else:
                translated += 1
        total = translated + fallback + missing
        rows.append({
            "locale": locale,
            "entries": cat.size,
            "catalogs": list(cat.source),
            "source_strings": len(source),
            "translated": translated,
            "fallback": fallback,
            "missing": missing,
            "total": total,
            # Computed, not asserted: the reader can reproduce it from the
            # three counts beside it.
            "coverage": round(100 * (translated + fallback) / total, 1) if total else 0.0,
            "translated_coverage": round(100 * translated / total, 1) if total else 0.0,
        })
    return rows


def screen_coverage(interface_id: Optional[str] = None) -> List[Dict[str, object]]:
    """Per screen, per language: which of its strings are actually translated.

    A screen's strings are the ones its contract names. Each is looked up by
    semantic key first and by English source string second, because the catalogs
    still key by source string; which lookup succeeded is reported, so the
    migration to keys is measurable rather than claimed.
    """
    index = translation_index()
    screens = ([contract(interface_id)] if interface_id else list(contracts()))
    rows: List[Dict[str, object]] = []
    catalogs = {locale: cat for locale, cat in _catalogs().items() if locale != "en"}
    for screen in screens:
        if screen is None:
            continue
        entries = [index[key] for key in screen.screen.translation_keys if key in index]
        if not entries:
            rows.append({
                "interface_id": screen.interface_id,
                "declared": screen.screen.declared,
                "keys": 0,
                "languages": [],
            })
            continue
        languages = []
        for locale, cat in catalogs.items():
            translated = keyed = fallback = 0
            missing_keys = []
            for entry in entries:
                value = cat.translation(entry.key, entry.source)
                if not value:
                    missing_keys.append(entry.key)
                elif value.strip() == entry.source.strip():
                    fallback += 1
                else:
                    translated += 1
                    if entry.key in cat.messages:
                        keyed += 1
            total = len(entries)
            languages.append({
                "locale": locale,
                "keys": total,
                "translated": translated,
                "fallback": fallback,
                "missing": len(missing_keys),
                "by_key": keyed,
                "by_source": translated - keyed,
                "coverage": round(100 * translated / total, 1) if total else 0.0,
                "missing_keys": missing_keys[:50],
            })
        rows.append({
            "interface_id": screen.interface_id,
            "declared": screen.screen.declared,
            "keys": len(entries),
            "languages": languages,
        })
    return rows


def hardcoded_source_strings(interface_id: str) -> Tuple[str, ...]:
    """Contract strings whose key is not in any catalog yet.

    These are the strings that today fall back to English because the catalogs
    key by source string. Reported rather than hidden: it is the size of the
    remaining migration to semantic keys.
    """
    index = translation_index()
    screen = contract(interface_id)
    if screen is None:
        return ()
    catalogs = [cat for locale, cat in _catalogs().items() if locale != "en"]
    unkeyed = []
    for key in screen.screen.translation_keys:
        entry = index.get(key)
        if entry is None:
            continue
        if not any(entry.key in cat.messages for cat in catalogs):
            unkeyed.append(entry.key)
    return tuple(unkeyed)


def counts() -> Dict[str, int]:
    """Machine-generated totals for the API, the doc and the studio."""
    languages = language_coverage()
    screens = screen_coverage()
    index = translation_index()
    catalogs = _catalogs()
    return {
        "languages": len(languages),
        "catalogs": sum(1 for cat in catalogs.values() if cat.source),
        "source_strings": len(source_strings()),
        "contract_keys": len(index),
        "contract_screens": len(screens),
        "translations_shipped": sum(cat.size for cat in catalogs.values()),
    }


__all__ = [
    "Catalog",
    "available_locales",
    "catalog",
    "counts",
    "hardcoded_source_strings",
    "language_coverage",
    "screen_coverage",
    "source_strings",
]
