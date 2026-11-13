#!/usr/bin/env python
"""Profile one document of each size through the real ingestion pipeline.

Round 4 measured that ingestion time grows superlinearly above ~512 KB
(128 KB 2.0 s ... 4 MB 321 s) and, from log timestamps, that the growth happens
after extraction - inside the analysis that turns text into words and signals.
Log timestamps cannot say *which* function, so this runs the same pipeline the
task manager runs, in-process, under ``cProfile``.

For each size it records the ten functions with the largest cumulative time,
the whole profile as top-level call counts, and a by-module breakdown, so the
hot spot is named by measurement rather than guessed.

Usage:
    python tools/audit/profile_ingest.py [--tag NAME]
Writes audit_report/evidence_scale/ingest_profile{_NAME}.json and prints a
summary. The 1 MB run is dominated by identifiers_intel._merge_overlaps; the
``after`` profile is the same pipeline with that merge made non-quadratic.
"""

from __future__ import annotations

import cProfile
import io
import json
import os
import pathlib
import pstats
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "evidence_scale"
DB = "file_analysis_profile"
CORPUS = pathlib.Path("/tmp/profile_corpus")

BASE_TEXT = ("The quarterly review is scheduled for 3 March 2027 in Rotterdam. "
             "Contact alice@example.org, bob@example.net or 10.0.0.1 and 192.168.0.7. "
             "Payment of 125000 EUR was agreed; see https://example.org/ledger. ")
SIZES = {"512kb": 512 * 1024, "1mb": 1024 * 1024, "2mb": 2 * 1024 * 1024}


def build_database() -> dict:
    import psycopg2
    from database.bootstrap import bootstrap_database

    cfg = {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT") or 5432),
        "user": os.environ.get("DB_USER") or "postgres",
        "password": os.environ.get("DB_PASSWORD") or "",
        "database": DB,
    }
    admin = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                             password=cfg["password"], dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = %s", (DB,))
        cur.execute(f'DROP DATABASE IF EXISTS "{DB}"')
        cur.execute(f'CREATE DATABASE "{DB}"')
    admin.close()
    os.environ.update(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]),
                      DB_USER=cfg["user"], DB_PASSWORD=cfg["password"], DB_NAME=DB)
    report = bootstrap_database(cfg)
    return {"applied_migrations": report["applied_migrations"]}


def make_document(key: str, size: int) -> pathlib.Path:
    CORPUS.mkdir(parents=True, exist_ok=True)
    path = CORPUS / f"profile_{key}.txt"
    text = f"Profile document {key}.\n"
    while len(text.encode("utf-8")) < size:
        text += BASE_TEXT
    path.write_text(text, encoding="utf-8")
    return path


def top_ten(profile: cProfile.Profile) -> list[dict]:
    stream = io.StringIO()
    stats = pstats.Stats(profile, stream=stream)
    stats.sort_stats("cumulative")
    rows = []
    for (filename, line, function), (cc, nc, tt, ct, _callers) in stats.stats.items():
        rows.append({
            "function": f"{function} ({pathlib.Path(filename).name}:{line})",
            "module": _module_of(filename),
            "calls": nc,
            "total_s": round(tt, 3),
            "cumulative_s": round(ct, 3),
        })
    rows.sort(key=lambda row: -row["cumulative_s"])
    return rows[:10]


def _module_of(filename: str) -> str:
    name = str(filename)
    marker = "/SYLTHARAE-Pro/"
    if marker in name:
        return name.split(marker, 1)[1].split("/")[0]
    return "stdlib/site-packages"


def module_breakdown(profile: cProfile.Profile) -> dict:
    stats = pstats.Stats(profile)
    totals: dict[str, float] = {}
    for (filename, _line, _function), (_cc, _nc, tt, _ct, _callers) in stats.stats.items():
        key = _module_of(filename)
        totals[key] = totals.get(key, 0.0) + tt
    return {k: round(v, 3) for k, v in sorted(totals.items(), key=lambda kv: -kv[1])[:12]}


def main() -> int:
    tag = ""
    if "--tag" in sys.argv:
        tag = "_" + sys.argv[sys.argv.index("--tag") + 1]
    import core.initialization as init

    init.INIT_MARKER_FILE = pathlib.Path("/tmp/sylth_appdata/.profile_marker")
    init.INIT_MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    init.mark_system_initialized()

    report: dict = {"database": build_database(), "runs": []}
    print("databases prepared:", report["database"])

    from pipeline.integrated_reader import IntegratedFileReader

    for key, size in SIZES.items():
        document = make_document(key, size)
        reader = IntegratedFileReader(
            max_workers=1,
            enable_monitoring=False,
            enable_storage=True,
            storage_source=f"profile source {key}",
            storage_side=f"profile side {key}",
        )
        profiler = cProfile.Profile()
        started = time.perf_counter()
        profiler.enable()
        try:
            result = reader.process_single_file(str(document))
        finally:
            profiler.disable()
        elapsed = round(time.perf_counter() - started, 1)

        run = {
            "size": key,
            "bytes": document.stat().st_size,
            "seconds": elapsed,
            "top10_cumulative": top_ten(profiler),
            "by_module": module_breakdown(profiler),
            "stored": bool(result),
        }
        report["runs"].append(run)
        print(f"\n{key} ({size} bytes): {elapsed} s, stored={bool(result)}")
        for row in run["top10_cumulative"]:
            print(f"  {row['cumulative_s']:8.2f}s cum  {row['total_s']:8.2f}s own  "
                  f"{row['calls']:>7} calls  {row['function']}")
        print("  by module:", dict(list(run["by_module"].items())[:6]))

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"ingest_profile{tag}.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
