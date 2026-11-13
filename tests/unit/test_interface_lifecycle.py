"""Unit: the lifecycle status means one thing everywhere.

`ACTIVE`, `EXPERIMENTAL`, `DEPRECATED` and `RETIRED` are promises: whether an
interface is navigable, whether it is switchable, whether it is marked and what
a reader is told. Before `core/interfaces/lifecycle.py` those answers lived at
each call site, which is how "deprecated" comes to mean four different things
in one application - visible here, hidden there, switchable somewhere else.

These tests hold the five consumers to the same policy: navigation, the
settings/administrative view, the interface API, the generated documentation,
and the interface model itself. A change that makes one disagree with the
others fails here rather than in production.

They also cover the four state words (`exists`, `enabled`, `visible`,
`accessible`), which must stay four answers: an interface can exist and be
enabled while a viewer may not see it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from core.interfaces import (
    Interface,
    InterfaceKind,
    InterfaceStatus,
    all_policies,
    get_interface,
    interface_conditions,
    status_policy,
)
from settings.interface_state import InterfaceState

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


class FakeUser:
    def __init__(self, role="admin", authenticated=True):
        self.role = role
        self.is_authenticated = authenticated

    def has_role(self, *roles):
        return self.role in roles


class FakeVisibility:
    class Config:
        def __init__(self, enabled):
            self.enabled = enabled
            self.category = "user"

    def __init__(self, states=None):
        self.interfaces = {k: self.Config(v) for k, v in (states or {}).items()}


def _state(**states):
    return InterfaceState(FakeVisibility(states))


class TestThePolicyIsAContract:
    def test_every_status_has_a_policy(self):
        policies = all_policies()
        assert set(policies) == {str(s) for s in InterfaceStatus}

    def test_active_is_navigable_and_unmarked(self):
        active = status_policy(InterfaceStatus.ACTIVE)
        assert active.navigable and active.switchable and active.badge is None

    def test_deprecated_still_works_and_says_so(self):
        deprecated = status_policy(InterfaceStatus.DEPRECATED)
        assert deprecated.navigable is True, "deprecated is not retired"
        assert deprecated.switchable is True
        assert deprecated.badge == "Deprecated"
        assert "still" in deprecated.note.lower()

    def test_retired_is_not_navigable_and_has_no_badge_to_show(self):
        retired = status_policy(InterfaceStatus.RETIRED)
        assert retired.navigable is False
        assert retired.switchable is False
        assert retired.documented_as_surface is False

    def test_experimental_is_marked_and_flag_gated(self):
        experimental = status_policy(InterfaceStatus.EXPERIMENTAL)
        assert experimental.badge == "Experimental"
        assert "flag" in experimental.note.lower()

    def test_a_policy_can_be_asked_for_by_name(self):
        assert status_policy("DEPRECATED").status is InterfaceStatus.DEPRECATED

    def test_an_unknown_status_is_an_error_not_a_default(self):
        with pytest.raises(KeyError):
            status_policy("SOMETHING_ELSE")


class TestNavigationFollowsThePolicy:
    def test_a_retired_interface_is_never_navigable(self):
        assert status_policy(InterfaceStatus.RETIRED).navigable is False

    def test_a_deprecated_entry_carries_its_badge(self, app):
        """The real registry's one deprecated interface, as navigation sees it."""
        from core.interfaces import build_navigation

        from core.interfaces import REGISTRY

        state = _state(**{i.interface_id: True for i in REGISTRY})
        groups = build_navigation(state, FakeUser("admin"), "import_export_page",
                                  url_for=lambda endpoint, **kw: f"/{endpoint}")
        entries = [e for g in groups for e in g.entries
                   if e.interface_id == "import_export_console"]
        assert entries, "the deprecated interface should still be navigable"
        assert entries[0].badge == "Deprecated"
        assert entries[0].deprecated is True
        assert entries[0].active is True


class TestAllFiveConsumersAgree:
    """navigation, settings view, API, documentation, model."""

    def test_the_settings_view_reports_the_policy(self):
        source = (PROJECT_ROOT / "settings/interface_state.py").read_text()
        assert "status_policy" in source
        assert '"status_policy"' in source

    def test_the_api_publishes_the_policy_once(self):
        source = (PROJECT_ROOT / "Api/routes/interfaces_api.py").read_text()
        assert "all_policies()" in source
        assert '"lifecycle"' in source

    def test_the_model_uses_the_policy_for_navigable(self):
        from core.interfaces.model import Interface as Model

        text = (PROJECT_ROOT / "core/interfaces/model.py").read_text()
        assert "from .lifecycle import policy" in text, (
            "the model's `navigable` must come from the lifecycle policy, not "
            "from its own list of statuses")
        assert "policy(self.status).navigable" in text

    def test_the_generated_documentation_states_the_policy(self):
        """The reference shows each interface's status, from the same registry."""
        from core.interfaces import REGISTRY

        text = (PROJECT_ROOT / "docs/INTERFACE_REGISTRY.md").read_text()
        for interface in REGISTRY:
            assert f"| {interface.status} |" in text, interface.interface_id
        deprecated = [i for i in REGISTRY if str(i.status) == "DEPRECATED"]
        assert deprecated, "the registry is expected to carry one deprecated entry"
        assert "Deprecated" in text

    def test_no_second_opinion_about_deprecation_exists(self):
        """Nothing may hard-code what a status means outside the policy."""
        offenders = []
        for folder in ("Api", "settings", "core", "templates", "static/js"):
            for path in (PROJECT_ROOT / folder).rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".html", ".js"}:
                    continue
                relative = str(path.relative_to(PROJECT_ROOT))
                if relative in {"core/interfaces/lifecycle.py",
                                "core/interfaces/evidence.py"}:
                    continue
                text = path.read_text(errors="ignore")
                if re.search(r'status\s*==\s*["\']DEPRECATED["\']', text):
                    offenders.append(relative)
        assert offenders == [], (
            "these files decide for themselves what DEPRECATED means; ask "
            "core.interfaces.status_policy instead:\n  " + "\n  ".join(offenders))


class TestTheFourStateWords:
    """exists / enabled / visible / accessible are four answers."""

    def test_exists_but_disabled(self):
        state = _state(file_library=False)
        conditions = interface_conditions("file_library", state, FakeUser())
        assert (conditions.exists, conditions.enabled, conditions.visible,
                conditions.accessible) == (True, False, False, False)
        assert "switched off" in conditions.reason

    def test_exists_enabled_but_not_visible_to_this_role(self):
        state = _state(users=True)
        conditions = interface_conditions("users", state, FakeUser("viewer"))
        assert (conditions.exists, conditions.enabled, conditions.visible,
                conditions.accessible) == (True, True, False, False)
        assert "admin" in conditions.reason

    def test_exists_but_no_such_interface(self):
        conditions = interface_conditions("not_real", _state(), FakeUser())
        assert (conditions.exists, conditions.enabled, conditions.visible,
                conditions.accessible) == (False, False, False, False)

    def test_never_collapses_to_one_boolean(self):
        """A hidden-but-enabled interface must not read as "off"."""
        state = _state(users=True)
        conditions = interface_conditions("users", state, FakeUser("viewer"))
        assert conditions.enabled is True and conditions.visible is False

    def test_authorization_is_not_claimed(self):
        state = _state(file_library=True)
        payload = interface_conditions("file_library", state, FakeUser()).to_dict()
        assert payload["authorization"] == "core/security", (
            "the registry must say who decides access, so no caller mistakes "
            "`accessible` for permission")

    def test_a_missing_dependency_is_explained(self):
        state = _state(batch_analysis=True, file_library=False)
        conditions = interface_conditions("batch_analysis", state, FakeUser())
        assert conditions.enabled is False
        assert "file_library" in conditions.reason

    def test_a_switched_off_feature_flag_is_explained(self):
        interface = Interface(
            interface_id="flagged", name="Flagged", description="Behind a flag.",
            domain="INTERNAL", route=None, icon="bi-flag",
            status=InterfaceStatus.EXPERIMENTAL, feature_flag="relationship_graph_v2",
        )
        import core.interfaces.registry as registry_module

        monkeypatched = registry_module.REGISTRY + (interface,)
        original, by_id = registry_module.REGISTRY, dict(registry_module._BY_ID)
        registry_module.REGISTRY = monkeypatched
        registry_module._BY_ID["flagged"] = interface
        try:
            state = _state(flagged=True)
            conditions = interface_conditions("flagged", state, FakeUser())
            assert conditions.enabled is False
            assert "relationship_graph_v2" in conditions.reason
        finally:
            registry_module.REGISTRY = original
            registry_module._BY_ID.clear()
            registry_module._BY_ID.update(by_id)


class TestFeatureDeclarations:
    """A feature is a cross-cutting capability with a real gate (§8)."""

    def test_every_declared_feature_is_gated_somewhere(self):
        from core.interfaces.evidence import feature_gate

        for feature in __import__("core.interfaces", fromlist=["FEATURES"]).FEATURES:
            gate = feature_gate(feature.feature_id)
            assert gate, (
                f"{feature.feature_id} is declared as a feature but nothing "
                f"gates it; either build the gate or declare it as something else")
            for relative in gate:
                assert (PROJECT_ROOT / relative).exists(), relative

    def test_the_declared_gate_really_gates_it(self):
        text = (PROJECT_ROOT / "templates/components/page_tips.html").read_text()
        assert "feature_enabled('page_tips')" in text

    def test_the_settings_audit_lists_the_non_features(self):
        from core.interfaces.evidence import NON_FEATURE_TOGGLES

        # Cross-cutting switches the application has that are *not* features.
        # Listing them here is what makes "is this a feature?" a decision.
        assert "system.animations_enabled" in NON_FEATURE_TOGGLES
        assert "search.enable_saved_searches" in NON_FEATURE_TOGGLES

    def test_a_feature_is_not_an_interface_and_the_other_way_round(self):
        from core.interfaces import FEATURES

        interface_ids = {i.interface_id for i in __import__(
            "core.interfaces", fromlist=["REGISTRY"]).REGISTRY}
        for feature in FEATURES:
            assert feature.feature_id not in interface_ids, feature.feature_id


class TestTheRegistryIsFrozen:
    """§10: from here, new product surfaces must be declared."""

    def test_the_directive_guardrails_all_exist(self):
        required = {
            "tests/integration/test_interface_coverage.py": "coverage",
            "tests/unit/test_interface_registry.py": "integrity",
            "tests/unit/test_interface_docs.py": "documentation",
            "tests/unit/test_registry_purity.py": "template purity",
            "tests/unit/test_interface_lifecycle.py": "lifecycle",
            "tests/security/test_interface_visibility.py": "visibility",
        }
        for relative in required:
            assert (PROJECT_ROOT / relative).exists(), relative

    def test_the_freeze_is_written_down(self):
        text = (PROJECT_ROOT / "docs/INTERFACE_REGISTRY.md").read_text().lower()
        assert "frozen" in text, (
            "the registry contract must state that it is frozen")
        for requirement in ("register", "navigation", "shortcut", "help", "visibility"):
            assert requirement in text, requirement

    def test_adding_a_page_without_registering_it_fails(self):
        """The freeze is not a promise; it is this test, in the coverage suite."""
        text = (PROJECT_ROOT / "tests/integration/test_interface_coverage.py").read_text()
        assert "unowned_user_interfaces" in text
