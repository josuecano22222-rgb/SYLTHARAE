"""English catalog: msgstr equals msgid (and clear fuzzy on export_surface msgids)."""

from __future__ import annotations

import re
import sys
from io import BytesIO
from pathlib import Path

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "core" / "i18n" / "export_surface_strings.py"
PO = ROOT / "translations" / "en" / "LC_MESSAGES" / "messages.po"
MIN_ENTRIES = 1000


def main() -> int:
    raw = PO.read_bytes()
    if len(raw) < 50_000:
        print(f"refusing to edit suspiciously small catalog ({len(raw)} bytes)", file=sys.stderr)
        return 1

    export_src = SRC.read_text(encoding="utf-8")
    export_msgids = set(re.findall(r"_\('([^']+)'\)", export_src))

    catalog = read_po(BytesIO(raw), locale="en")
    catalog.fuzzy = False
    if len(catalog) < MIN_ENTRIES:
        print(f"refusing to write: catalog has only {len(catalog)} entries", file=sys.stderr)
        return 1

    identity = 0
    for message in catalog:
        if not message.id or isinstance(message.id, (list, tuple)):
            continue
        if str(message.string or "") != message.id:
            message.string = message.id
            identity += 1
        if message.id in export_msgids and "fuzzy" in message.flags:
            message.flags.remove("fuzzy")

    tmp = PO.with_suffix(".po.tmp")
    buf = BytesIO()
    write_po(buf, catalog)
    data = buf.getvalue()
    if len(data) < len(raw) // 2:
        print(f"refusing to write: output ({len(data)} bytes) much smaller than input", file=sys.stderr)
        return 1
    tmp.write_bytes(data)
    tmp.replace(PO)

    print(f"set identity msgstr on {identity} entries; export_surface msgids: {len(export_msgids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
