#!/usr/bin/env python3
"""Repository performance anti-pattern scan (static heuristics)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]

PATTERNS = [
    (r"for .+ in .+:\s*\n\s*(.+execute\(|.+fetchall\(|load_text_content\(|open\(|Path\(.+\)\.read)", "N+1 / loop I/O or query", "high"),
    (r"OFFSET\s+%s", "SQL OFFSET pagination", "medium"),
    (r"ILIKE\s+'%", "Leading-wildcard ILIKE", "high"),
    (r"SELECT\s+\*", "SELECT *", "low"),
    (r"ThreadPoolExecutor\([^)]*max_workers\s*=\s*\d{2,}", "Large worker pool", "medium"),
    (r"while True:", "Unbounded loop", "low"),
]


def scan_file(path: Path) -> List[Dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings: List[Dict[str, str]] = []
    lines = text.splitlines()
    for pattern, problem, severity in PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            line_no = text[: match.start()].count("\n") + 1
            findings.append({
                "file": str(path.relative_to(ROOT)),
                "line": str(line_no),
                "problem": problem,
                "severity": severity,
                "snippet": lines[line_no - 1].strip()[:120] if line_no <= len(lines) else "",
                "measurement_required": "profile hot path under benchmark harness",
            })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "perf" / "ANTIPATTERN_AUDIT.md")
    args = parser.parse_args()

    skip = {"node_modules", ".git", "__pycache__", ".venv", "venv"}
    all_findings: List[Dict[str, str]] = []
    for path in ROOT.rglob("*"):
        if path.suffix not in {".py", ".js"}:
            continue
        if any(part in skip for part in path.parts):
            continue
        all_findings.extend(scan_file(path))

    by_sev: Dict[str, int] = {}
    for f in all_findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    lines = [
        "# Performance anti-pattern audit (automated)",
        "",
        f"Total findings: **{len(all_findings)}**",
        "",
        "| Severity | Count |",
        "|----------|------:|",
    ]
    for sev in ("high", "medium", "low"):
        lines.append(f"| {sev} | {by_sev.get(sev, 0)} |")
    lines.append("")
    lines.append("| File | Line | Severity | Problem | Snippet |")
    lines.append("|------|-----:|----------|---------|---------|")
    for f in sorted(all_findings, key=lambda x: (x["severity"], x["file"]))[:500]:
        lines.append(
            f"| `{f['file']}` | {f['line']} | {f['severity']} | {f['problem']} | `{f['snippet']}` |"
        )
    if len(all_findings) > 500:
        lines.append("")
        lines.append(f"_Truncated; {len(all_findings) - 500} additional rows omitted._")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
