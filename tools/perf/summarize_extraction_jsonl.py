#!/usr/bin/env python3
"""Print GREEN/YELLOW/RED summary from extraction_matrix_testdoc JSONL."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    tiers = Counter()
    by_ext: Counter = Counter()
    durations = []
    for line in args.jsonl.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("type") != "extraction":
            continue
        tiers[row.get("tier", "RED")] += 1
        by_ext[row.get("extension", "?")] += 1
        durations.append(float(row.get("duration_ms") or 0))
    durations.sort()
    p95 = durations[int(0.95 * (len(durations) - 1))] if durations else 0
    print("tiers", dict(tiers))
    print("extensions", len(by_ext))
    print("p95_duration_ms", round(p95, 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
