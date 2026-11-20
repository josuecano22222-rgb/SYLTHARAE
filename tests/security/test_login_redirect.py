"""The sign-in redirect may only continue inside this application.

``/auth/login?next=`` carries the page to return to after a successful sign-in,
and the value comes from the URL - so it is attacker-controlled. The check was
"starts with / and not with //", which stops ``//evil.example`` and
``https://evil.example`` but not the backslash form:

    /auth/login?next=/\\evil.example

Browsers normalise ``\\`` to ``/`` before resolving a URL, so that becomes
``//evil.example``; the user signs in on the real site and is then handed to
somebody else's. Percent-encoding does not help (``%5C`` is decoded first), so
the raw character has to be refused by the server.

These tests exercise the form-encoded sign-in, which is the one that answers
with a 302 - the JSON branch returns the target in the body and a client
navigation follows it just the same.
"""

from __future__ import annotations

import pytest

from Api.routes.auth import _safe_next_url

pytestmark = pytest.mark.security

HOSTILE = (
    "/\\evil.example",
    "/%5Cevil.example",
    "\\\\evil.example",
    "//evil.example",
    "///evil.example",
    "https://evil.example/x",
    "http://evil.example",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "/\t/evil.example",
    "/\r\nSet-Cookie: x=1",
    "",
)

SAFE = ("/", "/files", "/operations/jobs?state=failed", "/settings#storage")


@pytest.mark.parametrize("value", HOSTILE)
def test_a_hostile_continuation_becomes_the_application_root(value):
    assert _safe_next_url(value) == "/", f"{value!r} survived the check"


@pytest.mark.parametrize("value", SAFE)
def test_a_same_origin_path_is_kept(value):
    assert _safe_next_url(value) == value


def test_sign_in_with_a_hostile_next_answers_a_local_location(app):
    """The end-to-end shape: 302 and a Location that stays on this host."""
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    response = client.post("/auth/login?next=/\\evil.example",
                           data={"username": "nobody", "password": "nothing"},
                           headers={"Accept": "text/html"})
    # Wrong credentials: nothing is issued at all, which is the safest answer.
    assert response.status_code in (200, 401)

    from core.security.service import get_auth_service

    username, password = "redirect_probe", "redirect-probe-password-123"
    auth = get_auth_service()
    if auth.get_user_by_username(username) is None:
        auth.create_user(username, password, role="viewer")
    response = client.post("/auth/login?next=/\\evil.example",
                           data={"username": username, "password": password},
                           headers={"Accept": "text/html"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/", response.headers["Location"]
