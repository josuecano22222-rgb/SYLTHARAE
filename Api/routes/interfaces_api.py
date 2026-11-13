"""The registry as an administrative surface.

Answers one question in three shapes: *what does SYLTHARAE currently consider
part of its own product?*

* ``/api/interfaces`` - every interface with its domain, route, role,
  dependencies, settings, help, shortcut and state;
* ``/api/interfaces/coverage`` - how much of the served application the registry
  accounts for, generated from the URL map, never maintained by hand;
* ``/api/interfaces/inventory`` - the endpoint inventory itself, so the
  classification of any endpoint can be inspected rather than guessed at.

Read-only by design. Switching interfaces on and off is an administrative
action that belongs to the settings blueprint, which enforces the dependency
rules; these endpoints only report.
"""

from __future__ import annotations

import logging

from flask import current_app, g, jsonify, request

logger = logging.getLogger(__name__)


def register_interfaces_routes(app) -> None:
    """Register the registry views."""

    def _state():
        from settings import get_interface_manager

        return get_interface_manager().get_state()

    @app.route("/api/interfaces", methods=["GET"])
    def interfaces_registry():
        """The product model, with the state of this installation applied."""
        state = _state()
        domain = request.args.get("domain")
        # The states are reported *for the caller*: an administrator asking
        # what they may see must not be told "nothing" because the request had
        # no user attached.
        rows = state.interfaces_with_state(getattr(g, "user", None))
        if domain:
            wanted = domain.upper()
            rows = [r for r in rows if r["domain"] == wanted]

        from core.interfaces import all_policies

        return jsonify({
            "success": True,
            "summary": state.summary(),
            "state": {
                "report": state.state_report().__dict__,
                # exists / enabled / visible / accessible are four answers, not
                # one: an interface may exist and be enabled while a viewer may
                # not see it. Authorization is decided by core/security, which
                # this field names so no caller mistakes the registry for it.
                "authorization": "core/security",
            },
            # What each lifecycle status means, once, for every consumer.
            "lifecycle": {
                status: {
                    "navigable": p.navigable,
                    "switchable": p.switchable,
                    "badge": p.badge,
                    "note": p.note,
                }
                for status, p in all_policies().items()
            },
            "interfaces": rows,
        })

    @app.route("/api/interfaces/coverage", methods=["GET"])
    def interfaces_coverage():
        """How much of the application the registry accounts for."""
        from core.interfaces import build_inventory, registry_coverage

        records = build_inventory(current_app, include_api=False)
        return jsonify({"success": True, "coverage": registry_coverage(records)})

    @app.route("/api/interfaces/inventory", methods=["GET"])
    def interfaces_inventory():
        """Every endpoint, classified, with the interface that owns it."""
        from core.interfaces import build_inventory, counts

        include_api = request.args.get("include_api", "0") in ("1", "true", "yes")
        records = build_inventory(current_app, include_api=include_api)
        return jsonify({
            "success": True,
            "counts": counts(records),
            "endpoints": [r.to_dict() for r in records],
        })

    @app.route("/api/interfaces/<interface_id>", methods=["GET"])
    def interface_detail(interface_id: str):
        """One interface, in full: what it is, where it lives, what it needs."""
        from core.interfaces import get_dependents

        state = _state()
        row = next((r for r in state.interfaces_with_state(getattr(g, "user", None))
                    if r["interface_id"] == interface_id), None)
        if row is None:
            return jsonify({
                "success": False,
                "error": f"There is no interface called '{interface_id}'",
            }), 404

        row = dict(row)
        row["dependents"] = [d.interface_id for d in get_dependents(interface_id)]
        row["can_disable"] = state.can_disable(interface_id)[0]
        row["can_enable"] = state.can_enable(interface_id)[0]
        row["authorization"] = "core/security"
        return jsonify({"success": True, "interface": row})
