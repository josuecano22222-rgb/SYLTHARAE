"""The settings the browser shell needs on every page, for every signed-in role.

Found in the browser, not in the suite: every page load produced
``403 GET /api/settings/system`` and a console error, for an **analyst**. The
shell (``static/js/modules/core/system-settings.js``) fetches this on load to
apply the app name, date format, items-per-page and theme; when it is refused
it falls back to compiled defaults, so an analyst sees the *default* UI while
the administrator sees the configured one - a difference nobody would think to
report, and one that is invisible in a rendered-HTML test.

The fix follows the precedent already in the code for ``/api/settings/theme``:
the *presentation* block is readable by anyone signed in, the *operator* block
is not. The distinction is asserted here in both directions, so the fix cannot
silently become "the whole system category is public".
"""

from __future__ import annotations

import pytest

#: Read on every page load, by the shell, to render the page.
PRESENTATION_KEYS = {
    "app_name", "app_icon", "language", "timezone", "date_format", "time_format",
    "theme", "animations_enabled", "notifications_enabled", "show_breadcrumbs",
    "compact_mode", "auto_save", "confirm_actions", "items_per_page",
    "default_sort", "sort_direction",
}

#: Operator detail: how the process logs, and diagnostics that are an
#: administrator's decision. A reader does not need these to render a page.
OPERATOR_KEYS = {"log_level", "log_file", "debug_mode", "screen_inspector",
                 "action_logging_enabled"}


def _system(client):
    response = client.get("/api/settings/system")
    return response, (response.get_json() or {}).get("settings") or {}


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_a_reader_gets_the_presentation_settings_without_an_error(
        app, client_factory, role):
    """200 and the presentation block - this is the console error's root cause."""
    response, settings = _system(client_factory(role))
    assert response.status_code == 200, response.get_data(as_text=True)
    missing = PRESENTATION_KEYS - set(settings)
    assert not missing, f"the shell cannot render without {sorted(missing)}"
    assert settings["app_name"], "the app name must be filled in"


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_a_reader_does_not_get_the_operator_settings(app, client_factory, role):
    """The fix must not publish the log path and debug flags to every reader."""
    _response, settings = _system(client_factory(role))
    leaked = OPERATOR_KEYS & set(settings)
    assert not leaked, f"operator settings leaked to a {role}: {sorted(leaked)}"


def test_an_administrator_still_gets_the_whole_category(app, admin_client):
    response, settings = _system(admin_client)
    assert response.status_code == 200
    missing = (PRESENTATION_KEYS | OPERATOR_KEYS) - set(settings)
    assert not missing, f"the settings page cannot edit what it cannot read: {sorted(missing)}"


def test_the_operator_keys_are_still_admin_only_on_their_own_endpoint(
        app, client_factory):
    """A reader may read the presentation block, not a single operator key."""
    analyst = client_factory("analyst")
    for key in ("log_level", "debug_mode"):
        response = analyst.get(f"/api/settings/system/{key}")
        assert response.status_code in (403, 404), (
            f"GET /api/settings/system/{key} answered {response.status_code}")


def test_the_whole_settings_export_is_still_admin_only(app, client_factory):
    """The exemption is for one path, not for the settings surface."""
    analyst = client_factory("analyst")
    response = analyst.get("/api/settings/")
    assert response.status_code == 403, response.get_data(as_text=True)


def test_the_shell_read_is_not_throttled_while_browsing(app, client_factory):
    """It is read on every page load, like the theme; 75 in a row must pass."""
    analyst = client_factory("analyst")
    codes = {analyst.get("/api/settings/system").status_code for _ in range(75)}
    assert 429 not in codes, codes
