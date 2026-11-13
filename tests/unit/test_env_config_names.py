"""AUDIT-CONF-01: documented environment names must actually be honoured.

``.env.example`` and the installer write ``DB_POOL_MIN_CONNECTIONS`` /
``DB_POOL_MAX_CONNECTIONS`` and ``LOG_LEVEL``; before the fix the runtime only
read ``DB_MIN_CONNECTIONS`` / ``DB_MAX_CONNECTIONS`` and hard-coded INFO.
"""
import re
import sys
from pathlib import Path

import pytest

from database.database.config import DatabaseConfig

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def no_settings(monkeypatch):
    """Force DatabaseConfig.from_env onto its environment fallback path."""
    import settings

    def _boom():
        raise RuntimeError("settings unavailable (test)")

    monkeypatch.setattr(settings, "get_database_config", _boom, raising=False)
    for name in ("DB_MIN_CONNECTIONS", "DB_MAX_CONNECTIONS",
                 "DB_POOL_MIN_CONNECTIONS", "DB_POOL_MAX_CONNECTIONS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_documented_pool_names_are_read(no_settings):
    no_settings.setenv("DB_POOL_MIN_CONNECTIONS", "3")
    no_settings.setenv("DB_POOL_MAX_CONNECTIONS", "7")
    cfg = DatabaseConfig.from_env()
    assert (cfg.min_connections, cfg.max_connections) == (3, 7)


def test_legacy_pool_names_still_win(no_settings):
    no_settings.setenv("DB_POOL_MAX_CONNECTIONS", "7")
    no_settings.setenv("DB_MAX_CONNECTIONS", "9")
    assert DatabaseConfig.from_env().max_connections == 9


def test_pool_defaults_unchanged(no_settings):
    cfg = DatabaseConfig.from_env()
    assert (cfg.min_connections, cfg.max_connections) == (1, 20)


def test_web_app_honours_log_level():
    src = (REPO / "apps" / "web" / "app.py").read_text(encoding="utf-8")
    block = src[src.index("logging.basicConfig("):][:200]
    assert "level=logging.INFO," not in block, "log level must not be hard-coded"
    assert 'os.environ.get("LOG_LEVEL"' in src


def test_every_documented_live_variable_is_read_by_code():
    """Each variable .env.example presents as live must appear in the code."""
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    names = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{2,})=", example, re.M))
    reserved = {"MAX_WORKERS", "FILE_CHUNK_SIZE", "FILE_PROCESSING_TIMEOUT",
                "DB_POOL_TIMEOUT", "DB_QUERY_TIMEOUT",
                "ACTION_LOGGING_ENABLED", "PERFORMANCE_MONITORING"}
    code = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in REPO.rglob("*.py")
        if "tests" not in p.parts and ".venv" not in str(p)
        and p.name != "installer.py"
    )
    missing = sorted(n for n in names - reserved if f'"{n}"' not in code and f"'{n}'" not in code)
    assert not missing, f".env.example documents variables no code reads: {missing}"
    doc = (REPO / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    for name in reserved:
        assert name in example and name in doc


def test_database_get_db_config_honours_env_changed_at_runtime(monkeypatch):
    """AUDIT-HEALTH-01: env set after settings were cached must win (ARCH-02)."""
    from database import get_db_config
    from settings import get_database_config
    get_database_config()  # make sure the settings object is cached first
    monkeypatch.setenv("DB_HOST", "/run/after-install-socket")
    monkeypatch.setenv("DB_NAME", "installed_db")
    monkeypatch.setenv("DB_PORT", "5433")
    cfg = get_db_config()
    assert (cfg["host"], cfg["database"], cfg["port"]) == ("/run/after-install-socket", "installed_db", 5433)
