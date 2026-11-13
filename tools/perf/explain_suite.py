#!/usr/bin/env python3
"""Capture EXPLAIN plans for standard search query classes (requires live DB)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime, db_connection_probe


PLANS = [
    ("indexed/common_term_candidates", """
        SELECT COUNT(DISTINCT p.id)
        FROM paths p
        LEFT JOIN hash_contexts hc ON p.context_id = hc.id
        LEFT JOIN hashs h ON hc.hash_id = h.id
        WHERE EXISTS (
            SELECT 1 FROM words_hashs wh
            JOIN words w ON wh.word_id = w.id
            WHERE wh.hash_id = h.id AND w.word = %s
        )
    """, ("report",)),
    ("word_resolution/any_lookup", """
        SELECT word, id FROM words WHERE word = ANY(%s)
    """, (["report", "finance"],)),
    ("word_resolution/ilike_prefix", """
        SELECT id FROM words WHERE word ILIKE %s LIMIT 50
    """, ("%report%",)),
    ("count/distinct_paths", """
        SELECT COUNT(DISTINCT p.id)
        FROM paths p
        LEFT JOIN hash_contexts hc ON p.context_id = hc.id
        WHERE 1=1
    """, ()),
]


def capture(label: str, sql: str, params: tuple, out_dir: Path) -> dict:
    from Api.utils import execute_query

    rows = execute_query(
        f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) {sql}",
        params,
        fetch="all",
    ) or []
    text = "\n".join(r[0] for r in rows if r)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = label.replace("/", "_")
    path = out_dir / f"{stamp}_{safe}.txt"
    path.write_text(text + "\n", encoding="utf-8")
    est = act = None
    for line in text.splitlines():
        if "rows=" in line and "loops=" in line:
            # best-effort parse last rows= on line
            parts = line.split("rows=")
            if len(parts) > 1:
                chunk = parts[-1].split()[0]
                try:
                    if ".." in chunk:
                        a, b = chunk.split("..", 1)
                        est = est or a
                        act = b
                    else:
                        act = chunk
                except ValueError:
                    pass
    return {
        "label": label,
        "path": str(path.relative_to(ROOT)),
        "estimated_rows": est,
        "actual_rows": act,
        "plan_bytes": len(text.encode("utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "search" / "explain",
    )
    args = parser.parse_args()
    bootstrap_runtime()
    probe = db_connection_probe()
    if not probe.get("connected"):
        print(json.dumps({"error": probe.get("error"), "probe": probe}, indent=2))
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"probe": probe, "captures": []}
    for label, sql, params in PLANS:
        manifest["captures"].append(capture(label, sql, params, args.out_dir))
    manifest_path = args.out_dir / "manifest_latest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
