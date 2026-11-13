#!/usr/bin/env python3
"""Inventory every place a category / term / keyword label reaches a reader.

This is the surface a multilingual label layer has to cover, so it is generated
rather than hand-listed: a missed join is a label that stays English.

What it counts, and why each group matters
==========================================

``sql``      A Python string literal that selects ``w.word`` (the canonical
             label: a category's name *is* a ``words`` row). These are the reads
             that would need a translation join or a post-processing step.
``routes``   A Flask route handler whose function body is in the same module as
             one of those reads. Used to size the API surface, not to promise
             that every route returns a name.
``templates``Jinja files that render a category/word label.
``js``       Page scripts that insert a category/word label into the DOM. A
             translation applied on the server is invisible if the script
             re-derives the text from a category id, so these are listed too.

No network, no database, no imports of the application: run it with the system
python. Output is CSV on stdout so the numbers in a plan can be re-measured:

    python3 tools/audit/scan_term_label_surface.py [--csv PATH]

Owner: i18n plan (docs/plans/CATEGORY_TERM_I18N.md).
"""
from __future__ import annotations

import argparse
import ast
import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

SCAN_PY = ("Api", "core", "services", "database", "settings", "apps")
SCAN_TEMPLATES = ("templates", "static/js")

#: ``w.word`` used as a display label (the alias is what callers read).
WORD_LABEL = re.compile(r"\bw\.word\b")
#: A Flask route decorator: the next ``def`` in the file is its handler.
ROUTE = re.compile(r"@\w+\.route\(\s*[\"']([^\"']+)[\"']")
#: Template lines that print a category/word label.
TEMPLATE_LABEL = re.compile(
    r"categor\w*\.name|cat\.name|category_name|word\.word|\bword\b.*name|node\.name")
#: Script lines that write a category/word label into the page.
JS_LABEL = re.compile(r"\.name\b.*(categor|word|term|node)|(categor|word|term|node)\w*\.name\b")


def _iter_files(roots, suffix):
    for root in roots:
        base = ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob(f"*{suffix}")):
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _string_constants(tree):
    """Every string literal in the module, with its line number."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def scan_python():
    """Yield (kind, file, line, detail) for SQL label reads and route handlers."""
    for path in _iter_files(SCAN_PY, ".py"):
        if path.name.startswith("test_") or "/migrations/" in str(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        lines = text.splitlines()

        label_lines = set()
        for lineno, value in _string_constants(tree):
            if WORD_LABEL.search(value):
                alias = "name" if re.search(r"w\.word\s+(?:AS\s+)?name\b", value) else "word"
                label_lines.add(lineno)
                yield ("sql", path.relative_to(ROOT).as_posix(), lineno,
                       f"selects w.word as {alias}")
        # A ``# comment`` mentioning w.word is not a read.
        text_lines = {n for n in label_lines
                      if not lines[n - 1].lstrip().startswith("#")}
        if not text_lines:
            continue
        routes = [(m.group(1), text[:m.start()].count("\n") + 1)
                  for m in ROUTE.finditer(text)]
        yield ("module", path.relative_to(ROOT).as_posix(),
               min(text_lines), f"{len(text_lines)} label literal(s)")
        for rule, lineno in routes:
            yield ("routes", path.relative_to(ROOT).as_posix(), lineno, rule)


def scan_text_files(roots, suffix, pattern, kind):
    for path in _iter_files(roots, suffix):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hits = [(n, line.strip()) for n, line in enumerate(lines, 1)
                if pattern.search(line) and not line.lstrip().startswith(("//", "<!--"))]
        if hits:
            yield (kind, path.relative_to(ROOT).as_posix(), hits[0][0],
                   f"{len(hits)} line(s); first: {hits[0][1][:90]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", help="write the rows here as well as printing a summary")
    args = parser.parse_args(argv)

    rows = list(scan_python())
    rows += scan_text_files(SCAN_TEMPLATES, ".html", TEMPLATE_LABEL, "templates")
    rows += scan_text_files(("static/js",), ".js", JS_LABEL, "js")

    if args.csv:
        out = pathlib.Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["kind", "file", "line", "detail"])
            writer.writerows(rows)

    counts = {}
    for kind, *_rest in rows:
        counts[kind] = counts.get(kind, 0) + 1
    for kind in ("sql", "module", "routes", "templates", "js"):
        print(f"{kind:10} {counts.get(kind, 0)}")
    print(f"{'total':10} {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
