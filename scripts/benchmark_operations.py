#!/usr/bin/env python3
"""Performance benchmark: direct-service ingestion vs frontend job workflow.

Implements spec sections 13/33: identical workload through
  baseline: IngestionService.run() directly (what the CLI used to do)
  job:      POST-equivalent -> JobManager (worker thread) -> same service

Reports wall time, files/sec, MB/sec, queue/startup latency and asserts the
job layer introduces no major processing bottleneck.

Usage:
    python scripts/benchmark_operations.py [--files N] [--tolerance PCT]

Exit code 0 = within tolerance, 1 = regression beyond tolerance.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.init import (  # noqa: E402
    setup_project_path, initialize_settings, initialize_database_config,
)

TOTALS = {}


def make_corpus(root: Path, n_files: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    marker = os.urandom(4).hex()
    for i in range(n_files):
        (root / f"f{i:04d}.txt").write_text(
            f"benchmark {marker} file {i} lorem ipsum dolor sit amet\n" * 60,
            encoding="utf-8",
        )
    return root


def measure_ingest(path: Path, source: str, side: str) -> dict:
    from services.ingesting.options import IngestionOptions
    from services.ingesting.service import IngestionRequest, IngestionService

    svc = IngestionService()
    req = IngestionRequest(
        path=str(path), source=source, side=side,
        options=IngestionOptions(checkpoint="off"),
    )
    t0 = time.perf_counter()
    result = svc.run(req)
    elapsed = time.perf_counter() - t0
    assert result.success, result.errors
    stored = int(result.stats.get("files_stored") or 0)
    dupes = int(result.stats.get("files_duplicates") or 0)
    return {"elapsed": elapsed, "stored": stored, "dupes": dupes}


def measure_via_job(path: Path, source: str, side: str) -> dict:

    from services.jobs.manager import JobManager

    mgr = JobManager.get_instance()
    t0 = time.perf_counter()
    job = mgr.create_job(
        "ingestion", source=str(path),
        options={
            "path": str(path), "source": source, "side": side,
            "recursive": True, "processing": {"checkpoint": "off"},
        },
        created_by="benchmark",
    )
    job_id = job["job_id"]
    created = time.perf_counter() - t0  # HTTP-equivalent return latency

    deadline = time.time() + 600
    status = None
    started_at = None
    while time.time() < deadline:
        current = mgr.get(job_id)
        if current is None:
            time.sleep(0.05)
            continue
        if started_at is None and current.get("started_at"):
            started_at = time.perf_counter()
        status = current["status"]
        if status in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED",
                      "CANCELLED"):
            break
        time.sleep(0.05)
    assert status in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), mgr.get(job_id)
    startup = (started_at - t0) if started_at else 0.0
    stats = mgr.get(job_id)["stats"] or {}
    return {
        "elapsed": time.perf_counter() - t0,
        "stored": int(stats.get("files_stored") or 0),
        "dupes": int(stats.get("files_duplicates") or 0),
        "create_latency": created,
        "startup_latency": startup,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=150)
    parser.add_argument("--tolerance", type=float, default=35.0,
                        help="allowed slowdown of job path vs baseline, %%")
    args = parser.parse_args()

    setup_project_path(str(PROJECT_ROOT))
    initialize_settings(PROJECT_ROOT)
    initialize_database_config()

    marker = os.urandom(3).hex()
    corpus = make_corpus(Path(tempfile.mkdtemp(prefix="bench_corpus_")),
                         args.files)
    os.environ["INGESTION_ROOTS"] = str(corpus)

    print(f"Benchmark: {args.files} files")
    base = measure_ingest(corpus, f"bench-base-{marker}", "bench")
    print(f"  baseline (direct service): {base['elapsed']:.2f}s "
          f"{base['stored'] + base['dupes'] / max(base['elapsed'], 1e-9):.1f} files/sec "
          f"(stored={base['stored']}, dupes={base['dupes']})")

    job = measure_via_job(corpus, f"bench-job-{marker}", "bench")
    fps_base = (base["stored"] + base["dupes"]) / max(base["elapsed"], 1e-9)
    fps_job = (job["stored"] + job["dupes"]) / max(job["elapsed"], 1e-9)
    slowdown_pct = (job["elapsed"] / max(base["elapsed"], 1e-9) - 1) * 100

    print(f"  job (frontend workflow):   {job['elapsed']:.2f}s "
          f"{fps_job:.1f} files/sec "
          f"(stored={job['stored']}, dupes={job['dupes']})")
    print(f"  job create latency: {job['create_latency']*1000:.0f} ms "
          f"(HTTP-equivalent return)")
    print(f"  job startup latency: {job['startup_latency']*1000:.0f} ms")
    print(f"  slowdown vs baseline: {slowdown_pct:+.1f}% "
          f"(tolerance {args.tolerance}%)")

    shutil.rmtree(corpus, ignore_errors=True)

    out = {
        "files": args.files,
        "baseline_seconds": round(base["elapsed"], 3),
        "job_seconds": round(job["elapsed"], 3),
        "baseline_files_per_sec": round(fps_base, 2),
        "job_files_per_sec": round(fps_job, 2),
        "job_create_latency_ms": round(job["create_latency"] * 1000, 1),
        "job_startup_latency_ms": round(job["startup_latency"] * 1000, 1),
        "slowdown_pct": round(slowdown_pct, 1),
    }
    print(json.dumps(out, indent=2))

    if slowdown_pct > args.tolerance:
        print(f"FAIL: job path exceeds tolerance ({slowdown_pct:.1f}% > "
              f"{args.tolerance}%)")
        return 1
    print("PASS: no major processing bottleneck")
    return 0


if __name__ == "__main__":
    sys.exit(main())
