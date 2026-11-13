#!/usr/bin/env python
"""Kill the web process in the middle of an ingest and record what is left.

The failure-injection run for the database answers "what does a client see while
PostgreSQL is gone". This one answers the other question: when the *application*
stops mid-ingest, what survives, and is anything resumed?

Sequence, all against the running server and its own database:

1. sign in, record the row counts;
2. submit six 8 MB documents through ``POST /api/import-export/batch-import``;
3. wait two seconds - long enough for the reader/writer to be inside a document,
   short enough that the work cannot be finished;
4. ``SIGTERM`` the server process (the platform's stop signal, i.e. an orderly
   shutdown request, not a crash);
5. read the database directly: how many of the six rows exist, what their
   ``processing_status`` is, and whether any hash/signal rows exist without a
   matching path;
6. start the server again with the same command, wait for the port, sign in
   again, and compare the API's view with the database;
7. wait ten seconds and count again: if the count does not move, the interrupted
   work was not resumed (the task queue lives in the process, and the ``jobs``
   table is not written by this path).

Usage:
    python tools/audit/inject_restart.py --server-pid <pid> --password <admin> \
        --corpus /tmp/inject_big --db file_analysis_failinject

Writes audit_report/evidence_scale/restart_mid_job.json.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

import psycopg2
import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "evidence_scale"

SERVER_COMMAND = [
    "/tmp/sylth-venv/bin/python", "run_web.py",
]


def server_env(db: str, port: int, corpus: str) -> dict:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp/sylth_home",
        "DB_HOST": "/tmp/sylth_pg_data",
        "DB_PORT": "5432",
        "DB_USER": "postgres",
        "DB_PASSWORD": "",
        "DB_NAME": db,
        "APP_DATA_DIR": "/tmp/sylth_fi_appdata",
        "INGESTION_ROOTS": corpus,
        "FLASK_PORT": str(port),
        "FLASK_HOST": "0.0.0.0",
        "RATE_LIMIT_PER_MINUTE": "6000",
        "RATE_LIMIT_PER_HOUR": "100000",
        "RATE_LIMIT_LOGIN_PER_MINUTE": "600",
        "SESSION_COOKIE_SECURE": "false",
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SECRET_KEY": "injection-secret-key-0123456789abcdef",
        "AUTO_INSTALL": "0",
    }


def sign_in(base: str, password: str):
    session = requests.Session()
    session.get(f"{base}/auth/login", timeout=30)
    token = session.get(f"{base}/api/csrf-token", timeout=30).json()["csrf_token"]
    session.headers["X-CSRFToken"] = token
    resp = session.post(f"{base}/auth/login",
                        json={"username": "admin", "password": password}, timeout=30)
    token = session.get(f"{base}/api/csrf-token", timeout=30).json()["csrf_token"]
    session.headers["X-CSRFToken"] = token
    return session, resp


def db_state(name: str) -> dict:
    conn = psycopg2.connect(host="/tmp/sylth_pg_data", port=5432, user="postgres",
                            password="", dbname=name)
    conn.autocommit = True
    state: dict = {}
    with conn.cursor() as cur:
        for label, sql in (
            ("paths", "SELECT count(*) FROM paths"),
            ("hashs", "SELECT count(*) FROM hashs"),
            ("hash_contexts", "SELECT count(*) FROM hash_contexts"),
            ("paths_processing", "SELECT count(*) FROM paths WHERE processing_status = 'processing'"),
            ("paths_partial", "SELECT count(*) FROM paths WHERE processing_status = 'partially_processed'"),
            ("paths_processed", "SELECT count(*) FROM paths WHERE processing_status = 'processed'"),
            ("signals", "SELECT count(*) FROM content_signals"),
            ("jobs", "SELECT count(*) FROM jobs"),
            ("job_events", "SELECT count(*) FROM job_events"),
            # a hash written but never attached to a path is an orphan the
            # interrupted run left behind
            ("orphan_hashs", "SELECT count(*) FROM hashs h WHERE NOT EXISTS"
                             " (SELECT 1 FROM hash_contexts hc JOIN paths p ON p.context_id = hc.id"
                             "  WHERE hc.hash_id = h.id)"),
        ):
            cur.execute(sql)
            state[label] = cur.fetchone()[0]
        cur.execute("SELECT file_name, processing_status FROM paths"
                    " ORDER BY id DESC LIMIT 10")
        state["recent_paths"] = [{"file": r[0], "status": r[1]} for r in cur.fetchall()]
    conn.close()
    return state


def wait_for(base: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{base}/health", timeout=5).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--corpus", default="/tmp/inject_big")
    parser.add_argument("--db", default="file_analysis_failinject")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    port = int(base.rsplit(":", 1)[1])

    files = sorted(str(p) for p in pathlib.Path(args.corpus).iterdir() if p.is_file())
    record: dict = {"base_url": base, "files": [os.path.basename(f) for f in files],
                    "sizes": [os.path.getsize(f) for f in files]}

    session, login = sign_in(base, args.password)
    record["login"] = login.status_code
    record["db_before"] = db_state(args.db)

    source = session.post(f"{base}/api/sources",
                          json={"name": f"restart injection source {int(time.time())}"}, timeout=30).json()
    side = session.post(f"{base}/api/sides",
                        json={"name": f"restart injection side {int(time.time())}"}, timeout=30).json()
    source_id = (source.get("source") or source).get("id")
    side_id = (side.get("side") or side).get("id")
    if not source_id or not side_id:
        print(f"fixture creation failed: source={source} side={side}")
        return 1
    session.headers["X-CSRFToken"] = session.get(f"{base}/api/csrf-token",
                                                 timeout=30).json()["csrf_token"]

    started = time.time()
    queued = session.post(f"{base}/api/import-export/batch-import",
                          json={"file_paths": files, "source_id": source_id,
                                "side_id": side_id}, timeout=180)
    record["import_request"] = {"status": queued.status_code,
                                "ms": round((time.time() - started) * 1000, 1),
                                "body": queued.text[:600]}
    print(f"submitted {len(files)} documents: {queued.status_code}")
    try:
        body = queued.json()
        record["import_counts"] = {k: body.get(k) for k in
                                   ("total", "accepted", "rejected", "queued")}
        record["import_entry_statuses"] = {}
        for entry in body.get("results", []):
            key = entry.get("status")
            record["import_entry_statuses"][key] = record["import_entry_statuses"].get(key, 0) + 1
    except Exception:
        pass

    # Wait until the first row of this batch appears, so the kill lands while
    # rows are actually being written; the wait is bounded, and the time to
    # first row is itself a measurement.
    baseline = record["db_before"]["paths"]
    deadline = time.time() + 300
    first_row_at = None
    while time.time() < deadline:
        state = db_state(args.db)
        if state["paths"] > baseline:
            first_row_at = round(time.time() - started, 1)
            break
        time.sleep(0.5)
    record["seconds_to_first_row"] = first_row_at
    print("seconds to first row:", first_row_at)
    time.sleep(0.5)
    record["db_at_kill"] = db_state(args.db)
    record["alive_before_kill"] = True
    try:
        os.kill(args.server_pid, signal.SIGTERM)
    except ProcessLookupError:
        record["alive_before_kill"] = False
    killed_at = time.time()
    time.sleep(3.0)
    try:
        os.kill(args.server_pid, 0)
        record["process_gone"] = False
    except ProcessLookupError:
        record["process_gone"] = True
    record["kill_wall_seconds"] = round(time.time() - killed_at, 1)
    print("server signalled; process gone:", record["process_gone"])

    record["db_after_kill"] = db_state(args.db)

    env = server_env(args.db, port, args.corpus)
    log_path = pathlib.Path("/tmp/restart_inject_server.log")
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(SERVER_COMMAND, cwd=str(ROOT), env=env,
                                stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    record["restarted_pid"] = proc.pid
    record["restart_reached_health"] = wait_for(base)
    print("restarted pid", proc.pid, "health", record["restart_reached_health"])

    session, login = sign_in(base, args.password)
    record["login_after_restart"] = login.status_code
    record["paths_after_restart"] = session.get(f"{base}/api/paths?limit=5",
                                                timeout=30).status_code
    jobs = session.get(f"{base}/api/jobs?limit=10", timeout=30)
    record["jobs_after_restart"] = {"status": jobs.status_code, "body": jobs.text[:300]}
    record["db_after_restart"] = db_state(args.db)

    # does anything resume? two samples ten seconds apart
    time.sleep(10.0)
    record["db_after_waiting"] = db_state(args.db)
    record["resumed"] = (record["db_after_waiting"]["paths"]
                         != record["db_after_restart"]["paths"])
    print("paths after restart/wait:",
          record["db_after_restart"]["paths"], "->", record["db_after_waiting"]["paths"],
          "resumed:", record["resumed"])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "restart_mid_job.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"wrote {OUT / 'restart_mid_job.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
