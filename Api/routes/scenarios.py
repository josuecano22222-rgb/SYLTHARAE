"""Scenarios API (step 12).

=============================================  ================================
``GET    /api/scenarios``                      your scenarios (``?all=1``:
                                               every user's, administrators;
                                               ``?include_archived=1``)
``POST   /api/scenarios``                      create a draft ``{name, definition}``
``POST   /api/scenarios/validate``             parse a definition without
                                               saving: canonical form,
                                               fingerprint, errors, warnings
``GET    /api/scenarios/<id>``                 one scenario (owner or admin)
``PUT    /api/scenarios/<id>``                 rename / new definition (owner;
                                               a new definition returns an
                                               active scenario to draft)
``DELETE /api/scenarios/<id>``                 archive (kept for history)
``POST   /api/scenarios/<id>/dry-run``         dry-run the current definition
                                               (a ``scenario_dry_run`` job)
``GET    /api/scenarios/<id>/dry-runs``        dry-run reports (``limit``/``offset``)
``POST   /api/scenarios/<id>/activate``        needs a passed dry-run of this
                                               exact definition (< 24 h)
``POST   /api/scenarios/<id>/pause``           stop evaluating
``POST   /api/scenarios/<id>/resume``          evaluate again (re-checks the owner)
``POST   /api/scenarios/<id>/evaluate``        evaluate now (``scenario_evaluation`` job)
``GET    /api/scenarios/<id>/evaluations``     evaluation log
``GET    /api/scenarios/<id>/evaluations/<e>`` one evaluation or dry-run
``GET    /api/scenarios/<id>/outcomes``        append-only outcome history
                                               (``hash_id``, ``evaluation_id``)
``GET    /api/scenarios/<id>/versions``        every definition it has had
=============================================  ================================

Absent and not-permitted scenarios are both 404. Changes are audited.
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.security.flask_ext import current_user, login_required, write_access_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _actor():
    user = current_user()
    return user, getattr(user, "id", None), bool(user and user.has_role("admin"))


def _audit(action, scenario_id, detail=None):
    from core.security.service import get_auth_service

    user, uid, _ = _actor()
    get_auth_service().audit(action, user_id=uid, username=getattr(user, "username", None),
                             resource=f"scenario:{scenario_id}", detail=detail,
                             ip_address=request.remote_addr)


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("a JSON object is required")
    return data


#: What the editor starts from on /monitoring. Valid definitions (tested in
#: tests/unit/test_monitoring_page.py) that the user edits; they carry no
#: ids, so a scenario saved unchanged watches everything its owner may read.
STARTER_SCENARIO = {
    "criteria": {},
    "conditions": {
        "dated_soon": {"signals": {"signal_types": ["date_reference"]},
                       "min_confidence": "medium", "event_window_days": {"from": 0, "to": 30}},
        "dated": {"signals": {"signal_types": ["date_reference"]}},
    },
    "cases": [
        {"id": "soon", "when": {"all": ["dated_soon"]}, "outcome": "review"},
        {"id": "later", "when": {"all": ["dated"]}, "outcome": "watch"},
    ],
    "outcomes": {
        "review": {"label": "Needs review", "actions": ["notify"]},
        "watch": {"label": "Watch", "actions": []},
        "none": {"label": "No dated reference", "actions": []},
    },
    "default_outcome": "none",
    "strategy": "first_match",
    "notify_existing": False,
}
STARTER_RULE = {
    "criteria": {},
    "signals": {"signal_types": ["date_reference"]},
    "min_confidence": "medium",
    "event_window_days": {"from": 0, "to": 30},
    "unit": "content",
}


def register_scenario_routes(app):
    from Api.utils.utils import get_connection
    from services.detection.signal_query import SignalQueryError, _page, _positive_int
    from services.monitoring import scenarios as store
    from services.monitoring.rule_model import RuleDefinitionError
    from services.monitoring.rules import RuleError
    from services.monitoring.scenario_model import ScenarioDefinitionError, parse_definition

    def err(exc: RuleError):
        return _error(exc.code, exc.message, exc.status)

    @app.errorhandler(RuleDefinitionError)
    def _rule_definition_error(exc: RuleDefinitionError):
        """A definition the model refuses is a 400, not a collapsed request.

        ``validate_name``/``parse_definition`` raise ``RuleDefinitionError``
        (a ``ValueError``), which is not the ``RuleError`` the handlers catch.
        Without this registration an empty or invalid body escaped as an
        unhandled exception and the client saw 500 instead of the reason.
        """
        return _error("VALIDATION_FAILED", str(exc), 400)

    def with_conn(fn):
        with get_connection() as conn:
            return fn(conn)

    def visible(conn, scenario_id):
        _, uid, is_admin = _actor()
        return store.get_scenario(conn, scenario_id, user_id=uid, is_admin=is_admin)

    def body(allowed):
        data = _json_body()
        unknown = sorted(set(data) - set(allowed))
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(unknown)}")
        return data

    @app.route("/monitoring", methods=["GET"])
    @login_required
    def monitoring_page():
        """Scenarios (registry ``monitoring``). A view over the scenarios API
        only; write controls are hidden from viewers as a convenience - the API
        refuses their writes either way."""
        from flask import render_template

        from services.detection.signal_query import MAX_PAGE_SIZE
        from services.monitoring.scenario_model import STRATEGIES

        user, uid, is_admin = _actor()
        return render_template("Monitoring/monitoring.html", page_data={
            "user_id": uid, "is_admin": is_admin,
            "can_write": bool(user and user.has_role("analyst", "admin")),
            "max_page_size": MAX_PAGE_SIZE, "list_limit": store.MAX_LIST,
            "strategies": list(STRATEGIES),
            "starter_scenario": STARTER_SCENARIO,
            "job_poll_ms": 1500, "job_poll_limit": 400,
        })

    @app.route("/api/scenarios", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenarios_list():
        _, uid, is_admin = _actor()
        all_users = request.args.get("all") in ("1", "true")
        if all_users and not is_admin:
            return _error("FORBIDDEN", "only administrators may list every user's scenarios",
                          403)
        rows = with_conn(lambda c: store.list_scenarios(
            c, user_id=uid, is_admin=is_admin, all_users=all_users,
            include_archived=request.args.get("include_archived") in ("1", "true")))
        return jsonify({"success": True,
                        "scenarios": [store.scenario_to_api(r) for r in rows[:store.MAX_LIST]],
                        "count": min(len(rows), store.MAX_LIST),
                        "truncated": len(rows) > store.MAX_LIST, "limit": store.MAX_LIST})

    @app.route("/api/scenarios/validate", methods=["POST"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenarios_validate():
        try:
            data = body({"definition"})
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        try:
            parsed = parse_definition(data.get("definition"))
        except ScenarioDefinitionError as exc:
            return jsonify({"success": True, "valid": False, "errors": [str(exc)],
                            "warnings": []})
        return jsonify({"success": True, "valid": True, "errors": [],
                        "warnings": parsed.warnings(), "definition": parsed.canonical(),
                        "definition_fingerprint": parsed.fingerprint()})

    @app.route("/api/scenarios", methods=["POST"])
    @write_access_required
    def api_scenarios_create():
        try:
            data = body({"name", "definition"})
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        _, uid, _ = _actor()
        try:
            row = with_conn(lambda c: store.create_scenario(
                c, owner_id=uid, name=data.get("name"), definition=data.get("definition")))
        except RuleError as exc:
            return err(exc)
        _audit("scenario.created", row["id"], {"version": 1,
                                               "fingerprint": row["definition_fingerprint"]})
        return jsonify({"success": True, "scenario": store.scenario_to_api(row)}), 201

    @app.route("/api/scenarios/<int:scenario_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenario_get(scenario_id):
        try:
            row = with_conn(lambda c: visible(c, scenario_id))
        except RuleError as exc:
            return err(exc)
        return jsonify({"success": True, "scenario": store.scenario_to_api(row)})

    @app.route("/api/scenarios/<int:scenario_id>", methods=["PUT"])
    @write_access_required
    def api_scenario_update(scenario_id):
        try:
            data = body({"name", "definition"})
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        _, uid, is_admin = _actor()
        try:
            row = with_conn(lambda c: store.update_scenario(
                c, scenario_id, user_id=uid, is_admin=is_admin, name=data.get("name"),
                definition=data.get("definition")))
        except RuleError as exc:
            return err(exc)
        _audit("scenario.updated", scenario_id, {"version": row["version"],
                                                 "fingerprint": row["definition_fingerprint"],
                                                 "status": row["status"]})
        return jsonify({"success": True, "scenario": store.scenario_to_api(row)})

    def transition(scenario_id, action):
        _, uid, is_admin = _actor()
        try:
            if action == "activate":
                row = with_conn(lambda c: store.activate(c, scenario_id, user_id=uid,
                                                         is_admin=is_admin))
            else:
                row = with_conn(lambda c: store.set_status(c, scenario_id, action, user_id=uid,
                                                           is_admin=is_admin))
        except RuleError as exc:
            return err(exc)
        _audit(f"scenario.{action}", scenario_id,
               {"status": row["status"], "dry_run_id": row["activated_dry_run_id"]})
        return jsonify({"success": True, "scenario": store.scenario_to_api(row)})

    @app.route("/api/scenarios/<int:scenario_id>", methods=["DELETE"])
    @write_access_required
    def api_scenario_archive(scenario_id):
        return transition(scenario_id, "archive")

    @app.route("/api/scenarios/<int:scenario_id>/activate", methods=["POST"])
    @write_access_required
    def api_scenario_activate(scenario_id):
        return transition(scenario_id, "activate")

    @app.route("/api/scenarios/<int:scenario_id>/pause", methods=["POST"])
    @write_access_required
    def api_scenario_pause(scenario_id):
        return transition(scenario_id, "pause")

    @app.route("/api/scenarios/<int:scenario_id>/resume", methods=["POST"])
    @write_access_required
    def api_scenario_resume(scenario_id):
        return transition(scenario_id, "resume")

    def start_job(job_type, options, source):
        from services.jobs.manager import JobManager
        from services.jobs.models import job_to_api

        manager = JobManager.get_instance()
        user = current_user()
        try:
            job = manager.create_job(job_type, source=source, options=options,
                                     created_by=getattr(user, "username", None) or "system")
        except Exception:
            logger.exception("%s job creation failed", job_type)
            return _error("JOB_CREATE_FAILED", "The job could not be created", 500)
        if manager.synchronous:
            return jsonify({"success": True, "job": job_to_api(manager.get(job["job_id"]))})
        return jsonify({"success": True, "job": job_to_api(job)}), 202

    @app.route("/api/scenarios/<int:scenario_id>/dry-run", methods=["POST"])
    @write_access_required
    @limiter.limit("30 per minute")
    def api_scenario_dry_run(scenario_id):
        try:
            row = with_conn(lambda c: visible(c, scenario_id))
        except RuleError as exc:
            return err(exc)
        if row["status"] == "archived":
            return _error("CONFLICT", "an archived scenario cannot be dry-run", 409)
        _, uid, _ = _actor()
        _audit("scenario.dry_run", scenario_id, {"version": row["version"]})
        return start_job("scenario_dry_run", {"scenario_id": scenario_id, "requested_by": uid},
                         f"scenarios:{scenario_id}:dry-run")

    @app.route("/api/scenarios/<int:scenario_id>/evaluate", methods=["POST"])
    @write_access_required
    @limiter.limit("30 per minute")
    def api_scenario_evaluate(scenario_id):
        try:
            row = with_conn(lambda c: visible(c, scenario_id))
        except RuleError as exc:
            return err(exc)
        if row["status"] != "active":
            return _error("CONFLICT", f"a {row['status']} scenario is not evaluated", 409)
        return start_job("scenario_evaluation", {"scenario_ids": [scenario_id],
                                                 "trigger": "manual"},
                         f"scenarios:{scenario_id}")

    def paged(scenario_id, fetch):
        try:
            limit, offset = _page(request.args.get("limit", 50), request.args.get("offset", 0))
        except SignalQueryError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        try:
            items, total = with_conn(lambda c: (visible(c, scenario_id),
                                                fetch(c, limit, offset))[1])
        except RuleError as exc:
            return err(exc)
        return jsonify({"success": True, "items": items, "total": total, "limit": limit,
                        "offset": offset})

    @app.route("/api/scenarios/<int:scenario_id>/dry-runs", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenario_dry_runs(scenario_id):
        return paged(scenario_id, lambda c, lim, off: store.list_evaluations(
            c, scenario_id, kind="dry_run", limit=lim, offset=off))

    @app.route("/api/scenarios/<int:scenario_id>/evaluations", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenario_evaluations(scenario_id):
        return paged(scenario_id, lambda c, lim, off: store.list_evaluations(
            c, scenario_id, kind="evaluation", limit=lim, offset=off))

    @app.route("/api/scenarios/<int:scenario_id>/evaluations/<int:evaluation_id>",
               methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenario_evaluation(scenario_id, evaluation_id):
        try:
            item = with_conn(lambda c: (visible(c, scenario_id),
                                        store.get_evaluation(c, scenario_id, evaluation_id))[1])
        except RuleError as exc:
            return err(exc)
        return jsonify({"success": True, "evaluation": item})

    @app.route("/api/scenarios/<int:scenario_id>/outcomes", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenario_outcomes(scenario_id):
        try:
            hash_id = (_positive_int(request.args["hash_id"], "hash_id")
                       if request.args.get("hash_id") else None)
            evaluation_id = (_positive_int(request.args["evaluation_id"], "evaluation_id")
                             if request.args.get("evaluation_id") else None)
        except SignalQueryError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        from core.criteria.access import scope_for

        viewer_scope = scope_for(current_user())
        return paged(scenario_id, lambda c, lim, off: store.list_outcomes(
            c, scenario_id, hash_id=hash_id, evaluation_id=evaluation_id, limit=lim,
            offset=off, scope=viewer_scope))

    @app.route("/api/scenarios/<int:scenario_id>/versions", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_scenario_versions(scenario_id):
        try:
            versions = with_conn(lambda c: (visible(c, scenario_id),
                                            store.list_versions(c, scenario_id))[1])
        except RuleError as exc:
            return err(exc)
        return jsonify({"success": True, "versions": versions})
