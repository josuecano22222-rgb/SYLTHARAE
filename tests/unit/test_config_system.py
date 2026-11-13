"""Tests for the configuration system (config/ module).

Covers:
  - Environment detection
  - Environment overlay loading
  - Deep merge logic
  - Effective config loading
  - Config validation (valid, invalid, boundary cases)
  - Secret masking
  - Dot-notation value access
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
class TestGetEnvironment:
    def test_default_is_production(self):
        from config import get_environment
        with mock.patch.dict(os.environ, {}, clear=True):
            assert get_environment() == "production"

    def test_reads_flask_env(self):
        from config import get_environment
        with mock.patch.dict(os.environ, {"FLASK_ENV": "staging"}):
            assert get_environment() == "staging"

    def test_case_insensitive(self):
        from config import get_environment
        with mock.patch.dict(os.environ, {"FLASK_ENV": "Development"}):
            assert get_environment() == "development"

    def test_whitespace_stripped(self):
        from config import get_environment
        with mock.patch.dict(os.environ, {"FLASK_ENV": "  production  "}):
            assert get_environment() == "production"


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------
class TestDeepMerge:
    def test_flat_merge(self):
        from config import deep_merge
        result = deep_merge({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        from config import deep_merge
        base = {"db": {"host": "localhost", "port": 5432}}
        over = {"db": {"port": 5433}}
        result = deep_merge(base, over)
        assert result["db"]["host"] == "localhost"
        assert result["db"]["port"] == 5433

    def test_overlay_wins_on_type_conflict(self):
        from config import deep_merge
        result = deep_merge({"a": {"nested": True}}, {"a": "replaced"})
        assert result["a"] == "replaced"

    def test_metadata_keys_skipped(self):
        from config import deep_merge
        result = deep_merge({"a": 1}, {"_comment": "ignored", "b": 2})
        assert "_comment" not in result
        assert result["b"] == 2

    def test_does_not_mutate_inputs(self):
        from config import deep_merge
        base = {"a": {"x": 1}}
        over = {"a": {"y": 2}}
        result = deep_merge(base, over)
        assert "y" not in base["a"]
        assert result["a"]["y"] == 2


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
class TestValidateConfig:
    def _valid_config(self):
        return {
            "environment": "production",
            "database": {
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "database": "analysis",
                "pool": {"min_connections": 2, "max_connections": 25},
                "query_timeout": 60,
            },
            "security": {
                "password_min_length": 12,
                "max_failed_logins": 5,
                "lockout_minutes": 15,
                "session_hours": 12,
                "session_idle_hours": 6,
                "rate_limit": {"per_minute": 60, "per_hour": 600},
            },
            "processing": {
                "max_workers": 8,
                "file_processing_timeout": 1200,
            },
            "admin": {"username": "admin", "password": ""},
            "ingestion": {"roots": []},
        }

    def test_valid_config_passes(self):
        from config import validate_config
        valid, errors = validate_config(self._valid_config())
        assert valid is True
        assert errors == []

    def test_invalid_environment(self):
        from config import validate_config
        cfg = self._valid_config()
        cfg["environment"] = "invalid"
        valid, errors = validate_config(cfg)
        assert valid is False
        assert any("environment" in e for e in errors)

    def test_invalid_port(self):
        from config import validate_config
        cfg = self._valid_config()
        cfg["database"]["port"] = 99999
        valid, errors = validate_config(cfg)
        assert valid is False
        assert any("port" in e for e in errors)

    def test_min_conn_exceeds_max(self):
        from config import validate_config
        cfg = self._valid_config()
        cfg["database"]["pool"]["min_connections"] = 30
        cfg["database"]["pool"]["max_connections"] = 5
        valid, errors = validate_config(cfg)
        assert valid is False
        assert any("min_connections" in e and "exceed" in e for e in errors)

    def test_password_too_short(self):
        from config import validate_config
        cfg = self._valid_config()
        cfg["security"]["password_min_length"] = 4
        valid, errors = validate_config(cfg)
        assert valid is False
        assert any("password_min_length" in e for e in errors)

    def test_rate_limit_per_minute_exceeds_hourly(self):
        from config import validate_config
        cfg = self._valid_config()
        cfg["security"]["rate_limit"]["per_minute"] = 1000
        cfg["security"]["rate_limit"]["per_hour"] = 100
        valid, errors = validate_config(cfg)
        assert valid is False
        assert any("per_minute" in e and "exceed" in e for e in errors)

    def test_zero_workers(self):
        from config import validate_config
        cfg = self._valid_config()
        cfg["processing"]["max_workers"] = 0
        valid, errors = validate_config(cfg)
        assert valid is False
        assert any("max_workers" in e for e in errors)

    def test_empty_config_passes(self):
        """An empty config uses defaults which are valid."""
        from config import validate_config
        valid, errors = validate_config({})
        assert valid is True


# ---------------------------------------------------------------------------
# Dot-notation access
# ---------------------------------------------------------------------------
class TestGetConfigValue:
    def test_simple_key(self):
        from config import get_config_value
        assert get_config_value("environment", config={"environment": "staging"}) == "staging"

    def test_nested_key(self):
        from config import get_config_value
        cfg = {"database": {"host": "db.example.com"}}
        assert get_config_value("database.host", config=cfg) == "db.example.com"

    def test_missing_key_returns_default(self):
        from config import get_config_value
        assert get_config_value("nonexistent.key", default="fallback", config={}) == "fallback"

    def test_deeply_nested(self):
        from config import get_config_value
        cfg = {"security": {"rate_limit": {"per_minute": 30}}}
        assert get_config_value("security.rate_limit.per_minute", config=cfg) == 30


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------
class TestMaskSecrets:
    def test_masks_password(self):
        from config import mask_secrets
        cfg = {"database": {"password": "supersecretpassword123"}}
        masked = mask_secrets(cfg)
        assert masked["database"]["password"].startswith("****")
        assert masked["database"]["password"] != "supersecretpassword123"
        # Original not mutated
        assert cfg["database"]["password"] == "supersecretpassword123"

    def test_masks_short_password(self):
        from config import mask_secrets
        cfg = {"database": {"password": "ab"}}
        masked = mask_secrets(cfg)
        assert masked["database"]["password"] == "****"

    def test_empty_password_unchanged(self):
        from config import mask_secrets
        cfg = {"database": {"password": ""}}
        masked = mask_secrets(cfg)
        assert masked["database"]["password"] == ""

    def test_non_password_values_preserved(self):
        from config import mask_secrets
        cfg = {"database": {"host": "localhost", "port": 5432}}
        masked = mask_secrets(cfg)
        assert masked["database"]["host"] == "localhost"
        assert masked["database"]["port"] == 5432


# ---------------------------------------------------------------------------
# Environment overlay loading (from files)
# ---------------------------------------------------------------------------
class TestEnvironmentOverlay:
    def test_overlay_file_loaded(self, tmp_path):
        from config import deep_merge
        overlay = {"app": {"debug_mode": True}, "environment": "development"}
        _write_json(tmp_path / "development.json", overlay)

        # Patch _CONFIG_DIR to point to tmp_path
        import config as config_mod
        original = config_mod._CONFIG_DIR
        config_mod._CONFIG_DIR = tmp_path
        try:
            result = config_mod.load_environment_overlay("development")
            assert result["app"]["debug_mode"] is True
        finally:
            config_mod._CONFIG_DIR = original

    def test_missing_overlay_returns_empty(self, tmp_path):
        import config as config_mod
        original = config_mod._CONFIG_DIR
        config_mod._CONFIG_DIR = tmp_path
        try:
            result = config_mod.load_environment_overlay("nonexistent")
            assert result == {}
        finally:
            config_mod._CONFIG_DIR = original
