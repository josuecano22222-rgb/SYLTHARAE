"""ARCH-02 regression tests: configuration precedence is defaults < file < env.

The combined-suite incident: a settings file persisted with host=localhost
was served even after the test environment exported DB_HOST/DB_NAME pointing
at an ephemeral PostgreSQL. These tests pin the read-time precedence
guarantee at both consumption boundaries.
"""



def _with_file_settings(monkeypatch, tmp_path):
    """Persist a settings file that claims localhost and force its use."""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.delenv("DB_PORT", raising=False)
    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)

    import settings.settings_manager as sm
    import settings.settings_adapter as sa

    monkeypatch.setattr(sm, "_settings_manager", None)
    monkeypatch.setattr(sa, "_interface_manager", None)

    mgr = sm.get_settings_manager()
    mgr.settings.database.host = "localhost"
    mgr.settings.database.database = "analysis"
    mgr.save()

    # Simulate a long-lived process where the singleton was built earlier.
    return mgr


def test_env_overrides_settings_file_at_read_time(monkeypatch, tmp_path):
    _with_file_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("DB_HOST", "/tmp/sock")
    monkeypatch.setenv("DB_NAME", "file_analysis_test")
    monkeypatch.setenv("DB_PORT", "5433")

    from settings.config import get_db_config

    cfg = get_db_config()
    assert cfg["host"] == "/tmp/sock"
    assert cfg["database"] == "file_analysis_test"
    assert cfg["port"] == 5433


def test_settings_file_used_when_no_env(monkeypatch, tmp_path):
    _with_file_settings(monkeypatch, tmp_path)

    from settings.config import get_db_config

    cfg = get_db_config()
    assert cfg["host"] == "localhost"
    assert cfg["database"] == "analysis"


def test_db_hub_config_reads_env_at_construction(monkeypatch, tmp_path):
    _with_file_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("DB_HOST", "db.example.internal")
    monkeypatch.setenv("DB_NAME", "prod_db")
    monkeypatch.setenv("DB_USER", "svc")

    from database.database.config import DatabaseConfig

    cfg = DatabaseConfig.from_env()
    assert cfg.host == "db.example.internal"
    assert cfg.dbname == "prod_db"
    assert cfg.user == "svc"
    # Defaults still present for values the env did not set.
    assert cfg.port == 5432
