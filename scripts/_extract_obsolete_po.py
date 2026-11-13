"""One-off helper: print obsolete msgid/msgstr pairs from .po files."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_obsolete(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("#~ msgid "):
            i += 1
            continue
        if line == '#~ msgid ""':
            i += 1
            parts: list[str] = []
            while i < len(lines) and lines[i].startswith("#~ ") and not lines[i].startswith("#~ msgstr"):
                m = re.match(r'#~ "(.*)"\s*$', lines[i])
                if m:
                    parts.append(m.group(1))
                i += 1
            msgid = "".join(parts)
        else:
            m = re.match(r'#~ msgid "(.*)"\s*$', line)
            msgid = m.group(1) if m else ""
            i += 1
        if i >= len(lines) or not lines[i].startswith("#~ msgstr"):
            continue
        if lines[i] == '#~ msgstr ""':
            i += 1
            sparts: list[str] = []
            while i < len(lines) and lines[i].startswith("#~ ") and not lines[i].startswith("#~ msgid"):
                m = re.match(r'#~ "(.*)"\s*$', lines[i])
                if m:
                    sparts.append(m.group(1))
                i += 1
            msgstr = "".join(sparts)
        else:
            m = re.match(r'#~ msgstr "(.*)"\s*$', lines[i])
            msgstr = m.group(1) if m else ""
            i += 1
        if msgid and msgstr:
            pairs[msgid] = msgstr
    return pairs


if __name__ == "__main__":
    for lang in ("ar", "he", "fa", "hr"):
        text = (ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po").read_text(
            encoding="utf-8"
        )
        print(lang, len(parse_obsolete(text)))
