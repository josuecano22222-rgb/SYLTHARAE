"""Add missing report narrative/chrome msgids to Babel catalogs and compile .mo."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from babel.messages.catalog import Message
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[2]
TRANS = ROOT / "translations"
CATALOG = Path(__file__).with_name("report_profile_catalog.json")
LANGS = ("en", "ar", "he", "fa", "hr")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from core.reporting import REGISTRY

    chrome = [
        "Horizon Profile", "Place Profile", "Keyword Reach Profile",
        "Category Reach Profile",
        "Full Entry Report",
        "About the Full Entry Report",
        "Analyst categories",
        "Content characters",
        "Content truncated",
        "Entry %(n)s of %(total)s",
        "%(shown)s of %(total)s entries",
        "Full extracted text",
        "No extracted text is stored for this entry.",
        "Content shortened: the first %(shown)s of %(total)s characters are included.",
        "This listing was shortened at %(limit)s entries; more files matched.",
        "Print this report",
        "Use the browser print command.",
        "Analyst classifications",
        "(untitled)",
        "The Full Entry Report uses a dedicated printable template: one section per file, covering the full stored content of that entry.",
        "This report uses a dedicated printable template: one section per entry, with the full extracted text.",
        "Run an analytical report on the documents you may read, or the Full Entry Report for a printable dossier of each file's identity, classifications and extracted text. Findings appear as clear explanatory text; supporting tables and printable files follow.",
        "Full entry report (printable)",
        "Dedicated template: identity, analyst classifications, and the full extracted text of each selected file — ready to print.",
        "Printable dossier of each selected file, including its full extracted text",
        "Ingestion Batch Report",
        "Ingestion batch",
        "Ingestion batch report — printable dossier",
        "A complete account of one intake: the source and side that classified it, every outcome count, the file-type breakdown, and the categories, labels and keywords assigned to the files that entered in this batch.",
        "Outcome counts",
        "Files by type",
        "Files by type (processed / failed / skipped):",
        "Processing status",
        "Categories assigned in this batch",
        "Category labels assigned in this batch",
        "Keywords assigned in this batch",
        "Categories assigned:",
        "Category labels assigned:",
        "Keywords assigned:",
        "Signals detected",
        "Places mentioned",
        "Files in this batch",
        "Word hits",
        "%(n)s file(s)",
        "Listing %(listed)s of %(stored)s stored files; the listing is capped and the cap is stated.",
        "No per-type breakdown is stored for this batch.",
        "No automatic categories were assigned to files in this batch.",
        "No analyst category labels were assigned to files in this batch.",
        "No keywords were assigned to files in this batch.",
        "No detector signals were stored for this batch.",
        "No place mentions were stored for this batch.",
        "No stored file rows were identified for this batch.",
        "File binding: stored path identifiers",
        "File binding: recovered from the path watermark",
        "File binding: recovered from source, side and the job time window",
        "File binding: recovered from source, side, date and intake path",
        "File binding: recovered from the intake path under this source and side",
        "File binding: counts are available; individual files could not be bound",
    ]
    keys = list(dict.fromkeys(list(REGISTRY.translation_keys()) + chrome))
    plurals = list(REGISTRY.translation_plurals())
    data = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}

    pot_path = TRANS / "messages.pot"
    with pot_path.open("rb") as fh:
        pot = read_po(fh)
    added = 0
    for key in keys:
        if pot.get(key) is None:
            pot[key] = Message(key, string="", auto_comments=["report analytical narrative"])
            added += 1
    for p in plurals:
        entry = pot.get(p.singular)
        if entry is None or not isinstance(getattr(entry, "id", None), tuple) \
                or entry.id[1] != p.plural:
            if entry is not None and p.singular in pot:
                del pot[p.singular]
            pot[p.singular] = Message((p.singular, p.plural), string=("", ""),
                                      auto_comments=["report analytical plural"])
            added += 1
    with pot_path.open("wb") as fh:
        write_po(fh, pot, width=100)
    print(f"pot +{added}")

    missing_for_humans = []
    for lang in LANGS:
        path = TRANS / lang / "LC_MESSAGES" / "messages.po"
        with path.open("rb") as fh:
            catalog = read_po(fh, locale=lang)
        n = 0
        for key in keys:
            tr = "" if lang == "en" else data.get(key, {}).get(lang, "")
            entry = catalog.get(key)
            if entry is None:
                catalog[key] = Message(key, string=tr,
                                       auto_comments=["report analytical narrative"])
                n += 1
            elif lang != "en" and (not entry.string or entry.string == key) and tr:
                entry.string = tr
                entry.flags.discard("fuzzy")
                n += 1
            elif lang != "en" and (not entry.string or entry.string == key):
                missing_for_humans.append((lang, key))
        for p in plurals:
            entry = catalog.get(p.singular)
            forms = data.get(f"plural::{p.singular}", {}).get(lang)
            is_plural = entry is not None and isinstance(getattr(entry, "id", None), tuple)
            if not is_plural or (entry is not None and entry.id[1] != p.plural):
                if entry is not None and p.singular in catalog:
                    del catalog[p.singular]
                strings = _plural_tuple(catalog.num_plurals, forms, lang,
                                        p.singular, p.plural)
                catalog[p.singular] = Message(
                    (p.singular, p.plural), string=strings,
                    auto_comments=["report analytical plural"])
                n += 1
                if lang != "en" and not forms:
                    missing_for_humans.append((lang, p.singular + " / " + p.plural))
            elif lang != "en" and forms:
                entry.string = _plural_tuple(catalog.num_plurals, forms, lang,
                                             p.singular, p.plural)
                entry.flags.discard("fuzzy")
                n += 1
        with path.open("wb") as fh:
            write_po(fh, catalog, width=100)
        with path.with_suffix(".mo").open("wb") as fh:
            write_mo(fh, catalog)
        print(f"{lang} +{n}")

    if missing_for_humans:
        print(f"MISSING translations: {len(missing_for_humans)}")
        for lang, key in missing_for_humans[:25]:
            print(f"  [{lang}] {key[:100]}")
        return 1
    return 0


def _plural_tuple(n: int, forms, lang: str, singular: str, plural: str):
    """Return a tuple of length n for babel plural msgstrs."""
    if lang == "en" or not forms:
        return tuple("" for _ in range(n))
    singular_t, plural_t = forms[0], forms[1]
    if n == 2:
        return (singular_t, plural_t)
    if n == 3:
        return (singular_t, plural_t, plural_t)
    # Arabic (6)
    out = [plural_t] * n
    if n >= 2:
        out[1] = singular_t
    return tuple(out)


if __name__ == "__main__":
    raise SystemExit(main())
