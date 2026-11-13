"""Shared helpers: production advanced_search → EXPLAIN (ANALYZE, BUFFERS)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]

QuerySpec = Tuple[str, str, str]  # query_id, query_class, query text


def parse_explain_metrics(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "planning_time_ms": None,
        "execution_time_ms": None,
        "shared_hit_blocks": None,
        "shared_read_blocks": None,
        "temp_read_blocks": None,
        "temp_written_blocks": None,
        "scan_type": None,
        "join_strategy": None,
        "sort_strategy": None,
        "actual_rows": None,
        "estimated_rows": None,
        "expensive_nodes": [],
    }
    for line in text.splitlines():
        if line.startswith("Planning Time:"):
            out["planning_time_ms"] = float(line.split(":")[1].strip().split()[0])
        if line.startswith("Execution Time:"):
            out["execution_time_ms"] = float(line.split(":")[1].strip().split()[0])
        if "Seq Scan" in line and out["scan_type"] is None:
            out["scan_type"] = "Seq Scan"
        if "Index Scan" in line and out["scan_type"] is None:
            out["scan_type"] = "Index Scan"
        if "Index Only Scan" in line and out["scan_type"] is None:
            out["scan_type"] = "Index Only Scan"
        if "Nested Loop" in line and out["join_strategy"] is None:
            out["join_strategy"] = "Nested Loop"
        if "Hash Join" in line and out["join_strategy"] is None:
            out["join_strategy"] = "Hash Join"
        if "Sort" in line and "Method" not in line and out["sort_strategy"] is None:
            out["sort_strategy"] = "Sort"
        if "rows=" in line and "loops=" in line:
            m = re.search(r"rows=(\d+\.?\d*)(\.\.(\d+\.?\d*))?", line)
            if m:
                out["estimated_rows"] = m.group(1)
                out["actual_rows"] = m.group(3) or m.group(1)
        if "Buffers:" in line:
            hit = re.search(r"shared hit=(\d+)", line)
            read = re.search(r"shared read=(\d+)", line)
            if hit:
                out["shared_hit_blocks"] = int(hit.group(1))
            if read:
                out["shared_read_blocks"] = int(read.group(1))
            temp = re.search(r"temp read=(\d+)", line)
            if temp:
                out["temp_read_blocks"] = int(temp.group(1))
            tempw = re.search(r"temp written=(\d+)", line)
            if tempw:
                out["temp_written_blocks"] = int(tempw.group(1))
        time_m = re.search(r"actual time=([\d.]+)\.\.([\d.]+)", line)
        if time_m and ("Scan on" in line or "Join" in line or "Filter:" in line):
            table = None
            for token in ("words ", "words_hashs", "contents_raw", "paths ", "hashs "):
                if token in line:
                    table = token.strip()
                    break
            out["expensive_nodes"].append(
                {
                    "line": line.strip()[:240],
                    "actual_time_ms_end": float(time_m.group(2)),
                    "table_hint": table,
                }
            )
    nodes = out["expensive_nodes"]
    if nodes:
        nodes.sort(key=lambda n: n["actual_time_ms_end"], reverse=True)
        out["expensive_nodes"] = nodes[:8]
    return out


def explain_sql(sql: str, params: Tuple[Any, ...], out_dir: Path, label: str) -> Dict[str, Any]:
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
    metrics = parse_explain_metrics(text)
    fingerprint = hashlib.sha256((sql + repr(params)).encode("utf-8")).hexdigest()[:16]
    return {
        "label": label,
        "path": str(path.relative_to(ROOT)),
        "sql_fingerprint": fingerprint,
        "plan_bytes": len(text.encode("utf-8")),
        **metrics,
    }


def run_production_search(query: str) -> None:
    from Api.services.search_service import SearchService
    from core.search.perf import SearchTrace

    trace = SearchTrace()
    token = SearchTrace.bind(trace)
    try:
        SearchService.advanced_search(
            query,
            limit=25,
            offset=0,
            use_bm25=False,
            use_expansion=False,
            use_fuzzy=False,
            analyst_scope="all",
            fast_mode=True,
            include_snippets=False,
            count_mode="exact",
        )
    finally:
        SearchTrace.reset(token)


def capture_production_explain(
    queries: Sequence[QuerySpec],
    *,
    out_dir: Path,
    manifest_path: Path,
    probe: Dict[str, Any],
    meta: Dict[str, Any],
) -> Path:
    from Api.services.search_service import SearchService

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "probe": probe,
        "captures": [],
        **meta,
    }

    for qid, qclass, query in queries:
        run_production_search(query)
        short = SearchService._last_short_circuit
        plan = getattr(SearchService, "_last_search_plan", {}) or {}
        for stage, sql_attr, param_attr in (
            ("count", "_last_count_query", "_last_count_params"),
            ("candidate_generation", "_last_candidate_query", "_last_candidate_params"),
        ):
            sql = getattr(SearchService, sql_attr, None)
            params = getattr(SearchService, param_attr, None) or ()
            label = f"production/{qid}_{qclass}/{stage}"
            if not sql:
                manifest["captures"].append(
                    {
                        "query_class": qclass,
                        "query_id": qid,
                        "query": query,
                        "stage": stage,
                        "skipped": True,
                        "short_circuit": short,
                        "engine": plan.get("primary_engine"),
                    }
                )
                continue
            capture = explain_sql(sql, tuple(params), out_dir, label)
            capture.update(
                {
                    "query_class": qclass,
                    "query_id": qid,
                    "query": query,
                    "stage": stage,
                    "short_circuit": short,
                    "engine": plan.get("primary_engine"),
                }
            )
            manifest["captures"].append(capture)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path
