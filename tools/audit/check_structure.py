#!/usr/bin/env python
"""Structural checks: dead code, registry reality, i18n coverage, RTL.

Three questions the failing-test list cannot answer:

* Is there code nobody reaches? A module with no importer that is not an entry
  point is either dead or reachable only through a string (a job type, a
  plugin name) - both worth naming.
* Does the interface registry describe the product that exists? Every declared
  interface must resolve to a route, and every page route should be declared.
* Is every string the UI asks for present in every catalogue, and do the RTL
  locales actually get a right-to-left document?

Writes three CSVs under ``audit_report/findings/``.
"""

from __future__ import annotations

import argparse
import ast
import collections
import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"

SOURCE_DIRS = ("Api", "apps", "core", "database", "services", "pipeline",
               "settings", "classification", "concurrency", "reader_file")
ENTRY_HINTS = ("run_web", "run_cli", "run_import", "install", "wsgi",
               "__main__", "conftest", "verify_readiness")


def python_files(dirs=SOURCE_DIRS):
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def dead_code():
    """Modules no other module imports and no entry point starts."""
    all_files = list(python_files())
    imported = set()
    for path in all_files + list((ROOT / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                imported.add(mod)
                for alias in node.names:
                    imported.add(f"{mod}.{alias.name}" if mod else alias.name)
    rows = []
    for path in all_files:
        mod = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        name = path.stem
        if any(h in name for h in ENTRY_HINTS):
            continue
        # Migrations are discovered by the runner, not imported by name.
        if "migrations" in path.parts:
            continue
        if mod.endswith("__init__"):
            continue
        # imported directly, as a package member, or by a parent package
        hit = (mod in imported or name in {i.split(".")[-1] for i in imported})
        # referenced as a string (job type, plugin, template) anywhere?
        if not hit:
            text_refs = 0
            for other in list(all_files) + list((ROOT / "tests").rglob("*.py")):
                if other == path:
                    continue
                try:
                    body = other.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if f'"{mod}"' in body or f"'{mod}'" in body:
                    text_refs += 1
            if text_refs:
                continue
            # A message catalogue names the module it extracted strings from.
            for po in (ROOT / "translations").rglob("messages.po"):
                if str(path.relative_to(ROOT)) in po.read_text(
                        encoding="utf-8", errors="replace"):
                    text_refs += 1
                    break
            if text_refs:
                continue
        if hit:
            continue
        rows.append((str(path.relative_to(ROOT)), path.stat().st_size,
                     "no importer"))
    with (OUT / "dead_code.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["module", "bytes", "reason", "status"])
        for r in rows:
            w.writerow(list(r) + ["UNTOUCHED"])
    return rows


def registry_reality(app):
    from core.interfaces import registry

    # A page is "managed" when the registry lists it as an interface route or
    # as one of that interface's aliases (the file detail, the add-redirect,
    # the print view...).
    declared = set()
    for entry in registry.REGISTRY:
        declared.add(entry.route)
        declared.update(getattr(entry, "aliases", ()) or ())
    page_rules = {}
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule.startswith(("/api/", "/static")):
            continue
        if "/api/" in rule.rule:
            continue
        # Authentication, setup, the favicon and add-redirects are not
        # product pages and are deliberately outside the navigation registry.
        if rule.endpoint.startswith(("auth.", "setup.")) or rule.endpoint in (
                "favicon", "keywords_add", "side_add", "source_add"):
            continue
        if ":" in rule.rule or "<" in rule.rule:
            continue
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        try:
            src = pathlib.Path(sys.modules[view.__module__].__file__).read_text(
                encoding="utf-8", errors="replace")
        except Exception:
            src = ""
        if "render_template" in src:
            page_rules[rule.endpoint] = rule.rule
    rows = []
    for endpoint, rule in sorted(page_rules.items()):
        if endpoint in declared:
            continue
        rows.append((rule, endpoint, "page route is not in the interface registry"))
    with (OUT / "registry_gaps.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rule", "endpoint", "finding", "status"])
        for r in rows:
            w.writerow(list(r) + ["UNTOUCHED"])
    return rows


def i18n_coverage():
    """msgids used by templates/JS but absent from a catalogue.

    Returns ``None`` - not an empty list - when the inventory this reads has not
    been built. An audit check that answers "0 gaps" because it cannot see its
    input is worse than no check: it is a green light for a release nobody
    measured. ``main`` reports that case as "not measured" and points at the
    generator; ``catalogue_translation_gap`` below covers the other axis (entries
    that exist and are empty or fuzzy) straight from the catalogues, so the
    release has a real number either way.
    """
    inventory = ROOT / "audit_report" / "inventory" / "i18n_msgids.csv"
    langs = ["en", "ar", "he", "fa", "hr"]
    rows = []
    measured = inventory.exists()
    if measured:
        with inventory.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("coverage") != "gap":
                    continue
                present = {l for l in langs
                           if l in (row.get("catalogs") or "").split(",")}
                missing = [l for l in langs if l not in present]
                rows.append((row["msgid"], row["first_uses"], ",".join(missing)))
    with (OUT / "i18n_gaps.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["msgid", "used_in", "missing_from", "status"])
        for r in rows:
            w.writerow(list(r) + ["UNTOUCHED"])
    return rows if measured else None


def catalogue_translation_gap():
    """Untranslated/fuzzy catalogue entries, measured from the ``.po`` files.

    The other axis: the msgid is *in* the catalogue and its ``msgstr`` is blank
    or fuzzy. Counted by ``tools/i18n/export_translation_gaps.py`` (the same
    measurement the translator worksheets come from), so this stays true on a
    clean checkout where the inventory has not been built.
    """
    try:
        from tools.i18n import export_translation_gaps as gaps
    except Exception:                                  # pragma: no cover - import path only
        return None
    counts = {}
    for language in gaps.LANGUAGES:
        try:
            counts[language] = len(gaps.collect(language))
        except SystemExit:                             # catalogue missing
            counts[language] = None
    return counts


def rtl_check():
    """The document direction must be *derived* from the language, not fixed.

    The previous version looked for ``dir="{{`` and so reported "no dir
    attribute" for a template that writes
    ``dir="{% if current_language in [...] %}rtl{% else %}ltr{% endif %}"`` - a
    correct attribute the regex could not see. A yes/no on "does the attribute
    exist" was also the weaker question: the property that matters is that the
    value depends on the language, because a hardcoded ``dir="ltr"`` would pass
    the old test and render every Arabic page left-to-right.
    """
    base = ROOT / "templates" / "base.html"
    text = base.read_text(encoding="utf-8", errors="replace") if base.exists() else ""
    match = re.search(r"""\bdir\s*=\s*(["'])(.*?)\1""", text, re.S)
    value = match.group(2) if match else ""
    templated = bool(re.search(r"\{%|\{\{", value))
    return {
        "base_template_has_dir_attribute": bool(match),
        "direction_is_derived_from_the_language": templated and "rtl" in value.lower(),
        "base_template_mentions_rtl": "rtl" in text.lower() or "is_rtl" in text,
    }


CHECKS = ("dead", "registry", "i18n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=CHECKS,
                        help="run one check only (default: all of them)")
    args = parser.parse_args(argv)
    wanted = args.only or list(CHECKS)
    OUT.mkdir(parents=True, exist_ok=True)

    if "dead" in wanted:
        dead = dead_code()
        print(f"dead-code candidates: {len(dead)} -> findings/dead_code.csv")
        for r in dead[:20]:
            print("  ", r[0], r[1])

    if "registry" in wanted:
        import os
        os.environ.setdefault("FLASK_ENV", "testing")
        from apps.web.app import app

        gaps = registry_reality(app)
        print(f"page routes missing from the registry: {len(gaps)}")
        for r in gaps[:20]:
            print("  ", r)

    if "i18n" in wanted:
        i18n = i18n_coverage()
        if i18n is None:
            print("msgids with an incomplete catalogue: not measured "
                  "(audit_report/inventory/i18n_msgids.csv is missing; "
                  "run python tools/audit/build_inventories.py)")
        else:
            by_lang = collections.Counter()
            for r in i18n:
                for lang in r[2].split(","):
                    by_lang[lang] += 1
            print(f"msgids with an incomplete catalogue: {len(i18n)} {dict(by_lang)}")
        counts = catalogue_translation_gap()
        if counts is None:
            print("untranslated/fuzzy catalogue entries: not measured")
        else:
            total = sum(v for v in counts.values() if v)
            detail = ", ".join(f"{k} {v}" for k, v in counts.items())
            print(f"untranslated/fuzzy catalogue entries: {total} ({detail}) "
                  "-> docs/TRANSLATION_HANDOFF.md")

    if not args.only:
        print("RTL:", rtl_check())
    return 0


if __name__ == "__main__":
    sys.exit(main())
