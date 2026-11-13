"""Step 12 integration: the scenarios API over HTTP.

Real users logged in over HTTP, PostgreSQL, the real ingestion path, the
scenario_dry_run / scenario_evaluation jobs (run synchronously). Properties:
activation is refused until a dry-run of the exact current definition has
passed; a scenario is visible to its owner and administrators only, and
editable by its owner only; its notifications reach its owner only; every
change is audited.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]
_N = iter(range(10_000))


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _post(c, url, payload=None):
    return c.post(url, json=payload if payload is not None else {},
                  headers={"X-CSRFToken": _csrf(c)})


def _put(c, url, payload):
    return c.put(url, json=payload, headers={"X-CSRFToken": _csrf(c)})


def _delete(c, url):
    return c.delete(url, headers={"X-CSRFToken": _csrf(c)})


@pytest.fixture()
def sync_jobs(monkeypatch):
    from services.jobs.manager import JobManager

    sync = JobManager(synchronous=True)
    monkeypatch.setattr(JobManager, "get_instance", classmethod(lambda cls: sync))
    return sync


@pytest.fixture(scope="module")
def tenant(pg_db):
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                    " CURRENT_DATE) RETURNING id", (f"sa_side_{_U}",))
        side = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id", (f"sa_src_{_U}",))
        source = cur.fetchone()[0]
    conn.close()
    return {"source_id": source, "side_id": side, "pg_db": pg_db}


def _store(tenant, text):
    from database.services.contents_db_service import ContentDBService

    marker = f"{_U}sa{next(_N)}"
    return ContentDBService().process_full_document(
        hash_value=hashlib.sha256(marker.encode()).hexdigest(), source_id=tenant["source_id"],
        side_id=tenant["side_id"], file_name=f"{marker}.txt", file_path=f"/tmp/sa/{marker}.txt",
        file_size=100, file_type="txt", file_status="Read", file_date=date(2026, 1, 1),
        content_words=["sa", marker], raw_text=text, attempts=1)["hash_id"]


def _login_new(app, role):
    from core.security.service import get_auth_service

    username, password = f"sa_{role}_{uuid.uuid4().hex[:6]}", f"sa-{role}-password-123"
    get_auth_service().create_user(username, password, role=role)
    c = app.test_client()
    assert c.post("/auth/login", json={"username": username,
                                       "password": password}).status_code == 200
    return c


def _definition(tenant, **extra):
    d = {
        "criteria": {"sources": [tenant["source_id"]]},
        "conditions": {"dated": {"signals": {"signal_types": ["date_reference"]}}},
        "cases": [{"id": "c_dated", "when": {"all": ["dated"]}, "outcome": "dated"}],
        "outcomes": {"dated": {"label": "Dated", "actions": ["notify"]},
                     "none": {"label": "Nothing", "actions": []}},
        "default_outcome": "none",
        "strategy": "first_match",
    }
    d.update(extra)
    return d


def _create(client, tenant, **extra):
    resp = _post(client, "/api/scenarios", {"name": f"sc {uuid.uuid4().hex[:6]}",
                                            "definition": _definition(tenant, **extra)})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scenario"]


def _dry_run(client, sid):
    resp = _post(client, f"/api/scenarios/{sid}/dry-run")
    job = resp.get_json()["job"]
    assert resp.status_code == 200 and job["status"] == "COMPLETED", job
    runs = client.get(f"/api/scenarios/{sid}/dry-runs").get_json()["items"]
    return runs[0]


@pytest.fixture()
def active_scenario(app, tenant, sync_jobs):
    """An analyst's scenario that has passed its dry-run, been activated,
    baselined, and delivered one notification for a new content."""
    owner = _login_new(app, "analyst")
    sc = _create(owner, tenant)
    run = _dry_run(owner, sc["id"])
    assert run["status"] == "passed", run
    resp = _post(owner, f"/api/scenarios/{sc['id']}/activate")
    assert resp.status_code == 200, resp.get_json()
    assert _post(owner, f"/api/scenarios/{sc['id']}/evaluate").status_code == 200   # baseline
    hid = _store(tenant, f"The board meets on {next(_N) % 20 + 1} December 2026.")
    resp = _post(owner, f"/api/scenarios/{sc['id']}/evaluate")
    assert resp.status_code == 200 and resp.get_json()["job"]["status"] == "COMPLETED"
    listed = owner.get("/api/notifications?type=scenario_outcome").get_json()["notifications"]
    mine = [n for n in listed if n.get("scenario_id") == sc["id"]]
    assert len(mine) == 1 and mine[0]["addressed"] is True, listed
    return owner, sc, mine[0], hid


def test_activation_requires_a_passed_dry_run_of_the_current_definition(app, tenant, sync_jobs):
    owner = _login_new(app, "analyst")
    sc = _create(owner, tenant)
    assert sc["status"] == "draft" and sc["version"] == 1
    resp = _post(owner, f"/api/scenarios/{sc['id']}/activate")
    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "DRY_RUN_REQUIRED"
    # Evaluation of a draft is refused outright.
    assert _post(owner, f"/api/scenarios/{sc['id']}/evaluate").status_code == 409

    run = _dry_run(owner, sc["id"])
    assert run["status"] == "passed" and run["kind"] == "dry_run"
    report = run["report"]
    # The directive's dry-run contents: match count, notification count,
    # categories, sources, volume, validation.
    for key in ("population", "case_matches", "notifications_on_activation", "categories",
                "sources", "estimated_volume", "validation"):
        assert key in report, report.keys()

    # Changing the definition makes that dry-run stale.
    resp = _put(owner, f"/api/scenarios/{sc['id']}",
                {"definition": _definition(tenant, strategy="all_matching")})
    assert resp.status_code == 200 and resp.get_json()["scenario"]["version"] == 2
    resp = _post(owner, f"/api/scenarios/{sc['id']}/activate")
    assert resp.status_code == 409 and resp.get_json()["error"]["code"] == "DRY_RUN_REQUIRED"
    assert _dry_run(owner, sc["id"])["scenario_version"] == 2
    resp = _post(owner, f"/api/scenarios/{sc['id']}/activate")
    assert resp.status_code == 200 and resp.get_json()["scenario"]["status"] == "active"

    versions = owner.get(f"/api/scenarios/{sc['id']}/versions").get_json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]          # ascending, as for rules


def test_scenario_notifications_reach_only_their_owner(app, admin_client, active_scenario):
    owner, sc, alert, hid = active_scenario
    other = _login_new(app, "analyst")
    for c in (other, admin_client):
        listed = c.get("/api/notifications?type=scenario_outcome").get_json()["notifications"]
        assert all(n["id"] != alert["id"] for n in listed)
    # The outcome log carries the content and its outcome.
    items = owner.get(f"/api/scenarios/{sc['id']}/outcomes?hash_id={hid}").get_json()["items"]
    assert [i["outcomes"] for i in items] == [["dated"]]
    assert items[0]["delivery"] == "notified"
    # A link target the viewer may open: one file occurrence of the content.
    assert items[0]["path_id"] and items[0]["file_name"].endswith(".txt")


def test_visibility_and_edit_rights(app, admin_client, viewer_client, active_scenario, tenant):
    owner, sc, _, _ = active_scenario
    other = _login_new(app, "analyst")
    url = f"/api/scenarios/{sc['id']}"
    # Another analyst: indistinguishable from absent.
    for path in ("", "/outcomes", "/evaluations", "/dry-runs", "/versions"):
        assert other.get(url + path).status_code == 404, path
    assert _post(other, url + "/pause").status_code == 404
    assert all(s["id"] != sc["id"]
               for s in other.get("/api/scenarios").get_json()["scenarios"])
    # Viewers cannot write at all.
    assert _post(viewer_client, "/api/scenarios",
                 {"name": "v", "definition": _definition(tenant)}).status_code == 403
    assert viewer_client.get("/api/scenarios?all=1").status_code == 403
    # Admin sees and can pause, but cannot edit someone else's definition.
    assert admin_client.get(url).status_code == 200
    assert any(s["id"] == sc["id"]
               for s in admin_client.get("/api/scenarios?all=1").get_json()["scenarios"])
    resp = _put(admin_client, url, {"name": "hijack"})
    assert resp.status_code in (403, 404), resp.get_json()
    assert _post(admin_client, url + "/pause").get_json()["scenario"]["status"] == "paused"
    assert _post(owner, url + "/resume").get_json()["scenario"]["status"] == "active"
    assert _delete(owner, url).get_json()["scenario"]["status"] == "archived"
    assert _post(owner, url + "/dry-run").status_code == 409


def test_validation_is_explicit(app, tenant):
    owner = _login_new(app, "analyst")
    ok = _post(owner, "/api/scenarios/validate", {"definition": _definition(tenant)}).get_json()
    assert ok["valid"] is True and len(ok["definition_fingerprint"]) == 64
    missing_default = _definition(tenant)
    del missing_default["default_outcome"]
    bad = _post(owner, "/api/scenarios/validate", {"definition": missing_default}).get_json()
    assert bad["valid"] is False and bad["errors"]
    resp = _post(owner, "/api/scenarios", {"name": "x", "definition": missing_default})
    assert resp.status_code == 400
    # Errors raised by the helpers shared with rules are reported the same way.
    for broken in (_definition(tenant, strategy="random"), _definition(tenant, surprise=1)):
        v = _post(owner, "/api/scenarios/validate", {"definition": broken})
        assert v.status_code == 200 and v.get_json()["valid"] is False, v.get_json()
        resp = _post(owner, "/api/scenarios", {"name": "y", "definition": broken})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "VALIDATION_FAILED", resp.get_json()
    resp = _post(owner, "/api/scenarios", {"name": "x", "definition": _definition(tenant),
                                           "surprise": 1})
    assert resp.status_code == 400
    assert owner.get("/api/scenarios/1/outcomes?limit=abc").status_code in (400, 404)


def test_scenario_changes_are_audited(app, tenant, pg_db, sync_jobs):
    owner = _login_new(app, "analyst")
    sc = _create(owner, tenant)
    _dry_run(owner, sc["id"])
    _post(owner, f"/api/scenarios/{sc['id']}/activate")
    _post(owner, f"/api/scenarios/{sc['id']}/pause")
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT action FROM audit_log WHERE resource = %s ORDER BY id",
                    (f"scenario:{sc['id']}",))
        assert [r[0] for r in cur.fetchall()] == [
            "scenario.created", "scenario.dry_run", "scenario.activate", "scenario.pause"]
    conn.close()


def test_content_jobs_trigger_scenario_evaluation_only_when_scenarios_are_active(
        app, tenant, sync_jobs, monkeypatch):
    from services.monitoring import scenario_engine

    calls = []
    monkeypatch.setattr(scenario_engine, "active_scenario_ids", lambda conn: [1])
    real_create = sync_jobs.create_job

    def spy(job_type, **kw):
        if job_type == "scenario_evaluation":
            calls.append(kw["options"]["trigger"])
            return {"job_id": "spy"}
        return real_create(job_type, **kw)

    monkeypatch.setattr(sync_jobs, "create_job", spy)

    class Result:
        def __init__(self):
            self.cancelled, self.errors, self.warnings, self.stats = False, [], [], {}

    sync_jobs._enqueue_scenario_evaluation({"job_type": "ingestion", "job_id": "J1"},
                                           Result(), "ingestion")
    assert calls == ["ingestion"]
    monkeypatch.setattr(scenario_engine, "active_scenario_ids", lambda conn: [])
    sync_jobs._enqueue_scenario_evaluation({"job_type": "ingestion", "job_id": "J2"},
                                           Result(), "ingestion")
    assert calls == ["ingestion"]

    def broken(conn):
        raise RuntimeError("db down")
    monkeypatch.setattr(scenario_engine, "active_scenario_ids", broken)
    result = Result()
    sync_jobs._enqueue_scenario_evaluation({"job_type": "ingestion", "job_id": "J3"},
                                           result, "ingestion")
    assert result.warnings and "Scenarios were not evaluated" in result.warnings[0]


def test_the_monitoring_page_renders_the_contract_for_each_role(app, admin_client, viewer_client):
    import json
    import re

    def page_data(client):
        resp = client.get("/monitoring")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # The script tag has type="application/json" attribute, so regex must allow for it
        raw = re.search(r'id="monitoring-page-data"[^>]*>(.*?)</script>', html, re.S).group(1)
        return html, json.loads(raw)

    html, data = page_data(viewer_client)
    assert data["can_write"] is False and data["is_admin"] is False
    assert 'id="createScenarioModal"' not in html  # no write access = no modal
    analyst = _login_new(app, "analyst")
    html, data = page_data(analyst)
    assert data["can_write"] is True and 'id="createScenarioModal"' in html
    assert data["strategies"] == ["first_match", "all_matching", "highest_priority"]
    html, data = page_data(admin_client)
    assert data["is_admin"] is True
    assert "scenarios-page.js" in html
    assert app.test_client().get("/monitoring").status_code in (302, 401)


def test_invalid_scenario_payloads_are_refused_not_collapsed(app, tenant):
    """An empty or incomplete body is a 400, never a 500.

    ``create_scenario`` validates the name through ``validate_name`` and the
    definition through ``parse_definition``; both raise ``RuleDefinitionError``
    (a ``ValueError``), which is *not* the ``RuleError`` the handlers caught.
    ``POST /api/scenarios`` with ``{}`` therefore answered 500 with a stack
    trace instead of naming what was missing, and the same held for an
    invalid definition on create and update.
    """
    owner = _login_new(app, "analyst")

    empty = _post(owner, "/api/scenarios", {})
    assert empty.status_code == 400, empty.get_data(as_text=True)
    assert empty.get_json()["success"] is False

    no_name = _post(owner, "/api/scenarios", {"definition": _definition(tenant)})
    assert no_name.status_code == 400, no_name.get_data(as_text=True)

    bad_definition = _post(owner, "/api/scenarios",
                           {"name": "audit probe", "definition": {"criteria": {}}})
    assert bad_definition.status_code == 400, bad_definition.get_data(as_text=True)

    sid = _create(owner, tenant)["id"]
    bad_put = _put(owner, f"/api/scenarios/{sid}", {"name": "renamed",
                                                    "definition": {"criteria": {}}})
    assert bad_put.status_code == 400, bad_put.get_data(as_text=True)
