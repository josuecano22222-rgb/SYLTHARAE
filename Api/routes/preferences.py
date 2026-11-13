"""Per-user navigation preferences API (owner request: manageability).

========================================  ======================================
``GET    /api/preferences/navigation``    the sidebar catalog with this user's
                                          preferences merged in (every role)
``PUT    /api/preferences/navigation``    replace this user's preferences:
                                          ``{"entries": [{interface_id, hidden,
                                          position}, ...]}`` (every role, own
                                          preferences only)
``DELETE /api/preferences/navigation``    back to the declared navigation
========================================  ======================================

The registry decides what EXISTS and what a user may be SHOWN; a
preference can only narrow that (hide an entry, reorder within a domain).
Every change is audited. There is no JSON for the user to write: the
settings page drives this API with fields and buttons.
"""

from __future__ import annotations

import logging
import uuid

from flask import jsonify, request

from core.interfaces.navigation import build_navigation
from core.security.flask_ext import current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)


def _error(code, message, status):
    return jsonify({"success": False, "error": {
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:12]}}), status


def _actor():
    user = current_user()
    return user, getattr(user, "id", None)


def _audit(action, detail=None):
    from core.security.service import get_auth_service

    user, uid = _actor()
    get_auth_service().audit(action, user_id=uid, username=getattr(user, "username", None),
                             resource="navigation_prefs", detail=detail,
                             ip_address=request.remote_addr)


def register_preference_routes(app):
    from Api.utils.utils import get_connection
    from services.navigation_prefs import (NavigationPrefsError, get_prefs,
                                           reset_prefs, set_prefs)

    @app.route("/api/preferences/navigation", methods=["GET"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def api_navigation_prefs_get():
        """The catalog the settings page edits: every sidebar-eligible
        interface - including the ones this user has hidden, which must
        stay listed so the choice can be undone - with the user's
        hidden/position choice merged in, ordered as the sidebar shows it
        (positioned entries first, then the declared order)."""
        user, uid = _actor()
        with get_connection() as conn:
            prefs = get_prefs(conn, uid)
        from flask import url_for as flask_url_for

        from settings import get_interface_manager

        try:
            state = get_interface_manager().get_state()
        except Exception:  # pragma: no cover - defensive, same as common.py
            state = None
        groups = build_navigation(state, user, url_for=flask_url_for) \
            if state is not None else ()
        entries = []
        for group in groups:
            rows = []
            for index, entry in enumerate(group.entries, start=1):
                pref = prefs.get(entry.interface_id) or {}
                position = pref.get("position")
                rows.append((bool(position is None), position or 0, index, {
                    "interface_id": entry.interface_id,
                    "label": entry.label,
                    "domain": group.domain,
                    "domain_label": group.label,
                    "icon": entry.icon,
                    "url": entry.url,
                    "route": entry.route,
                    "description": entry.description,
                    "shortcut": entry.shortcut,
                    "badge": entry.badge,
                    "note": entry.note,
                    "hidden": bool(pref.get("hidden")),
                    "position": position,
                    "declared_index": index,
                }))
            rows.sort(key=lambda r: r[:3])
            entries.extend(r[3] for r in rows)
        return jsonify({"success": True, "entries": entries})

    @app.route("/api/preferences/navigation", methods=["PUT"])
    @login_required
    @limiter.limit("30 per minute")
    def api_navigation_prefs_put():
        user, uid = _actor()
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            return _error("VALIDATION_FAILED",
                          "a JSON object with an entries list is required", 400)
        try:
            with get_connection() as conn:
                stored = set_prefs(conn, uid, data["entries"])
        except NavigationPrefsError as exc:
            return _error(exc.code, exc.message, 400)
        _audit("navigation.preferences", {"entries": len(stored)})
        return jsonify({"success": True, "entries": [
            {"interface_id": k, "hidden": v["hidden"], "position": v["position"]}
            for k, v in sorted(stored.items())]})

    @app.route("/api/preferences/navigation", methods=["DELETE"])
    @login_required
    @limiter.limit("30 per minute")
    def api_navigation_prefs_delete():
        _, uid = _actor()
        with get_connection() as conn:
            reset_prefs(conn, uid)
        _audit("navigation.preferences_reset", {})
        return jsonify({"success": True})
