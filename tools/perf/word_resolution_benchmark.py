#!/usr/bin/env python3
"""Compare word resolution: ILIKE scan vs exact ANY lookup."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime, db_connection_probe

bootstrap_runtime()

from tools.perf.benchmark_common import hardware_profile, summarize_latencies, utc_now_iso, write_jsonl


def _sample_terms(count: int) -> List[str]:
    from Api.utils import execute_query

    rows = execute_query(
        "SELECT word FROM words ORDER BY id LIMIT %s",
        (count,),
        fetch="all",
    ) or []
    return [str(r[0]) for r in rows if r and r[0]]


def _bench_ilike(terms: List[str], iterations: int) -> List[float]:
    from Api.utils import execute_query

    pattern = f"%{terms[0]}%" if terms else "%x%"
    samples: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        execute_query(
            "SELECT id FROM words WHERE word ILIKE %s LIMIT 50",
            (pattern,),
            fetch="all",
        )
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def _bench_any(terms: List[str], iterations: int) -> List[float]:
    from Api.utils import execute_query

    keys = [t.lower() for t in terms[: min(10, len(terms))]]
    samples: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        execute_query(
            "SELECT word, id FROM words WHERE word = ANY(%s)",
            (keys,),
            fetch="all",
        )
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--term-counts", default="1,10,100")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "perf" / "runs" / "word_resolution.jsonl")
    args = parser.parse_args()

    probe = db_connection_probe()
    counts = [int(x.strip()) for x in args.term_counts.split(",") if x.strip()]
    records = [{
        "type": "meta",
        "hardware": hardware_profile(),
        "db_probe": probe,
        "started_at": utc_now_iso(),
    }]
    if not probe.get("connected"):
        records.append({"type": "error", "error": probe.get("error")})
        write_jsonl(args.out, records)
        print(args.out)
        return 1
    for n in counts:
        terms = _sample_terms(n)
        if not terms:
            records.append({"type": "error", "term_count": n, "error": "no words in table"})
            continue
        ilike = summarize_latencies(_bench_ilike(terms, args.iterations))
        exact = summarize_latencies(_bench_any(terms, args.iterations))
        records.append({
            "type": "word_resolution",
            "term_count": n,
            "terms_sampled": len(terms),
            "ilike_single_prefix": ilike,
            "exact_any_lookup": exact,
        })
    records.append({"type": "meta", "finished_at": utc_now_iso()})
    write_jsonl(args.out, records)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
