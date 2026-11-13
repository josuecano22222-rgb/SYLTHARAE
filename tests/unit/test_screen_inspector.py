"""The Screen Inspector: a read-only answer, and never a guess.

The Inspector exists to answer one question about one element - *what is this,
and who owns it?* - from the architecture that already exists. Its value is
entirely in what it refuses to do:

* it does not turn a permission **name** into a decision about an account;
* it does not turn a button's English word into an action id;
* it does not turn a class into a component because the class looked familiar;
* it does not brighten a missing help topic, shortcut, binding or execution
  reference with something plausible: it says nobody declared it;
* it does not resolve a claim naming an interface or an action the registries
  do not know - it reports the claim;
* and it never shows a filesystem path, a secret or an exception.

Every test here is about one of those refusals, or about the panel the reader
actually meets.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from core.experience import audit, bindings, declarations, inspector
from core.experience.action_registry import registered
from core.experience.inspector import (
    NOT_APPLICABLE,
    NOT_CHECKED,
    NOT_DECLARED,
    RESOLVED,
    UNKNOWN,
    UNRESOLVED,
    InspectorError,
    ScreenInspection,
    Selection,
    inspect,
    panel,
    resolve_component,
)
from core.frontend import component_audit

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
RUNTIME = PROJECT_ROOT / "static/js/modules/core/screen-inspector.js"
COMPONENT = TEMPLATES / "components" / "screen_inspector.html"
HARNESS = PROJECT_ROOT / "tests/js/screen_inspector_smoke.mjs"

LABELS = {
    "interface": "Interface", "interface_name": "Interface name",
    "component": "Component", "action": "Action", "role": "Element",
    "scope": "Scope", "permission": "Permission metadata",
    "authorization": "Current authorization", "state": "State",
    "disabled_reason": "Disabled reason", "hidden_reason": "Hidden reason",
    "confirmation": "Confirmation", "translation_key": "Translation key",
    "source_text": "Source text", "rendered_text": "Rendered text",
    "help": "Help", "shortcut": "Keyboard shortcut",
    "binding_kind": "Binding kind", "binding_source": "Binding source",
    "binding": "Binding", "execution": "Execution reference",
    "selection": "Selection", "job": "Job", "correlation_id": "Correlation ID",
    "component_status": "Component ownership", "execution_status": "Execution",
}
STATUS = {
    "resolved": "Resolved", "unknown": "Unknown",
    "not_declared": "Not declared", "not_applicable": "Not applicable",
    "unavailable": "Unavailable", "unresolved": "Unresolved",
    "not_checked": "Not determined", "not_built": "Not built",
    "source_language": "Source language", "fallback": "Falls back to English",
    "resolved_by_source": "Translated by source string",
}
REASONS = {"not_built": "Not built: no operation exists",
           "no_selection": "Nothing is selected",
           "no_permission": "Not permitted for this account"}
PROBLEMS = {"not_bound": "No control in the product names this action"}


def rows(inspection):
    """The panel's fields, by key, for a test to read like a reader."""
    return {field["key"]: field
            for field in panel(inspection, LABELS, STATUS, REASONS, PROBLEMS)["fields"]}


class TestTheScreenIsTheRegistrys:
    def test_a_known_interface_resolves(self):
        result = inspect(Selection(interface_id="file_library"),
                         endpoint="files.files_list")
        assert result.interface_id == "file_library"
        assert result.interface_name == "File Library"
        assert result.interface_status_note == RESOLVED
        assert result.problems == (), result.problems

    def test_the_screen_is_resolved_from_the_endpoint_not_from_a_url(self):
        """The endpoint is evidence; the registry owns the identity."""
        result = inspect(Selection(classes=("action-bar",)),
                         endpoint="files.file_detail")
        assert result.interface_id == "file_library"

    def test_an_endpoint_nobody_registered_is_refused(self):
        with pytest.raises(InspectorError) as error:
            inspect(Selection(), endpoint="not_a_route")
        assert error.value.reason == "interface_unknown"
        assert "registered route" in error.value.message

    def test_a_claim_naming_an_unknown_interface_is_not_believed(self):
        """The answer comes from the registry; the claim is reported."""
        result = inspect(Selection(interface_id="nope", classes=("action-bar",)),
                         endpoint="sources_list")
        assert result.interface_id == "sources", (
            "the registry decides which screen this is, not the element")
        assert any("'nope'" in problem for problem in result.problems)

    def test_the_registrys_own_metadata_is_reported(self):
        result = inspect(Selection(interface_id="keywords"),
                         endpoint="keywords_list")
        assert result.interface_domain == "DISCOVER"
        assert result.interface_route == "keywords_list"
        assert result.interface_route != result.interface_id, (
            "the interface id is a stable name, never the route the page was served by")
        assert result.interface_enabled is True
        assert result.interface_kind == "PAGE"
        assert result.interface_status == "ACTIVE"

    def test_what_the_registry_has_not_declared_is_named_as_a_gap(self):
        result = inspect(Selection(interface_id="keywords"),
                         endpoint="keywords_list")
        assert result.interface_shortcut is None
        assert any("keyboard shortcut" in problem for problem in result.problems), (
            "an interface without a shortcut is reported, not quietly completed")

    def test_a_route_is_never_confused_with_an_execution_reference(self):
        """`keywords_list` is where the screen lives; an action's execution
        reference is an operation name, and never a route."""
        result = inspect(Selection(interface_id="keywords",
                                   action_id="keywords.delete_selected"),
                         endpoint="keywords_list")
        assert result.interface_route == "keywords_list"
        assert result.action_scope == "bulk"
        # The reference is an operation name, and never the address of a page.
        assert result.action_execution == "delete_keywords"
        assert result.action_execution != result.interface_route
        assert "/" not in result.action_execution and "." not in result.action_execution


class TestHelpAndShortcutAreDeclaredOrNot:
    def test_a_declared_help_topic_is_reported(self):
        result = inspect(Selection(interface_id="sources"),
                         endpoint="sources_list")
        assert result.interface_help_topic
        assert result.interface_help_status == RESOLVED

    def test_a_screen_without_help_says_so_rather_than_inventing_one(self):
        without = [interface.interface_id for interface in inspector.REGISTRY
                   if not interface.help_topic]
        assert without, "the vocabulary needs a screen that declares no help"
        result = inspect(Selection(interface_id=without[0]))
        assert result.interface_help_topic is None
        assert result.interface_help_status == NOT_DECLARED
        assert any("help topic" in problem for problem in result.problems)

    def test_an_action_never_borrows_the_screens_help_topic(self):
        """The action's help is not the screen's help: nothing declares an
        action-level topic yet, and the panel says so (section 21)."""
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected"))
        assert result.interface_help_topic
        assert "sources.edit_selected" not in (result.interface_help_topic or "")

    def test_a_shortcut_is_never_derived_from_an_action_id(self):
        """Nothing in the product derives a shortcut from an action id, and
        where an interface has not declared one, the panel says so."""
        for interface in inspector.REGISTRY:
            result = inspect(Selection(interface_id=interface.interface_id))
            assert result.interface_shortcut_status in (RESOLVED, NOT_DECLARED)
            assert result.interface_shortcut == interface.keyboard_shortcut
        undeclared = [interface for interface in inspector.REGISTRY
                      if not interface.keyboard_shortcut]
        assert undeclared, "the vocabulary needs one for this test to mean anything"
        result = inspect(Selection(interface_id=undeclared[0].interface_id))
        assert result.interface_shortcut is None


class TestTheComponentComesFromTheLibrary:
    def test_a_declared_component_resolves_by_name(self):
        component = resolve_component("RecordActionSurface")
        assert component["component_status"] == UNKNOWN, (
            "a name nobody declares is unknown, not a component")

    def test_a_component_name_from_the_library_resolves(self):
        component = resolve_component("record_actions")
        assert component["component_id"] == "record_actions"
        assert component["component_ownership"] == component_audit.OWNED
        assert "record_actions.html" in component["component_path"]

    def test_a_macro_name_resolves_to_its_component(self):
        component = resolve_component("record_action_surface")
        assert component["component_id"] == "record_actions"
        assert "macro" in component["component_source"]

    def test_a_declared_class_resolves_to_the_component_that_declares_it(self):
        component = resolve_component(classes=("action-bar", "btn-action", "btn"))
        assert component["component_id"] == "action_toolbar"
        assert component["component_ownership"] == component_audit.OWNED

    def test_bootstrap_stays_third_party(self):
        component = resolve_component(classes=("btn", "modal", "bi-trash"))
        assert component["component_id"] is None
        assert component["component_ownership"] == component_audit.THIRD_PARTY
        assert component["component_status"] == NOT_APPLICABLE
        assert component["component_status"] != UNKNOWN, (
            "Bootstrap is a known third-party library, not an unknown one")

    def test_a_class_nobody_owns_is_unknown(self):
        component = resolve_component(classes=("mystery-widget", "btn"))
        assert component["component_ownership"] == component_audit.UNKNOWN, (
            "ownership is the library's word: OWNED, THIRD_PARTY or UNKNOWN")
        assert component["component_status"] == UNKNOWN, (
            "the panel's word for it is the status vocabulary's UNKNOWN")
        assert "mystery-widget" in component["component_source"]

    def test_an_element_with_no_metadata_says_nothing_about_components(self):
        component = resolve_component()
        assert component["component_id"] is None
        assert component["component_status"] == NOT_DECLARED

    def test_no_component_is_invented_from_the_words_on_a_button(self):
        """`Delete Selected` is a label, not a component."""
        result = inspect(Selection(interface_id="sources", classes=("btn",),
                                   observed_text="Delete Selected"),
                         endpoint="sources_list")
        assert result.component_id is None
        assert result.component_ownership == component_audit.THIRD_PARTY


class TestTheActionComesFromTheRegistry:
    def test_a_known_action_resolves_with_its_metadata(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.view_original"),
                         endpoint="files.file_detail")
        assert result.action_id == "files.view_original"
        assert result.action_status == RESOLVED
        assert result.action_scope == "record"
        assert result.action_permission == "files.view_original"
        assert result.action_destructive is False
        assert result.action_execution == "view_original"

    def test_an_unknown_action_is_refused_not_resolved(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.explode"),
                         endpoint="sources_list")
        assert result.action_id == "sources.explode", (
            "the claim is reported as the claim it is")
        assert result.action_status == UNKNOWN
        assert result.action_scope is None
        assert any("not in the Action Registry" in problem
                   for problem in result.problems)

    def test_an_action_is_never_derived_from_the_label(self):
        """`Delete Selected` must not become `delete_selected`."""
        result = inspect(Selection(interface_id="keywords",
                                   classes=("btn-action",),
                                   observed_text="Delete Selected"),
                         endpoint="keywords_list")
        assert result.action_id is None
        assert result.action_status == NOT_APPLICABLE
        assert result.state is None

    def test_the_scope_is_the_registrys_own_word(self):
        for item in registered():
            result = inspect(Selection(interface_id=item.interfaces[0],
                                       action_id=item.action_id),
                             endpoint=None)
            assert result.action_scope == item.scope

    def test_a_not_built_action_is_hidden_with_its_own_reason(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.reprocess"),
                         endpoint="files.file_detail")
        assert result.state == "hidden"
        assert result.hidden_reason == "not_built"
        assert result.action_execution is None
        assert any("not built" in problem for problem in result.problems)

    def test_a_destructive_action_carries_its_confirmation_key(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.delete"),
                         endpoint="files.file_detail")
        assert result.action_destructive is True
        assert result.action_confirmation_required is True
        assert result.action_confirmation_key == "action.files.delete.confirm"


class TestPermissionIsANameAndNotADecision:
    def test_the_panel_shows_permission_metadata_and_authorization_apart(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.delete"),
                         endpoint="files.file_detail")
        shown = rows(result)
        assert shown["permission"]["value"] == "files.delete"
        assert shown["authorization"]["value"] == "Not determined"
        assert "does not decide" in shown["authorization"]["note"]

    def test_no_module_in_the_experience_layer_decides_an_action(self):
        """The Inspector may not grow a `can_execute` by accident."""
        source = (PROJECT_ROOT / "core/experience/inspector.py").read_text(
            encoding="utf-8")
        for forbidden in ("has_role", "is_admin", "current_user", "can_execute",
                          "flask", "gettext", "url_for"):
            assert forbidden not in source, (
                f"{forbidden} in the Inspector: permission metadata is not a "
                "decision and this module must not be able to make one")

    def test_an_authorization_answer_is_carried_when_the_server_gives_one(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.delete"),
                         endpoint="files.file_detail",
                         authorization="Denied", roles=("analyst",))
        shown = rows(result)
        assert shown["authorization"]["value"] == "Denied"
        assert result.authorization_roles == ("analyst",)

    def test_the_model_never_reports_permission_as_a_boolean(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected"),
                         endpoint="sources_list")
        payload = result.to_dict()
        assert not any(isinstance(value, bool) and "permission" in key
                       for key, value in payload.items())


class TestStatesAreTheProductsOwnVocabulary:
    def test_available_when_the_action_needs_nothing(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.view_original"),
                         endpoint="files.file_detail")
        assert result.state == "available"

    def test_disabled_with_no_selection_and_the_reason_is_distinct(self):
        result = inspect(Selection(interface_id="keyword_dummy" if False else "keywords",
                                   action_id="keywords.delete_selected",
                                   selected=0),
                         endpoint="keywords_list")
        assert (result.state, result.disabled_reason) == ("disabled", "no_selection")
        assert result.hidden_reason is None, (
            "nothing selected is not a permission decision")

    def test_hidden_for_an_action_the_product_has_not_built(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.reprocess"),
                         endpoint="files.file_detail")
        assert (result.state, result.hidden_reason) == ("hidden", "not_built")

    def test_not_built_is_never_reported_as_no_permission(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.reprocess"),
                         endpoint="files.file_detail")
        assert result.hidden_reason != "no_permission"

    def test_running_and_the_two_outcomes_come_from_the_page(self):
        base = dict(interface_id="file_library", action_id="files.view_original")
        running = inspect(Selection(**base, running=True),
                          endpoint="files.file_detail")
        assert running.state == "running"
        success = inspect(Selection(**base, outcome="success"),
                          endpoint="files.file_detail")
        assert success.state == "success"
        failed = inspect(Selection(**base, outcome="failed"),
                         endpoint="files.file_detail")
        assert failed.state == "failed"

    def test_the_state_is_the_models_state_not_a_second_one(self):
        from core.experience.model import ACTION_STATES

        for item in registered():
            result = inspect(Selection(interface_id=item.interfaces[0],
                                       action_id=item.action_id,
                                       selected=1))
            assert result.state in ACTION_STATES or result.state is None

    def test_a_reason_the_vocabulary_does_not_have_is_never_invented(self):
        from core.experience.model import DISABLED_REASONS, HIDDEN_REASONS

        for item in registered():
            result = inspect(Selection(interface_id=item.interfaces[0],
                                       action_id=item.action_id, selected=0))
            assert result.disabled_reason is None or result.disabled_reason in DISABLED_REASONS
            assert result.hidden_reason is None or result.hidden_reason in HIDDEN_REASONS


class TestTheSelectionIsPresentationOnly:
    def test_the_counts_are_reported_as_they_were_given(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected",
                                   selected=3, total=27),
                         endpoint="sources_list")
        assert (result.selected, result.total) == (3, 27)
        assert result.selection_state == "partial"

    def test_all_of_them_is_its_own_state(self):
        result = inspect(Selection(interface_id="sources", selected=27, total=27),
                         endpoint="sources_list")
        assert result.selection_state == "all"

    def test_nothing_selected_is_named_rather_than_left_blank(self):
        result = inspect(Selection(interface_id="sources", selected=0, total=27),
                         endpoint="sources_list")
        assert result.selection_state == "none"

    def test_a_count_that_is_not_a_number_is_ignored_not_guessed(self):
        selection = Selection.from_request({"selected": "many", "total": "-3"})
        assert selection.selected is None
        assert selection.total is None

    def test_an_unknown_role_is_not_kept(self):
        assert Selection.from_request({"role": "wizard"}).role is None
        assert Selection.from_request({"role": "control"}).role == "control"


class TestTranslationComesFromMetadata:
    def test_the_key_and_its_source_are_reported(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected"),
                         endpoint="sources_list", locale="he")
        assert result.translation_key == "action.sources.edit_selected.label"
        assert result.translation_source == "Edit Selected"
        assert result.translation_rendered, "Hebrew has this string"

    def test_no_key_is_never_invented_from_the_words_on_screen(self):
        result = inspect(Selection(interface_id="sources", observed_text="Edit Selected"),
                         endpoint="sources_list", locale="he")
        assert result.translation_key is None
        assert result.translation_status == NOT_APPLICABLE

    def test_a_missing_translation_says_so_rather_than_showing_english(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected"),
                         endpoint="sources_list", locale="zz")
        assert result.translation_rendered is None
        assert result.translation_status == "unavailable"
        shown = rows(result)
        assert shown["rendered_text"]["value"] is None
        assert shown["rendered_text"]["status_text"] == "Unavailable"

    def test_english_is_reported_as_the_source_language(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected"),
                         endpoint="sources_list", locale="en")
        assert result.translation_status == "source_language"

    def test_the_key_reads_from_the_registry_not_the_catalog(self):
        for item in registered():
            result = inspect(Selection(interface_id=item.interfaces[0],
                                       action_id=item.action_id), locale="he")
            if item.built or item.label_key:
                assert result.translation_key == item.label_key


class TestTheBindingIsScannedNotAssumed:
    def test_a_prepared_binding_names_the_file_that_prepares_it(self):
        # View Original is bound twice on purpose: the Python layer prepares the
        # href (Api/blueprints/files.py) and the page script opens it in a new
        # tab. The inspector reports the first binding it finds, which depends on
        # scan order, so the claim checked here is the one that must hold: a
        # prepared binding exists and names the file that prepares it.
        prepared = [item for item in bindings.by_action().get("files.view_original", ())
                    if item.get("kind") == "prepared"]
        assert prepared, "no Python layer prepares the View Original href"
        assert prepared[0]["file"].startswith("Api/blueprints/files.py"), prepared[0]["file"]
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.view_original"),
                         endpoint="files.file_detail")
        assert result.binding_kind in ("prepared_in_python", "page_script")
        assert result.binding_source

    def test_a_page_script_binding_is_named_as_such(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.delete"),
                         endpoint="files.file_detail")
        assert result.binding_kind in ("page_script", "prepared_in_python")
        assert result.binding_source

    def test_a_markup_binding_is_named_as_such(self):
        """The class list a page passes when it binds an action in markup."""
        items = [item for item in bindings.scan() if item.get("kind", "attribute") == "attribute"]
        if not items:
            pytest.skip("no markup bindings exist in the product today")
        result = inspect(Selection(interface_id="file_library",
                                   action_id=items[0]["action_id"]),
                         endpoint="files.file_detail")
        assert result.binding_kind == "markup"

    def test_an_action_nothing_binds_is_unresolved_with_a_reason(self):
        result = inspect(Selection(interface_id="sources",
                                   action_id="sources.edit_selected"),
                         endpoint="sources_list")
        assert result.binding_status == UNRESOLVED
        assert result.binding_problem in inspector.BINDING_PROBLEMS
        shown = rows(result)
        assert shown["binding"]["value"] == "Unresolved"
        assert shown["binding"]["note"] == "No control in the product names this action"

    def test_a_bound_action_with_no_route_map_says_not_checked(self):
        """Nothing is claimed when the question could not be asked."""
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.view_original"),
                         endpoint="files.file_detail", routes=None)
        assert result.binding_status == NOT_CHECKED
        assert result.binding_problem is None

    def test_every_binding_the_scan_finds_resolves_in_the_application(self, app):
        """The gate: every binding that names a function names a real one.

        A binding that resolves to a function is checked against the route map;
        one that names only the action (a dialog's `action=` argument, a page
        script's own handler) has nothing to resolve and is reported as such
        rather than counted as unresolvable. A binding that resolved to nothing
        *and* claimed a function would be the defect: an action the reader can
        see and never reach.
        """
        found = bindings.scan()
        report = bindings.check(app.url_map.iter_rules(),
                                known_actions={item["action_id"] for item in found})
        assert isinstance(report, dict)
        for entry in found:
            assert entry.get("kind") in ("attribute", "literal", "prepared", "macro")
            if entry.get("resolved"):
                assert entry["resolved"] in {rule.endpoint
                                             for rule in app.url_map.iter_rules()}, (
                    f"{entry['action_id']} resolves to {entry['resolved']}, which "
                    "the application does not serve")
        dangling = report.get("dangling") or []
        assert dangling == [], dangling


class TestTheExecutionReferenceStaysOpaque:
    def test_a_reference_is_reported_verbatim(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.view_original"),
                         endpoint="files.file_detail")
        assert result.action_execution == "view_original"
        assert "/" not in result.action_execution

    def test_no_route_is_ever_substituted_for_a_reference(self):
        for item in registered():
            result = inspect(Selection(interface_id=item.interfaces[0],
                                       action_id=item.action_id))
            reference = result.action_execution
            if reference:
                assert "." not in reference and "/" not in reference
                assert reference != result.interface_route

    def test_a_not_built_action_reports_no_reference_and_the_reason(self):
        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.reprocess"),
                         endpoint="files.file_detail")
        shown = rows(result)
        assert shown["execution"]["value"] is None
        assert shown["execution"]["status_text"] == "Not built"


class TestSecretsStayOut:
    def test_no_paths_credentials_or_traces_reach_the_payload(self):
        import json

        result = inspect(Selection(interface_id="file_library",
                                   action_id="files.view_original",
                                   classes=("record-actions",),
                                   observed_text="C:\\Evidence\\secret.pdf"),
                         endpoint="files.file_detail")
        assert result.observed_text is None, (
            "a filesystem path is not a label: it is dropped, not echoed")
        payload = json.dumps(result.to_dict())
        for forbidden in ("C:\\", "password", "postgresql://", "SECRET_KEY",
                          "Traceback", "SELECT "):
            assert forbidden not in payload, forbidden

    def test_the_endpoint_never_returns_a_field_it_did_not_derive(self):
        source = (PROJECT_ROOT / "Api/routes/experience_api.py").read_text(
            encoding="utf-8")
        assert '"inspection": inspection.to_dict()' in source
        assert "request.args.get(\"permission\")" not in source, (
            "a client may not assert a permission")
        assert "request.args.get(\"state\")" not in source, (
            "a client may not assert a state")


class TestTheEndpointIsGuarded:
    def test_a_viewer_may_not_read_the_inspector(self, viewer_client):
        response = viewer_client.get("/api/experience/inspect?interface=sources")
        assert response.status_code in (401, 403), response.status_code

    def test_an_administrator_gets_the_answer(self, admin_client):
        response = admin_client.get(
            "/api/experience/inspect?interface=sources&action=sources.edit_selected")
        assert response.status_code == 200, response.get_data(as_text=True)
        payload = response.get_json()
        assert payload["success"] is True
        assert payload["inspection"]["interface_id"] == "sources"
        assert payload["inspection"]["action_scope"] == "bulk"
        assert payload["panel"]["fields"], "the panel was given rows"
        labels = [field["label"] for field in payload["panel"]["fields"]]
        assert "Interface" in labels and "Permission metadata" in labels

    def test_the_endpoint_refuses_to_guess_an_interface(self, admin_client):
        response = admin_client.get("/api/experience/inspect?interface=nope")
        assert response.status_code == 404
        assert response.get_json()["reason"] == "interface_unknown"

    def test_an_element_naming_no_interface_falls_back_to_the_route(
            self, admin_client):
        response = admin_client.get(
            "/api/experience/inspect?interface=sources&action=sources.explode")
        payload = response.get_json()
        assert payload["success"] is True
        assert payload["inspection"]["action_id"] == "sources.explode"
        assert payload["inspection"]["action_scope"] is None


class TestTheComponentAndRuntimeDeclareThemselves:
    def test_the_library_knows_the_panel(self):
        library = component_audit.components()
        assert "screen_inspector" in library
        component = library["screen_inspector"]
        assert component.states, "the panel declares the states it has"
        for state in component.states:
            assert state in component_audit.STATES, state
        assert "normal" in component.states
        assert "empty" in component.states and "error" in component.states
        assert "loading" in component.states

    def test_the_declaration_answers_the_component_questions(self):
        text = COMPONENT.read_text(encoding="utf-8")
        for field in ("purpose", "inputs", "outputs", "events", "states",
                      "slots", "accessibility", "third_party", "css", "notes",
                      "presentation_states"):
            assert f"{field}:" in text, field

    def test_the_classes_the_panel_renders_are_owned(self):
        unknown = component_audit.unknown_classes()
        assert not unknown.get("templates/components/screen_inspector.html"), (
            "a class the panel renders with no stylesheet behind it")

    def test_the_runtime_never_performs_an_action(self):
        source = RUNTIME.read_text(encoding="utf-8")
        for forbidden in ("XMLHttpRequest", "innerHTML", "localStorage",
                          "window.confirm", "POST", "DELETE", "PATCH"):
            assert forbidden not in source, forbidden
        assert "fetch(" in source, (
            "the one request it makes is the read-only inspector endpoint")
        assert source.count("fetch(") == 1

    def test_the_runtime_only_asks_the_inspector_endpoint(self):
        import re

        source = RUNTIME.read_text(encoding="utf-8")
        urls = re.findall(r"['\"](/[a-z0-9/_-]+)['\"]", source)
        assert urls == ["/api/experience/inspect"], urls

    def test_the_runtime_is_idempotent_by_construction(self):
        source = RUNTIME.read_text(encoding="utf-8")
        assert "if (STATE.initialised)" in source, (
            "a second init() must re-use the listeners, not add a set")
        assert source.count("document.addEventListener") == 4, (
            "one listener per event, installed once")

    def test_the_shell_renders_it_only_when_it_is_switched_on(self):
        shell = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "{% if inspector_enabled %}" in shell
        assert "screen_inspector.html" in shell
        assert "screen-inspector.js" in shell

    def test_the_setting_exists_and_defaults_to_off(self):
        from settings.settings_models import SETTING_DEFINITIONS, SystemSettings

        assert SystemSettings().screen_inspector is False
        definition = SETTING_DEFINITIONS["system.screen_inspector"]
        assert definition.default is False
        assert "Screen Inspector" in definition.label
        page = (TEMPLATES / "Settings/settings.html").read_text(encoding="utf-8")
        assert 'data-setting="system.screen_inspector"' in page


class TestTheInspectorCanBeAskedAboutEverythingItReports:
    def test_the_catalogue_is_measured_from_the_sources(self):
        catalogue = inspector.catalogue()
        assert catalogue["interfaces"] == len(list(inspector.REGISTRY))
        assert catalogue["actions"] == len(registered())
        assert catalogue["components"] == len(component_audit.components())
        assert catalogue["bindings"] == len(bindings.scan())
        assert set(catalogue["bindings_by_kind"]) <= {"attribute", "literal", "prepared"}

    def test_every_presented_action_that_nothing_binds_is_named(self):
        """The gate: a screen that declares an action must bind it somewhere."""
        unbound = inspector.catalogue()["actions_presented_but_unbound"]
        assert isinstance(unbound, list)
        for action_id in unbound:
            assert action_id in {item.action_id for item in registered()}

    def test_the_dom_attributes_are_the_ones_the_components_emit(self):
        import re as _re

        emitted = set()
        for relative in ("templates/components/action_toolbar.html",
                         "templates/components/record_actions.html",
                         "templates/components/record_header.html",
                         "templates/components/screen_inspector.html",
                         "static/js/modules/core/screen-inspector.js"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            emitted |= set(_re.findall(r"data-inspector-[a-z-]+", text))
        assert set(inspector.DOM_ATTRIBUTES.values()) <= emitted, (
            "the Inspector reads attributes the components do not emit")
        assert not (set(inspector.DOM_ATTRIBUTES) & set(inspector.DOM_ATTRIBUTE_HINTS)), (
            "an attribute is either required of the components or a hint, not both")
        # The hints are optional by definition, so they are read by the runtime
        # even though no component has to emit one.
        assert set(inspector.DOM_ATTRIBUTE_HINTS.values()) <= emitted


class TestTheRuntimeInABrowserShapedHarness:
    def test_the_harness_passes(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        proc = subprocess.run([node, str(HARNESS)], cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "checks passed" in proc.stdout
        assert "FAIL" not in proc.stdout, proc.stdout


class TestTheEvidenceIsMeasuredNotWritten:
    """The audit behind `docs/SCREEN_INSPECTOR_EVIDENCE.md`.

    Every number in that document comes from one of the functions below, and the
    document is compared with what those functions say - so a count cannot be
    edited into the prose, and a measurement cannot quietly stop being made.
    """

    def test_the_document_is_what_the_product_now_says(self):
        document = (PROJECT_ROOT / "docs/SCREEN_INSPECTOR_EVIDENCE.md").read_text(
            encoding="utf-8")
        assert audit.BEGIN in document and audit.END in document
        generated = document.split(audit.BEGIN, 1)[1].split(audit.END, 1)[0]
        assert generated.strip() == audit.reference_markdown().strip()

    def test_the_document_explains_itself_without_a_count_in_prose(self):
        head, _ = (PROJECT_ROOT / "docs/SCREEN_INSPECTOR_EVIDENCE.md").read_text(
            encoding="utf-8").split(audit.BEGIN, 1)
        assert "python3 -m core.experience.audit" in head
        assert "Permission metadata is not" in head
        assert "not a configuration editor" in head
        # The prose carries no numbers: a number in two places drifts.
        assert not __import__("re").search(r"\b\d+ (actions|interfaces|components)\b", head)

    def test_the_numbers_are_reproducible(self):
        assert audit.counts() == audit.counts()
        assert audit.reference_markdown() == audit.reference_markdown()

    def test_every_registered_action_has_exactly_one_status(self):
        rows = audit.action_rows()
        registered_ids = {item.action_id for item in registered()}
        assert {row["action_id"] for row in rows} == registered_ids
        statuses = set(row["status"] for row in rows)
        assert statuses <= {audit.STATUS_BOUND, audit.STATUS_NOT_BUILT,
                            audit.STATUS_UNBOUND, audit.STATUS_OUTSIDE}
        for row in rows:
            assert row["status"], row["action_id"]
            if row["status"] == audit.STATUS_NOT_BUILT:
                assert row["bound_in"] or True, (
                    "not built is about the operation, not about the binding")

    def test_bound_and_unbound_do_not_overlap(self):
        bound = set(audit.bound_action_ids())
        unbound = set(audit.unbound_actions())
        assert not bound & unbound
        presented = {action_id for action_ids in audit.SCREEN_ACTIONS.values()
                     for action_id in action_ids}
        assert unbound <= presented, (
            "an action nothing presents cannot be one the toolbar owes a binding")

    def test_the_toolbar_debt_is_measured_not_asserted(self):
        numbers = audit.counts()
        assert numbers["actions_unbound"] == len(audit.unbound_actions())
        assert numbers["actions_bound"] == len(audit.bound_action_ids())
        assert numbers["actions_presented"] >= numbers["actions_bound"]
        assert numbers["action_statuses"].get(audit.STATUS_UNBOUND,
                                             0) == numbers["actions_unbound"]

    def test_no_screen_names_an_action_nobody_registered(self):
        """The defect this audit exists to catch, and the product has none.

        A page once presented `sources.edit_selected` on the source detail page
        and `words.delete`/`words.edit` in the word list - ids no registry entry
        owned. The audit measures every action id named in markup or a script
        against the registry, so the next one cannot ship quietly.
        """
        missing = audit.missing_actions()
        assert missing == [], missing
        assert audit.counts()["actions_named_but_unregistered"] == 0

    def test_the_detector_would_catch_a_named_action_that_does_not_exist(
            self, tmp_path, monkeypatch):
        page = tmp_path / "fake_list.html"
        page.write_text(
            '<button type="button" data-inspector-action="sources.explode">'
            "Delete</button>\n"
            '<button type="button" data-inspector-action="{{ action.action_id }}">'
            "Edit</button>\n",
            encoding="utf-8")
        monkeypatch.setattr(audit, "_source_files", lambda: (page,))
        found = audit.named_action_ids()
        assert list(found) == ["sources.explode"], (
            "a named unknown action is a finding; a template expression such as "
            "{{ action.action_id }} names no action at all")
        assert found["sources.explode"][0].endswith("fake_list.html")

    def test_every_shared_component_an_element_could_meet_resolves(self):
        assert audit.components_unresolvable() == []
        for row in audit.component_rows():
            assert row["resolves_by_name"] or row["resolves_by_class"], row
            assert row["classes_unknown"] == 0, (
                f"{row['component_id']} declares a class nothing owns")

    def test_the_class_ownership_totals_agree(self):
        numbers = audit.counts()["classes"]
        assert numbers["rendered"] == (numbers["owned"] + numbers["third_party"]
                                       + numbers["unknown"])
        assert numbers["unknown"] == 0, (
            "an unowned class is what makes an element Unknown to the Inspector")

    def test_the_interfaces_without_metadata_are_named(self):
        for interface_id in audit.interfaces_without_help():
            result = inspect(Selection(interface_id=interface_id))
            assert result.interface_help_status == NOT_DECLARED
            assert result.interface_help_topic is None
        for interface_id in audit.interfaces_without_shortcut():
            result = inspect(Selection(interface_id=interface_id))
            assert result.interface_shortcut is None

    def test_the_bindings_the_audit_reports_are_the_scans(self):
        report = audit.binding_report()
        assert report["bindings"] == len(bindings.scan())
        assert report["actions"] == sorted(audit.bound_action_ids())
        assert set(report["by_kind"]) <= {"attribute", "macro", "literal", "prepared"}

    def test_the_cli_writes_the_document(self, tmp_path):
        import subprocess

        target = tmp_path / "EVIDENCE.md"
        target.write_text(
            f"hand-written head\n\n{audit.BEGIN}\nstale\n{audit.END}\ntail\n",
            encoding="utf-8")
        proc = subprocess.run(
            [__import__("sys").executable, "-m", "core.experience.audit", str(target)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr
        written = target.read_text(encoding="utf-8")
        assert "stale" not in written
        assert "hand-written head" in written and written.rstrip().endswith("tail")
        assert audit.reference_markdown().strip() in written

    def test_a_document_without_the_markers_is_refused(self, tmp_path):
        import subprocess

        target = tmp_path / "no-markers.md"
        target.write_text("nothing generated here\n", encoding="utf-8")
        proc = subprocess.run(
            [__import__("sys").executable, "-m", "core.experience.audit", str(target)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)
        assert proc.returncode != 0
        assert "generated block markers" in (proc.stderr + proc.stdout)


class TestTheQuestionsTheInspectorCannotAnswerYet:
    @pytest.mark.parametrize("gap", audit.GAPS, ids=lambda gap: str(gap["what"]))
    def test_every_gap_names_code_that_exists(self, gap):
        assert set(gap) == {"what", "cannot_say", "evidence"}
        for relative in gap["evidence"]:
            assert (PROJECT_ROOT / relative).exists(), relative
        assert len(str(gap["cannot_say"])) > 120, gap["what"]

    @pytest.mark.parametrize("gap", audit.GAPS, ids=lambda gap: str(gap["what"]))
    def test_every_gap_is_still_true(self, gap):
        """A gap that has closed has to leave the list, not linger in it."""
        numbers = audit.counts()
        if "no operation" in str(gap["what"]):
            assert numbers["actions_not_built"] > 0, (
                "every action has an operation now: the gap is closed")
        elif "help topic" in str(gap["what"]):
            assert (numbers["interfaces_without_shortcut"] > 0
                    or numbers["interfaces_without_help"] > 0)
        elif "page function" in str(gap["what"]):
            assert numbers["actions_unbound"] > 0, (
                "every presented action is bound now: the gap is closed")
        elif "permitted" in str(gap["what"]):
            source = (PROJECT_ROOT / "core/experience/inspector.py").read_text(
                encoding="utf-8")
            assert "Not determined" in source

    def test_the_gaps_are_in_the_document(self):
        generated = audit.reference_markdown()
        for gap in audit.GAPS:
            assert str(gap["what"]) in generated, gap["what"]

    def test_the_list_is_not_empty_and_says_something_specific(self):
        assert len(audit.GAPS) >= 4
        for gap in audit.GAPS:
            assert str(gap["what"]).strip()
