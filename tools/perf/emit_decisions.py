#!/usr/bin/env python3
"""Emit optimization decision records + capacity rows from a benchmark JSONL file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.decision_record import records_from_jsonl_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "perf" / "runs" / "search_decisions.jsonl",
    )
    parser.add_argument(
        "--capacity-md",
        type=Path,
        default=ROOT / "docs" / "perf" / "CAPACITY_REPORT.md",
        help="Append measured rows (does not remove template)",
    )
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    hw = next((r.get("hardware") for r in rows if r.get("type") == "meta"), None)
    corpus = None
    for r in rows:
        if r.get("dataset_files") is not None:
            corpus = str(r["dataset_files"])
            break

    decisions = records_from_jsonl_rows(rows, benchmark=args.jsonl.stem, hardware_profile=hw)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for rec in decisions:
            handle.write(json.dumps(rec, sort_keys=True))
            handle.write("\n")
    print(args.out)

    if decisions and args.capacity_md.exists():
        lines = ["", "## Measured runs (auto-appended)", ""]
        lines.append(
            "| Corpus | Query | Engine | P50 | P95 | P99 | Results | Dominant | Share% | Confidence |"
        )
        lines.append("|-------:|-------|--------|----:|----:|----:|--------:|----------|-------:|------------|")
        for d in decisions:
            lines.append(
                f"| {d.get('corpus')} | {d.get('query_class')} | {d.get('engine')} | "
                f"{d.get('p50_ms')} | {d.get('p95_ms')} | {d.get('p99_ms')} | "
                f"{d.get('result_count')} | {d.get('dominant_stage')} | "
                f"{d.get('dominant_share_pct')} | {d.get('confidence')} |"
            )
        with args.capacity_md.open("a", encoding="utf-8") as md:
            md.write("\n".join(lines) + "\n")
        print(f"appended {len(decisions)} rows to {args.capacity_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
