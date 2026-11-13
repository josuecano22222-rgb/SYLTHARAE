#!/usr/bin/env python3
"""Catalog maintenance: report state, restore reviewed translations, prune artifacts.

The catalogs distinguish five states, and the tool keeps them distinct:

=============================  ==================================================
state                          meaning
=============================  ==================================================
approved                       a non-empty, non-fuzzy msgstr
empty                          a live entry with no msgstr at all
fuzzy                          a msgstr a translator or ``msgmerge`` marked for
                               review (a *carried-over* translation: it belongs
                               to a similar older msgid, so it cannot be trusted)
obsolete                       a ``#~`` pair - the msgid left the sources; the
                               translation is kept as catalog history
artifact                       an entry an extractor created out of code rather
                               than out of user-visible text (NARR-02:
                               ``msgid``/``msgid_plural``), which nothing looks up
=============================  ==================================================

What the tool will and will not do:

* **Restore** an ``empty`` or ``fuzzy`` entry from that catalog's own ``obsolete``
  block when the msgid is identical and the placeholders agree with the msgid.
  This returns a reviewed translation to the entry it was reviewed for; it is
  provenance-preserving and reversible.
* **Prune** an ``artifact`` entry, because no runtime path can look it up.
* **Never** fill a missing translation with English, and never touch an
  ``approved`` entry. What is left unfilled is reported by language, with the
  count, so the remaining work is visible instead of silently "complete".
* Never drop catalog history: the obsolete entries are compared before and after
  a write, and the tool refuses to keep a result that lost any.

``--check`` (the default) reports and changes nothing. ``--write`` writes the
catalogs and recompiles every ``messages.mo``. Both are idempotent: a second
``--write`` reports nothing restored and nothing pruned.

Usage::

    python3 scripts/catalog_maintenance.py            # report only
    python3 scripts/catalog_maintenance.py --write    # restore, prune, compile
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._extract_obsolete_po import parse_obsolete  # noqa: E402

#: Offered languages plus Croatian (see settings/languages.py and
#: tests/integration/test_translation_coverage.py).
LANGS = ("en", "ar", "he", "fa", "hr")

#: Identifiers a translation call handed to the extractor as if they were text,
#: which no literal in the sources produces (NARR-02):
#:
#: * ``msgid`` / ``msgid_plural`` - ``core/analytics/narrative.py`` passed its
#:   record's lookups to ``ngettext``, so every catalog gained the pair;
#: * ``title`` - ``core/reporting/render.py`` passed ``a["title"]`` to
#:   ``gettext``, and the stored title's own text is what the sync tool writes.
#:
#: A name belongs here only while the extraction cannot produce it as a literal:
#: ``tests/unit/test_catalog_maintenance.py`` asserts that for each name, so the
#: vocabulary cannot be used to delete a message a page actually shows.
ARTIFACT_IDS = ("msgid", "msgid_plural", "title")

#: ``%(name)s``-style fields, the only interpolation this project's catalogs use.
_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def catalog_path(lang: str, root: Path = ROOT) -> Path:
    """The catalog file for ``lang``; ``root`` makes the tool testable."""
    return root / "translations" / lang / "LC_MESSAGES" / "messages.po"


def _forms(message) -> List[str]:
    string = message.string
    if string is None:
        return []
    return list(string) if isinstance(string, (list, tuple)) else [string]


def _placeholders(*texts: str) -> set:
    return {name for text in texts for name in _PLACEHOLDER.findall(text or "")}


def _obsolete_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.startswith("#~ msgid"))


def _is_artifact(message) -> bool:
    """True for an entry an extractor made out of code identifiers.

    Both halves must look like identifiers: a real sentence such as
    ``"%(n)s msgid rows"`` is a translatable message that happens to quote an
    identifier, and is left alone. Babel 2.x hands a plural entry over as an
    ``id`` tuple, so the halves are read through the two accessors.
    """
    singular, plural = _singular_of(message), _plural_of(message)
    if not singular:
        return False
    if plural:
        return singular in ARTIFACT_IDS and plural in ARTIFACT_IDS
    return singular in ARTIFACT_IDS and not _PLACEHOLDER.search(singular)


def _plural_of(message) -> str:
    """The ``msgid_plural`` of a plural entry, or ""."""
    if isinstance(message.id, tuple):        # older Babel: id carries the pair
        return message.id[1] if len(message.id) > 1 else ""
    return getattr(message, "id_plural", "") or ""


def _singular_of(message) -> str:
    if isinstance(message.id, tuple):
        return message.id[0] if message.id else ""
    return message.id or ""


def inspect(lang: str, root: Path = ROOT) -> Dict[str, object]:
    """Read one catalog and classify every entry."""
    path = catalog_path(lang, root)
    if not path.exists():
        raise SystemExit(f"{lang}: no catalog at {path}")
    text = path.read_text(encoding="utf-8")
    with path.open("rb") as handle:
        catalog = read_po(handle, locale=lang)
    if catalog.num_plurals is not None and catalog.num_plurals < 1:
        raise SystemExit(f"{lang}: malformed Plural-Forms header "
                         f"(nplurals={catalog.num_plurals})")

    obsolete = parse_obsolete(text)
    report: Dict[str, object] = {
        "path": str(path), "approved": 0, "empty": [], "fuzzy": [],
        "artifacts": [], "restorable": [], "unreviewed": [],
        "obsolete": len(obsolete), "live": 0,
    }
    for message in catalog:
        if not _singular_of(message):
            continue  # the header entry
        report["live"] += 1
        if _is_artifact(message):
            report["artifacts"].append(_singular_of(message))
            continue
        forms = _forms(message)
        if not forms or any(form == "" for form in forms):
            report["empty"].append(_singular_of(message))
            state = "empty"
        elif message.fuzzy:
            report["fuzzy"].append(_singular_of(message))
            state = "fuzzy"
        else:
            report["approved"] += 1
            continue
        previous = obsolete.get(_singular_of(message))
        if previous and _placeholders(previous) == _placeholders(*forms, _singular_of(message),
                                                                 _plural_of(message)):
            report["restorable"].append(_singular_of(message))
        elif state in ("empty", "fuzzy"):
            report["unreviewed"].append(_singular_of(message))
    return report


def _write_catalog(lang: str, *, restore: Iterable[str],
                   root: Path = ROOT) -> Tuple[int, int]:
    """Restore ``restore`` from the obsolete block and prune artifacts."""
    path = catalog_path(lang, root)
    text = path.read_text(encoding="utf-8")
    obsolete = parse_obsolete(text)
    before = _obsolete_count(path)
    wanted = set(restore)
    restored = pruned = 0

    with path.open("rb") as handle:
        catalog = read_po(handle, locale=lang)
    for message in list(catalog):
        singular = _singular_of(message)
        if not singular:
            continue
        if _is_artifact(message):
            del catalog[message.id]
            pruned += 1
            continue
        if singular not in wanted:
            continue
        message.string = obsolete[singular]
        message.flags.discard("fuzzy")
        restored += 1

    with path.open("wb") as handle:
        write_po(handle, catalog, width=88)
    if _obsolete_count(path) != before:
        raise SystemExit(f"{lang}: refusing to keep the result - obsolete entries went "
                         f"from {before} to {_obsolete_count(path)}")
    return restored, pruned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true",
                        help="write the restorations and prune artifacts (default: report only)")
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    args = parser.parse_args()

    reports = {lang: inspect(lang) for lang in LANGS}
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2, default=list))
    else:
        for lang, report in reports.items():
            note = " (source language: an empty msgstr is the msgid)" if lang == "en" else ""
            print(f"{lang}: live={report['live']} approved={report['approved']} "
                  f"empty={len(report['empty'])} fuzzy={len(report['fuzzy'])} "
                  f"artifacts={len(report['artifacts'])} obsolete={report['obsolete']} "
                  f"restorable={len(report['restorable'])} "
                  f"needs_human_translation={0 if lang == 'en' else len(report['unreviewed'])}"
                  f"{note}")
            if lang != "en":
                for name in report["unreviewed"][:3]:
                    print(f"    needs work: {name[:70]!r}")

    if not args.write:
        print("report only: nothing written (use --write to apply)")
        return

    total_r = total_p = 0
    for lang, report in reports.items():
        restored, pruned = _write_catalog(lang, restore=report["restorable"])
        total_r += restored
        total_p += pruned
        if restored or pruned:
            print(f"{lang}: restored {restored} from obsolete, pruned {pruned} artifact(s)")
    if total_r == 0 and total_p == 0:
        print("nothing to write: every catalog is already at its safe state")
        return
    subprocess.run([sys.executable, "-m", "babel.messages.frontend",
                    "compile", "-d", "translations"], check=True, cwd=ROOT)
    print("compiled .mo")


if __name__ == "__main__":
    main()
