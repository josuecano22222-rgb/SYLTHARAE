#!/usr/bin/env python3
"""Fill analyst-categorization msgstr in all locale catalogs (en/ar/he/fa/hr)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._extract_obsolete_po import parse_obsolete  # noqa: E402
from scripts.analyst_i18n_extra import EXTRA  # noqa: E402
from tests.integration.test_analyst_i18n import NEW_MSGIDS  # noqa: E402

LANGS = ("en", "ar", "he", "fa", "hr")

# Server-only strings on analyst_categorization.html not always in NEW_MSGIDS.
ADDITIONAL_MSGIDS: tuple[str, ...] = (
    "Manual, analyst-driven categorization layer — fully separate from system-generated smart categorization.",
    "These labels belong exclusively to the analyst (manual) taxonomy. They are never shared with smart categories.",
    "Every categorization action with analyst, timestamp, category, originating query and affected files.",
    "Apply Filters",
)

ALL_MSGIDS: tuple[str, ...] = tuple(dict.fromkeys((*NEW_MSGIDS, *ADDITIONAL_MSGIDS)))


def _mapping_for(lang: str) -> dict[str, str]:
    po_path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
    obsolete = parse_obsolete(po_path.read_text(encoding="utf-8"))
    merged = {**obsolete, **EXTRA.get(lang, {})}
    return merged


def _apply(lang: str) -> None:
    mapping = _mapping_for(lang) if lang != "en" else {}
    path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as handle:
        catalog = read_po(handle, locale=lang)
    for msgid in ALL_MSGIDS:
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
    print(f"analyst categorization i18n: {lang}")


def main() -> None:
    for lang in LANGS:
        _apply(lang)
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "compile", "-d", "translations"],
        check=True,
        cwd=ROOT,
    )
    print("compiled .mo")


if __name__ == "__main__":
    main()
