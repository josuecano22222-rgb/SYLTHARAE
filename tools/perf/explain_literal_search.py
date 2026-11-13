#!/usr/bin/env python3
"""EXPLAIN production advanced_search SQL for PG_LITERAL diagnosis (Q07/Q08/Q10)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime, db_connection_probe  # noqa: E402
from tools.perf.benchmark_common import dataset_stats_from_db, git_commit, schema_version  # noqa: E402
from tools.perf.explain_search_common import capture_production_explain  # noqa: E402

bootstrap_runtime()

LITERAL_QUERY_CLASSES = [
    ("Q07", "boolean_or", "report OR invoice OR contract OR memo"),
    ("Q08", "phrase", '"annual financial report"'),
    ("Q10", "regex", "rep[a-z]{2,4}"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "search" / "explain",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Manifest filename (default manifest_literal_latest.json)",
    )
    parser.add_argument(
        "--query-id",
        action="append",
        help="Limit to Q07, Q08, Q10 (repeatable)",
    )
    args = parser.parse_args()
    probe = db_connection_probe()
    if not probe.get("connected"):
        import json

        print(json.dumps({"error": probe.get("error"), "probe": probe}, indent=2))
        return 1

    ds = dataset_stats_from_db()
    queries = LITERAL_QUERY_CLASSES
    if args.query_id:
        allow = set(args.query_id)
        queries = [q for q in LITERAL_QUERY_CLASSES if q[0] in allow]
    manifest_name = args.manifest or (args.out_dir / "manifest_literal_latest.json")
    manifest_path = capture_production_explain(
        queries,
        out_dir=args.out_dir,
        manifest_path=manifest_name,
        probe=probe,
        meta={
            "track": "PG_LITERAL_diagnosis",
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
