"""Regression: the command-line installer must work on a clean checkout.

Two defects found by the round-10 clean-install drill (`git clone` of the
branch, fresh venv, fresh PostgreSQL 16 cluster, `python install.py
--non-interactive`):

* ``write_config_json`` did ``data["app"]["debug_mode"] = ...`` on a dict
  seeded only from ``config.example.json``. That file is not in the repository
  (``git ls-files | grep config.example`` is empty - only ``.env.example`` is
  tracked), so ``data`` was ``{}`` and the installer died with
  ``KeyError: 'app'`` before writing anything.
* ``run_dependency_check`` imported the *distribution* name as a module
  (``import python-docx``), which never imports, so a fully installed
  environment reported "11 missing" and re-ran pip over the whole manifest;
  the reinstall then printed "All packages installed" without ever checking.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _installer():
    spec = importlib.util.spec_from_file_location("sylth_install_cli", PROJECT_ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONFIG = {
    "FLASK_ENV": "production",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USER": "postgres",
    "DB_PASSWORD": "",
    "DB_NAME": "analysis",
    "APP_ADMIN_USERNAME": "admin",
    "APP_ADMIN_PASSWORD": "Secret-Passw0rd!",
    "PASSWORD_MIN_LENGTH": "12",
    "SECURITY_MAX_FAILED_LOGINS": "5",
    "SECURITY_LOCKOUT_MINUTES": "15",
    "SECURITY_SESSION_HOURS": "12",
    "SECURITY_SESSION_IDLE_HOURS": "6",
    "RATE_LIMIT_PER_MINUTE": "60",
    "RATE_LIMIT_PER_HOUR": "600",
    "FLASK_SECRET_KEY": "",
    "FLASK_HOST": "0.0.0.0",
    "FLASK_PORT": "5000",
    "MAX_WORKERS": "8",
    "FILE_PROCESSING_TIMEOUT": "1200",
    "INGESTION_ROOTS": "",
    "LOG_LEVEL": "INFO",
}


def test_config_json_is_written_when_there_is_no_example_file(tmp_path, monkeypatch):
    installer = _installer()
    monkeypatch.setattr(installer, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(installer, "CONFIG_EXAMPLE", tmp_path / "config.example.json")
    assert not (tmp_path / "config.example.json").exists()

    installer.write_config_json(_CONFIG)

    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert written["environment"] == "production"
    assert written["app"]["debug_mode"] is False
    assert written["database"]["database"] == "analysis"
    assert written["security"]["rate_limit"]["per_minute"] == 60
    assert written["admin"]["username"] == "admin"
    assert written["processing"]["max_workers"] == 8


def test_the_example_file_is_used_when_it_exists(tmp_path, monkeypatch):
    installer = _installer()
    example = tmp_path / "config.example.json"
    example.write_text(json.dumps({
        "environment": "production",
        "app": {"debug_mode": True, "theme": "dark"},
        "database": {},
        "security": {"rate_limit": {}},
        "admin": {},
        "processing": {},
        "ingestion": {"roots": []},
    }), encoding="utf-8")
    monkeypatch.setattr(installer, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(installer, "CONFIG_EXAMPLE", example)

    installer.write_config_json(_CONFIG)

    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    # The example's unknown keys survive; the gathered config wins.
    assert written["app"]["theme"] == "dark"
    assert written["app"]["debug_mode"] is False


def test_an_installed_environment_reports_no_missing_packages(tmp_path):
    installer = _installer()
    assert installer._missing_requirements(PROJECT_ROOT / "requirements.txt") == []


def test_a_genuinely_absent_package_is_reported(tmp_path):
    installer = _installer()
    req = tmp_path / "requirements.txt"
    req.write_text("Flask>=3.0\nsyltharae-package-that-does-not-exist>=1.0\n", encoding="utf-8")
    assert installer._missing_requirements(req) == ["syltharae-package-that-does-not-exist>=1.0"]
