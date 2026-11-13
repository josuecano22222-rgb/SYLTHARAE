"""Retention API (step 21) — administrators only.

========================================  ======================================
``GET    /retention``                     the Retention page
``GET    /api/retention``                 every policy: effective days, what
                                          it deletes, eligible rows now, table
                                          size, oldest row
``PUT    /api/retention/<area>``          ``{days}`` — 0 = keep forever
                                          (validated twice: the settings
                                          definition and the policy model)
``POST   /api/retention/run``             ``{area?}`` — prune now through the
                                          ``retention`` job (JobManager)
========================================  ======================================

Nothing is deleted silently: every applied area writes one
``retention.applied`` audit row (actor, days, deleted count), and every
policy change writes ``retention.policy_changed``. Areas whose policy is
0 days are kept forever — the default for everything whose deletion
would destroy evidence (scenario outcomes, notifications, artifact bytes,
the revision log, the audit log).
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.security.flask_ext import admin_required, current_user
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _actor():
    user = current_user()
    return user, getattr(user, "id", None), bool(user and user.has_role("admin"))


def _audit(action, detail):
    from core.security.service import get_auth_service

    user, uid, _ = _actor()
    get_auth_service().audit(action, user_id=uid, username=getattr(user, "username", None),
                             resource="retention", detail=detail,
                             ip_address=request.remote_addr)


def register_retention_routes(app):
    from Api.utils.utils import get_connection
    from services.retention import service
    from services.retention.model import AREAS, MAX_DAYS, RetentionError, policy_for

    def with_conn(fn):
        with get_connection() as conn:
            return fn(conn)

    # -- page --------------------------------------------------------------
    @app.route("/retention", methods=["GET"])
    @admin_required
    def retention_page():
        """Retention policies (registry ``retention``): what is pruned, how
        old, what it costs right now - and Run now. The same unified table
        component as every other list."""
        from flask import render_template

        return render_template("Retention/retention.html", page_data={
            "is_admin": True,
            "areas": list(AREAS),
            "max_days": MAX_DAYS,
        })

    # -- policies ------------------------------------------------------------
    @app.route("/api/retention", methods=["GET"])
    @admin_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_retention_overview():
        overview = with_conn(service.overview)
        return jsonify({"success": True, "items": overview,
                        "count": len(overview)})

    @app.route("/api/retention/<area>", methods=["PUT"])
    @admin_required
    def api_retention_set_days(area):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - {"days"}:
            return _error("VALIDATION_FAILED", "a JSON object with 'days' is"
                          " required", 400)
        from services.retention.model import validate_days

        try:
            days = validate_days(area, data.get("days"))
        except RetentionError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)
        from services.retention.model import setting_key

        from settings.settings_adapter import get_settings

        get_settings().set_setting(setting_key(area), days)
        _audit("retention.policy_changed", {"area": area, "days": days})
        return jsonify({"success": True, "area": area, "days": days,
                        "kept_forever": days == 0})

    # -- run now ---------------------------------------------------------------
    @app.route("/api/retention/run", methods=["POST"])
    @admin_required
    def api_retention_run():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or set(data) - {"area"}:
            return _error("VALIDATION_FAILED", "a JSON object is required", 400)
        area = data.get("area")
        areas = None
        if area is not None:
            try:
                policy_for(area)
            except RetentionError as exc:
                return _error("VALIDATION_FAILED", str(exc), 400)
            areas = [area]
        from services.jobs.manager import JobManager

        user, _, _ = _actor()
        try:
            job = JobManager.get_instance().create_job(
                "retention", source="retention:manual",
                options={"areas": areas, "actor": getattr(user, "username", None)
                         or "system"},
                created_by=getattr(user, "username", None) or "system")
        except ValueError as exc:
            return _error("JOB_CREATE_FAILED", str(exc), 500)
        _audit("retention.run_requested", {"area": area, "job_id": job["job_id"]})
        return jsonify({"success": True, "job_id": job["job_id"]})
