"""Runtime check of scenarios against a running server.

    python tools/verify/runtime_check_scenarios.py <base-url> <state-dir>

Requires a server started against the database from ``runtime_provision.py``
(same ``<state-dir>``). HTTP only - no application imports: what is verified
is the running process, its background job worker included. Responses are
saved under ``<state-dir>/evidence/scenarios_*.json``; the rendered
/monitoring page and the owner's session are saved for
``monitoring_page_runtime.mjs``.

Central checks: activation is refused without a dry-run of the exact
definition; the dry-run's prediction of the notifications on activation is
what the first evaluation then delivers; a re-evaluation records nothing new;
notifications and scenarios are invisible to other users.
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
    ids = info["ids"]
    evidence = state / "evidence"
    evidence.mkdir(exist_ok=True)

    def save(name, body):
        (evidence / f"scenarios_{name}.json").write_text(
            json.dumps(body, indent=2, ensure_ascii=False))

    anon = Client(base)
    status, _, _ = anon.request("GET", "/api/scenarios")
    check(status == 401, f"anonymous GET /api/scenarios -> {status} (401 expected)")

    clients = {}
    for key in ("analyst", "analyst2", "admin", "viewer"):
        c = Client(base)
        username, password = info["users"][key]
        status, _, _ = c.login(username, password)
        check(status == 200 and c.cookie, f"{key} login -> {status}")
        _, raw, _ = c.request("GET", "/api/csrf-token")
        c.csrf = json.loads(raw)["csrf_token"]
        clients[key] = c
    owner = clients["analyst"]

    def call(client, method, path, body=None, **params):
        status, raw, _ = client.request(method, path, body, params or None)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"raw": raw[:200].decode(errors="replace")}

    def run_job(path):
        status, body = call(owner, "POST", path)
        check(status == 202, f"POST {path} -> {status} (202, job)")
        job_id = body["job"]["job_id"]
        deadline = time.time() + 90
        while time.time() < deadline:
            _, job = call(owner, "GET", f"/api/jobs/{job_id}")
            if job.get("job", {}).get("status") in TERMINAL:
                return job["job"]
            time.sleep(0.5)
        return job.get("job", {})

    def definition(notify_existing):
        return {
            "criteria": {"sources": [ids["source_alpha"]]},
            "conditions": {
                "soon": {"signals": {"signal_types": ["date_reference"]},
                         "event_window_days": {"from": 0, "to": 14}},
                "dated": {"signals": {"signal_types": ["date_reference"]}},
            },
            "cases": [{"id": "c_soon", "when": {"all": ["soon"]}, "outcome": "urgent"},
                      {"id": "c_dated", "when": {"all": ["dated"]}, "outcome": "dated"}],
            "outcomes": {"urgent": {"label": "Urgent", "actions": ["notify"]},
                         "dated": {"label": "Dated", "actions": []},
                         "none": {"label": "Nothing dated", "actions": []}},
            "default_outcome": "none", "strategy": "first_match",
            "notify_existing": notify_existing,
        }

    status, _ = call(clients["viewer"], "POST", "/api/scenarios",
                     {"name": "viewer", "definition": definition(False)})
    check(status == 403, f"viewer POST /api/scenarios -> {status} (403 expected)")
    broken = definition(False)
    del broken["default_outcome"]
    status, body = call(owner, "POST", "/api/scenarios/validate", {"definition": broken})
    check(status == 200 and body.get("valid") is False,
          f"a definition without a default outcome is invalid ({body.get('errors')})")

    stamp = int(time.time())
    created = {}
    for key, notify_existing in (("baseline", False), ("notify", True)):
        status, body = call(owner, "POST", "/api/scenarios",
                            {"name": f"runtime {key} {stamp}",
                             "definition": definition(notify_existing)})
        save(f"{key}_created", body)
        check(status == 201 and body["scenario"]["status"] == "draft",
              f"{key} scenario created as a draft -> {status}")
        created[key] = body["scenario"]

    for key, sc in created.items():
        sid = sc["id"]
        status, body = call(owner, "POST", f"/api/scenarios/{sid}/activate")
        check(status == 409 and body["error"]["code"] == "DRY_RUN_REQUIRED",
              f"{key}: activation before a dry-run -> {status} {body.get('error', {}).get('code')}")
        job = run_job(f"/api/scenarios/{sid}/dry-run")
        check(job.get("status") == "COMPLETED", f"{key}: dry-run job -> {job.get('status')}")
        status, runs = call(owner, "GET", f"/api/scenarios/{sid}/dry-runs")
        save(f"{key}_dry_run", runs)
        run = runs["items"][0]
        report = run["report"]
        check(run["status"] == "passed", f"{key}: dry-run passed ({run['status']})")
        check(report["population"] >= 1 and report["snapshot"]["isolation"]
              == "repeatable_read_read_only", f"{key}: population {report['population']} on"
              f" one snapshot {report['snapshot']['id']}")
        check(set(report["case_matches"]) <= {"c_soon", "c_dated"}
              and "estimated_volume" in report and report["sources"]["total"] >= 1,
              f"{key}: report has case matches, sources and volume")
        sc["predicted"] = report["notifications_on_activation"]
        status, outcomes = call(owner, "GET", f"/api/scenarios/{sid}/outcomes")
        check(outcomes["total"] == 0, f"{key}: the dry-run recorded no outcome")
        status, body = call(owner, "POST", f"/api/scenarios/{sid}/activate")
        check(status == 200 and body["scenario"]["status"] == "active",
              f"{key}: activation after the dry-run -> {status}")

    check(created["baseline"]["predicted"]["basis"] == "baseline"
          and created["baseline"]["predicted"]["notifications"] == 0,
          f"baseline scenario predicts no notification ({created['baseline']['predicted']})")
    check(created["notify"]["predicted"]["notifications"] >= 1,
          f"notify_existing scenario predicts notifications ({created['notify']['predicted']})")

    for key, sc in created.items():
        sid = sc["id"]
        job = run_job(f"/api/scenarios/{sid}/evaluate")
        check(job.get("status") == "COMPLETED", f"{key}: evaluation job -> {job.get('status')}")
        status, log = call(owner, "GET", f"/api/scenarios/{sid}/evaluations")
        save(f"{key}_evaluations", log)
        first = log["items"][0]
        check(first["status"] == "completed" and first["owner_role"] == "analyst",
              f"{key}: evaluation completed with the owner's role recorded")
        check(first["counts"].get("notifications", 0) == sc["predicted"]["notifications"],
              f"{key}: delivered {first['counts'].get('notifications', 0)} notifications ="
              f" dry-run prediction {sc['predicted']['notifications']}")
        status, outcomes = call(owner, "GET", f"/api/scenarios/{sid}/outcomes", limit=200)
        save(f"{key}_outcomes", outcomes)
        deliveries = {o["delivery"] for o in outcomes["items"]}
        check(outcomes["total"] >= 1, f"{key}: outcomes recorded ({outcomes['total']})")
        check(deliveries <= ({"baseline"} if key == "baseline"
                             else {"notified", "recorded", "overflow"}),
              f"{key}: deliveries {sorted(deliveries)}")
        check(all(o["path_id"] for o in outcomes["items"]),
              f"{key}: every outcome names a file the viewer may open")
        sc["outcomes_total"] = outcomes["total"]

    notify_id = created["notify"]["id"]
    status, listed = call(owner, "GET", "/api/notifications", type="scenario_outcome", limit=200)
    mine = [n for n in listed["notifications"] if n.get("scenario_id") == notify_id]
    save("owner_notifications", mine)
    check(len(mine) == created["notify"]["predicted"]["notifications"],
          f"owner sees {len(mine)} scenario notifications")
    check(all(n["addressed"] and n["priority"] in ("high", "medium", "low") for n in mine),
          "every scenario notification is addressed, with a derived priority")
    check(all(n["metadata"].get("scenario_evaluation_id") for n in mine),
          "each notification names the evaluation that produced it")
    alert_id = mine[0]["id"] if mine else 0
    for key in ("analyst2", "admin", "viewer"):
        c = clients[key]
        status, _ = call(c, "GET", f"/api/notifications/{alert_id}")
        check(status == 404, f"{key} GET another user's scenario notification -> {status} (404)")
        status, other = call(c, "GET", "/api/notifications", limit=1000)
        check(alert_id not in {n["id"] for n in other.get("notifications", [])},
              f"{key}'s notification list excludes it")
    status, _ = call(clients["analyst2"], "GET", f"/api/scenarios/{notify_id}")
    check(status == 404, f"another analyst GET the scenario -> {status} (404)")
    status, _ = call(clients["analyst2"], "GET", f"/api/scenarios/{notify_id}/outcomes")
    check(status == 404, f"another analyst GET its outcomes -> {status} (404)")
    status, _ = call(clients["admin"], "GET", f"/api/scenarios/{notify_id}")
    check(status == 200, f"administrator GET the scenario -> {status}")
    status, body = call(clients["admin"], "PUT", f"/api/scenarios/{notify_id}", {"name": "x"})
    check(status == 403, f"administrator cannot edit another user's scenario -> {status}")

    # Re-evaluation: nothing changed, so nothing is recorded or notified.
    for key, sc in created.items():
        run_job(f"/api/scenarios/{sc['id']}/evaluate")
        status, log = call(owner, "GET", f"/api/scenarios/{sc['id']}/evaluations")
        again = log["items"][0]
        status, outcomes = call(owner, "GET", f"/api/scenarios/{sc['id']}/outcomes")
        check(again["counts"].get("notifications", 0) == 0
              and outcomes["total"] == sc["outcomes_total"],
              f"{key}: re-evaluation adds nothing ({again['counts']})")

    # A new definition returns the scenario to draft; its dry-run is stale.
    status, body = call(owner, "PUT", f"/api/scenarios/{notify_id}",
                        {"definition": dict(definition(True), strategy="all_matching")})
    check(status == 200 and body["scenario"]["status"] == "draft"
          and body["scenario"]["version"] == 2, "a new definition is version 2, back to draft")
    status, body = call(owner, "POST", f"/api/scenarios/{notify_id}/activate")
    check(status == 409, f"the version-1 dry-run does not activate version 2 -> {status}")
    status, versions = call(owner, "GET", f"/api/scenarios/{notify_id}/versions")
    check([v["version"] for v in versions["versions"]] == [1, 2], "both versions are kept")

    status, raw, _ = owner.request("GET", "/monitoring")
    check(status == 200, f"GET /monitoring as the owner -> {status}")
    (evidence / "monitoring_page.html").write_bytes(raw)
    (state / "monitoring_session.txt").write_text(owner.cookie)
    (evidence / "monitoring_ids.json").write_text(json.dumps(
        {"baseline": created["baseline"]["id"], "notify": notify_id}))
    status, raw, _ = clients["viewer"].request("GET", "/monitoring")
    check(status == 200 and b'id="scenarioNew"' not in raw,
          f"GET /monitoring as a viewer -> {status}, without write controls")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
