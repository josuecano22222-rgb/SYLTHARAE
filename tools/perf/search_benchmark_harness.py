#!/usr/bin/env python3
"""
Structured search benchmark harness (Wave: measurement).

Emits JSONL records with stage breakdown from SearchTrace — not a single elapsed time.

  python tools/perf/search_benchmark_harness.py --suite tools/perf/query_suite.json \\
      --iterations 5 --cold --warm --out docs/perf/runs/latest.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime  # noqa: E402

bootstrap_runtime()

from tools.perf.benchmark_common import (  # noqa: E402
    dataset_stats_from_db,
    hardware_profile,
    summarize_latencies,
    trace_breakdown,
    utc_now_iso,
    write_jsonl,
)
from core.search.perf import SearchTrace  # noqa: E402
from tools.perf.decision_record import records_from_jsonl_rows  # noqa: E402
from tools.perf.bootstrap_runtime import db_connection_probe  # noqa: E402


def _run_list_once(
    *,
    query: str,
    per_page: int,
    offset: int,
    count_mode: str,
    case_sensitive: bool,
    whole_word: bool,
    cursor_after_score: Optional[float],
    cursor_after_id: Optional[int],
) -> Dict[str, Any]:
    from Api.services.search_service import SearchService

    trace = SearchTrace()
    token = SearchTrace.bind(trace)
    try:
        results, total = SearchService.advanced_search(
            query,
            limit=per_page,
            offset=offset,
            use_bm25=False,
            use_expansion=False,
            use_fuzzy=False,
            analyst_scope="all",
            fast_mode=True,
            include_snippets=False,
            count_mode=count_mode,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            cursor_after_score=cursor_after_score,
            cursor_after_id=cursor_after_id,
        )
        trace_dict = trace.to_dict()
        plan = getattr(SearchService, "_last_search_plan", {}) or {}
        engine = plan.get("primary_engine", "unknown")
        return {
            "result_count": len(results),
            "total_count": total,
            "engine": engine,
            "plan": plan,
            "trace": trace_dict,
            **trace_breakdown(trace_dict),
        }
    finally:
        SearchTrace.reset(token)


def _run_snippets_once(query: str, stubs: List[Dict[str, Any]]) -> Dict[str, float]:
    from Api.services.search_service import SearchService

    trace = SearchTrace()
    token = SearchTrace.bind(trace)
    try:
        t0 = time.perf_counter()
        SearchService.enrich_results_with_snippets(stubs, query=query)
        wall = (time.perf_counter() - t0) * 1000.0
        td = trace.to_dict()
        br = trace_breakdown(td)
        br["wall_ms"] = round(wall, 3)
        return br
    finally:
        SearchTrace.reset(token)


def benchmark_query_entry(
    entry: Dict[str, Any],
    *,
    iterations: int,
    cold: bool,
    warm: bool,
    count_mode: str,
    include_snippets: bool,
) -> List[Dict[str, Any]]:
    qid = entry["id"]
    query = entry.get("query") or ""
    options = entry.get("options") or {}
    per_page = int(entry.get("per_page") or 25)
    records: List[Dict[str, Any]] = []
    ds = dataset_stats_from_db()

    def one_list(offset: int = 0, cursor_score=None, cursor_id=None):
        return _run_list_once(
            query=query,
            per_page=per_page,
            offset=offset,
            count_mode=count_mode,
            case_sensitive=bool(options.get("case_sensitive")),
            whole_word=bool(options.get("whole_word")),
            cursor_after_score=cursor_score,
            cursor_after_id=cursor_id,
        )

    phases = []
    if cold:
        phases.append("cold")
    if warm:
        phases.append("warm")
    if not phases:
        phases = ["single"]

    for phase in phases:
        list_samples: List[float] = []
        last_payload: Dict[str, Any] = {}
        for _ in range(iterations):
            last_payload = one_list()
            list_samples.append(float(last_payload.get("total_ms") or 0.0))

        base = {
            "recorded_at": utc_now_iso(),
            "query_id": qid,
            "label": entry.get("label"),
            "query": query,
            "phase": phase,
            "iterations": iterations,
            "concurrency": 1,
            "per_page": per_page,
            "count_mode": count_mode,
            **ds,
            **summarize_latencies(list_samples),
            **{k: last_payload.get(k) for k in (
                "engine", "result_count", "total_count",
                "db_ms", "candidate_ms", "count_ms", "snippet_ms",
                "plan_ms", "serialize_ms", "rank_ms",
            )},
        }
        records.append(base)

    if entry.get("pagination"):
        pag = entry["pagination"]
        pages = pag.get("pages") or [1, 10]
        pp = int(pag.get("per_page") or per_page)
        for page in pages:
            off = (page - 1) * pp
            off_payload = one_list(offset=off)
            records.append({
                "recorded_at": utc_now_iso(),
                "query_id": qid,
                "label": entry.get("label"),
                "query": query,
                "phase": "pagination_offset",
                "pagination_mode": "offset",
                "page": page,
                "offset": off,
                "per_page": pp,
                **ds,
                **summarize_latencies([float(off_payload.get("total_ms") or 0.0)]),
                **trace_breakdown(off_payload.get("trace") or {}),
                "engine": off_payload.get("engine"),
            })

    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Search benchmark harness (structured JSONL)")
    parser.add_argument("--suite", type=Path, default=ROOT / "tools" / "perf" / "query_suite.json")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--cold", action="store_true", help="Record cold phase label")
    parser.add_argument("--warm", action="store_true", help="Record warm phase (default if neither)")
    parser.add_argument("--count-mode", default="skip", choices=["skip", "timed", "exact"])
    parser.add_argument("--query-id", action="append", help="Run subset of suite ids (repeatable)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "perf" / "runs" / "search_latest.jsonl")
    parser.add_argument("--include-snippets", action="store_true")
    args = parser.parse_args()

    if not args.cold and not args.warm:
        args.warm = True

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    entries = suite.get("queries") or []
    if args.query_id:
        allow = set(args.query_id)
        entries = [e for e in entries if e.get("id") in allow]

    probe = db_connection_probe()
    hw = hardware_profile()
    all_records: List[Dict[str, Any]] = []
    meta = {
        "hardware": hw,
        "db_probe": probe,
        "suite_version": suite.get("version"),
        "started_at": utc_now_iso(),
    }
    all_records.append({"type": "meta", **meta})
    if not probe.get("connected"):
        all_records.append({
            "type": "error",
            "error": probe.get("error") or "database not connected",
            "hint": "Configure DB_* in .env (same as run_web.py) and re-run",
        })

    if probe.get("connected"):
        for entry in entries:
            try:
                all_records.extend(
                    benchmark_query_entry(
                        entry,
                        iterations=max(1, args.iterations),
                        cold=args.cold,
                        warm=args.warm,
                        count_mode=args.count_mode,
                        include_snippets=args.include_snippets,
                    )
                )
            except Exception as exc:
                all_records.append({
                    "type": "error",
                    "query_id": entry.get("id"),
                    "error": str(exc),
                })

    all_records.append({"type": "meta", "finished_at": utc_now_iso()})
    write_jsonl(args.out, all_records)
    decisions_path = args.out.with_name(args.out.stem + "_decisions.jsonl")
    decisions = records_from_jsonl_rows(
        all_records,
        benchmark=args.out.stem,
        hardware_profile=hw,
    )
    write_jsonl(decisions_path, decisions)
    print(decisions_path)
    print(args.out)
    print(f"records={len(all_records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
