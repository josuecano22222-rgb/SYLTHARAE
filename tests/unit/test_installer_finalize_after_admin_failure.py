"""A failed administrator step must not leave the system "initialized".

Found by the round-10 clean-install drill. ``install.py --non-interactive`` on
an install directory whose ``.env`` no longer carried ``APP_ADMIN_PASSWORD``:

    ✓ Database & schema   Created 'syltharae_clean'; 3 migration(s) applied
    ✗ Administrator       Password must be at least 12 characters
    ✓ Runtime directories  7 directories ensured
    ✓ Finalize            System marked as initialized

Steps D and E ran anyway, so ``.system_initialized`` was written with
``SELECT count(*) FROM users`` = 0. The result is a system nobody can log in
to: /auth/login has no account, and /setup refuses because the marker says the
installation is done. The administrator step is the one step whose failure
leaves the system unusable, so it has to stop the sequence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import installer  # noqa: E402

_CFG = {
    "DB_HOST": "localhost", "DB_PORT": "5432", "DB_USER": "postgres",
    "DB_PASSWORD": "", "DB_NAME": "analysis",
    "APP_ADMIN_USERNAME": "admin", "APP_ADMIN_PASSWORD": "",
    "FLASK_ENV": "production", "FLASK_PORT": "5000", "FLASK_HOST": "0.0.0.0",
}


@pytest.fixture
def no_marker(monkeypatch, tmp_path):
    """Record whether the initialization marker was written."""
    written = tmp_path / ".system_initialized"
    calls = {"mark": 0}

    def _mark():
        calls["mark"] += 1
        written.write_text("")

    monkeypatch.setattr(installer, "mark_system_initialized", _mark)
    monkeypatch.setattr(installer, "write_env_file", lambda config: None)
    monkeypatch.setattr(installer, "apply_config_to_environ", lambda config: None)
    monkeypatch.setattr(
        installer, "ensure_database_and_schema",
        lambda *a, **k: {"database_created": True, "applied_migrations": ["0001"]})
    monkeypatch.setattr(
        installer, "ensure_gazetteer_seed",
        lambda *a, **k: {"place_count": 223, "name_count": 1155, "changed": True})
    yield written, calls


def test_a_failed_admin_step_stops_before_the_marker(no_marker):
    written, calls = no_marker

    def _fail_admin(username, password, ip_address=""):
        raise ValueError("Password must be at least 12 characters")

    import core.installer as module

    original = module.ensure_admin_account
    module.ensure_admin_account = _fail_admin
    try:
        result = installer.run_installation(_CFG)
    finally:
        module.ensure_admin_account = original

    assert result["ok"] is False
    names = [step["name"] for step in result["steps"]]
    assert "Administrator" in names
    admin_step = next(s for s in result["steps"] if s["name"] == "Administrator")
    assert admin_step["ok"] is False
    assert "Finalize" not in names, (
        "the installer marked the system initialized although no administrator "
        "exists; the only recovery is deleting the marker by hand"
    )
    assert calls["mark"] == 0
    assert not written.exists()
    assert "administrator" in (result.get("error") or "").lower()


def test_a_successful_install_still_marks_the_system(no_marker):
    written, calls = no_marker
    import core.installer as module

    original = module.ensure_admin_account
    module.ensure_admin_account = lambda username, password, ip="": {"created": True}
    try:
        result = installer.run_installation(_CFG)
    finally:
        module.ensure_admin_account = original

    assert result["ok"] is True
    assert [s["name"] for s in result["steps"]][-1] == "Finalize"
    assert written.exists() and calls["mark"] == 1
