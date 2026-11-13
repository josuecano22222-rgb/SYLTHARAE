"""The Action Registry, and the semantics it is allowed to have.

Five properties are protected here, and each of them has been a real failure
mode somewhere in this product's history:

* **One catalog.** Ids are unique, namespaced by resource, and the screens read
  their actions from here instead of restating them, so the same action cannot
  mean two things on two pages.
* **Description, never execution.** An execution reference is an opaque word.
  It is not a URL, not a Flask endpoint, not a path, and nothing in the package
  can resolve it - the registry must not become a second router.
* **A permission is a name.** Every action names one, the vocabulary owns the
  spelling, and no module in ``core/experience`` turns a permission into an
  authorisation decision. The server still authorises every request itself.
* **States carry their reason.** Disabled and hidden are different situations
  with different vocabularies, and "nothing is selected" never becomes "you may
  not do this".
* **The declarations agree with the registry.** A screen shows what the catalog
  says it shows, and the contract says the same thing.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from core.experience import action_registry, declarations
from core.experience.action_registry import (
    ACTION_REGISTRY,
    action,
    by_namespace,
    counts,
    for_interface,
    namespaced,
    registered,
    to_json,
    without_operation,
)
from core.experience.contract import contract
from core.experience.model import (
    ACTION_NAMESPACES,
    ACTION_SCOPES,
    ACTION_STATES,
    DISABLED_REASONS,
    HIDDEN_REASONS,
    SELECTION_RULES,
    ActionDefinition,
    ActionState,
    ContractError,
)
from core.experience.permissions import ACTION_PERMISSIONS, PERMISSION_NOTES
from core.interfaces import REGISTRY

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "core/experience"
REGISTRY_SOURCE = (PACKAGE / "action_registry.py").read_text(encoding="utf-8")
INTERFACE_IDS = {interface.interface_id for interface in REGISTRY}


class TestOneCatalog:
    def test_ids_are_unique_and_namespaced_by_resource(self):
        seen = set()
        for item in registered():
            assert item.action_id not in seen, item.action_id
            seen.add(item.action_id)
            namespace = item.action_id.split(".", 1)[0]
            assert namespace in ACTION_NAMESPACES, item.action_id
            assert item.namespace == namespace

    def test_every_action_says_where_it_can_appear(self):
        for item in registered():
            assert item.interfaces, item.action_id
            for interface_id in item.interfaces:
                assert interface_id in INTERFACE_IDS, (item.action_id, interface_id)

    def test_a_duplicate_id_is_refused_by_the_registry(self):
        """The check that runs at import, exercised on purpose."""
        first = action("files.delete_selected")
        with pytest.raises(ContractError) as error:
            action_registry._unique_ids((first, first))
        assert "registered twice" in str(error.value)

    def test_the_lookup_helpers_agree_with_the_catalog(self):
        assert registered() == ACTION_REGISTRY
        assert action("files.delete_selected").scope == "bulk"
        assert action("files.not_a_thing") is None
        assert for_interface("keywords") == tuple(
            item for item in ACTION_REGISTRY if "keywords" in item.interfaces)
        assert set(by_namespace()) == {item.namespace for item in ACTION_REGISTRY}
        assert sum(len(items) for items in by_namespace().values()) == len(registered())

    def test_a_screen_maps_its_actions_to_operations(self):
        mapping = namespaced("words")
        assert mapping["words.delete_selected"] == "delete_words"
        assert "" not in mapping.values()  # every word action has an operation


class TestDescriptionNotExecution:
    def test_an_execution_reference_is_one_opaque_word(self):
        for item in registered():
            if item.execution is None:
                continue
            assert re.fullmatch(r"[a-z][a-z0-9_]*", item.execution), (
                f"{item.action_id}: {item.execution!r} is not an opaque "
                "operation reference")

    def test_no_execution_reference_is_a_route_a_url_or_a_path(self):
        for item in registered():
            reference = item.execution or ""
            for forbidden in ("/", "\\", "<", ">", "?", "&", ":", ".", " "):
                assert forbidden not in reference, (item.action_id, reference)

    def test_no_execution_reference_is_a_flask_endpoint(self):
        """The registry must not be able to reach the application's routing."""
        from apps.web.app import app

        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        for item in registered():
            assert (item.execution or "") not in endpoints, (
                f"{item.action_id} names the Flask endpoint {item.execution!r}; "
                "an action describes an operation, not a route")

    def test_the_module_offers_no_way_to_run_anything(self):
        """A guard against the one change that would break this layer."""
        tree = ast.parse(REGISTRY_SOURCE)
        functions = {node.name for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert functions == {
            "_unique_ids", "_check_scope", "registered", "registered_ids",
            "action", "for_interface", "without_operation", "not_built",
            "all_actions", "actions_for", "actions_for_scope", "by_namespace",
            "namespaced", "counts", "to_json", "to_dict",
        }, functions
        # The set above is the whole API, and every name in it is a question
        # about the catalog. Reading the calls rather than the text keeps the
        # module's own prose - which says there is no run() here to be tempted
        # by - out of the judgement.
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (getattr(node.func, "id", None)
                        or getattr(node.func, "attr", None))
                if name:
                    called.add(name)
        forbidden_calls = {name for name in called if re.match(
            r"^(run|run_action|perform|dispatch|execute|eval|exec|system|popen)$",
            name)}
        assert not forbidden_calls, forbidden_calls

    def test_the_registry_does_not_import_the_service_or_the_route_layer(self):
        tree = ast.parse(REGISTRY_SOURCE)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
        assert imported <= {"__future__", "typing", "model"}, imported

    def test_an_action_that_has_no_operation_is_counted_not_hidden(self):
        missing = without_operation()
        assert missing, "this measurement exists because some actions have no owner"
        for item in missing:
            assert item.execution is None
        assert counts()["without_execution"] == len(missing)
        assert counts()["with_execution"] == len(registered()) - len(missing)

    def test_nothing_here_drives_a_page(self):
        """No template or runtime resolves an action to a URL by itself."""
        for path in (PROJECT_ROOT / "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "action_registry" not in text, path
        runtime = (PROJECT_ROOT / "static/js/modules/core/action-toolbar.js")
        assert "execution" not in runtime.read_text(encoding="utf-8"), (
            "the toolbar renders the scope it is given; it never learns what an "
            "action does or where it goes")


class TestAPermissionIsAName:
    def test_every_action_names_a_declared_permission(self):
        for item in registered():
            assert item.permission in ACTION_PERMISSIONS, item.action_id

    def test_the_vocabulary_has_no_duplicates_and_explains_itself(self):
        assert len(set(ACTION_PERMISSIONS)) == len(ACTION_PERMISSIONS)
        assert set(PERMISSION_NOTES) == set(ACTION_PERMISSIONS)
        for name, note in PERMISSION_NOTES.items():
            assert note.endswith(".") and len(note.split()) >= 2, (name, note)

    def test_the_vocabulary_grants_nothing(self):
        """No callable, no mapping to a role: it is a naming scheme."""
        tree = ast.parse((PACKAGE / "permissions.py").read_text(encoding="utf-8"))
        for node in tree.body:
            assert isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign,
                                     ast.AnnAssign, ast.Expr)), ast.dump(node)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert isinstance(target, ast.Name)
        assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef))
                       for node in tree.body)

    def test_no_layer_decides_anything_from_a_permission(self):
        """Visibility metadata must not become an authorisation check.

        The server authorises every request in ``core/security`` and the route
        guard. If this package ever asks a question with a permission string,
        the decision has moved to the wrong place.
        """
        suspicious = re.compile(
            r"(if|while|assert|return)\s+[^\n]*permission[^\n]*"
            r"(==|!=|in\s+\{|in\s+\()")
        for path in PACKAGE.rglob("*.py"):
            assert not suspicious.search(path.read_text(encoding="utf-8")), (
                f"{path.name} makes a decision from a permission name")

    def test_the_api_says_the_same_thing(self):
        """The registry reports names, and claims no enforcement."""
        payload = [item["permission"] for item in to_json()]
        assert set(payload) <= set(ACTION_PERMISSIONS)
        assert "authorisation" in (PACKAGE / "permissions.py").read_text(
            encoding="utf-8").lower()


class TestStatesCarryTheirReason:
    def test_the_vocabularies_are_closed_and_disjoint(self):
        assert set(DISABLED_REASONS) & set(HIDDEN_REASONS) == {"not_built"}
        assert "no_selection" in DISABLED_REASONS
        assert "no_permission" in HIDDEN_REASONS
        assert "no_selection" not in HIDDEN_REASONS
        assert "no_permission" not in DISABLED_REASONS
        for name in ACTION_STATES:
            assert name == name.lower()

    def test_a_disabled_state_must_say_why(self):
        with pytest.raises(ContractError):
            ActionState(action_id="files.delete_selected", state="disabled")
        with pytest.raises(ContractError):
            ActionState(action_id="files.delete_selected", state="disabled",
                        disabled_reason="no_permission")

    def test_a_hidden_state_must_say_why_and_only_that_way(self):
        with pytest.raises(ContractError):
            ActionState(action_id="files.delete_selected", state="hidden")
        with pytest.raises(ContractError):
            ActionState(action_id="files.delete_selected", state="hidden",
                        hidden_reason="no_selection")

    def test_a_decided_state_carries_no_reason(self):
        with pytest.raises(ContractError):
            ActionState(action_id="files.delete_selected", state="available",
                        disabled_reason="no_selection")

    def test_derive_reflects_facts_instead_of_deciding_them(self):
        definition = action("files.delete_selected")

        # Nothing selected: present, unusable, and it says why.
        none = ActionState.derive(definition, selected=0)
        assert (none.state, none.disabled_reason) == ("disabled", "no_selection")
        assert none.confirmation_required is True

        # Selected: usable, and it still has to be confirmed.
        some = ActionState.derive(definition, selected=3)
        assert (some.state, some.disabled_reason) == ("available", None)

        # Not permitted: hidden, and that is the only way to get there.
        hidden = ActionState.derive(definition, selected=3, permitted=False)
        assert (hidden.state, hidden.hidden_reason) == ("hidden", "no_permission")

    def test_derive_never_invents_a_permission_decision(self):
        """Permission granted means the action is considered, not hidden.

        The one thing that can still keep an allowed action off the screen is
        the product not having built it - and that is a fact about the product,
        not a decision about the reader, so it says so in its own reason.
        """
        for item in registered():
            for selected in (0, 1, 5):
                state = ActionState.derive(item, selected=selected, permitted=True)
                if item.built:
                    assert state.state != "hidden", item.action_id
                else:
                    assert (state.state, state.hidden_reason) == (
                        "hidden", "not_built"), item.action_id
                    assert state.hidden_reason != "no_permission", item.action_id

    def test_a_missing_operation_is_a_named_disabled_reason(self):
        definition = action("sources.export_selected")
        state = ActionState.derive(definition, selected=0)
        assert state.disabled_reason == "no_selection"
        not_built = ActionState.derive(definition, selected=2, unavailable="not_built")
        assert (not_built.state, not_built.disabled_reason) == ("disabled", "not_built")

    def test_an_action_on_one_member_of_a_selection_stays_available(self):
        """The semantic that used to be a gap: 'one of N', not a fifth scope."""
        definition = action("keywords.edit_selected")
        assert definition.scope == "selection"
        assert definition.selection_rule == "one"
        state = ActionState.derive(definition, selected=3)
        assert state.state == "available"
        assert ActionState.derive(
            definition, selected=0).disabled_reason == "no_selection"


class TestTheModelRefusesWhatItCannotDescribe:
    def test_a_bulk_action_acts_on_the_whole_selection(self):
        with pytest.raises(ContractError) as error:
            ActionDefinition(
                action_id="files.do_many", label_key="action.files.do_many.label",
                source="Do many", interfaces=("file_library",), scope="bulk",
                requires_selection=True, selection_rule="one")
        assert "whole" in str(error.value)

    def test_a_selection_action_must_say_how(self):
        with pytest.raises(ContractError) as error:
            ActionDefinition(
                action_id="files.edit_some", label_key="action.files.edit_some.label",
                source="Edit some", interfaces=("file_library",),
                scope="selection", requires_selection=True)
        assert "selection_rule" in str(error.value)

    def test_a_page_action_cannot_act_on_a_selection(self):
        with pytest.raises(ContractError):
            ActionDefinition(
                action_id="files.do_all", label_key="action.files.do_all.label",
                source="Do all", interfaces=("file_library",), scope="page",
                selection_rule="all")

    def test_an_unknown_namespace_is_refused(self):
        with pytest.raises(ContractError) as error:
            ActionDefinition(
                action_id="widgets.wiggle", label_key="action.widgets.wiggle.label",
                source="Wiggle", interfaces=("file_library",))
        assert "namespace" in str(error.value)

    def test_an_unknown_permission_is_refused(self):
        with pytest.raises(ContractError) as error:
            ActionDefinition(
                action_id="files.export_everything",
                label_key="action.files.export_everything.label",
                source="Export", interfaces=("file_library",),
                permission="files.export_everything")
        assert "permission" in str(error.value)

    def test_a_route_shaped_execution_reference_is_refused(self):
        for reference in ("/api/files/1/original", "files.file_detail",
                          "https://example.invalid/x", "C:\\Evidence\\a.pdf"):
            with pytest.raises(ContractError):
                ActionDefinition(
                    action_id="files.open_it",
                    label_key="action.files.open_it.label", source="Open",
                    interfaces=("file_library",), execution=reference)


class TestTheDeclarationsAgreeWithTheRegistry:
    @pytest.mark.parametrize("interface_id", sorted(declarations.DECLARED_SCREENS))
    def test_a_screen_shows_what_the_registry_says(self, interface_id):
        """Same definitions, in the screen's order, and no others.

        The screen declaration owns the order (a toolbar puts its destructive
        action last) and the registry owns what each one *is*. Comparing id
        lists keeps both halves honest: a definition cannot be restated here,
        and an action cannot be presented by a screen the registry does not
        attribute it to.
        """
        declared = declarations.actions(interface_id)
        available = {item.action_id: item for item in for_interface(interface_id)}
        assert set(item.action_id for item in declared) <= set(available)
        assert [item.action_id for item in declared] == list(
            declarations.SCREEN_ACTIONS[interface_id]), (
                "the declared order is what the surface draws")
        for item in declared:
            assert item == available[item.action_id], (
                "the screen must carry the registry's definition, not a copy")

    @pytest.mark.parametrize("interface_id", sorted(declarations.DECLARED_SCREENS))
    def test_the_contract_carries_the_registry_definitions(self, interface_id):
        item = contract(interface_id)
        assert item is not None
        assert [action.action_id for action in item.screen.actions] == [
            action.action_id for action in declarations.actions(interface_id)]

    def test_a_screen_does_not_restate_an_action(self):
        """The declarations must not grow their own copy of an action."""
        source = (PACKAGE / "declarations.py").read_text(encoding="utf-8")
        assert '"actions": (' not in source

    def test_every_registered_action_reaches_a_contract(self):
        """An action nobody can be shown is a definition without a home."""
        carried = {item.action_id
                   for interface in {i for a in registered() for i in a.interfaces}
                   for item in (contract(interface).screen.actions)}
        missing = [item.action_id for item in registered()
                   if item.action_id not in carried]
        assert missing == []

    def test_the_catalog_is_measured_not_asserted(self):
        values = counts()
        assert values["actions"] == len(registered())
        assert values["by_scope"] == {
            scope: sum(1 for item in registered() if item.scope == scope)
            for scope in ACTION_SCOPES}
        assert values["destructive"] == sum(1 for item in registered()
                                           if item.destructive)
        assert values["with_confirmation"] >= values["destructive"]
        assert values["selection_actions"] == values["by_scope"]["selection"]
        assert set(values["kinds"]) == set(ACTION_SCOPES)
        assert set(SELECTION_RULES) == {"all", "one"}
