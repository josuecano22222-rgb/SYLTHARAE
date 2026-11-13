#!/usr/bin/env python3
"""Count table scan nodes in an EXPLAIN ANALYZE text file."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def count_scans(text: str, table: str) -> dict:
    pattern = re.compile(
        rf"Seq Scan on public\.{re.escape(table)}\s+(\S+)?.*loops=(\d+)",
        re.IGNORECASE,
    )
    hits = pattern.findall(text)
    total_loops = sum(int(loops) for _alias, loops in hits)
    return {"scan_nodes": len(hits), "total_loops": total_loops, "aliases": [h[0] for h in hits]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("explain_file", type=Path)
    args = parser.parse_args()
    text = args.explain_file.read_text(encoding="utf-8")
    for table in ("contents_raw", "words"):
        stats = count_scans(text, table)
        print(f"{table}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
