#!/usr/bin/env python3
"""Ensure interface registry names and descriptions exist in all locale catalogs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.interfaces.domains import DOMAIN_LABELS  # noqa: E402
from core.interfaces.registry import FEATURES, REGISTRY  # noqa: E402
from scripts.registry_desc_i18n import DESC_ROWS, DOMAIN_ROWS, NAME_ROWS  # noqa: E402

LANGS = ("en", "ar", "he", "fa", "hr")
LANG_INDEX = {"ar": 1, "he": 2, "fa": 3, "hr": 4}


def _registry_msgids() -> tuple[str, ...]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            ids.append(value)

    for interface in REGISTRY:
        add(interface.name)
        add(interface.description or "")
    for feature in FEATURES:
        add(feature.name)
        add(feature.description or "")
    for label in DOMAIN_LABELS.values():
        add(label)
    return tuple(ids)


def _translation_maps() -> dict[str, dict[str, str]]:
    by_lang: dict[str, dict[str, str]] = {lang: {} for lang in LANGS if lang != "en"}
    for english, ar, he, fa, hr in DOMAIN_ROWS + NAME_ROWS:
        for lang, text in (("ar", ar), ("he", he), ("fa", fa), ("hr", hr)):
            by_lang[lang][english] = text
    for english, ar, he, fa, hr in DESC_ROWS:
        for lang, text in (("ar", ar), ("he", he), ("fa", fa), ("hr", hr)):
            by_lang[lang][english] = text
    return by_lang


def _apply(lang: str, mapping: dict[str, str], msgids: tuple[str, ...]) -> None:
    path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as handle:
        catalog = read_po(handle, locale=lang)
    for msgid in msgids:
        message = catalog.get(msgid)
        if message is None:
            text = msgid if lang == "en" else mapping.get(msgid, msgid)
            catalog.add(msgid, string=text)
            continue
        if lang == "en":
            if not message.string:
                message.string = msgid
        else:
            text = mapping.get(msgid)
            if text:
                message.string = text
            elif not message.string:
                message.string = msgid
        message.flags.discard("fuzzy")
    with path.open("wb") as handle:
        write_po(handle, catalog, width=88)
    print(f"interface registry i18n: {lang}")


def main() -> None:
    msgids = _registry_msgids()
    maps = _translation_maps()
    _apply("en", {}, msgids)
    for lang in ("ar", "he", "fa", "hr"):
        _apply(lang, maps[lang], msgids)
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "compile", "-d", "translations"],
        check=True,
        cwd=ROOT,
    )
    print("compiled .mo")


if __name__ == "__main__":
    main()
