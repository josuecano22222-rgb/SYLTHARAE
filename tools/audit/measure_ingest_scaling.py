#!/usr/bin/env python
"""Measure how ingestion time grows with document size - by running it.

The failure-injection runs showed that a single 8 MB plain-text document was
still ``running`` after five minutes. That observation alone cannot say whether
ingestion is slow, superlinear, or stuck, so this measures the curve: documents
of 1/8, 1/4, 1/2, 1 and 2 MB are generated, each is submitted alone through
``POST /api/import-export/batch-import``, and the time until its ``paths`` row
(and the signals that follow it) appears is recorded. Every document is written
to the same approved ingestion root and is unique, so nothing is deduplicated.

The measured quantity is "seconds until the row is committed", which is what a
user waits for; the phase of the task is sampled from
``GET /upload/active-tasks`` while waiting, so a stalled task is distinguishable
from a slow one.

Usage:
    python tools/audit/measure_ingest_scaling.py --password <admin> \
        --db file_analysis_failinject --corpus /tmp/inject_corpus/scaling

Writes audit_report/evidence_scale/ingest_scaling.json.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import psycopg2
import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "evidence_scale"

BASE_TEXT = ("The quarterly review is scheduled for 3 March 2027 in Rotterdam. "
             "Contact alice@example.org, bob@example.net or 10.0.0.1 and 192.168.0.7. "
             "Payment of 125000 EUR was agreed; see https://example.org/ledger. ")
SIZES = [128 * 1024, 256 * 1024, 512 * 1024, 1024 * 1024, 2 * 1024 * 1024]
CAP_SECONDS = 240


def sign_in(base: str, password: str):
    session = requests.Session()
    session.get(f"{base}/auth/login", timeout=30)
    session.headers["X-CSRFToken"] = session.get(f"{base}/api/csrf-token",
                                                 timeout=30).json()["csrf_token"]
    resp = session.post(f"{base}/auth/login",
                        json={"username": "admin", "password": password}, timeout=30)
    session.headers["X-CSRFToken"] = session.get(f"{base}/api/csrf-token",
                                                 timeout=30).json()["csrf_token"]
    return session, resp


def counts(db: str) -> tuple[int, int]:
    conn = psycopg2.connect(host="/tmp/sylth_pg_data", port=5432, user="postgres",
                            password="", dbname=db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM paths")
        paths = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM content_signals")
        signals = cur.fetchone()[0]
    conn.close()
    return paths, signals


def make_document(path: pathlib.Path, size: int) -> None:
    text = f"Scaling document of {size} bytes.\n"
    while len(text.encode("utf-8")) < size:
        text += BASE_TEXT
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--password", required=True)
    parser.add_argument("--db", default="file_analysis_failinject")
    parser.add_argument("--corpus", default="/tmp/inject_corpus/scaling")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    corpus = pathlib.Path(args.corpus)
    corpus.mkdir(parents=True, exist_ok=True)

    session, login = sign_in(base, args.password)
    if login.status_code != 200:
        print("login failed:", login.status_code, login.text[:200])
        return 1

    stamp = int(time.time())
    source = session.post(f"{base}/api/sources",
                          json={"name": f"scaling source {stamp}"}, timeout=30).json()
    side = session.post(f"{base}/api/sides",
                        json={"name": f"scaling side {stamp}"}, timeout=30).json()
    source_id, side_id = source.get("id"), side.get("id")
    if not source_id or not side_id:
        print("fixture creation failed:", source, side)
        return 1

    record: dict = {"base_url": base, "cap_seconds": CAP_SECONDS, "runs": []}
    for size in SIZES:
        document = corpus / f"scaling_{size}.txt"
        make_document(document, size)
        paths_before, signals_before = counts(args.db)
        session.headers["X-CSRFToken"] = session.get(f"{base}/api/csrf-token",
                                                     timeout=30).json()["csrf_token"]
        submitted = time.time()
        resp = session.post(f"{base}/api/import-export/batch-import",
                            json={"file_paths": [str(document)], "source_id": source_id,
                                  "side_id": side_id}, timeout=60)
        entry: dict = {"bytes": document.stat().st_size,
                       "submit_status": resp.status_code,
                       "submit_body": resp.text[:300],
                       "paths_before": paths_before,
                       "phases": []}
        done_seconds = None
        while time.time() - submitted < CAP_SECONDS:
            time.sleep(2.0)
            paths_now, signals_now = counts(args.db)
            try:
                tasks = session.get(f"{base}/upload/active-tasks", timeout=30).json()
                tasks = tasks.get("tasks") if isinstance(tasks, dict) else tasks
            except Exception:
                tasks = None
            phase = None
            if tasks:
                phase = (tasks[0] or {}).get("status") or (tasks[0] or {}).get("phase")
            entry["phases"].append({"t": round(time.time() - submitted, 1),
                                    "paths": paths_now, "signals": signals_now,
                                    "task": phase})
            if paths_now > paths_before:
                done_seconds = round(time.time() - submitted, 1)
                # let the signals of this document settle before the next run
                time.sleep(3.0)
                paths_final, signals_final = counts(args.db)
                entry["signals_written"] = signals_final - signals_before
                break
        entry["seconds_until_row"] = done_seconds
        entry["timed_out"] = done_seconds is None
        record["runs"].append(entry)
        print(f"{entry['bytes']:>9} bytes -> "
              f"{'no row within ' + str(CAP_SECONDS) + ' s' if entry['timed_out'] else str(done_seconds) + ' s'}"
              f"  signals+{entry.get('signals_written')}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ingest_scaling.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"wrote {OUT / 'ingest_scaling.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
