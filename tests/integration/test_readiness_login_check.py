"""The readiness checker must be usable on a fresh installation.

``verify_readiness.py`` is the operator-facing gate, so its checks have to
describe the deployment truthfully. The login check asserted ``/auth/login``
returns 200 and therefore failed with "returned 302" on every fresh install:
the application redirects the login route to the first-run setup page, which is
exactly what a new deployment does before an administrator exists. The check now
follows that redirect and proves the setup page is served (and still fails if
the setup page itself does not load).
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import verify_readiness  # noqa: E402

pytestmark = pytest.mark.usefixtures("pg_db")


def _registered(name):
    for fn in verify_readiness.CHECKS:
        if getattr(fn, "check_name", None) == name:
            return fn
    raise AssertionError(f"readiness check {name!r} not registered")


def test_login_check_accepts_the_first_run_redirect():
    """302 to /setup is a valid state, not a readiness failure."""
    result = _registered("Login endpoint available")()
    assert result.passed, result.error
    assert "/auth/login" in result.evidence
    # Either the login page was served directly, or the first-run setup page
    # was reached through the redirect - both are usable deployments.
    assert "302" in result.evidence or "200" in result.evidence, result.evidence


def test_every_check_is_registered_with_a_name_and_section():
    """A check that cannot be identified cannot be reported."""
    assert verify_readiness.CHECKS, "no readiness checks registered"
    for fn in verify_readiness.CHECKS:
        assert getattr(fn, "check_name", None), fn
        result = None
        try:
            result = fn()
        except AssertionError:  # pragma: no cover - defensive
            raise
        assert result is not None
        assert result.section, result
