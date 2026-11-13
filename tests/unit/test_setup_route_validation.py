"""Contract tests for validation at the unauthenticated first-run setup API."""
from __future__ import annotations

from pathlib import Path

from flask import Flask
import pytest

from Api.routes import setup as setup_routes


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup_client():
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(TESTING=True, SECRET_KEY="setup-route-test-secret")
    # The setup API translates its messages with flask_babel, which needs Babel
    # registered on the app (the production app always does this).
    from flask_babel import Babel
    Babel(app)
    app.jinja_env.globals.update(
        _=lambda message, **values: message % values if values else message,
        csrf_token=lambda: "setup-test-csrf-token",
        current_language="en",
    )
    app.register_blueprint(setup_routes.setup_bp)
    app.add_url_rule("/", endpoint="index", view_func=lambda: "home")
    return app.test_client()


def test_setup_wizard_page_renders_its_required_controls(setup_client, monkeypatch):
    monkeypatch.setattr(setup_routes, "_is_initialized", lambda: False)

    response = setup_client.get("/setup")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for control_id in (
        "db_host", "db_port", "db_user", "db_name", "db_password",
        "admin_username", "admin_password", "admin_password2", "sec_pw_min",
        "app_timeout", "btn-test-db", "btn-back", "btn-next",
    ):
        assert f'id="{control_id}"' in html
    assert 'role="list" aria-label="Installation steps"' in html
    assert 'aria-live="polite" aria-atomic="true"' in html


def test_database_test_requires_a_json_object_and_validates_before_connecting(
    setup_client, monkeypatch
):
    # The endpoint is only open during the first-run window (AUDIT-SETUP-01).
    monkeypatch.setattr(setup_routes, "_is_initialized", lambda: False)
    calls = []
    monkeypatch.setattr(
        "core.installer.test_database_connection",
        lambda **kwargs: calls.append(kwargs) or {"ok": True},
    )

    for body in (None, [], "not an object"):
        response = setup_client.post("/api/setup/test-database", json=body)
        assert response.status_code == 400
        assert response.json["ok"] is False
    assert calls == []

    for body in (
        {"host": 17, "password": "secret"},
        {"host": "db", "port": True, "password": "secret"},
        {"host": "db", "port": 5432.5, "password": "secret"},
        {"host": "db", "port": 65536, "password": "secret"},
        {"host": "db", "password": ""},
    ):
        response = setup_client.post("/api/setup/test-database", json=body)
        assert response.status_code == 400
        assert response.json["ok"] is False
    assert calls == []

    response = setup_client.post(
        "/api/setup/test-database",
        json={
            "host": "  database.internal  ",
            "port": "5433",
            "user": "  analyst  ",
            "password": "  pass phrase  ",
            "database": "  analysis  ",
        },
    )
    assert response.status_code == 200
    assert response.json == {"ok": True}
    assert calls == [{
        "host": "database.internal",
        "port": 5433,
        "user": "analyst",
        "password": "  pass phrase  ",
        "database": "analysis",
    }]


def test_install_rejects_non_object_json_without_running_installer(setup_client, monkeypatch):
    monkeypatch.setattr(setup_routes, "_reject_if_initialized", lambda: None)
    calls, ips = [], []
    monkeypatch.setattr(
        "core.installer.run_installation",
        lambda config, client_ip="": calls.append(config) or ips.append(client_ip) or {"ok": True},
    )

    for body in (None, [], "not an object"):
        response = setup_client.post("/api/setup/install", json=body)
        assert response.status_code == 400
        assert response.json["ok"] is False
    assert calls == []
    assert not setup_routes._install_lock.locked()


def test_install_validates_configuration_bounds_and_relationships_before_running(
    setup_client, monkeypatch
):
    monkeypatch.setattr(setup_routes, "_reject_if_initialized", lambda: None)
    calls, ips = [], []
    monkeypatch.setattr(
        "core.installer.run_installation",
        lambda config, client_ip="": calls.append(config) or ips.append(client_ip) or {"ok": True},
    )
    base = {
        "db_password": "database secret",
        "admin_password": "a-secure-password-12",
    }
    invalid_payloads = (
        {**base, "db_port": True},
        {**base, "db_port": 65536},
        {**base, "password_min_length": 7},
        {**base, "admin_password": "short"},
        {**base, "admin_password": "x" * 257},
        {**base, "environment": "unknown"},
        {**base, "log_level": "TRACE"},
        {**base, "session_hours": 4, "session_idle_hours": 5},
        {**base, "rate_limit_per_minute": 601, "rate_limit_per_hour": 600},
        {**base, "db_host": "db\nhost"},
    )

    for payload in invalid_payloads:
        response = setup_client.post("/api/setup/install", json=payload)
        assert response.status_code == 400, payload
        assert response.json["ok"] is False
    assert calls == []
    assert not setup_routes._install_lock.locked()


def test_install_passes_normalized_bounded_configuration_to_installer(
    setup_client, monkeypatch
):
    monkeypatch.setattr(setup_routes, "_reject_if_initialized", lambda: None)
    calls, ips = [], []
    monkeypatch.setattr(
        "core.installer.run_installation",
        lambda config, client_ip="": calls.append(config) or ips.append(client_ip) or {"ok": True},
    )
    response = setup_client.post(
        "/api/setup/install",
        json={
            "db_host": " db.internal ",
            "db_port": "5433",
            "db_user": " analyst ",
            "db_password": " db password ",
            "db_name": " analysis ",
            "admin_username": " administrator ",
            "admin_password": "a-secure-password-12",
            "environment": "staging",
            "flask_host": "0.0.0.0",
            "flask_port": "5050",
            "max_workers": "4",
            "log_level": "WARNING",
            "ingestion_roots": " /srv/incoming ",
            "max_failed_logins": "7",
            "lockout_minutes": "20",
            "session_hours": "24",
            "session_idle_hours": "8",
            "password_min_length": "12",
            "rate_limit_per_minute": "100",
            "rate_limit_per_hour": "1000",
            "file_processing_timeout": "1800",
        },
    )

    assert response.status_code == 200
    assert response.json == {"ok": True, "redirect": "/"}
    assert calls == [{
        "DB_HOST": "db.internal",
        "DB_PORT": "5433",
        "DB_USER": "analyst",
        "DB_PASSWORD": " db password ",
        "DB_NAME": "analysis",
        "APP_ADMIN_USERNAME": "administrator",
        "APP_ADMIN_PASSWORD": "a-secure-password-12",
        "FLASK_ENV": "staging",
        "FLASK_PORT": "5050",
        "FLASK_HOST": "0.0.0.0",
        "MAX_WORKERS": "4",
        "LOG_LEVEL": "WARNING",
        "INGESTION_ROOTS": "/srv/incoming",
        "SECURITY_MAX_FAILED_LOGINS": "7",
        "SECURITY_LOCKOUT_MINUTES": "20",
        "SECURITY_SESSION_HOURS": "24",
        "SECURITY_SESSION_IDLE_HOURS": "8",
        "PASSWORD_MIN_LENGTH": "12",
        "RATE_LIMIT_PER_MINUTE": "100",
        "RATE_LIMIT_PER_HOUR": "1000",
        "FILE_PROCESSING_TIMEOUT": "1800",
    }]
    # The administrator's creation is audited with the wizard user's address.
    assert ips == ["127.0.0.1"]
    assert not setup_routes._install_lock.locked()
