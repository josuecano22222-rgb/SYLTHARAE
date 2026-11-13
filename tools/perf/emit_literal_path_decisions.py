#!/usr/bin/env python3
"""Emit PG_LITERAL investigation decision records from baseline benchmark JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.decision_record import build_decision_record, dominant_stage  # noqa: E402

LITERAL_QUERY_IDS = {"Q07", "Q08", "Q10"}

NEXT_TEST = "explain_literal_candidate_sql"

CLASS_ALIASES = {
    "or_union": "boolean_or",
    "phrase": "phrase",
    "regex": "regex",
}


def _literal_hypothesis(row: Dict[str, Any], stage: str) -> str:
    qid = str(row.get("query_id") or "")
    if qid == "Q07":
        return "boolean OR literal union — candidate SQL cost (not yet EXPLAIN-decomposed)"
    if qid == "Q08":
        return "quoted phrase literal predicates across metadata + contents_raw"
    if qid == "Q10":
        return "regex-like plain query — slow path / content or token matching (planner: PG_INVERTED label)"
    return f"{stage} on literal or regex-adjacent path"


def build_literal_decision(row: Dict[str, Any], *, benchmark: str) -> Dict[str, Any]:
    rec = build_decision_record(row, benchmark=benchmark)
    stage, share = dominant_stage(row)
    label = str(row.get("label") or "")
    rec["query_class"] = CLASS_ALIASES.get(label, label)
    rec["investigation_track"] = "PG_LITERAL"
    rec["dominant_stage"] = stage
    rec["dominant_share_pct"] = share
    rec["hypothesis"] = _literal_hypothesis(row, stage)
    rec["recommended_next_test"] = NEXT_TEST
    rec["confidence"] = "measured"
    rec["phase"] = row.get("phase")
    rec["status"] = "diagnosis_pending"
    return rec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "jsonl",
        type=Path,
        default=ROOT / "docs" / "perf" / "runs" / "search_full.jsonl",
        nargs="?",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "perf" / "runs" / "pg_literal_path_decisions.jsonl",
    )
    parser.add_argument(
        "--phase",
        default="warm",
        help="Benchmark phase to canonicalize (default warm)",
    )
    args = parser.parse_args()

    rows: List[Dict[str, Any]] = [
        json.loads(line)
        for line in args.jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decisions: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("query_id") not in LITERAL_QUERY_IDS:
            continue
        if args.phase and row.get("phase") != args.phase:
            continue
        decisions.append(build_literal_decision(row, benchmark=args.jsonl.stem))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for rec in decisions:
            handle.write(json.dumps(rec, sort_keys=True))
            handle.write("\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
