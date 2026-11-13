#!/usr/bin/env python3
"""ILIKE / LIKE / LOWER audit for search-related paths."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"\b(ILIKE|LIKE|LOWER\s*\(|UPPER\s*\()", re.IGNORECASE)


def classify(path: Path, line: str) -> str:
    lower = line.lower()
    if "words" in lower and "ilike" in lower:
        return "expensive_word_scan"
    if "ilike '%" in lower or "ilike \"%\"%" in lower:
        return "leading_wildcard"
    if "file_name" in lower:
        return "metadata_filename"
    if "paths" in lower or "contents_raw" in lower:
        return "content_or_path"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "perf" / "ILIKE_AUDIT.md")
    args = parser.parse_args()

    rows: List[Dict[str, str]] = []
    skip = {"node_modules", ".git", "__pycache__", ".venv"}
    for path in ROOT.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        try:
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if PATTERN.search(line):
                    rows.append({
                        "file": str(path.relative_to(ROOT)),
                        "line": str(i),
                        "class": classify(path, line),
                        "snippet": line.strip()[:140],
                    })
        except OSError:
            continue

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1

    out = [
        "# ILIKE / LIKE audit",
        "",
        "| Class | Count |",
        "|-------|------:|",
    ]
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.append(f"| {k} | {v} |")
    out.append("")
    out.append("| File | Line | Class | Snippet |")
    out.append("|------|-----:|-------|---------|")
    for r in rows[:400]:
        out.append(f"| `{r['file']}` | {r['line']} | {r['class']} | `{r['snippet']}` |")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
