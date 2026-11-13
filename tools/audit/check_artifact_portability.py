#!/usr/bin/env python
"""Fail when a committed artifact names this machine.

Numbers in audit_report/ are supposed to be reproducible anywhere. A path like
``/home/alice/SYLTHARAE-Pro/...`` or a database URI with ``127.0.0.1:5433``
turns a measurement into a local anecdote - and leaks the workstation's user
name into a public repository.

The check is deliberately narrow: it looks for absolute home directories, temp
directories, Windows profile paths and loopback hosts with a port. A bare
``localhost`` in prose is not a finding; a connection string is.

    python tools/audit/check_artifact_portability.py            # report
    python tools/audit/check_artifact_portability.py --strict   # exit 1
"""
from __future__ import annotations

import argparse
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCAN_SUFFIXES = {".md", ".csv", ".json", ".txt", ".log"}

PATTERNS = {
    "home-path": re.compile(r"/home/[A-Za-z0-9._-]+"),
    "users-path": re.compile(r"/Users/[A-Za-z0-9._-]+"),
    "temp-path": re.compile(r"/tmp/[A-Za-z0-9._-]+"),
    "windows-profile": re.compile(r"C:\\\\Users\\\\[A-Za-z0-9._-]+"),
    "loopback-with-port": re.compile(r"(?:127\.0\.0\.1|localhost):\d{2,5}"),
}

#: Raw logs are evidence: rewriting a path inside a captured run would make the
#: file say something that was never printed. Everything captured before round
#: 20 is therefore listed once in the baseline below and left alone; anything
#: new must be portable from the moment it is written.
BASELINE_FILE = pathlib.Path("audit_report/r20/portability_baseline.txt")


def load_baseline(root: pathlib.Path) -> set[str]:
    path = root / BASELINE_FILE
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def scan(root: pathlib.Path, baseline: set[str] | None = None):
    baseline = baseline or set()
    findings = []
    for path in sorted((root / "audit_report").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        rel = str(path.relative_to(root))
        if rel in baseline:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if len(line) > 400:
                line = line[:400]
            for name, pattern in PATTERNS.items():
                match = pattern.search(line)
                if match:
                    findings.append({"file": str(rel), "line": line_no, "kind": name,
                                     "match": match.group(0)})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    parser.add_argument("--write-baseline", action="store_true",
                        help="record the current offenders as accepted history")
    options = parser.parse_args()
    if options.write_baseline:
        raw = scan(ROOT)
        path = ROOT / BASELINE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        files = sorted({item["file"] for item in raw})
        path.write_text(
            "# Artifacts captured before round 20 that contain machine-specific\n"
            "# fragments. They are raw logs: rewriting them would falsify evidence.\n"
            "# Every file NOT listed here must be portable.\n"
            + "\n".join(files) + "\n", encoding="utf-8")
        print(f"baseline written: {len(files)} file(s) -> {path}")
        return 0
    findings = scan(ROOT, load_baseline(ROOT))
    print(f"{len(findings)} machine-specific fragment(s) outside the baseline")
    by_file = {}
    for item in findings:
        by_file.setdefault(item["file"], []).append(item)
    for file, items in sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:25]:
        kinds = sorted({item["kind"] for item in items})
        print(f"  {file}: {len(items)} ({', '.join(kinds)}) e.g. {items[0]['match']}")
    return 1 if (options.strict and findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
