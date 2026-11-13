"""Integration: the registry accounts for the application, and governs it.

This is where the architectural contract meets the real Flask application:

* every user-facing page it serves is owned by exactly one interface - a page
  added without a registry entry fails here, which is the guardrail;
* the request gate serves what an interface owns and refuses what nothing owns;
* dependencies are enforced when an operator switches something off, with an
  explanation rather than a silent broken configuration;
* resetting uses the registry's defaults, not "enable everything";
* a settings file written by an earlier version still works, and its choices
  reach the interfaces that replaced them.
"""

from __future__ import annotations

import pathlib

import pytest

from core.interfaces import (
    REGISTRY,
    build_inventory,
    get_dependencies,
    get_interface,
    get_interface_for_endpoint,
    registry_coverage,
    unowned_user_interfaces,
    validate_registry,
)
from core.interfaces.inventory import EndpointClass
from settings.interface_state import InterfaceState


@pytest.fixture(scope="module")
def settings_model_paths():
    """Every ``category.key`` the settings model actually has."""
    from dataclasses import fields as dc_fields

    from settings.settings_models import AllSettings

    model = AllSettings()
    known = set()
    for field in dc_fields(model):
        value = getattr(model, field.name)
        inner = getattr(value, "__dataclass_fields__", None)
        if inner:
            known |= {f"{field.name}.{name}" for name in inner}
        else:
            known.add(field.name)
    return known


class TestCoverage:
    """Every page the application serves is accounted for."""

    def test_every_user_facing_page_has_exactly_one_owner(self, app):
        records = build_inventory(app)
        unowned = unowned_user_interfaces(records)
        assert unowned == [], (
            "Unregistered user-facing page endpoint(s): "
            + ", ".join(r.endpoint for r in unowned))
        assert registry_coverage(records)["page_endpoints_owned"] == \
            registry_coverage(records)["page_endpoints"]

    def test_no_two_interfaces_own_the_same_endpoint(self, app):
        owners = {}
        for interface in REGISTRY:
            for endpoint in interface.endpoints:
                owners.setdefault(endpoint, []).append(interface.interface_id)
        clashes = {e: o for e, o in owners.items() if len(o) > 1}
        assert clashes == {}

    def test_every_owned_endpoint_exists_in_the_application(self, app):
        live = {rule.endpoint for rule in app.url_map.iter_rules()}
        for interface in REGISTRY:
            for endpoint in interface.endpoints:
                assert endpoint in live, f"{interface.interface_id} names {endpoint}"

    def test_the_registry_validates_against_the_live_application(
            self, app, settings_model_paths):
        issues = validate_registry(
            REGISTRY,
            known_endpoints={r.endpoint for r in app.url_map.iter_rules()},
            known_settings=settings_model_paths,
        )
        errors = [i for i in issues if i.severity == "error"]
        assert errors == [], "\n".join(str(i) for i in errors)

    def test_redirects_are_declared_and_really_redirect(self, admin_client):
        """``/upload`` is a compatibility address, not a second interface."""
        from core.interfaces import EndpointClass

        records = build_inventory(admin_client.application)
        redirects = {r.endpoint for r in records
                     if r.classification == EndpointClass.REDIRECT}
        assert redirects, "the compatibility redirect must be declared"

        response = admin_client.get("/upload")
        assert response.status_code in (301, 302, 303, 307, 308)
        assert "/operations/input" in response.headers.get("Location", "")

        for endpoint in redirects:
            owner = get_interface_for_endpoint(endpoint)
            assert owner is not None, f"{endpoint} redirects nowhere in the registry"

    def test_coverage_numbers_are_generated(self, app):
        coverage = registry_coverage(build_inventory(app))
        assert coverage["page_endpoints"] > 0
        assert coverage["unowned_user_interface"] == []
        assert isinstance(coverage["by_classification"], dict)
        # The classification breakdown holds only real classifications.
        assert set(coverage["by_classification"]) <= {str(c) for c in EndpointClass}


class TestTheRequestGate:
    """What an interface owns is served; what nothing owns is not."""

    def test_owned_pages_are_served(self, admin_client):
        for path in ("/archives", "/files", "/operations/jobs", "/settings"):
            assert admin_client.get(path).status_code == 200, path

    def test_infrastructure_is_never_gated(self, admin_client):
        """Health, locale and auth are how the application works."""
        assert admin_client.get("/health").status_code == 200
        assert admin_client.get("/set_language/en").status_code in (200, 302)

    def test_an_unowned_endpoint_is_refused(self, app, admin_client, monkeypatch):
        """A page nobody registered is not part of the surface.

        The old adapter answered ``True`` for any endpoint missing from its
        hand-written map, which made an unregistered page indistinguishable
        from a registered one. The gate now refuses it, so the failure is
        visible in development rather than in production.
        """
        import settings.interface_state as state_module

        monkeypatch.setattr(state_module, "get_interface_for_endpoint", lambda endpoint: None)
        response = admin_client.get("/archives")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

    def test_a_disabled_interface_is_refused_and_can_be_restored(self, admin_client):
        """Switching an interface off hides it; switching it back restores it."""
        assert admin_client.post("/api/settings/interfaces/keywords",
                                 json={"enabled": False}).status_code == 200
        assert admin_client.get("/keywords").status_code == 302
        assert admin_client.post("/api/settings/interfaces/keywords",
                                 json={"enabled": True}).status_code == 200
        assert admin_client.get("/keywords").status_code == 200

    def test_an_unknown_interface_cannot_be_switched_on(self, admin_client):
        response = admin_client.post("/api/settings/interfaces/does_not_exist",
                                     json={"enabled": True})
        assert response.status_code == 409
        assert "does_not_exist" in response.get_json()["error"]


class TestDependencies:
    """Enforced, and explained."""

    def test_disabling_a_required_dependency_is_refused_with_a_reason(self, admin_client):
        dependents = [i.name for i in REGISTRY if "file_library" in i.dependencies]
        assert dependents, "the registry must declare at least one dependent"

        response = admin_client.post("/api/settings/interfaces/file_library",
                                     json={"enabled": False})
        assert response.status_code == 409
        body = response.get_json()
        assert body["success"] is False
        assert "cannot be switched off" in body["error"]
        for name in dependents[:2]:
            assert name in body["error"]

        # ... and nothing was changed by the refusal
        assert admin_client.get("/files").status_code == 200

    def test_a_dependent_can_be_switched_off_first(self, admin_client):
        """The message must describe a way out, not just a wall."""
        assert admin_client.post("/api/settings/interfaces/batch_analysis",
                                 json={"enabled": False}).status_code == 200
        response = admin_client.post("/api/settings/interfaces/file_library",
                                     json={"enabled": False})
        # Other dependents remain, so this is still refused - but the reason
        # must no longer name the interface we just switched off.
        assert response.status_code == 409
        assert "Batch Analysis" not in response.get_json()["error"]

        # restore
        assert admin_client.post("/api/settings/interfaces/batch_analysis",
                                 json={"enabled": True}).status_code == 200

    def test_a_dependent_cannot_run_without_its_dependency(self, admin_client):
        """A stored state that cannot work is reported, not hidden.

        The dependency may have been switched off by an older version, or by
        editing the file directly. The interface stays switched on - mutating
        an operator's choices silently is worse - but it is not treated as
        usable, and the reason is visible.
        """
        from settings import get_interface_manager

        manager = get_interface_manager()
        state = manager.get_state()
        assert state.is_enabled("batch_analysis")

        stored = manager.settings.interfaces.interfaces["file_library"]
        original = stored.enabled
        try:
            stored.enabled = False
            assert state.is_enabled("batch_analysis") is False
            assert state.missing_dependencies("batch_analysis") == ("file_library",)
            report = state.state_report()
            assert ("batch_analysis", "file_library") in report.missing_dependencies
            assert report.consistent is False
            # The switch itself is still reported as on, so the screen can say
            # "enabled, but its dependency is off" rather than just "off".
            assert "batch_analysis" in report.enabled
        finally:
            stored.enabled = original
            assert state.is_enabled("batch_analysis") is True

    def test_dependency_report_names_both_sides(self, admin_client):
        body = admin_client.get("/api/interfaces/file_library").get_json()
        assert "batch_analysis" in body["interface"]["dependents"]
        assert body["interface"]["can_disable"] is False
        assert get_dependencies("batch_analysis") == ("file_library",)


class TestDefaults:
    """One definition of the default, and the registry owns it."""

    def test_reset_uses_registry_defaults_not_enable_everything(self, admin_client, monkeypatch):
        """An interface whose default is off must stay off after a reset.

        The old reset enabled every key it found in the settings file, which
        made the file authoritative over the product model. This registers an
        interface whose default is off, stores an explicit "on" for it, resets
        and requires the registry default to win.
        """
        import core.interfaces.registry as registry_module
        from core.interfaces import Interface, defaults as registry_defaults
        from settings import get_interface_manager

        experimental = Interface(
            interface_id="experimental_probe",
            name="Experimental Probe",
            description="A capability that is part of the model but off by default.",
            domain="INTERNAL",
            route=None,
            icon="bi-box",
            default_enabled=False,
        )
        monkeypatch.setattr(registry_module, "REGISTRY", REGISTRY + (experimental,))
        monkeypatch.setitem(registry_module._BY_ID, "experimental_probe", experimental)

        from settings import get_settings_manager

        manager = get_interface_manager()
        stored = get_settings_manager()

        # This test writes to the installation's settings file, which is shared
        # with the running application, so it is put back exactly as it was
        # found - even if an assertion below fails.
        snapshot = {
            key: (config.enabled, getattr(config, "category", None))
            for key, config in stored.settings.interfaces.interfaces.items()
        }
        try:
            stored.set("interfaces.experimental_probe.enabled", True)
            assert manager.is_interface_enabled("experimental_probe") is True

            reset = manager.reset_interfaces_to_defaults()
            assert reset["experimental_probe"] is False
            assert registry_defaults()["experimental_probe"] is False
            assert manager.is_interface_enabled("experimental_probe") is False
            assert manager.get_state().stored("experimental_probe") is False

            # A reset writes the registry's default for every interface, and
            # for the ones the file already had that is their own value back.
            for interface_id, default in registry_defaults().items():
                if interface_id in snapshot:
                    assert manager.get_state().stored(interface_id) is default, interface_id
        finally:
            stored.settings.interfaces.interfaces.pop("experimental_probe", None)
            for key, (enabled, category) in snapshot.items():
                config = stored.settings.interfaces.interfaces.get(key)
                if config is not None:
                    config.enabled = enabled
            stored.save(create_backup=False)

    def test_the_registry_is_the_only_source_of_defaults(self):
        from settings import get_interface_manager

        manager = get_interface_manager()
        for interface_id, default in manager.get_state().defaults().items():
            interface = get_interface(interface_id)
            if interface is not None:
                assert interface.default_enabled == default, interface_id


class TestMigration:
    """A settings file from an earlier version keeps working."""

    def test_legacy_ids_reach_their_replacement(self, admin_client):
        from settings import get_interface_manager

        manager = get_interface_manager()
        report = manager.get_state().state_report()
        migrated = dict(report.migrated_from)
        # The demo/installation fixtures ship the old keys (file_analysis,
        # upload_files, file_browser, advanced_search, analytics); whatever is
        # present must have been folded onto a live interface.
        for legacy, canonical in migrated.items():
            assert get_interface(canonical) is not None, (legacy, canonical)

    def test_a_legacy_only_file_still_produces_a_working_interface_set(self, tmp_path, monkeypatch):
        """Delete every canonical key, keep the legacy ones, and load."""
        import pathlib
        from settings.settings_manager import SettingsManager, get_settings_manager

        manager = get_settings_manager()
        file_path = tmp_path / "settings.json"
        data = manager.export()
        interfaces = data["interfaces"]["interfaces"]
        for canonical in ("archives", "input_ingestion", "file_library"):
            interfaces.pop(canonical, None)
        interfaces["file_analysis"] = {"enabled": False, "category": "user"}
        interfaces["upload_files"] = {"enabled": True, "category": "user"}
        interfaces["file_browser"] = {"enabled": True, "category": "core"}
        file_path.write_text(__import__("json").dumps(data))

        migrated_manager = SettingsManager(pathlib.Path(file_path))
        migrated_manager.load()
        state = InterfaceState(migrated_manager.settings.interfaces)

        # The explicit choice on the old key is honoured by the new interface.
        assert state.stored("archives") is False
        assert state.is_enabled("archives") is False
        # Merged ids: an "on" anywhere carries over.
        assert state.stored("input_ingestion") is True
        assert state.stored("file_library") is True

    def test_unknown_stored_ids_do_not_activate_anything(self, tmp_path):
        """A key nothing declares is kept in the file, and does nothing."""
        import json

        from settings.settings_manager import SettingsManager, get_settings_manager

        manager = get_settings_manager()
        data = manager.export()
        data["interfaces"]["interfaces"]["totally_unknown_switch"] = {
            "enabled": True, "category": "user"}
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data))

        loaded = SettingsManager(pathlib.Path(path))
        loaded.load()
        state = InterfaceState(loaded.settings.interfaces)

        assert state.is_enabled("totally_unknown_switch") is False
        assert "totally_unknown_switch" in state.state_report().unknown_stored
        # Kept, not deleted: it is the operator's file.
        assert "totally_unknown_switch" in loaded.settings.interfaces.interfaces

    def test_retired_ids_are_reported_but_harmless(self, admin_client):
        from settings import get_interface_manager, get_settings_manager

        manager = get_settings_manager()
        state = get_interface_manager().get_state()
        # A retired key is still writable so a migration can carry its value;
        # it just cannot switch anything on.
        manager.set("interfaces.file_upload.enabled", True)
        try:
            report = state.state_report()
            # Reported as retired, and its value carried onto the interface
            # that owns the capability: neither is allowed to vanish.
            assert "file_upload" in report.retired_stored
            assert ("file_upload", "input_ingestion") in report.migrated_from
            # It never becomes an interface of its own.
            assert state.is_enabled("file_upload") is False
            assert get_interface("file_upload") is None
            assert not any(e == "file_upload" for i in REGISTRY for e in i.endpoints)
        finally:
            manager.settings.interfaces.interfaces.pop("file_upload", None)
            manager.save(create_backup=False)


class TestTheRegistryView:
    """An administrator can ask what the product considers part of itself."""

    def test_summary_is_available(self, admin_client):
        body = admin_client.get("/api/interfaces").get_json()
        assert body["success"] is True
        assert body["summary"]["interfaces"] == len(REGISTRY)
        assert sum(body["summary"]["by_domain"].values()) == len(REGISTRY)

    def test_every_entry_answers_the_reader_questions(self, admin_client):
        """What is it, where does it live, who sees it, is it on, what does it
        need, which settings, how do I reach help, does it have a shortcut."""
        rows = admin_client.get("/api/interfaces").get_json()["interfaces"]
        assert rows
        for row in rows:
            for key in ("interface_id", "name", "description", "domain", "route",
                        "aliases", "icon", "default_enabled", "required_role",
                        "dependencies", "settings", "feature_flag", "help_topic",
                        "keyboard_shortcut", "kind", "status", "enabled"):
                assert key in row, (row.get("interface_id"), key)

    def test_one_interface_can_be_inspected(self, admin_client):
        body = admin_client.get("/api/interfaces/batch_analysis").get_json()
        assert body["interface"]["name"] == "Batch Analysis"
        assert body["interface"]["domain"] == "ANALYZE"
        assert body["interface"]["dependencies"] == ["file_library"]
        assert body["interface"]["help_topic"]
        assert body["interface"]["keyboard_shortcut"]

    def test_an_unknown_interface_is_a_clean_404(self, admin_client):
        response = admin_client.get("/api/interfaces/not_a_thing")
        assert response.status_code == 404
        assert "not_a_thing" in response.get_json()["error"]

    def test_the_inventory_can_be_inspected(self, admin_client):
        body = admin_client.get("/api/interfaces/inventory").get_json()
        assert body["counts"]["UNOWNED_USER_INTERFACE"] == 0
        assert all("classification" in row for row in body["endpoints"])

    def test_coverage_is_served(self, admin_client):
        coverage = admin_client.get("/api/interfaces/coverage").get_json()["coverage"]
        assert coverage["unowned_user_interface"] == []

class TestTheEvidenceReport:
    """The evidence document is regenerated, not maintained.

    Step 1-2 of the directive ends with evidence, and evidence that is typed by
    hand is how the same application came to be described as having 57, 40 and
    41 endpoints in one document. These tests hold the committed report to the
    application the suite is running against.

    To regenerate after a deliberate change::

        INFORAXIS_WRITE_EVIDENCE=1 python3 -m pytest \
            tests/integration/test_interface_coverage.py -k evidence
    """

    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
    REPORT = PROJECT_ROOT / "docs/REGISTRY_EVIDENCE.md"
    INVENTORY = PROJECT_ROOT / "docs/endpoint_inventory.json"

    @staticmethod
    def _writing():
        import os

        return os.environ.get("INFORAXIS_WRITE_EVIDENCE") == "1"

    def test_the_evidence_report_matches_the_application(self, app, admin_client):
        from core.interfaces.evidence import (
            BEGIN_APPLICATION,
            END_APPLICATION,
            application_block,
            navigation_observation,
        )

        document = self.REPORT.read_text()
        assert BEGIN_APPLICATION in document and END_APPLICATION in document
        # Rendered navigation is part of the evidence: the report records what
        # the application actually served, not what the registry intended.
        generated = application_block(
            app, navigation_observation=navigation_observation(admin_client)).strip()
        embedded = document.split(BEGIN_APPLICATION, 1)[1].split(END_APPLICATION, 1)[0].strip()

        if self._writing():
            # splice() replaces exactly what sits between the markers. A bare
            # document.replace(embedded, generated) is a trap when the block is
            # still empty: replacing "" inserts the whole generated text between
            # every character of the document.
            from core.interfaces.evidence import splice

            self.REPORT.write_text(splice(document, None, generated.rstrip("\n") + "\n"))
            pytest.skip("evidence report regenerated")

        assert embedded == generated, (
            "docs/REGISTRY_EVIDENCE.md is out of date with the application; "
            "regenerate with INFORAXIS_WRITE_EVIDENCE=1")

    def test_the_endpoint_inventory_matches_the_application(self, app):
        from core.interfaces.evidence import endpoint_inventory

        generated = endpoint_inventory(app)

        if self._writing():
            self.INVENTORY.write_text(generated)
            pytest.skip("endpoint inventory regenerated")

        assert self.INVENTORY.exists(), (
            "docs/endpoint_inventory.json is missing; regenerate with "
            "INFORAXIS_WRITE_EVIDENCE=1")
        assert self.INVENTORY.read_text() == generated, (
            "docs/endpoint_inventory.json is out of date with the application; "
            "regenerate with INFORAXIS_WRITE_EVIDENCE=1")

    def test_the_inventory_is_deterministic(self, app):
        """Two runs produce the same file, so a diff means a real change."""
        from core.interfaces.evidence import endpoint_inventory

        assert endpoint_inventory(app) == endpoint_inventory(app)
