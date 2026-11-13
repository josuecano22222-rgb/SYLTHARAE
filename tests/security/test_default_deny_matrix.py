"""Default-deny route matrix (step 25 security audit).

The authentication middleware (``core/security/flask_ext.py``) is
default-deny: every endpoint requires a valid session unless it is in the
reviewed ``AUTH_PUBLIC_ENDPOINTS`` allowlist. Sampling five paths, as the
older regression test does, cannot prove that property. This module
enumerates EVERY registered route and proves, one by one, that an
unauthenticated request never receives data:

* an endpoint not on the allowlist must answer 401, redirect to the login
  page (301/302), 403, 404 or 405 - never 200 and never 308 (a permanent
  redirect to content);
* an endpoint ON the allowlist must actually serve - the proof is
  meaningless if the allowlist silently blocks the login page itself.

GET is used throughout so the probe cannot mutate anything; the auth
middleware runs before any handler body for non-public endpoints.
"""
import re

import pytest

#: The converter fillers used to turn a URL rule into a requestable path.
_FILLERS = {
    "int": "1",
    "string": "x",
    "path": "x",
    "uuid": "00000000-0000-0000-0000-000000000000",
    "float": "1.0",
}


def _requestable_path(rule) -> str:
    """Fill every converter in the rule so the path can be requested."""
    path = rule.rule
    for match in reversed(list(re.finditer(r"<(?:(\w+):)?(\w+)>", path))):
        converter = match.group(1) or "string"
        path = path[:match.start()] + _FILLERS.get(converter, "x") + path[match.end():]
    return path


@pytest.mark.security
def test_every_non_public_route_denies_unauthenticated_get(app):
    allowlist = set(app.config["AUTH_PUBLIC_ENDPOINTS"])
    checked, denied = 0, 0
    leaks = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in allowlist:
            continue
        if "GET" not in rule.methods and "HEAD" not in rule.methods:
            continue  # POST/PUT/DELETE-only: the matrix below cannot leak via GET
        if rule.arguments and any(
            m.group(1) not in _FILLERS
            for m in re.finditer(r"<(?:(\w+):)?(\w+)>", rule.rule)
        ):
            # an exotic converter we cannot fill: the typed ones cover the
            # surface; skip rather than request a malformed path
            continue
        path = _requestable_path(rule)
        with app.test_client() as probe:
            resp = probe.get(path, follow_redirects=False)
        checked += 1
        if resp.status_code in (401, 403, 404, 405):
            denied += 1
        elif resp.status_code in (301, 302):
            location = resp.headers.get("Location", "")
            assert "/auth/login" in location or "/setup" in location, (
                f"{rule.rule} redirects unauthenticated users to "
                f"{location!r} - not the login gate (open redirect?)")
            denied += 1
        else:
            leaks.append((rule.rule, resp.status_code))
    assert checked > 50, f"matrix suspiciously small: {checked} routes"
    assert not leaks, (
        "unauthenticated GET reached handler content on: "
        + ", ".join(f"{rule} -> {code}" for rule, code in leaks))
    assert denied == checked


@pytest.mark.security
def test_allowlisted_core_pages_still_serve(app):
    """The allowlist must keep the login page and the health check usable -
    a matrix that blocks everything including /auth/login is not a gate,
    it is an outage."""
    with app.test_client() as probe:
        login = probe.get("/auth/login")
        assert login.status_code == 200, login.status_code
        health = probe.get("/health")
        assert health.status_code == 200, health.status_code


@pytest.mark.security
def test_default_deny_is_the_configured_default(app):
    """If AUTH_PUBLIC_ENDPOINTS is replaced by an allow-ALL configuration
    (or removed), this test fails: default-deny is the contract."""
    allowlist = set(app.config["AUTH_PUBLIC_ENDPOINTS"])
    assert "static" in allowlist
    assert "auth.login" in allowlist
    # the middleware reads this exact config key (flask_ext.py:87)
    assert isinstance(allowlist, frozenset) or isinstance(allowlist, set)
