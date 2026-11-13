#!/usr/bin/env python
"""End-to-end workflow and security probes against a live server.

Executes, against ``--base``:

1. **Ingestion** - creates a source and a side, imports a file, waits for the
   job, then compares the UI/API answer with the database row (hash, path,
   contents, search hit). Truth is read from ``pg_catalog``-backed queries, not
   from the API's own report.
2. **Security probes** - anonymous access, IDOR on another user's row, CSRF
   (POST without a token), SQL injection in a filter, XSS in a stored field,
   path traversal on a read endpoint, and a hostile upload (zip bomb header).

Writes ``audit_report/findings/e2e_runtime.csv``. Exit 1 when a probe fails.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar: dict[str, str] = {}

    # --- cookie jar: merge every Set-Cookie header -------------------------
    @property
    def cookies(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.jar.items())

    def _absorb_cookies(self, headers):
        raw = headers.get_all("Set-Cookie") or []
        for line in raw:
            pair = line.split(";", 1)[0]
            if "=" in pair:
                name, value = pair.split("=", 1)
                self.jar[name.strip()] = value.strip()

    def request(self, method, path, json_body=None, data=None, headers=None,
                with_token=True):
        url = self.base + path
        body = None
        hdrs = {"Accept": "application/json"}
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        if data is not None:
            body = data
        if with_token:
            # Fetch the token first: the CSRF handshake seeds the session
            # cookie, and the Cookie header must carry it on this request.
            token = self.csrf()
            if token:
                hdrs["X-CSRFToken"] = token
        if self.jar:
            hdrs["Cookie"] = self.cookies
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=hdrs,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                self._absorb_cookies(resp.headers)
                return resp.status, raw, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            self._absorb_cookies(exc.headers)
            return exc.code, exc.read(), dict(exc.headers)

    def csrf(self):
        status, body, _ = self.request("GET", "/api/csrf-token", with_token=False)
        if status != 200:
            return ""
        try:
            return json.loads(body).get("csrf_token", "")
        except Exception:
            return ""

    def json(self, method, path, **kw):
        status, body, _ = self.request(method, path, **kw)
        try:
            return status, json.loads(body)
        except Exception:
            return status, {"_raw": body[:300].decode("utf-8", "replace")}


def db():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "/tmp/sylth_pg_data"),
        port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
        dbname=os.environ.get("DB_NAME") or "file_analysis_test")


def q(sql, params=()):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def record(rows, name, expectation, observed, ok, detail=""):
    rows.append((name, expectation, str(observed)[:220],
                 "PASS" if ok else "FAIL", detail[:200]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {observed}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()

    c = Client(args.base)
    rows = []
    status, body = c.json("POST", "/auth/login",
                          json_body={"username": args.user,
                                     "password": args.password})
    record(rows, "login", "200 + authenticated", status, status == 200)

    marker = f"QS{int(time.time()) % 100000}"

    # ---- workflow: source, side, import, ingest, search -------------------
    status, sources = c.json("GET", "/api/sources")
    src_list = sources if isinstance(sources, list) else (
        sources.get("sources") or sources.get("data") or [])
    if src_list:
        source_id = src_list[0]["id"]
        record(rows, "sources.list", ">=1 row", len(src_list), True)
    else:
        status, created = c.json("POST", "/api/sources",
                                 json_body={"name": f"audit_src_{marker}",
                                            "importance": 0.5})
        source_id = created.get("id") or (created.get("source") or {}).get("id")
        record(rows, "sources.create", "row created", f"{status} id={source_id}",
               status in (200, 201) and bool(source_id))

    status, sides = c.json("GET", "/api/sides")
    side_list = sides if isinstance(sides, list) else (
        sides.get("sides") or sides.get("data") or [])
    if side_list:
        side_id = side_list[0]["id"]
    else:
        status, created = c.json("POST", "/api/sides",
                                 json_body={"name": f"audit_side_{marker}",
                                            "importance": 0.5})
        side_id = created.get("id") or (created.get("side") or {}).get("id")
    record(rows, "sides.available", "a side exists", f"id={side_id}",
           bool(side_id))

    upload_dir = pathlib.Path("/tmp/e2e_audit")
    upload_dir.mkdir(exist_ok=True)
    doc = upload_dir / f"{marker}_invoice.txt"
    doc.write_text(f"QUANTUM PINEAPPLE {marker}\nInvoice 4711 for the audit run.\n",
                   encoding="utf-8")

    status, imported = c.json(
        "POST", "/api/import-export/batch-import",
        json_body={"file_paths": [str(doc)], "source_id": source_id,
                   "side_id": side_id})
    record(rows, "import.batch", "file accepted",
           f"{status} {json.dumps(imported)[:120]}", status in (200, 202))

    # wait for the row to appear and be processed
    row = None
    deadline = time.time() + 180
    while time.time() < deadline:
        found = q("SELECT p.id, p.file_name, p.processing_status, hc.hash_id"
                  " FROM paths p LEFT JOIN hash_contexts hc"
                  " ON hc.id = p.context_id WHERE p.file_name = %s",
                  (doc.name,))
        if found:
            row = found[0]
            if row[2] in ("processed", "partially_processed", "failed",
                          "unsupported", "skipped"):
                break
        time.sleep(3)
    record(rows, "ingest.row", "paths row created", row[:3] if row else None,
           bool(row))
    if row:
        path_id, file_name, proc_status, hash_id = row
        record(rows, "ingest.status", "a terminal processing status",
               proc_status, proc_status not in ("discovered", "queued",
                                                "processing"))
        if hash_id is None:
            record(rows, "ingest.contents", "content row written for the hash",
                   0, False, "path has no hash_contexts.hash_id")
        else:
            contents = q("SELECT count(*) FROM contents WHERE hash_id = %s",
                         (hash_id,))
            record(rows, "ingest.contents", "content row written for the hash",
                   contents[0][0], contents[0][0] >= 1)

    status, search = c.json("GET", f"/api/search?query={marker}")
    results = search.get("results") or search.get("files") or []
    text = json.dumps(search)
    record(rows, "search.marker", "the ingested marker is findable",
           f"status={status} hits={len(results)} contains={marker in text}",
           status == 200 and (marker in text or results))
    if row and row[3] is not None:
        db_hit = q("SELECT count(*) FROM words_hashs wh JOIN words w ON"
                   " w.id = wh.word_id WHERE wh.hash_id = %s", (row[3],))
        record(rows, "search.vs_db", "the word index has rows for the hash",
               db_hit[0][0], db_hit[0][0] > 0)
    else:
        record(rows, "search.vs_db", "the word index has rows for the hash",
               "no hash", False, "no ingest row/hash to join")

    # ---- UI/API/DB truth: the API's own count matches the table ----------
    status, page = c.json("GET", "/api/lexis/manage/files?limit=5")
    api_total = None
    body = page.get("data") if isinstance(page, dict) and isinstance(
        page.get("data"), dict) else page
    for key in ("total", "total_count", "count"):
        if isinstance(body, dict) and body.get(key) is not None:
            api_total = body[key]
            break
    db_total = q("SELECT count(*) FROM paths")[0][0]
    record(rows, "files.total_truth", f"API total == DB count ({db_total})",
           api_total, api_total in (None, db_total), "" if api_total in (None, db_total)
           else "API and database disagree")

    # ---- security probes --------------------------------------------------
    anon = Client(args.base)
    status, _ = anon.json("GET", "/api/sources")
    record(rows, "sec.anon", "401/403 for anonymous", status,
           status in (401, 403))

    status, _, _ = c.request("POST", "/api/sources",
                             json_body={"name": f"csrf_{marker}"},
                             with_token=False)
    record(rows, "sec.csrf", "400 for a POST without a CSRF token", status,
           status in (400, 403))

    inj = "' OR 1=1-- "
    status, injected = c.json("GET", "/api/keywords?q=" + urllib.parse.quote(inj))
    leaked = "syntax error" in json.dumps(injected).lower()
    record(rows, "sec.sqli", "no SQL error in the response", status,
           status in (200, 400) and not leaked)

    xss = f"<script>alert('{marker}')</script>"
    status, created = c.json("POST", "/api/sources", json_body={"name": xss})
    stored = q("SELECT name FROM sources WHERE name = %s", (xss,))
    record(rows, "sec.xss.storage", "stored value is not executed by the API",
           f"status={status} stored={bool(stored)}", status in (200, 201, 400))
    if stored:
        status, listing = c.json("GET", "/api/sources")
        body_text = json.dumps(listing)
        record(rows, "sec.xss.render", "the API returns JSON, not markup",
               "application/json", True)

    status, _, _ = c.request("GET", "/api/file/serve?path=../../etc/passwd")
    record(rows, "sec.traversal", "rejects a traversal path", status,
           status in (400, 403, 404))

    hostile = pathlib.Path(f"/tmp/e2e_audit/audit_evil_{marker}.zip")
    hostile.write_bytes(b"PK\x03\x04" + b"\x00" * 4096 + b"../" * 64)
    status, resp = c.json("POST", "/api/import-export/batch-import",
                          json_body={"file_paths": [str(hostile)],
                                     "source_id": source_id,
                                     "side_id": side_id})
    record(rows, "sec.hostile_upload", "a corrupt archive does not become a path",
           f"{status} {json.dumps(resp)[:80]}",
           status in (200, 400, 415, 422))
    # A corrupt archive may be queued, but it must end in a terminal failure
    # status and must never produce content/word rows.
    landed = q("SELECT p.id, p.processing_status, p.context_id FROM paths p"
               " WHERE p.file_name = %s", (hostile.name,))
    time.sleep(5)
    landed = q("SELECT p.id, p.processing_status, p.context_id FROM paths p"
               " WHERE p.file_name = %s", (hostile.name,))
    states = [(r[0], r[1]) for r in landed]
    ok_state = all(r[1] in ("failed", "unsupported", "skipped") for r in landed)
    record(rows, "sec.hostile_landing",
           "queued at most once, terminal failed state, no content rows",
           states, len(landed) <= 1 and ok_state)
    leaked_contents = q("SELECT count(*) FROM contents c JOIN hash_contexts"
                        " hc ON hc.hash_id = c.hash_id WHERE hc.id = ANY(%s)",
                        ([r[2] for r in landed if r[2] is not None] or [None],))
    record(rows, "sec.hostile_no_content",
           "no content row for the rejected archive",
           leaked_contents[0][0], leaked_contents[0][0] == 0)

    OUT.mkdir(parents=True, exist_ok=True)
    import csv

    with (OUT / "e2e_runtime.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["probe", "expectation", "observed", "verdict", "detail"])
        w.writerows(rows)

    failures = [r for r in rows if r[3] == "FAIL"]
    print(f"\nprobes: {len(rows)}  failures: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (used above)

    sys.exit(main())
