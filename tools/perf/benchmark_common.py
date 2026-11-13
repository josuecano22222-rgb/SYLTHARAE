"""Shared benchmark utilities: hardware profile, percentiles, JSON records."""

from __future__ import annotations

import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def schema_version() -> Optional[str]:
    migrations = sorted((ROOT / "database" / "migrations").glob("m*.py"))
    if not migrations:
        return None
    return migrations[-1].stem


def postgres_settings() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        from Api.utils import execute_query

        rows = execute_query(
            """
            SELECT name, setting, unit
            FROM pg_settings
            WHERE name IN (
                'shared_buffers', 'effective_cache_size', 'work_mem',
                'maintenance_work_mem', 'max_connections', 'server_version'
            )
            ORDER BY name
            """,
            fetch="all",
        ) or []
        for row in rows:
            out[str(row[0])] = f"{row[1]}{row[2] or ''}"
    except Exception as exc:
        out["error"] = str(exc)
    return out


def hardware_profile() -> Dict[str, Any]:
    profile: Dict[str, Any] = {
        "recorded_at": utc_now_iso(),
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_bytes": None,
        "application_commit": git_commit(),
        "schema_version": schema_version(),
        "project_root": str(ROOT),
    }
    try:
        import psutil

        vm = psutil.virtual_memory()
        profile["ram_bytes"] = vm.total
        profile["ram_available_bytes"] = vm.available
    except ImportError:
        pass
    profile["postgresql"] = postgres_settings()
    return profile


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))
    return round(ordered[idx], 3)


def summarize_latencies(samples_ms: Sequence[float]) -> Dict[str, float]:
    if not samples_ms:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "median_ms": 0.0, "mean_ms": 0.0}
    return {
        "p50_ms": percentile(samples_ms, 0.50),
        "p95_ms": percentile(samples_ms, 0.95),
        "p99_ms": percentile(samples_ms, 0.99),
        "median_ms": round(statistics.median(samples_ms), 3),
        "mean_ms": round(statistics.fmean(samples_ms), 3),
    }


def span_ms(trace_dict: Dict[str, Any], *operations: str) -> float:
    by_op = trace_dict.get("by_operation_ms") or {}
    return round(sum(float(by_op.get(op, 0.0)) for op in operations), 3)


def trace_breakdown(trace_dict: Dict[str, Any]) -> Dict[str, float]:
    timings = trace_dict.get("timings") or trace_dict.get("performance_model") or {}
    by_op = trace_dict.get("by_operation_ms") or {}
    return {
        "total_ms": float(trace_dict.get("total_ms") or timings.get("T_total") or 0.0),
        "db_ms": float(timings.get("T_db") or span_ms(trace_dict, "db_count", "db_candidates")),
        "candidate_ms": float(by_op.get("db_candidates") or 0.0),
        "count_ms": float(by_op.get("db_count") or 0.0),
        "snippet_ms": float(
            timings.get("T_snippet")
            or span_ms(trace_dict, "snippets", "snippet_enrich", "io_text_windows")
        ),
        "plan_ms": float(by_op.get("plan") or timings.get("T_plan") or 0.0),
        "serialize_ms": float(timings.get("T_serialize") or by_op.get("serialize") or 0.0),
        "rank_ms": float(timings.get("T_rank") or by_op.get("rank") or 0.0),
    }


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


def dataset_stats_from_db() -> Dict[str, Any]:
    stats: Dict[str, Any] = {"dataset_files": None, "index_words": None, "database_bytes": None}
    try:
        from Api.utils import execute_query

        row = execute_query("SELECT COUNT(*) FROM paths", fetch="one")
        stats["dataset_files"] = int(row[0]) if row else 0
        row = execute_query("SELECT COUNT(*) FROM words", fetch="one")
        stats["index_words"] = int(row[0]) if row else 0
        row = execute_query(
            "SELECT pg_database_size(current_database())",
            fetch="one",
        )
        stats["database_bytes"] = int(row[0]) if row else None
    except Exception as exc:
        stats["error"] = str(exc)
    return stats


def warm_os_cache_hint() -> None:
    """Best-effort noop on Windows; on Linux optionally drop caches (skipped by default)."""
    return


def timed_iterations(
    fn,
    *,
    iterations: int,
    discard_first: bool = False,
) -> List[float]:
    samples: List[float] = []
    for i in range(iterations):
        t0 = time.perf_counter()
        fn()
        elapsed = (time.perf_counter() - t0) * 1000.0
        if discard_first and i == 0:
            continue
        samples.append(elapsed)
    return samples
