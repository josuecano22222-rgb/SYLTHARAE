"""``--non-interactive`` must be able to take the admin password from the environment.

Found in the round-10 clean-install drill on a fresh clone with a fresh venv:
with ``APP_ADMIN_PASSWORD`` exported and 23 characters long, the installer still
stopped at

    ✓ Database & schema      Created 'syltharae_p2fresh'; 3 migration(s) applied
    ✗ Administrator          Password must be at least 12 characters

The cause is that ``gather_config`` builds its defaults from the ``.env`` *file*
only. On a clean checkout there is no ``.env`` (and ``.env.example`` cannot carry
a password), so in non-interactive mode every password prompt returns its empty
default: an unattended install could never create an administrator. Environment
variables are the documented way to configure a deployment (.env.example, the
deployment docs), so they have to be read - and they win over the file, which is
the precedence the rest of the application already uses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

installer = pytest.importorskip("install")


@pytest.fixture()
def no_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "ENV_FILE", tmp_path / ".env")
    return tmp_path / ".env"


def test_the_admin_password_can_come_from_the_environment(no_env_file, monkeypatch):
    monkeypatch.setenv("APP_ADMIN_PASSWORD", "Fresh-Install-Passw0rd!")
    monkeypatch.setenv("APP_ADMIN_USERNAME", "analyst")
    monkeypatch.setenv("DB_NAME", "syltharae_p2fresh")

    config = installer.gather_config(ni=True)

    assert config["APP_ADMIN_PASSWORD"] == "Fresh-Install-Passw0rd!"
    assert config["APP_ADMIN_USERNAME"] == "analyst"
    assert config["DB_NAME"] == "syltharae_p2fresh"


def test_the_environment_wins_over_the_env_file(no_env_file, monkeypatch):
    no_env_file.write_text("DB_NAME=from_file\nDB_HOST=/tmp/from_file\n", encoding="utf-8")
    monkeypatch.setenv("DB_NAME", "from_environment")
    # DB_HOST is set by other tests' fixtures when the whole suite runs; this
    # test is about the precedence rule, so start from a clean pair.
    monkeypatch.delenv("DB_HOST", raising=False)

    config = installer.gather_config(ni=True)

    assert config["DB_NAME"] == "from_environment"
    # Keys only in the file still apply.
    assert config["DB_HOST"] == "/tmp/from_file"


def test_a_missing_env_file_is_not_an_error(no_env_file, monkeypatch):
    monkeypatch.delenv("APP_ADMIN_PASSWORD", raising=False)
    config = installer.gather_config(ni=True)
    assert config["APP_ADMIN_PASSWORD"] == ""
