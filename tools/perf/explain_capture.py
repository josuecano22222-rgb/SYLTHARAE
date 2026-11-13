#!/usr/bin/env python3
"""Capture EXPLAIN (ANALYZE, BUFFERS) for search SQL templates (Wave 1)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime  # noqa: E402

bootstrap_runtime()

from Api.utils import execute_query  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture PostgreSQL plans for search queries")
    parser.add_argument("--label", required=True, help="Short name for this plan file")
    parser.add_argument("--sql-file", required=True, type=Path, help="SQL file with a single statement")
    parser.add_argument("--params-json", default="[]", help="JSON array of query parameters")
    args = parser.parse_args()

    sql = args.sql_file.read_text(encoding="utf-8").strip()
    params = json.loads(args.params_json)
    plan_rows = execute_query(
        f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) {sql}",
        tuple(params),
        fetch="all",
    ) or []
    plan_text = "\n".join(row[0] for row in plan_rows if row)

    out_dir = ROOT / "docs" / "search" / "explain"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{stamp}_{args.label}.txt"
    out_path.write_text(plan_text + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
