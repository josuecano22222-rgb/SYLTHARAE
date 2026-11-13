"""Runtime check of the Detection page (coverage, run history, re-detection)
against a running server.

    python tools/verify/runtime_check_detection.py <base-url> <state-dir>

HTTP only (no application imports), against a server started on the
database from ``runtime_provision.py`` (documents stored through the real
ingestion path, so their runs were written by ingestion).

Checks: 401 before sign-in; analysts and viewers refused (page, API and the
re-detect POST); the page renders with the production CSP and the page
module; the coverage is one exact snapshot whose per-detector buckets add up;
every ingested document has a run per detector naming its file; filters
narrow on the server and invalid filters are 400 naming the parameter;
paging with limit 1 returns each run exactly once; a ``stale`` re-detection
for temporal processes exactly the stale count the page showed and leaves
nothing stale; a listed-ids re-detection rewrites that content's run with
trigger ``redetection`` and the job's id; each re-detection is in the audit
log as ``signals.redetect`` with its scope, detectors and id count.
Saves the page HTML and the admin session for ``detection_page_runtime.mjs``.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_check_signals import Client, check, failures  # noqa: E402

TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}


def main(base, state_dir):
    state = Path(state_dir)
    info = json.loads((state / "env.json").read_text())
    hashes = {k[5:]: v for k, v in info["ids"].items() if k.startswith("hash_")}
    check(len(hashes) >= 2, f"provisioned documents: {sorted(hashes)}")

    anon = Client(base)
    for path in ("/api/signals/detection/status", "/api/signals/detection/runs"):
        status, _, _ = anon.request("GET", path)
        check(status == 401, f"anonymous GET {path} -> {status}")

    clients = {}
    for key in ("admin", "analyst", "viewer"):
        c = Client(base)
        username, password = info["users"][key]
        status, _, _ = c.login(username, password)
        check(status == 200 and c.cookie, f"{key} login -> {status}")
        # The session rotates at sign-in: fetch its CSRF token, as the next page does.
        _, raw, _ = c.request("GET", "/api/csrf-token")
        c.csrf = json.loads(raw)["csrf_token"]
        clients[key] = c
    admin = clients["admin"]

    def call(client, method, path, body=None, **params):
        status, raw, headers = client.request(method, path, body, params or None)
        try:
            return status, json.loads(raw), headers
        except ValueError:
            return status, {"raw": raw[:200].decode("utf-8", "replace")}, headers

    for key in ("analyst", "viewer"):
        for path in ("/api/signals/detection/status", "/api/signals/detection/runs"):
            status, _, _ = call(clients[key], "GET", path)
            check(status == 403, f"{key} GET {path} -> {status}")
        status, _, _ = clients[key].request("GET", "/signals/detection")
        check(status in (302, 403), f"{key} GET /signals/detection -> {status}")
    status, _, _ = call(clients["analyst"], "POST", "/api/signals/redetect", {"scope": "stale"})
    check(status == 403, f"analyst POST /api/signals/redetect -> {status}")
    no_csrf = Client(base)
    no_csrf.cookie = admin.cookie
    status, _, _ = call(no_csrf, "POST", "/api/signals/redetect", {"scope": "stale"})
    check(status == 400, f"admin POST without CSRF token -> {status} (refused)")

    status, raw, headers = admin.request("GET", "/signals/detection")
    html = raw.decode("utf-8", "replace")
    csp = headers.get("Content-Security-Policy", "")
    check(status == 200, f"admin GET /signals/detection -> {status}")
    check("detection-page.js" in html and 'id="detection-page-labels"' in html,
          "page carries the module and its labels")
    check(csp and "unsafe-inline" not in csp.split("script-src", 1)[-1].split(";", 1)[0],
          "production CSP without unsafe-inline scripts")
    (state / "evidence").mkdir(parents=True, exist_ok=True)
    (state / "evidence" / "detection_page.html").write_text(html, encoding="utf-8")
    (state / "detection_session.txt").write_text(admin.cookie, encoding="utf-8")

    # ---- coverage
    status, cov, _ = call(admin, "GET", "/api/signals/detection/status")
    check(status == 200 and cov.get("exact") is True and cov.get("snapshot"),
          f"status -> {status}, exact snapshot {cov.get('snapshot')}")
    dets = {d["detector"]: d for d in cov.get("detectors", [])}
    check(set(dets) == {"temporal", "places"}, f"detectors {sorted(dets)}")
    for name, d in dets.items():
        runs = sum(d["at_current_version"].values()) + sum(d["at_older_versions"].values())
        check(runs + d["never_analysed"] >= cov["analysable_contents"]
              and d["never_analysed"] <= d["stale"] <= cov["analysable_contents"],
              f"{name}: runs {runs} + never {d['never_analysed']} cover"
              f" {cov['analysable_contents']} analysable; stale {d['stale']}")
        check(d["current_version_available"] == (d["current_version"] is not None),
              f"{name}: current version {d['current_version']}")
    print("coverage:", json.dumps({n: {"current": d["current_version"], "stale": d["stale"],
                                       "never": d["never_analysed"]} for n, d in dets.items()}))

    # ---- runs: every ingested document has a run per detector, naming its file
    for key, hash_id in hashes.items():
        status, page, _ = call(admin, "GET", "/api/signals/detection/runs", hash_id=str(hash_id))
        items = page.get("items", [])
        check(status == 200 and {r["detector"] for r in items} == {"temporal", "places"},
              f"{key}: runs for {sorted(r['detector'] for r in items)}")
        check(all(r["file_name"] == f"{key}.txt" and r["path_id"] for r in items),
              f"{key}: runs name {sorted({r['file_name'] for r in items})}")
    status, page, _ = call(admin, "GET", "/api/signals/detection/runs", detector="places")
    check(status == 200 and page["items"] and all(r["detector"] == "places" for r in page["items"]),
          "detector filter narrows on the server")
    for params, needle in (({"detector": "nope"}, "detector"), ({"status": "gone"}, "status"),
                           ({"version": "current"}, "detector"), ({"limit": "0"}, "limit"),
                           ({"offset": "-1"}, "offset"), ({"x": "1"}, "x")):
        status, body, _ = call(admin, "GET", "/api/signals/detection/runs", **params)
        msg = (body.get("error") or {}).get("message", "")
        check(status == 400 and needle in msg, f"runs {params} -> {status} {msg!r}")

    status, full, _ = call(admin, "GET", "/api/signals/detection/runs", limit="200")
    seen, offset = [], 0
    while True:
        status, page, _ = call(admin, "GET", "/api/signals/detection/runs", limit="1",
                               offset=str(offset))
        seen += [(r["hash_id"], r["detector"]) for r in page.get("items", [])]
        if not page.get("has_more"):
            break
        offset += 1
    want = [(r["hash_id"], r["detector"]) for r in full.get("items", [])]
    check(seen == want and not full.get("has_more"),
          f"paging by 1 returned {len(seen)} runs, each once, in list order")

    # ---- re-detection: stale for temporal processes exactly the stale count
    def run_job(body):
        status, resp, _ = call(admin, "POST", "/api/signals/redetect", body)
        job = resp.get("job") or {}
        check(status == 202 and job.get("job_id"), f"POST redetect {body} -> {status}")
        deadline = time.time() + 120
        while time.time() < deadline:
            status, resp, _ = call(admin, "GET", f"/api/jobs/{job['job_id']}")
            job = resp.get("job") or {}
            if job.get("status") in TERMINAL:
                break
            time.sleep(0.5)
        check(job.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS"),
              f"job {job.get('job_id')} -> {job.get('status')} {job.get('result_summary')}")
        return job

    # Make work for re-detection. Ingestion just ran at the current version,
    # so nothing is stale; put the database in the state an upgrade leaves:
    # one content analysed by an older temporal release, one never analysed.
    # (The check's only direct database write; a fixture step, no app code.)
    import psycopg2

    env = info["env"]
    db = psycopg2.connect(host=env["DB_HOST"], port=int(env["DB_PORT"]), user=env["DB_USER"],
                          password=env["DB_PASSWORD"] or None, dbname=env["DB_NAME"])
    (older_key, older_hash), (never_key, never_hash) = sorted(hashes.items())[:2]
    with db, db.cursor() as cur:
        cur.execute("UPDATE content_signal_runs SET detector_ver = 'temporal-0.9-runtime-old'"
                    " WHERE hash_id = %s AND detector = 'temporal'", (older_hash,))
        cur.execute("DELETE FROM content_signal_runs WHERE hash_id = %s AND detector = 'temporal'",
                    (never_hash,))
    db.close()
    status, cov, _ = call(admin, "GET", "/api/signals/detection/status")
    t1 = next(d for d in cov["detectors"] if d["detector"] == "temporal")
    check(t1["never_analysed"] == 1 and t1["at_older_versions"]["complete"] == 1 and t1["stale"] == 2,
          f"after an upgrade: temporal never {t1['never_analysed']}, older"
          f" {t1['at_older_versions']['complete']}, stale {t1['stale']}")
    status, page, _ = call(admin, "GET", "/api/signals/detection/runs", detector="temporal",
                           version="older")
    check([r["hash_id"] for r in page.get("items", [])] == [older_hash]
          and page["items"][0]["is_current_version"] is False,
          f"version=older lists exactly {older_key}")
    stale_before = t1["stale"]
    job = run_job({"scope": "stale", "detectors": ["temporal"]})
    processed = (job.get("result_summary") or {}).get("processed")
    check(processed == stale_before, f"stale job processed {processed} == shown stale {stale_before}")
    status, cov2, _ = call(admin, "GET", "/api/signals/detection/status")
    t2 = next(d for d in cov2["detectors"] if d["detector"] == "temporal")
    check(t2["stale"] == 0 and t2["never_analysed"] == 0, f"temporal stale after job: {t2['stale']}")

    key, hash_id = sorted(hashes.items())[0]
    job2 = run_job({"scope": "hash_ids", "hash_ids": [hash_id], "detectors": ["temporal"]})
    status, page, _ = call(admin, "GET", "/api/signals/detection/runs", hash_id=str(hash_id),
                           detector="temporal")
    [run] = page["items"] or [{}]
    check(run.get("trigger") == "redetection" and run.get("job_id") == job2.get("job_id")
          and run.get("detector_ver") == t2["current_version"],
          f"{key}: run rewritten by job {run.get('job_id')} ({run.get('trigger')})")

    status, audit, _ = call(admin, "GET", "/api/audit", action="signals.redetect", limit="5")
    details = [e.get("detail") or {} for e in audit.get("items", [])]
    check(status == 200 and len(details) >= 2, f"audit signals.redetect entries: {len(details)}")
    if len(details) >= 2:
        check(details[0] == {"scope": "hash_ids", "detectors": ["temporal"], "hash_id_count": 1},
              f"newest audit detail {details[0]}")
        check(details[1] == {"scope": "stale", "detectors": ["temporal"], "hash_id_count": None},
              f"previous audit detail {details[1]}")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
