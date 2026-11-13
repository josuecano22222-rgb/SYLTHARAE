#!/usr/bin/env python3
"""Inventory every commit hash the reports cite, and say whether it resolves.

A report that says "measured at this commit" is only as good as the object it
names. History in this repository has been re-created at least twice (see
``audit_report/r20/REPORT.md``), which leaves citations pointing at objects a
fresh clone cannot fetch. This tool does not rewrite those reports — it makes
the gap countable, so a reader knows which claims can still be checked.

Usage::

    python3 tools/audit/check_cited_commits.py                  # table on stdout
    python3 tools/audit/check_cited_commits.py --csv OUT.csv    # same, machine-readable
    python3 tools/audit/check_cited_commits.py --fail-missing   # exit 1 if any live
                                                                # citation is unreachable

Exit status is 0 unless ``--fail-missing`` is passed and a file listed in
``LIVE_SCOPES`` cites an object that does not resolve. Older rounds are frozen
records; they are reported, never enforced.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator, List, NamedTuple

ROOT = Path(__file__).resolve().parents[2]

#: Reports that are still maintained: a broken citation here is a defect.
LIVE_SCOPES = ("audit_report/r21_i18n", "docs/plans", "docs/reference")

SCAN_ROOTS = ("audit_report", "docs", "tools", "tests")
#: The inventory lists every cited hash, so scanning it would cite itself.
SKIP = {"audit_report/r21_i18n/cited_commits.csv"}

#: Hashes that a report cites *because they are unreachable* — the record of the
#: defect. Naming them is the point, so they must not fail the check.
DOCUMENTED_MISSING = {
    "audit_report/r21_i18n/REPORT.md": {"3703c90"},
}
SUFFIXES = {".md", ".csv", ".txt", ".json", ".py"}
#: A commit id needs at least one letter — a bare digit run is a size or a count.
HASH = re.compile(r"(?<![0-9a-zA-Z])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9a-zA-Z])")
ALL_ZERO = re.compile(r"^0+$")


class Citation(NamedTuple):
    commit: str
    path: str
    count: int
    reachable: bool
    kind: str


def _resolve(commit: str) -> str:
    """Return the object type, or '' when the object is not in this repository."""
    done = subprocess.run(
        ["git", "cat-file", "-t", commit],
        cwd=ROOT, capture_output=True, text=True,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _scopes(skip: Path | None = None) -> Iterator[Path]:
    """Every report/source file to scan, minus the inventory being written."""
    for root in SCAN_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.relative_to(ROOT).as_posix() in SKIP:
                continue
            if skip is not None and path.resolve() == skip:
                continue
            if path.is_file() and path.suffix in SUFFIXES:
                yield path


def _is_citation_table(path: Path) -> bool:
    """A CSV whose first column is ``id`` lists test identifiers, not commits.

    Parameterised test ids contain long hex runs (``…accepts_well_formed_digests
    [aaaa…]``); counting those as citations would bury the real ones.
    """
    if path.suffix != ".csv":
        return False
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            return handle.readline().strip().lower().startswith("id,")
    except OSError:
        return False


def collect(skip: Path | None = None) -> List[Citation]:
    resolved = {}
    rows: List[Citation] = []
    for path in _scopes(skip):
        rel = path.relative_to(ROOT).as_posix()
        try:
            if _is_citation_table(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        counts = {}
        for match in HASH.findall(text):
            if ALL_ZERO.match(match):
                continue  # git's null oid, not a citation
            if len(match) not in (7, 40):
                continue  # 8/12/64 hex runs are hashes of content, not commits
            counts[match] = counts.get(match, 0) + 1
        for commit, count in sorted(counts.items()):
            if commit not in resolved:
                resolved[commit] = _resolve(commit)
            kind = resolved[commit]
            rows.append(Citation(commit, rel, count,
                                 bool(kind), kind or "missing"))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="write the inventory here as well")
    parser.add_argument("--fail-missing", action="store_true",
                        help="exit 1 when a live-scope file cites a missing object")
    parser.add_argument("--scope", help="only report citations from paths starting here")
    args = parser.parse_args(argv)

    target = Path(args.csv).resolve() if args.csv else None
    rows = collect(target)
    if args.scope:
        rows = [r for r in rows if r.path.startswith(args.scope)]

    if args.csv:
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["commit", "path", "occurrences", "resolves", "object_type"])
            for r in rows:
                writer.writerow([r.commit, r.path, r.count,
                                 "yes" if r.reachable else "no", r.kind])
        print(f"wrote {out} ({len(rows)} citations)")

    print(f"{'commit':10} {'resolves':8} {'uses':4} path")
    for r in rows:
        print(f"{r.commit:10} {'yes' if r.reachable else 'NO':8} {r.count:<4} {r.path}")

    documented = {(path, commit)
                  for path, commits in DOCUMENTED_MISSING.items()
                  for commit in commits}
    missing = [r for r in rows if not r.reachable]
    live = [r for r in missing
            if any(r.path.startswith(scope) for scope in LIVE_SCOPES)
            and (r.path, r.commit) not in documented]
    print(f"\n{len(rows)} citations, {len(missing)} unreachable "
          f"({len({r.commit for r in missing})} distinct, "
          f"{len({r.path for r in missing})} files), "
          f"{len(live)} of them in maintained reports", file=sys.stderr)
    if live:
        for r in live:
            print(f"  UNREACHABLE {r.commit} cited by {r.path}", file=sys.stderr)
        if args.fail_missing:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
