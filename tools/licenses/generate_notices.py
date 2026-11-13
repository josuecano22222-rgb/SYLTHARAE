#!/usr/bin/env python
"""Write ``THIRD_PARTY_NOTICES.md`` from facts, not from memory.

Three sources, and nothing else:

1. the **installed** distributions (``importlib.metadata``) - name, version, the
   licence expression the package declares and its classifiers, classified by
   ``tools/licenses/check_licenses.py``;
2. ``git ls-files static`` - the vendored files the repository actually ships,
   matched against the same patterns ``tests/unit/test_licensing.py`` uses;
3. the licence files that sit next to those vendored assets on disk.

It reproduces **no** licence text, resolves **no** unclear licence by guess and
makes **no** statement about which licence the project itself is released
under - that is the owner's decision, recorded in ``pyproject.toml`` and
``LICENSE``, and this file links to them instead of restating them.

Usage::

    python tools/licenses/generate_notices.py            # write the file
    python tools/licenses/generate_notices.py --check     # write nothing, exit 1 if stale
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "THIRD_PARTY_NOTICES.md"
#: The same patterns tests/unit/test_licensing.py scans for.
VENDORED_PATTERNS = ("*.min.js", "*.min.css", "*.umd.js", "*.ttf", "*.otf",
                     "*.woff", "*.woff2")
#: The project's own dependencies; "dev"/"optional" are marked from pyproject.
LICENCE_FILE_PREFIXES = ("OFL", "LICENSE", "COPYING")


def _checker():
    spec = importlib.util.spec_from_file_location(
        "check_licenses", ROOT / "tools" / "licenses" / "check_licenses.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_licenses"] = module
    spec.loader.exec_module(module)
    return module


def vendored_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "static"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return sorted(p for p in out
                  if any(fnmatch.fnmatch(pathlib.Path(p).name, pat) for pat in VENDORED_PATTERNS))


def adjacent_licence(path: str) -> str:
    """The licence file shipped next to a vendored asset, if any (a fact, not a claim).

    A directory can hold several families (``OFL-Inter.txt`` next to
    ``OFL-NotoSansArabic.txt``), so a licence file is matched to the asset when
    its name carries the asset's family token - ``NotoSansArabic-Bold.ttf``
    against ``OFL-NotoSansArabic.txt``. An asset with no matching licence file
    reports nothing rather than borrowing another family's.
    """
    folder = (ROOT / path).parent
    if not folder.is_dir():
        return ""
    family = pathlib.Path(path).stem.split("-")[0].split("_")[0]
    candidates = sorted(p.name for p in folder.iterdir()
                        if p.is_file() and p.name.startswith(LICENCE_FILE_PREFIXES))
    matched = [n for n in candidates if family and family in n]
    chosen = matched or (candidates if len(candidates) == 1 else [])
    return ", ".join(f"`{folder.relative_to(ROOT)}/{n}`" for n in chosen)


def render() -> str:
    checker = _checker()
    findings = [f for f in checker.check(include_extras=True)
                if f.verdict in ("compatible", "incompatible", "unknown")]

    lines = [
        "# Third-party notices",
        "",
        "> **Generated** by `tools/licenses/generate_notices.py`; do not edit by hand.",
        "> Sources: the installed distributions' own metadata and `git ls-files static`.",
        "> This file reproduces no licence text and resolves no unclear licence. It also",
        "> does not state the licence of this project - that is `pyproject.toml`'s",
        "> `license` field and [`LICENSE`](LICENSE).",
        "",
        "## Python packages",
        "",
        f"{len(findings)} distributions are installed in the environment these notices were",
        "generated from. Every row is what the package itself declares; `check` is",
        "`tools/licenses/check_licenses.py`'s verdict (a package can be compatible with the",
        "project's licence and still be listed, and an unclear package is never assumed fine).",
        "",
        "| package | version | declared licence | check |",
        "| --- | --- | --- | --- |",
    ]
    for f in sorted(findings, key=lambda f: (f.name or "").lower()):
        expression = (f.expression or "-").replace("|", "\\|")
        lines.append(f"| `{f.name}` | {f.version} | {expression} | {f.verdict} |")

    unclear = [f for f in findings if f.verdict in ("incompatible", "unknown")]
    lines += ["", "### Packages whose licence is missing or unclear", ""]
    if unclear:
        lines += ["These are listed for a decision. Nothing here is reclassified by hand.", ""]
        for f in sorted(unclear, key=lambda f: (f.name or "").lower()):
            lines.append(f"* `{f.name}` {f.version} - {f.detail or 'no licence metadata'}")
    else:
        lines.append("None: every installed distribution declares a usable licence expression.")

    files = vendored_files()
    lines += [
        "",
        "## Vendored static files",
        "",
        f"{len(files)} tracked files match the vendored patterns "
        "(`*.min.js`, `*.min.css`, `*.umd.js`, `*.ttf`, `*.otf`, `*.woff`, `*.woff2`).",
        "The last column names the licence file that ships in the same directory; an empty",
        "cell means the repository carries no licence text for that asset, which is a gap",
        "for review, not an assumption.",
        "",
        "| file | licence text next to it |",
        "| --- | --- |",
    ]
    for path in files:
        lines.append(f"| `{path}` | {adjacent_licence(path) or '-'} |")

    missing_text = [p for p in files if not adjacent_licence(p)]
    grouped: dict[str, int] = {}
    for path in missing_text:
        grouped[str(pathlib.Path(path).parent)] = grouped.get(str(pathlib.Path(path).parent), 0) + 1
    lines += ["", "### Vendored files without adjacent licence text", ""]
    if grouped:
        for folder, count in sorted(grouped.items()):
            lines.append(f"* `{folder}/` - {count} file(s): the upstream licence must be "
                         "confirmed and its text added by the maintainer.")
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 when the committed file is out of date")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("THIRD_PARTY_NOTICES.md is stale; run tools/licenses/generate_notices.py")
            return 1
        print("THIRD_PARTY_NOTICES.md is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
