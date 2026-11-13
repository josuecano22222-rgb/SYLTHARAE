#!/usr/bin/env python
"""Split the untranslated msgids into one blank worksheet per language.

The source is ``audit_report/findings/i18n_gaps.csv`` (written by
``tools/audit/check_structure.py``): one row per msgid with the catalogue
entries it is missing from. This script only *rearranges* those facts into the
shape a translator works in - ``msgid``, the file that uses it, and an empty
``translation`` column. It never fills that column: machine-translating legal
and intelligence screens would turn a red test green and degrade the product,
so every worksheet is delivered blank.

Usage::

    python tools/audit/export_i18n_gaps.py

Writes ``audit_report/findings/i18n_gaps_<language>.csv`` and prints the row
count per language. A language with no gaps gets no file.
"""

from __future__ import annotations

import collections
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "audit_report" / "findings" / "i18n_gaps.csv"
OUT = ROOT / "audit_report" / "findings"

#: Catalogue languages, in the order the product ships them.
LANGUAGES = ("en", "ar", "he", "fa", "hr")

COLUMNS = ("msgid", "source_file", "translation")


def main() -> int:
    rows = list(csv.DictReader(SOURCE.open(encoding="utf-8")))
    per_language: dict[str, list[dict]] = collections.OrderedDict(
        (lang, []) for lang in LANGUAGES)
    seen: dict[str, set] = {lang: set() for lang in LANGUAGES}

    for row in rows:
        msgid = row["msgid"]
        source = row["used_in"]
        for lang in (l.strip() for l in row["missing_from"].split(",")):
            if lang not in per_language:
                print(f"unknown language {lang!r} in {SOURCE.name}", file=sys.stderr)
                continue
            if (lang, msgid) in seen[lang]:
                continue
            seen[lang].add((lang, msgid))
            per_language[lang].append({"msgid": msgid, "source_file": source,
                                       "translation": ""})

    written = 0
    for lang, entries in per_language.items():
        if not entries:
            continue
        target = OUT / f"i18n_gaps_{lang}.csv"
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(sorted(entries, key=lambda e: (e["source_file"], e["msgid"])))
        written += len(entries)
        print(f"{target.relative_to(ROOT)}: {len(entries)} msgid(s), translation column empty")
    print(f"{len(rows)} gap row(s) across {len(LANGUAGES)} language(s); "
          f"{written} worksheet row(s) written, none translated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
