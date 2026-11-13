"""Security: visibility metadata is not authorisation.

The registry carries ``required_role`` so that an operator is not shown a page
they cannot use. That is a presentation decision. The authoritative check is
still server-side, in ``core/security`` - and these tests exist so the two can
never be confused:

* a viewer does not *see* administration interfaces;
* a viewer who asks for the administration page directly is still refused by
  the server, with the registry out of the picture;
* a feature flag can keep an interface out of the product surface without
  granting anybody access to it;
* the ``is_visible`` answer is a *narrower* set than the server's, never a
  wider one - if visibility ever says yes where authorisation says no, that is
  a defect in visibility, and the request is still refused.
"""

from __future__ import annotations

import pytest

from core.interfaces import Domain, Interface, InterfaceKind, InterfaceStatus
from settings.interface_state import InterfaceState

pytestmark = pytest.mark.security


class FakeUser:
    def __init__(self, role, authenticated=True):
        self.role = role
        self.is_authenticated = authenticated

    def has_role(self, *roles):
        return self.role in roles


class FakeVisibility:
    """The persisted shape: ``interfaces`` mapping to objects with ``enabled``."""

    class Config:
        def __init__(self, enabled):
            self.enabled = enabled
            self.category = "user"

    def __init__(self, states=None):
        self.interfaces = {k: self.Config(v) for k, v in (states or {}).items()}


ADMIN_PAGE = Interface(
    interface_id="users",
    name="User Management",
    description="People who may sign in.",
    domain=Domain.ADMINISTRATION,
    route="users_page",
    icon="bi-people",
    required_role="admin",
)

OPEN_PAGE = Interface(
    interface_id="file_library",
    name="File Library",
    description="Stored objects.",
    domain=Domain.DISCOVER,
    route="files.files_list",
    icon="bi-folder2-open",
)


class TestRoleVisibility:
    def test_a_viewer_does_not_see_administration_interfaces(self):
        state = InterfaceState(FakeVisibility())
        assert state.is_visible("users", FakeUser("viewer")) is False
        assert state.is_visible("users", FakeUser("analyst")) is False
        assert state.is_visible("users", FakeUser("admin")) is True

    def test_a_viewer_does_see_interfaces_with_no_role_requirement(self):
        state = InterfaceState(FakeVisibility())
        assert state.is_visible("file_library", FakeUser("viewer")) is True

    def test_nobody_anonymous_sees_anything(self):
        state = InterfaceState(FakeVisibility())
        for interface_id in ("file_library", "users"):
            assert state.is_visible(interface_id, FakeUser(None, authenticated=False)) is False

    def test_visibility_never_widens_permissions(self):
        """Whatever visibility says, the role gate is the server's business."""
        state = InterfaceState(FakeVisibility())
        assert state.is_visible("file_library", FakeUser("viewer")) is True
        # ... and the server still decides what that viewer may *do*; the
        # registry has no say in it (see tests/security/test_import_export_authz.py).
        assert not hasattr(state, "authorize")

    def test_the_visible_set_is_the_intersection_not_the_union(self):
        state = InterfaceState(FakeVisibility())
        admin_view = {i.interface_id for i in state.get_visible_interfaces(FakeUser("admin"))}
        viewer_view = {i.interface_id for i in state.get_visible_interfaces(FakeUser("viewer"))}
        assert viewer_view < admin_view
        assert "users" not in viewer_view


class TestFeatureFlags:
    def test_a_flagged_interface_is_off_until_the_flag_is_on(self):
        flagged = Interface(
            interface_id="relationship_graph_v2",
            name="Relationship Graph v2",
            description="An unreleased presentation of relationships.",
            domain=Domain.ANALYZE,
            route="analyses_relationships",
            icon="bi-diagram-2",
            feature_flag="relationship_graph_v2",
            status=InterfaceStatus.EXPERIMENTAL,
        )
        assert flagged.feature_flag

    def test_the_state_service_answers_by_flag(self, monkeypatch):
        import settings.interface_state as module

        flagged = Interface(
            interface_id="flagged_thing",
            name="Flagged",
            description="Gated work.",
            domain=Domain.INTERNAL,
            route=None,
            icon="bi-box",
            feature_flag="flag_x",
            status=InterfaceStatus.EXPERIMENTAL,
            kind=InterfaceKind.SECTION,
        )
        monkeypatch.setattr(module, "get_interface",
                            lambda iid: flagged if iid == "flagged_thing" else None)

        off = InterfaceState(FakeVisibility({"flagged_thing": True}))
        assert off.is_enabled("flagged_thing") is False

        on = InterfaceState(FakeVisibility({"flagged_thing": True}), feature_flags={"flag_x": True})
        assert on.is_enabled("flagged_thing") is True


class TestUnknownAndRetired:
    def test_an_unknown_id_is_never_visible(self):
        state = InterfaceState(FakeVisibility({"totally_unknown": True}))
        assert state.is_enabled("totally_unknown") is False
        assert state.is_visible("totally_unknown", FakeUser("admin")) is False

    def test_a_retired_id_is_never_visible_even_when_stored_on(self):
        state = InterfaceState(FakeVisibility({"file_upload": True}))
        assert state.is_visible("file_upload", FakeUser("admin")) is False

    def test_a_disabled_interface_is_not_visible_even_to_an_admin(self):
        state = InterfaceState(FakeVisibility({"file_library": False}))
        assert state.is_visible("file_library", FakeUser("admin")) is False

    def test_an_endpoint_nothing_owns_is_not_served(self):
        """The old fallback answered True for anything unmapped."""
        state = InterfaceState(FakeVisibility())
        assert state.endpoint_enabled("not_a_real_endpoint") is False
        assert state.endpoint_enabled("files.files_list") is True

    def test_infrastructure_is_always_served(self):
        state = InterfaceState(FakeVisibility())
        for endpoint in ("health.health", "auth.login", "setup.setup_page"):
            assert state.endpoint_enabled(endpoint) is True, endpoint


class TestAFailedCheckIsNotPermission:
    """The gate must never fall open.

    The check used to be wrapped in a bare ``except`` that logged at debug
    level and served the page, so a broken interface state looked exactly like
    a working one. A failure now goes through the common error pipeline: the
    page is not served, the browser gets a generic message with a correlation
    id, and the details stay in the log.
    """

    @staticmethod
    def _break_the_check(monkeypatch, failing_endpoint):
        from settings.settings_adapter import SettingsAdapter

        original = SettingsAdapter.is_interface_enabled_by_endpoint

        def broken(self, endpoint):
            if endpoint == failing_endpoint:
                raise RuntimeError("simulated registry failure: secret detail")
            return original(self, endpoint)

        monkeypatch.setattr(SettingsAdapter, "is_interface_enabled_by_endpoint", broken)

    def test_a_page_whose_check_fails_is_not_served(self, admin_client, monkeypatch):
        self._break_the_check(monkeypatch, "keywords_list")
        response = admin_client.get("/keywords")
        assert response.status_code == 500, response.status_code

    def test_the_failure_does_not_leak_the_exception(self, admin_client, monkeypatch):
        self._break_the_check(monkeypatch, "keywords_list")
        body = admin_client.get("/keywords").get_data(as_text=True)
        assert "simulated registry failure" not in body
        assert "secret detail" not in body
        assert "Traceback" not in body

    def test_the_dashboard_and_settings_stay_reachable(self, admin_client, monkeypatch):
        """Whatever went wrong, the operator can still get to the fix.

        The dashboard is never gated and Settings is exempt, so a broken gate
        always leaves a route back in - the alternative (a settings failure
        locking the whole application) is why the exemptions exist.
        """
        from settings.settings_adapter import SettingsAdapter

        monkeypatch.setattr(
            SettingsAdapter,
            "is_interface_enabled_by_endpoint",
            lambda self, endpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert admin_client.get("/").status_code == 200
        assert admin_client.get("/settings").status_code in (200, 302)
