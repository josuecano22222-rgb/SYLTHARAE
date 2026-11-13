#!/usr/bin/env python3
"""Throughput vs. dataset size, measured with the *same* pipeline.

Answers "does throughput or resource use degrade as the dataset grows?" by
measurement: the real ingestion runs over corpora of increasing size with
identical settings, each against a fresh database so the comparison is
like-for-like.

Usage:
    measure_throughput_scaling.py [--sizes 1000,10000,25000] [--out /tmp/scaling.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]

FIELDS = ("label", "corpus_files", "corpus_mb", "elapsed_s", "fps",
          "ms_per_file", "rate_first_decile", "rate_mid", "rate_last_decile",
          "rss_max_mb", "rss_end_mb", "threads_max", "fd_max", "cpu_mean",
          "db_size_mb", "index_size_mb", "paths_rows", "words_rows",
          "duplicate_hash_groups", "duplicate_path_rows", "pending", "percent",
          "failed", "skipped", "unsupported")


def _row(summary: dict) -> dict:
    db = summary.get("db", {})
    ledger = summary.get("ledger", {})
    stats = summary.get("stats", {})
    return {
        "label": summary["label"],
        "corpus_files": summary["corpus_files"],
        "corpus_mb": round(summary["corpus_bytes"] / 1e6, 2),
        "elapsed_s": round(summary["elapsed_s"], 1),
        "fps": summary["files_per_second"],
        "ms_per_file": summary["ms_per_file"],
        "rate_first_decile": summary.get("rate_first_decile_fps"),
        "rate_mid": summary.get("rate_mid_fps"),
        "rate_last_decile": summary.get("rate_last_decile_fps"),
        "rss_max_mb": summary.get("rss_max_mb"),
        "rss_end_mb": summary.get("rss_end_mb"),
        "threads_max": summary.get("threads_max"),
        "fd_max": summary.get("fd_max"),
        "cpu_mean": summary.get("cpu_percent_mean"),
        "db_size_mb": round((db.get("db_size_bytes") or 0) / 1e6, 1),
        "index_size_mb": round((db.get("index_size_bytes") or 0) / 1e6, 1),
        "paths_rows": db.get("paths_rows"),
        "words_rows": db.get("words_rows"),
        "duplicate_hash_groups": db.get("duplicate_hash_groups"),
        "duplicate_path_rows": db.get("duplicate_path_rows"),
        "pending": ledger.get("files_pending"),
        "percent": ledger.get("percent"),
        "failed": stats.get("failed"),
        "skipped": stats.get("skipped"),
        "unsupported": stats.get("unsupported"),
    }


def run_size(size: int, workdir: Path, workers: int) -> dict:
    corpus = workdir / f"corpus_{size}"
    if not corpus.exists():
        subprocess.run([sys.executable, str(HERE / "gen_corpus.py"), str(corpus),
                        "--files", str(size), "--dirs-per-level", "5", "--depth", "4",
                        "--avg-bytes", "4096", "--formats", "text,json,csv,binary,zip",
                        "--seed", "20260917"], check=True,
                       stdout=subprocess.DEVNULL, timeout=1800)
    label = f"n{size}"
    out = workdir / f"out_{label}"
    subprocess.run([sys.executable, str(HERE / "soak_ingest.py"), str(corpus),
                    "--out", str(out), "--label", label, "--workers", str(workers),
                    "--max-conn", "25", "--pool", "8",
                    "--pgdata", str(workdir / f"pg_{label}")],
                   check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=3600)
    return _row(json.loads((out / "summary.json").read_text()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="1000,10000,25000")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="scaling.json")  # not a guessable path in shared /tmp
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="scaling_"))
    workdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for size in [int(s) for s in args.sizes.split(",") if s.strip()]:
        row = run_size(size, workdir, args.workers)
        rows.append(row)
        print(json.dumps(row), flush=True)

    Path(args.out).write_text(json.dumps(rows, indent=1))
    print(f"\nwrote {args.out}")
    print(f"{'files':>8} {'fps':>8} {'ms/file':>8} {'rss_max':>8} {'fd':>4} "
          f"{'dbcpu':>6} {'dupes':>6} {'pending':>7}")
    for row in rows:
        print(f"{row['corpus_files']:>8} {row['fps']:>8} {row['ms_per_file']:>8} "
              f"{row['rss_max_mb']:>8} {row['fd_max']:>4} {row['cpu_mean']:>6} "
              f"{row['duplicate_path_rows']:>6} {row['pending']:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
