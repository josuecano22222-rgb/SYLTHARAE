"""
Compare PostgreSQL list search vs ripgrep on a bounded text corpus.

This does NOT replace production benchmarks — it helps populate the decision
matrix in docs/search/PERFORMANCE_ARCHITECTURE.md.

Example:

    python tools/perf/search_engine_compare.py --query needle --files 1000
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def bench_rg(corpus_dir: Path, pattern: str) -> dict:
    from core.search.ripgrep import RipgrepScanner, RipgrepConfig, resolve_ripgrep_binary

    binary = resolve_ripgrep_binary()
    if not binary:
        return {"error": "rg not found"}
    scanner = RipgrepScanner(RipgrepConfig(enabled=True, binary_path=binary, min_content_bytes=1))
    t0 = time.perf_counter()
    proc = subprocess.run(
        [binary, "--json", "--stats", "-e", pattern, str(corpus_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    stats_line = [ln for ln in (proc.stdout or "").splitlines() if '"type":"summary"' in ln.replace(" ", "")]
    return {
        "engine": "ripgrep",
        "elapsed_ms": round(elapsed_ms, 2),
        "returncode": proc.returncode,
        "summary": stats_line[-1] if stats_line else None,
    }


def bench_pg(query: str, iterations: int) -> dict:
    from Api.services.search_service import SearchService

    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        SearchService.advanced_search(
            query,
            limit=20,
            offset=0,
            use_bm25=False,
            analyst_scope="all",
            fast_mode=True,
            include_snippets=False,
            count_mode="skip",
        )
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return {
        "engine": "postgresql_advanced",
        "median_ms": samples[len(samples) // 2],
        "samples_ms": samples,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="needle")
    parser.add_argument("--files", type=int, default=200)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="syltharae-rg-bench-") as tmp:
        root = Path(tmp)
        for i in range(args.files):
            (root / f"{i}.txt").write_text(
                f"line {i}\nneedle here sometimes\nfooter\n", encoding="utf-8"
            )
        rg = bench_rg(root, args.query)
    pg = bench_pg(args.query, args.iterations)
    print({"ripgrep_filesystem": rg, "postgresql_indexed": pg})


if __name__ == "__main__":
    main()
