#!/usr/bin/env python3
"""EXPLAIN production advanced_search candidate/count SQL (Q03/Q04 short-circuit path)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime, db_connection_probe  # noqa: E402
from tools.perf.benchmark_common import dataset_stats_from_db, git_commit, schema_version  # noqa: E402
from tools.perf.explain_search_common import capture_production_explain  # noqa: E402

bootstrap_runtime()

INDEXED_MISS_QUERY_CLASSES = [
    ("Q03", "rare_miss", "xylophonezzzz"),
    ("Q04", "zero_result", "zzzznoindexterm99999"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "search" / "explain",
    )
    args = parser.parse_args()
    probe = db_connection_probe()
    if not probe.get("connected"):
        print(json.dumps({"error": probe.get("error"), "probe": probe}, indent=2))
        return 1

    ds = dataset_stats_from_db()
    manifest_path = capture_production_explain(
        INDEXED_MISS_QUERY_CLASSES,
        out_dir=args.out_dir,
        manifest_path=args.out_dir / "manifest_latest.json",
        probe=probe,
        meta={
            "track": "PG_INVERTED_missing_term_short_circuit",
            "commit": git_commit(),
            "schema": schema_version(),
            "corpus_size": probe.get("path_count") or ds.get("dataset_files"),
            "database_version": probe.get("server_version") or "",
        },
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
