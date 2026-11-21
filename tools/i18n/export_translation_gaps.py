#!/usr/bin/env python
"""List, per catalogue, the entries a human translator still has to author.

This is the *handoff* form of the translation gap, not a fix for it. The
integration suite fails while an entry in ``ar``/``he``/``fa``/``hr`` has an
empty ``msgstr`` or is marked fuzzy (``tests/integration/test_translation_coverage.py``).
Filling those entries by machine would turn the test green and make the product
worse: the strings are legal, intelligence and safety wording. So the work is
handed over as a worksheet, and this script only rearranges facts:

* ``msgid`` (and ``msgid_plural``) - the source text, verbatim;
* ``status`` - ``missing`` (no draft at all), ``fuzzy`` (a draft exists but the
  source changed, or it was never reviewed) or ``partial`` (a plural entry with
  some forms filled in);
* ``current_draft`` - the existing but untrusted draft, for a fuzzy/partial row
  only. It is reference material: the translator decides, and overwrites;
* ``translation`` - **always empty**. Nothing here ever writes into
  ``translations/``.

Every row also carries the source locations the ``.pot`` records, reduced to a
``module`` so the sheets can be split by area (``templates/Analysis``,
``static/js``, ...). One worksheet per language is written to
``audit_report/findings/i18n_untranslated_<lang>.csv`` next to a
``summary.json`` with the counts per language and per module.

Usage::

    python tools/i18n/export_translation_gaps.py           # write the worksheets
    python tools/i18n/export_translation_gaps.py --check   # fail if stale
    python tools/i18n/export_translation_gaps.py --locale ar

``--check`` compares the worksheets that are on disk with the catalogs as they
are now and exits 1 when they no longer agree - after a translator handback, or
after any catalog edit, the sheets are regenerated in the same commit.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = ROOT / "translations"
OUT = ROOT / "audit_report" / "findings"

#: The languages the coverage test requires. ``en`` is the source language and
#: is not part of the handoff; ``hr`` is here because its catalogue is
#: maintained even though it is not selectable in the UI (see
#: docs/TRANSLATION_HANDOFF.md for the decision brief).
LANGUAGES = ("ar", "he", "fa", "hr")

COLUMNS = ("msgid", "msgid_plural", "status", "module", "current_draft",
           "translation", "sources")


def _module(locations) -> str:
    """Reduce a location list to one area name (``templates/Analysis``)."""
    if not locations:
        return "(no location)"
    path = locations[0][0]
    parts = path.split("/")
    if len(parts) == 1:
        return parts[0]
    return "/".join(parts[:2])


def _source_text(value) -> str:
    """A msgid/msgstr is ``str`` (singular) or a tuple (plural forms)."""
    if value is None:
        return ""
    if isinstance(value, (tuple, list)):
        return "\n".join(str(v) for v in value)
    return str(value)


def collect(language: str) -> list[dict]:
    """The rows one catalogue contributes, sorted by module then source text."""
    path = CATALOGS / language / "LC_MESSAGES" / "messages.po"
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} is missing")
    catalog = read_po(path.open(encoding="utf-8"))

    rows = []
    for message in catalog:
        if not message.id:            # the header entry
            continue
        plural = isinstance(message.id, tuple)
        draft = _source_text(message.string)
        forms = [draft] if not plural else list(message.string or [])

        if message.fuzzy:
            status = "fuzzy"
        elif not any((form or "").strip() for form in forms):
            status = "missing"
        elif plural and any(not (form or "").strip() for form in forms):
            status = "partial"
        else:
            continue                   # translated and trusted

        rows.append({
            "msgid": _source_text(message.id),
            "msgid_plural": _source_text(message.id_plural) if plural else "",
            "status": status,
            "module": _module(message.locations),
            # A draft is context for a fuzzy/partial row. A missing row has none.
            "current_draft": draft if status in ("fuzzy", "partial") else "",
            "translation": "",
            "sources": " ".join(f"{name}:{line}" for name, line in message.locations),
        })
    rows.sort(key=lambda row: (row["module"], row["msgid"], row["status"]))
    return rows


def render(language: str) -> tuple[str, list[dict]]:
    """The exact CSV text for one language, and the rows it came from."""
    rows = collect(language)
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue(), rows


def summary(rows_by_language: dict[str, list[dict]]) -> dict:
    """Counts per language, per module and per status."""
    out = {"languages": {}, "total": 0}
    for language, rows in rows_by_language.items():
        by_status = collections.Counter(row["status"] for row in rows)
        by_module = collections.Counter(row["module"] for row in rows)
        out["languages"][language] = {
            "total": len(rows),
            "by_status": dict(sorted(by_status.items())),
            "by_module": dict(sorted(by_module.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
        out["total"] += len(rows)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if the sheets on disk are stale")
    parser.add_argument("--locale", action="append", choices=LANGUAGES,
                        help="restrict to one language (repeatable)")
    args = parser.parse_args(argv)

    languages = tuple(args.locale) if args.locale else LANGUAGES
    rendered, rows_by_language = {}, {}
    for language in languages:
        rendered[language], rows_by_language[language] = render(language)

    counts = summary(rows_by_language)
    payload = json.dumps(counts, indent=2, ensure_ascii=False) + "\n"

    stale = []
    targets = {language: OUT / f"i18n_untranslated_{language}.csv" for language in languages}
    for language, path in targets.items():
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != rendered[language]:
                stale.append(path)
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered[language], encoding="utf-8")

    summary_path = OUT / "i18n_untranslated_summary.json"
    if args.check:
        if languages == LANGUAGES:
            current = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
            if current != payload:
                stale.append(summary_path)
        if stale:
            print("the worksheets are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path.relative_to(ROOT)}", file=sys.stderr)
            print("run: python tools/i18n/export_translation_gaps.py", file=sys.stderr)
            return 1
        print(f"worksheets agree with the catalogs ({counts['total']} row(s))")
        return 0
    if languages == LANGUAGES:
        OUT.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(payload, encoding="utf-8")

    for language in languages:
        entry = counts["languages"][language]
        statuses = ", ".join(f"{k} {v}" for k, v in entry["by_status"].items())
        print(f"{language}: {entry['total']} entr(y/ies) - {statuses}")
        for module, count in list(entry["by_module"].items())[:6]:
            print(f"    {count:4d}  {module}")
    print(f"\n{counts['total']} entr(y/ies) across {len(languages)} language(s); "
          f"every 'translation' column is empty by construction")
    print(f"worksheets: {OUT.relative_to(ROOT)}/i18n_untranslated_<lang>.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
