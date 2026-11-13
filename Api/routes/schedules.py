"""Schedules API (step 20).

========================================  ======================================
``GET    /schedules``                     the Schedules page
``GET    /api/schedules``                 your schedules (``?all=1``: every
                                          user's, administrators only)
``POST   /api/schedules``                 create:
                                          ``{schedule_type, name,
                                          interval_minutes, payload}``
``GET    /api/schedules/<id>``            one schedule (owner or
                                          administrator)
``PUT    /api/schedules/<id>``            edit name / interval / payload /
                                          enabled (owner or administrator)
``DELETE /api/schedules/<id>``            delete (owner or administrator)
``POST   /api/schedules/<id>/pause``      stop firing
``POST   /api/schedules/<id>/resume``     fire again from now + interval
``POST   /api/schedules/<id>/run_now``    fire immediately (does not move the
                                          cadence)
``GET    /api/schedules/<id>/jobs``       the jobs this schedule created
========================================  ======================================

What may be scheduled: periodic evaluation of every active scenario
(administrators only, like the manual trigger), or retention pruning
(administrators only).

Authorization is a hard boundary, at creation and at every fire: the
owner's current role and activity are re-read each time the schedule runs.
A schedule whose owner lost the required role is
disabled with a stated reason - nothing runs as somebody who may no longer
run it. Every management change and every fire is audited.
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.security.flask_ext import (
    current_user, login_required, write_access_required)
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

MAX_NAME = 200
MAX_JOB_HISTORY = 50


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _actor():
    user = current_user()
    return user, getattr(user, "id", None), bool(user and user.has_role("admin"))


def _can_manage(user, schedule, is_admin: bool) -> bool:
    return is_admin or (user is not None
                        and schedule.get("owner_user_id") == getattr(user, "id", None))


def _audit(action, schedule_id, detail=None):
    from core.security.service import get_auth_service

    user, uid, _ = _actor()
    get_auth_service().audit(action, user_id=uid, username=getattr(user, "username", None),
                             resource=f"job_schedule:{schedule_id}", detail=detail,
                             ip_address=request.remote_addr)


def register_schedule_routes(app):
    from Api.utils.utils import get_connection
    from services.scheduling import model, store
    from services.scheduling.model import ScheduleError

    def with_conn(fn):
        with get_connection() as conn:
            return fn(conn)

    def schedule_error(exc: ScheduleError):
        return _error("VALIDATION_FAILED", str(exc), 400)

    # -- page ------------------------------------------------------------
    @app.route("/schedules", methods=["GET"])
    @login_required
    def schedules_page():
        """Scheduled evaluations and retention (registry ``schedules``).
        A view over the schedules API; the same unified table component as
        every other list."""
        from flask import render_template

        user, uid, is_admin = _actor()
        return render_template("Scheduling/schedules.html", page_data={
            "user_id": uid, "is_admin": is_admin,
            "can_write": bool(user and user.has_role("analyst", "admin")),
            "schedule_types": list(model.SCHEDULE_TYPES),
            "min_interval": model.MIN_INTERVAL_MINUTES,
            "max_interval": model.MAX_INTERVAL_MINUTES,
        })

    # -- list / create -----------------------------------------------------
    @app.route("/api/schedules", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_schedules_list():
        _, uid, is_admin = _actor()
        all_users = request.args.get("all") in ("1", "true")
        if all_users and not is_admin:
            return _error("FORBIDDEN",
                          "only administrators may list every user's schedules", 403)
        rows = with_conn(lambda c: store.list_schedules(
            c, owner_user_id=uid, all_users=all_users))
        return jsonify({"success": True, "items": rows,
                        "count": len(rows), "all_users": all_users})

    @app.route("/api/schedules", methods=["POST"])
    @write_access_required
    def api_schedules_create():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "a JSON object is required", 400)
        unknown = sorted(set(data) - {"schedule_type", "name", "interval_minutes",
                                      "payload"})
        if unknown:
            return _error("VALIDATION_FAILED",
                          f"unknown field(s): {', '.join(unknown)}", 400)
        user, uid, is_admin = _actor()
        schedule_type = data.get("schedule_type")
        try:
            needed = model.required_role(schedule_type)
        except ScheduleError as exc:
            return schedule_error(exc)
        if needed == "admin" and not is_admin:
            return _error("FORBIDDEN",
                          f"a {schedule_type} schedule requires an administrator", 403)
        try:
            row = with_conn(lambda c: store.create_schedule(
                c, schedule_type=schedule_type, name=data.get("name"),
                owner_user_id=uid, payload=data.get("payload"),
                interval_minutes=data.get("interval_minutes")))
        except ScheduleError as exc:
            return schedule_error(exc)
        _audit("schedule.created", row["id"],
               {"schedule_type": row["schedule_type"], "name": row["name"],
                "interval_minutes": row["interval_minutes"],
                "payload": row["payload"]})
        return jsonify({"success": True, "schedule": row}), 201

    # -- one schedule ------------------------------------------------------
    def _load(schedule_id):
        """The schedule plus the actor's rights over it, or an error tuple."""
        row = with_conn(lambda c: store.get_schedule(c, schedule_id))
        if row is None:
            return None, _error("NOT_FOUND", f"schedule {schedule_id} does not exist",
                                404)
        user, _, is_admin = _actor()
        if not _can_manage(user, row, is_admin):
            # Absent and not-permitted look the same (the rules convention).
            return None, _error("NOT_FOUND",
                                f"schedule {schedule_id} does not exist", 404)
        return row, None

    @app.route("/api/schedules/<int:schedule_id>", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_schedules_get(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        return jsonify({"success": True, "schedule": row})

    @app.route("/api/schedules/<int:schedule_id>", methods=["PUT"])
    @write_access_required
    def api_schedules_update(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("VALIDATION_FAILED", "a JSON object is required", 400)
        unknown = sorted(set(data) - {"name", "interval_minutes", "payload",
                                      "enabled"})
        if unknown:
            return _error("VALIDATION_FAILED",
                          f"unknown field(s): {', '.join(unknown)}", 400)
        try:
            fresh = with_conn(lambda c: store.update_schedule(
                c, schedule_id,
                name=data.get("name"),
                payload=data["payload"] if "payload" in data else None,
                interval_minutes=data.get("interval_minutes"),
                enabled=data.get("enabled")))
        except ScheduleError as exc:
            return schedule_error(exc)
        _audit("schedule.updated", schedule_id,
               {"fields": sorted(data), "enabled": fresh["enabled"],
                "interval_minutes": fresh["interval_minutes"]})
        return jsonify({"success": True, "schedule": fresh})

    @app.route("/api/schedules/<int:schedule_id>", methods=["DELETE"])
    @write_access_required
    def api_schedules_delete(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        with_conn(lambda c: store.delete_schedule(c, schedule_id))
        _audit("schedule.deleted", schedule_id,
               {"name": row["name"], "schedule_type": row["schedule_type"]})
        return jsonify({"success": True, "deleted": schedule_id})

    # -- pause / resume / run now -------------------------------------------
    @app.route("/api/schedules/<int:schedule_id>/pause", methods=["POST"])
    @write_access_required
    def api_schedules_pause(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        fresh = with_conn(lambda c: store.update_schedule(
            c, schedule_id, enabled=False))
        _audit("schedule.paused", schedule_id, {"name": row["name"]})
        return jsonify({"success": True, "schedule": fresh})

    @app.route("/api/schedules/<int:schedule_id>/resume", methods=["POST"])
    @write_access_required
    def api_schedules_resume(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        # Re-decide the authorization right now: resuming a schedule whose
        # owner can no longer run it is refused, not silently kept disabled.
        user, _, is_admin = _actor()
        owner = with_conn(lambda c: store.owner_of(c, schedule_id))
        eligible, reason = model.owner_eligible(
            row["schedule_type"], owner["role"], owner["is_active"])
        if not eligible and not is_admin:
            return _error("FORBIDDEN",
                          f"this schedule can no longer run: {reason}", 409)
        fresh = with_conn(lambda c: store.update_schedule(c, schedule_id,
                                                          enabled=True))
        _audit("schedule.resumed", schedule_id, {"name": row["name"]})
        return jsonify({"success": True, "schedule": fresh})

    @app.route("/api/schedules/<int:schedule_id>/run_now", methods=["POST"])
    @write_access_required
    def api_schedules_run_now(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        # The fire-now path needs the APScheduler-backed runner. In this tree
        # services.scheduling.scheduler declares ``ScheduleRunner`` only - there
        # is no ``Scheduler`` singleton, and the module's APScheduler import is
        # an undeclared dependency - so the call raised ImportError /
        # AttributeError inside the route's net and answered 500 for a feature
        # that is simply not wired. Say that instead: 503, with the reason, and
        # leave the schedule untouched (nothing is recorded as having fired).
        try:
            from services.scheduling import scheduler as scheduler_module
        except Exception as exc:  # pragma: no cover - deployment dependent
            logging.getLogger(__name__).warning(
                "run_now unavailable: the scheduler module cannot be imported (%s)", exc)
            return _error("SCHEDULER_UNAVAILABLE",
                          "the job scheduler is not available in this deployment", 503)
        runner = getattr(scheduler_module, "Scheduler", None)
        if runner is None:
            logging.getLogger(__name__).warning(
                "run_now unavailable: services.scheduling.scheduler has no Scheduler")
            return _error("SCHEDULER_UNAVAILABLE",
                          "the job scheduler is not available in this deployment", 503)
        outcome = runner.get_instance().fire_now(row)
        if outcome["status"] == "disabled":
            return _error("FORBIDDEN",
                          f"this schedule can no longer run: {outcome.get('reason')}",
                          409)
        if outcome["status"] == "error":
            return _error("JOB_CREATE_FAILED",
                          "the job could not be created; the failure is recorded "
                          "on the schedule", 500)
        _audit("schedule.run_now", schedule_id,
               {"name": row["name"], "job_id": outcome.get("job_id")})
        return jsonify({"success": True, "result": outcome})

    # -- the schedule's jobs -------------------------------------------------
    @app.route("/api/schedules/<int:schedule_id>/jobs", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_schedules_jobs(schedule_id):
        row, failure = _load(schedule_id)
        if failure:
            return failure
        limit = min(request.args.get("limit", 20, type=int) or 20, MAX_JOB_HISTORY)
        jobs = with_conn(lambda c: _schedule_jobs(c, schedule_id, limit))
        return jsonify({"success": True, "items": jobs, "count": len(jobs)})

    def _schedule_jobs(conn, schedule_id, limit):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT job_id, job_type, status, progress, source, created_by,"
                " created_at, options"
                " FROM jobs"
                " WHERE options->>'schedule_id' = %s"
                " ORDER BY created_at DESC, job_id DESC LIMIT %s",
                (str(schedule_id), limit))
            columns = [d[0] for d in cur.description]
            rows = [dict(zip(columns, r, strict=True)) for r in cur.fetchall()]
        conn.rollback()
        for row in rows:
            row["created_at"] = row["created_at"].isoformat() if row["created_at"] \
                else None
        return rows
