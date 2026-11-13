"""
Benchmark search pipeline stages (PostgreSQL list + snippet hydration).

Usage (requires running DB and migrated schema):

    python tools/perf/search_pipeline_benchmark.py --query "test" --iterations 10

Records median wall time for:
  - SearchService.advanced_search (fast, no snippets)
  - SearchService.enrich_results_with_snippets (bounded window load + rg)
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _median(samples):
    return statistics.median(samples) if samples else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="test")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--per-page", type=int, default=20)
    args = parser.parse_args()

    from Api.services.search_service import SearchService

    list_times = []
    snippet_times = []
    last_results = []

    for _ in range(args.iterations):
        t0 = time.perf_counter()
        results, total = SearchService.advanced_search(
            args.query,
            limit=args.per_page,
            offset=0,
            use_bm25=False,
            use_expansion=False,
            use_fuzzy=False,
            analyst_scope="all",
            fast_mode=True,
            include_snippets=False,
            count_mode="skip",
        )
        list_times.append((time.perf_counter() - t0) * 1000.0)
        last_results = results

    if last_results:
        stubs = [{"id": r["id"], "file_name": r.get("file_name", ""), "file_type": r.get("file_type"),
                  "source_name": r.get("source_name", ""), "side_name": r.get("side_name", "")}
                 for r in last_results]
        for _ in range(min(3, args.iterations)):
            t0 = time.perf_counter()
            SearchService.enrich_results_with_snippets(stubs, query=args.query)
            snippet_times.append((time.perf_counter() - t0) * 1000.0)

    print("Search pipeline benchmark")
    print(f"  query={args.query!r} iterations={args.iterations} per_page={args.per_page}")
    print(f"  list_ms median={_median(list_times):.1f}  samples={list_times}")
    if snippet_times:
        print(f"  snippet_hydration_ms median={_median(snippet_times):.1f}  samples={snippet_times}")
    print(f"  last_total={total if 'total' in dir() else 'n/a'}")


if __name__ == "__main__":
    main()
