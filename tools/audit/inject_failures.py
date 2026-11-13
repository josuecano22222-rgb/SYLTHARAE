#!/usr/bin/env python
"""Stop PostgreSQL in the middle of an ingest and write down what happened.

The point is not to prove the application survives - it is to record, without
interpretation, what a client sees while the database is gone and what state the
work is in once it returns. Every step below is executed against a real server
process (``run_web.py``) talking to a real PostgreSQL cluster started by
``pgserver``; nothing is mocked.

Sequence:

1. sign in and queue a batch import of 400 documents through
   ``POST /api/import-export/batch-import`` - the application's own ingestion
   entry point;
2. two seconds later, stop PostgreSQL with ``pg_ctl stop -m immediate`` as the
   worker is writing (immediate shutdown = the crash case, no clean rollback);
3. poll ``/health``, ``/api/jobs``, ``/api/paths`` and ``/api/reports/overview``
   every second while the database is down, recording status, latency and
   whether the body leaks driver internals;
4. start PostgreSQL again, poll until it accepts connections, then record what
   the same endpoints answer and what state the queued job is in;
5. queue a second import after recovery - if ingestion works again, the outage
   was survived; if it does not, that is the finding.

Usage:
    python tools/audit/inject_failures.py --base-url http://127.0.0.1:5055 \
        --password <admin password> --corpus /tmp/inject_corpus

Writes audit_report/evidence_scale/failure_injection.json.
Exit status is 0 whenever the observations were written; the script reports
findings, it does not assert on them.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "evidence_scale"
PGDATA = "/tmp/sylth_pg_data"
PG_CTL = "/tmp/sylth-venv/lib/python3.11/site-packages/pgserver/pginstall/bin/pg_ctl"

#: A body containing any of these has leaked server internals to the client.
LEAK_MARKERS = ("Traceback (most recent call last)", "psycopg2.", "OperationalError",
                "UndefinedTable", "UndefinedColumn", "psycopg", "connection already closed",
                "SELECT ", "INSERT INTO ")


def now() -> str:
    return time.strftime("%H:%M:%S")


def refresh_csrf(session, base: str) -> str:
    """Re-read the CSRF token bound to the current session cookie.

    Signing in rotates the session, so a token fetched before the sign-in is
    rejected afterwards; the token has to be taken again from the endpoint the
    interface uses (``/api/csrf-token``) before the next write.
    """
    token = session.get(f"{base}/api/csrf-token", timeout=30).json().get("csrf_token", "")
    session.headers["X-CSRFToken"] = token
    return token


def sign_in(base: str, password: str):
    session = requests.Session()
    session.get(f"{base}/auth/login", timeout=30)
    refresh_csrf(session, base)
    resp = session.post(f"{base}/auth/login",
                        json={"username": "admin", "password": password}, timeout=30)
    # A first-run account carries must_change_password; every API answers 403
    # (``password_change_required``) until it is changed, so the harness changes
    # it through the same endpoint the interface uses and signs in again.
    user = (resp.json() or {}).get("user") or {}
    if resp.status_code == 200 and user.get("must_change_password"):
        refresh_csrf(session, base)
        changed_password = password + "-changed"
        change = session.post(f"{base}/auth/change-password",
                              json={"current_password": password,
                                    "new_password": changed_password}, timeout=30)
        print(f"temporary password changed: {change.status_code}")
        session.post(f"{base}/auth/logout", timeout=30)
        session = requests.Session()
        refresh_csrf(session, base)
        resp = session.post(f"{base}/auth/login",
                            json={"username": "admin",
                                  "password": changed_password}, timeout=30)
    refresh_csrf(session, base)
    session.password = password + "-changed" if user.get("must_change_password") else password
    session.must_change = bool(user.get("must_change_password"))
    return session, resp


def probe(session, base, path, timeout=10):
    started = time.perf_counter()
    try:
        resp = session.get(base + path, timeout=timeout)
        elapsed = (time.perf_counter() - started) * 1000.0
        body = resp.text or ""
        return {
            "at": now(),
            "path": path,
            "status": resp.status_code,
            "ms": round(elapsed, 1),
            "bytes": len(body),
            "leak": [m for m in LEAK_MARKERS if m in body],
            "body_head": " ".join(body.split())[:220],
        }
    except Exception as exc:  # the client itself fails when the server is wedged
        return {
            "at": now(),
            "path": path,
            "status": None,
            "ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "leak": [],
            "body_head": "",
        }


def pg(action: str) -> dict:
    cmd = [PG_CTL, "-D", PGDATA]
    if action == "stop":
        cmd += ["-m", "immediate", "stop"]
    else:
        cmd += ["-o", "-h '' -k /tmp/sylth_pg_data", "-l", "/tmp/pg_fi_server.log", "start"]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return {"action": action, "rc": proc.returncode,
            "out": (proc.stdout + proc.stderr).strip()[:400],
            "seconds": round(time.time() - started, 1)}


def db_snapshot(name: str) -> dict:
    """What the database says about the interrupted work, read directly.

    The point is to compare what the API showed the client with what was
    actually committed, so both are recorded.
    """
    import psycopg2

    snapshot: dict = {"database": name}
    try:
        conn = psycopg2.connect(host=os.environ.get("DB_HOST", "/tmp/sylth_pg_data"),
                                port=int(os.environ.get("DB_PORT") or 5432),
                                user=os.environ.get("DB_USER", "postgres"),
                                password=os.environ.get("DB_PASSWORD", ""),
                                dbname=name)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM paths")
            snapshot["paths"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM hashs")
            snapshot["hashs"] = cur.fetchone()[0]
            cur.execute("SELECT job_id, job_type, status, progress, current_phase,"
                        " started_at, completed_at, errors FROM jobs"
                        " ORDER BY created_at DESC LIMIT 5")
            snapshot["jobs"] = [
                {"job_id": row[0], "type": row[1], "status": row[2], "progress": row[3],
                 "phase": row[4], "started": str(row[5]), "completed": str(row[6]),
                 "errors": str(row[7])[:300]}
                for row in cur.fetchall()
            ]
            cur.execute("SELECT status, count(*) FROM jobs GROUP BY status")
            snapshot["job_status_counts"] = dict(cur.fetchall())
            cur.execute("SELECT count(*) FROM paths WHERE processing_status "
                        "NOT IN ('processed')")
            snapshot["paths_not_processed"] = cur.fetchone()[0]
        conn.close()
    except Exception as exc:
        snapshot["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--password", required=True,
                        help="admin password; when the account still carries a "
                             "first-run temporary password the harness changes it "
                             "to <password>-changed through /auth/change-password")
    parser.add_argument("--corpus", default="/tmp/inject_corpus")
    parser.add_argument("--db", default="file_analysis_failinject",
                        help="database name, for the state snapshot at the end")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    files = sorted(str(p) for p in pathlib.Path(args.corpus).iterdir() if p.is_file())
    record: dict = {"base_url": base, "corpus_files": len(files), "steps": []}

    session, login = sign_in(base, args.password)
    record["login"] = {"status": login.status_code, "body": login.text[:200]}
    if login.status_code != 200:
        print("login failed:", login.status_code, login.text[:300])
        return 1

    source = session.post(f"{base}/api/sources", json={"name": f"failure injection source {int(time.time())}"},
                          timeout=30).json()
    side = session.post(f"{base}/api/sides", json={"name": f"failure injection side {int(time.time())}"},
                        timeout=30).json()
    source_id = (source.get("source") or source).get("id")
    side_id = (side.get("side") or side).get("id")
    if not source_id or not side_id:
        print(f"fixture creation failed: source={source} side={side}")
        return 1
    record["fixtures"] = {"source_id": source_id, "side_id": side_id}

    started = time.time()
    queued = session.post(f"{base}/api/import-export/batch-import",
                          json={"file_paths": files, "source_id": source_id,
                                "side_id": side_id}, timeout=120)
    record["import_request"] = {"status": queued.status_code,
                                "ms": round((time.time() - started) * 1000, 1),
                                "body": queued.text[:400]}
    print(f"queued {len(files)} files: {queued.status_code}")

    time.sleep(2.0)
    record["before_outage"] = [probe(session, base, p) for p in
                               ("/api/jobs?limit=5", "/api/paths?limit=5")]

    record["steps"].append(pg("stop"))
    print("postgres stopped:", record["steps"][-1]["out"].replace("\n", " ")[:120])

    during = []
    for _ in range(15):
        during.append(probe(session, base, "/health", timeout=15))
        during.append(probe(session, base, "/api/jobs?limit=5", timeout=15))
        during.append(probe(session, base, "/api/paths?limit=5", timeout=15))
        during.append(probe(session, base, "/api/reports/overview", timeout=15))
        time.sleep(1.0)
    record["during_outage"] = during

    record["steps"].append(pg("start"))
    print("postgres started:", record["steps"][-1]["out"].replace("\n", " ")[:120])

    ready = None
    for _ in range(60):
        attempt = probe(session, base, "/health", timeout=10)
        if attempt.get("status") == 200:
            ready = attempt
            break
        time.sleep(1.0)
    record["recovery_health"] = ready or {"status": None, "note": "health never returned 200"}
    print("health after restart:", (ready or {}).get("status"))

    recovery_note = None
    if (ready or {}).get("status") == 200:
        session2, login2 = sign_in(base, session.password)
        recovery_note = {"login_status": login2.status_code,
                         "body": login2.text[:200]}
        session = session2
    record["recovery_login"] = recovery_note

    after = [probe(session, base, p) for p in
             ("/api/jobs?limit=5", "/api/paths?limit=5", "/api/reports/overview",
              "/api/signals?limit=1")]
    record["after_recovery"] = after
    for item in after:
        print(f"  {item['status']} {item['ms']:7.1f} ms {item['path']}")

    jobs = next((i for i in after if i["path"].startswith("/api/jobs")), {})
    try:
        payload = json.loads(next(i for i in after if i["path"].startswith("/api/jobs"))["body_head"])
    except Exception:
        payload = {}
    record["job_state_after_recovery"] = payload

    # a second import: does ingestion still work at all?
    second = session.post(f"{base}/api/import-export/batch-import",
                          json={"file_paths": files[:20], "source_id": source_id,
                                "side_id": side_id}, timeout=120)
    record["import_after_recovery"] = {"status": second.status_code,
                                       "body": second.text[:400]}
    print("second import:", second.status_code)

    time.sleep(8)
    listing = probe(session, base, "/api/paths?limit=1")
    record["paths_after_second_import"] = listing
    print("paths listing after second import:", listing["status"], listing["body_head"][:120])

    record["db_snapshot"] = db_snapshot(args.db)
    print("db snapshot:", json.dumps(record["db_snapshot"])[:300])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "failure_injection.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"wrote {OUT / 'failure_injection.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
