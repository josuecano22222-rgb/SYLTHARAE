"""The Experience Contract as a read-only surface.

Three questions, three answers, all generated from the sources of truth that
already exist - the interface registry, the declared screen definitions and the
translation catalogs:

* ``/api/experience/contracts`` - every screen's contract: identity, navigation,
  actions, columns, filters, states, help, shortcuts, layout and the translation
  keys it needs;
* ``/api/experience/contracts/<interface_id>`` - one screen, for the inspector;
* ``/api/experience/coverage`` - what is translated, per language and per
  screen, measured from the catalogs rather than estimated.

Read-only on purpose, and the reason is the same one the registry API states:
*reading* a contract is safe, and the write side is an administrative action
that has to validate, version, preview and audit itself. That side is the
Experience Studio, and until it exists these endpoints report the current state
of the experience rather than pretending to configure it.
"""

from __future__ import annotations

import logging

from flask import jsonify, request
from core.validation import query_int
from flask_babel import gettext as _
from flask_babel import get_locale

from core.security.flask_ext import admin_required

logger = logging.getLogger(__name__)

#: How many contracts a list response returns before it says it truncated.
DEFAULT_LIMIT = 200


# ---------------------------------------------------------------------------
# The Screen Inspector
# ---------------------------------------------------------------------------
#: What each row of the panel is called. The panel's words belong to the layer
#: that has a locale - this one - which is why the model that produces the
#: values does not carry any.
def _inspector_labels():
    labels = {
        "_title": _("Screen Inspector"),
        "interface": _("Interface"),
        "interface_name": _("Interface name"),
        "component": _("Component"),
        "action": _("Action"),
        "role": _("Element"),
        "scope": _("Scope"),
        "permission": _("Permission metadata"),
        "authorization": _("Current authorization"),
        "state": _("State"),
        "disabled_reason": _("Disabled reason"),
        "hidden_reason": _("Hidden reason"),
        "confirmation": _("Confirmation"),
        "translation_key": _("Translation key"),
        "source_text": _("Source text"),
        "rendered_text": _("Rendered text"),
        "help": _("Help"),
        "shortcut": _("Keyboard shortcut"),
        "binding_kind": _("Binding kind"),
        "binding_source": _("Binding source"),
        "binding": _("Binding"),
        "execution": _("Execution reference"),
        "selection": _("Selection"),
        "job": _("Job"),
        "correlation_id": _("Correlation ID"),
        "interface_status": _("Screen metadata"),
        "component_status": _("Component ownership"),
        "action_status": _("Action metadata"),
        "translation_status": _("Translation status"),
        "binding_status": _("Binding status"),
        "execution_status": _("Execution"),
    }
    status_labels = {
        "resolved": _("Resolved"),
        "unknown": _("Unknown"),
        "not_declared": _("Not declared"),
        "not_applicable": _("Not applicable"),
        "unavailable": _("Unavailable"),
        "unresolved": _("Unresolved"),
        "not_checked": _("Not determined"),
        "not_built": _("Not built"),
        "derived": _("Derived from the registry"),
        "source_language": _("Source language"),
        "fallback": _("Falls back to English"),
        "resolved_by_source": _("Translated by source string"),
        "page_script": _("Page script"),
        "prepared_in_python": _("Prepared in Python"),
        "markup": _("Markup"),
    }
    # Why an action is not usable, in the vocabulary's own words. The token is
    # what the product says; this is how a person reads it.
    reason_labels = {
        "no_selection": _("Nothing is selected"),
        "single_selection_required": _("Exactly one record must be selected"),
        "not_built": _("Not built: no operation exists"),
        "unavailable": _("Unavailable right now"),
        "record_archived": _("The record is archived"),
        "original_missing": _("The original file is not available"),
        "no_permission": _("Not permitted for this account"),
        "not_applicable": _("Does not apply to this element"),
    }
    problem_labels = {
        "unknown_action": _("No action is registered with that id"),
        "no_matching_endpoint": _("The address it carries is not served"),
        "not_bound": _("No control in the product names this action"),
        "interface_mismatch": _("The screen does not present this action"),
        "endpoint_not_named": _("The binding names no endpoint"),
    }
    return labels, status_labels, reason_labels, problem_labels


def register_experience_routes(app) -> None:
    """Register the experience contract views."""

    @app.route("/api/experience/contracts", methods=["GET"])
    def experience_contracts():
        """Every screen's contract, with the counts this build produced."""
        from core.experience import contract_counts, contracts_json, screens_without_experience

        domain = request.args.get("domain")
        items = contracts_json()
        if domain:
            items = [item for item in items if item["domain"] == domain]
        declared = request.args.get("declared")
        if declared is not None:
            wanted = declared.lower() in ("1", "true", "yes")
            items = [item for item in items if item["screen"]["declared"] is wanted]

        limit = query_int("limit", DEFAULT_LIMIT, minimum=1, maximum=500)
        counts = contract_counts()
        return jsonify({
            "success": True,
            "counts": counts,
            "total": len(items),
            "returned": min(len(items), limit),
            "truncated": len(items) > limit,
            "contracts": items[:limit],
            # The honest gap, named rather than implied: screens with a derived
            # contract that nobody has described yet.
            "screens_without_experience": list(screens_without_experience()),
        })

    @app.route("/api/experience/contracts/<interface_id>", methods=["GET"])
    def experience_contract(interface_id: str):
        """One screen's contract, or a clear answer that it is not registered."""
        from core.experience import check_contract, contract, hardcoded_source_strings

        item = contract(interface_id)
        if item is None:
            return jsonify({
                "success": False,
                "error": "No interface is registered with that id",
                "interface_id": interface_id,
            }), 404
        return jsonify({
            "success": True,
            "contract": item.to_dict(),
            # `problems` is what the contract itself is wrong about; an empty
            # list is the normal answer for a registered interface.
            "problems": check_contract(item),
            "keys_still_keyed_by_source": list(
                hardcoded_source_strings(interface_id)),
        })

    @app.route("/api/experience/actions", methods=["GET"])
    def experience_actions():
        """The Action Registry, as data.

        The catalog behind the Action Toolbar, the Record Action Surface and
        the Screen Inspector. It reports what each action declares - scope,
        permission, confirmation, the operation that owns it - and counts what
        is still missing (an action shown on a screen that no service owns yet).
        Reading it is safe; nothing here performs an action, and no route here
        can: an execution reference is an opaque name, never a URL.
        """
        from core.experience.action_registry import counts, to_json

        interface_id = request.args.get("interface")
        scope = request.args.get("scope")
        items = to_json()
        if interface_id:
            items = [item for item in items if interface_id in item["interfaces"]]
        if scope:
            items = [item for item in items if item["scope"] == scope]
        limit = query_int("limit", DEFAULT_LIMIT, minimum=1, maximum=500)
        return jsonify({
            "success": True,
            "counts": counts(),
            "total": len(items),
            "returned": min(len(items), limit),
            "truncated": len(items) > limit,
            "actions": items[:limit],
        })

    @app.route("/api/experience/actions/<action_id>", methods=["GET"])
    def experience_action(action_id: str):
        """One action, or a clear answer that nothing registers it."""
        from core.experience.action_registry import action

        item = action(action_id)
        if item is None:
            return jsonify({
                "success": False,
                "error": "No action is registered with that id",
                "action_id": action_id,
            }), 404
        return jsonify({"success": True, "action": item.to_dict()})

    @app.route("/api/experience/permissions", methods=["GET"])
    def experience_permissions():
        """The permission vocabulary, with the actions that name each one.

        A vocabulary, not an authority: the server authorises every request
        itself, and this endpoint only reports which domain an action belongs
        to so an administrator can read the catalog.
        """
        from core.experience.action_registry import registered
        from core.experience.permissions import ACTION_PERMISSIONS, PERMISSION_NOTES

        used = {}
        for item in registered():
            used.setdefault(item.permission, []).append(item.action_id)
        return jsonify({
            "success": True,
            "note": ("Names for visibility only. The server authorises every "
                     "request independently; naming a permission grants "
                     "nothing."),
            "permissions": [
                {"permission": name, "description": PERMISSION_NOTES.get(name, ""),
                 "actions": sorted(used.get(name, []))}
                for name in ACTION_PERMISSIONS],
            # Declared for actions the catalog does not carry yet (the
            # taxonomy, search and export families). Reported rather than
            # hidden, because a vocabulary that silently runs ahead of the
            # product is how a permission ends up meaning two things.
            "reserved": sorted(name for name in ACTION_PERMISSIONS
                               if name not in used),
        })

    @app.route("/api/experience/coverage", methods=["GET"])
    def experience_coverage():
        """Translation coverage, per language and per screen."""
        from core.experience import (
            coverage_counts,
            language_coverage,
            screen_coverage,
        )

        interface_id = request.args.get("interface")
        return jsonify({
            "success": True,
            "counts": coverage_counts(),
            "languages": language_coverage(),
            "screens": screen_coverage(interface_id),
        })


    @app.route("/api/experience/inspect", methods=["GET"])
    @admin_required
    def experience_inspect():
        """What the architecture knows about one element of one screen.

        The client says *which* element - the ids it read from the element's
        own diagnostic attributes, the class list, how many rows are selected,
        whether the operation it started is still running. The server says what
        that means, from the registries and the binding scan, and it refuses the
        questions it cannot answer honestly:

        * a permission is reported as *metadata* - the name the registry gives
          it - and never as a decision, because the decision belongs to the
          endpoint that will be called;
        * an authorisation answer is only shown when the server gave one;
        * a claim naming an interface or an action nobody registered is reported
          as a claim, not resolved into something nearby;
        * an execution reference stays the opaque name it is: no URL is
          substituted for it, and no filesystem path is ever shown.
        """
        from core.experience.inspector import (
            InspectorError,
            Selection,
            inspect,
            panel,
        )
        from core.security.flask_ext import current_user

        selection = Selection.from_request({
            "interface": request.args.get("interface"),
            "component": request.args.get("component"),
            "action": request.args.get("action"),
            "binding_kind": request.args.get("binding_kind"),
            "binding_source": request.args.get("binding_source"),
            "role": request.args.get("role"),
            "classes": request.args.get("classes"),
            "text": request.args.get("text"),
            "selected": request.args.get("selected"),
            "total": request.args.get("total"),
            "running": request.args.get("running") in ("1", "true", "yes"),
            "outcome": request.args.get("outcome"),
            "job": request.args.get("job"),
            "correlation_id": request.args.get("correlation_id"),
        })
        user = current_user()
        roles = getattr(user, "roles", ()) if user else ()
        try:
            inspection = inspect(
                selection,
                endpoint=request.args.get("endpoint"),
                routes=app.url_map.iter_rules(),
                locale=str(get_locale() or "en"),
                roles=roles,
            )
        except InspectorError as error:
            # The refusal is the answer: nothing is resolved by guessing.
            return jsonify({"success": False, "reason": error.reason,
                            "error": error.message}), 404

        labels, status_labels, reason_labels, problem_labels = _inspector_labels()
        return jsonify({
            "success": True,
            "inspection": inspection.to_dict(),
            "panel": panel(inspection, labels, status_labels, reason_labels,
                           problem_labels),
        })
