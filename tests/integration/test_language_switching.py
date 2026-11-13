"""Changing the interface language must take effect on the next page.

The reported defect: choosing a language updated the stored choice, but the page
kept rendering in the previous language. Cause: the first request copied the
system language into ``session['language']``, and the resolver checked the
session before the explicit ``user_language`` cookie. Every later choice that
only changed the cookie was ignored.

These tests pin the fix from the first run onward:
1. A fresh install with no account: each choice in the wizard applies on the
   next render, in the same browser, with no session written by the visit.
2. The login-screen switcher (cookie only, no session) applies.
3. Signed-in ``/set_language`` applies each time, and the cookie it sets
   overrides any older session value.
"""

from __future__ import annotations

import re

import pytest

from tests.integration.test_setup_wizard_i18n import uninstalled  # noqa: F401

pytestmark = pytest.mark.integration

RTL = ("ar", "fa", "he")


@pytest.fixture(autouse=True)
def _restore_system_language(app):
    """The wizard persists system.language; put the default back after each test."""
    yield
    from settings import get_settings

    get_settings().set_setting("system.language", "en")
    live = app.config.get("SETTINGS")
    if live is not None:  # the resolver reads this in-memory copy
        live.system.language = "en"
    app.config["BABEL_DEFAULT_LOCALE"] = "en"


def _html_lang(resp):
    """Return (lang, dir) from the page's <html> tag; dir is None when absent."""
    html = resp.get_data(as_text=True)
    tag = re.search(r"<html\b[^>]*>", html)
    assert tag, "page has no <html> tag"
    lang = re.search(r"\blang=[\"']?([\w-]+)", tag.group(0))
    direction = re.search(r"\bdir=[\"']?(\w+)", tag.group(0))
    assert lang, f"<html> tag has no lang: {tag.group(0)}"
    return lang.group(1), direction.group(1) if direction else None


def _expect_dir(lang):
    return "rtl" if lang in RTL else "ltr"


@pytest.mark.parametrize("sequence", [("ar", "fa", "en", "he", "ar")])
def test_wizard_choices_apply_in_order_on_one_browser(app, uninstalled, sequence):
    client = app.test_client()
    assert _html_lang(client.get("/setup"))[0] == "en"  # first visit, no session yet
    for lang in sequence:
        r = client.post("/api/setup/language", json={"language": lang})
        assert r.status_code == 200, r.get_data(as_text=True)
        got, direction = _html_lang(client.get("/setup"))
        assert got == lang, f"chose {lang}, page still {got}"
        assert direction == _expect_dir(lang)


def test_a_wizard_visit_does_not_pin_the_language_in_the_session(app, uninstalled):
    client = app.test_client()
    client.get("/setup")
    with client.session_transaction() as sess:
        assert "language" not in sess


def test_the_switcher_cookie_applies_without_a_session(app):
    client = app.test_client()
    assert _html_lang(client.get("/auth/login"))[0] == "en"
    for lang in ("ar", "he", "fa", "en"):
        client.set_cookie("user_language", lang)
        got, direction = _html_lang(client.get("/auth/login"))
        assert got == lang, f"switched to {lang}, login page still {got}"


def test_signed_in_set_language_applies_each_time(app, admin_client):
    for lang in ("ar", "fa", "en", "he"):
        r = admin_client.get(f"/set_language/{lang}", headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 200
        assert any(c.startswith("user_language=" + lang) for c in r.headers.getlist("Set-Cookie"))
        got, direction = _html_lang(admin_client.get("/", follow_redirects=True))
        assert got == lang
        assert direction == _expect_dir(lang)


def test_a_newer_cookie_overrides_an_older_session_language(app, admin_client):
    admin_client.get("/set_language/he", headers={"X-Requested-With": "XMLHttpRequest"})
    admin_client.set_cookie("user_language", "ar")
    got, _ = _html_lang(admin_client.get("/", follow_redirects=True))
    assert got == "ar"
