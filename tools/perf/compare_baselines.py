#!/usr/bin/env python3
"""Compare two search benchmark JSONL runs (informational CI helper)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]


def _load_rows(path: Path) -> List[Dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return [r for r in rows if r.get("query_id") and r.get("p95_ms") is not None]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--warn-pct", type=float, default=15.0)
    parser.add_argument("--fail-pct", type=float, default=30.0)
    args = parser.parse_args()

    base = {r["query_id"]: r for r in _load_rows(args.baseline)}
    cand = {r["query_id"]: r for r in _load_rows(args.candidate)}
    exit_code = 0
    for qid, brow in base.items():
        crow = cand.get(qid)
        if not crow:
            continue
        b = float(brow.get("p95_ms") or 0)
        c = float(crow.get("p95_ms") or 0)
        if b <= 0:
            continue
        delta_pct = round(100.0 * (c - b) / b, 2)
        status = "ok"
        if delta_pct >= args.fail_pct:
            status = "fail"
            exit_code = 1
        elif delta_pct >= args.warn_pct:
            status = "warn"
        print(f"{qid}: baseline_p95={b}ms candidate_p95={c}ms delta={delta_pct}% [{status}]")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
