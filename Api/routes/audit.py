"""Audit log viewer (administrators only; read-only).

==================================  =============================================
``GET /admin/audit``                the Audit Log page (registry ``audit_log``)
``GET /api/audit``                  entries, newest first: ``action``,
                                    ``username``, ``user_id``, ``resource``
                                    (prefix), ``since`` / ``until`` (ISO 8601),
                                    ``before_id`` (keyset), ``limit`` <= 200
``GET /api/audit/actions``          the distinct action names (filter menu)
``GET /api/audit/<id>``             one entry
==================================  =============================================

The log is where the chain ends: ``DATA_EXPORTED`` (every file that left the
system, with its digest), ``report.run``, ``report.artifact``, rule and
scenario changes, sign-ins and user administration. Viewing it is not an
export and is not itself audited per request; nothing here writes.
"""

from __future__ import annotations

import uuid

from flask import jsonify, request

from core.security.flask_ext import admin_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def register_audit_routes(app):
    from Api.utils.utils import get_connection
    from core.security import audit_query as audit

    @app.route("/admin/audit", methods=["GET"])
    @admin_required
    def audit_page():
        """The audit log (registry ``audit_log``). A view over the audit API."""
        from flask import render_template

        return render_template("auth/audit.html", page_data={
            "page_size": audit.DEFAULT_LIMIT, "max_page_size": audit.MAX_LIMIT,
        })

    @app.route("/api/audit", methods=["GET"])
    @admin_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_audit_entries():
        try:
            filters = audit.parse_filters(request.args.to_dict(flat=True))
            with get_connection() as conn:
                page = audit.list_entries(conn, filters)
        except audit.AuditQueryError as exc:
            return _error(exc.code, exc.message, exc.status)
        return jsonify(dict(page, success=True))

    @app.route("/api/audit/actions", methods=["GET"])
    @admin_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_audit_actions():
        try:
            with get_connection() as conn:
                body = audit.actions(conn)
        except audit.AuditQueryError as exc:
            return _error(exc.code, exc.message, exc.status)
        return jsonify(dict(body, success=True))

    @app.route("/api/audit/<int:entry_id>", methods=["GET"])
    @admin_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_audit_entry(entry_id):
        try:
            with get_connection() as conn:
                entry = audit.get_entry(conn, entry_id)
        except audit.AuditQueryError as exc:
            return _error(exc.code, exc.message, exc.status)
        if entry is None:
            return _error("NOT_FOUND", "audit entry not found", 404)
        return jsonify({"success": True, "entry": entry})
