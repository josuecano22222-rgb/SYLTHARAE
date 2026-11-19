"""The database, not the marker file, decides whether this system is installed.

Root cause (AUDIT-C1): ``Api/routes/setup.py`` answered "is this system
installed?" from ``.system_initialized`` - a file under the application data
directory. ``_check_database_setup`` was a global ``before_request`` that
redirected *every* request to ``/setup`` when that file was missing, and the
marker is written by only two code paths (the wizard's install step and
``install.py``), and only when ``config.json`` exists - an installation
configured from ``.env``, which is what the installer writes, never gets one.

Reproduced consequence: on a fully installed database with an administrator,
deleting the marker made ``GET /auth/login`` return ``302 -> /setup`` while a
working account, its files and its audit trail were all still in PostgreSQL.
The wizard could not repair it either: ``ensure_admin_account`` no-ops when
accounts exist, so ``/api/setup/install`` is not a recovery path.

These tests pin the new authority order:

* PostgreSQL answers the question; the marker is a fast path and a fallback for
  when the database is unreachable;
* an unanswerable question fails closed - the setup endpoints stay shut, and
  sign-in is never hijacked;
* a missing marker on an installed system is healed instead of acted upon.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from Api.routes import setup as setup_routes

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Every test starts with no cached answer, and leaves none behind."""
    setup_routes.reset_initialization_cache()
    yield
    setup_routes.reset_initialization_cache()


@pytest.fixture
def marker(tmp_path, monkeypatch):
    """An isolated data root with no marker; returns a writer for one."""
    import core.app_paths as app_paths
    import core.initialization as init

    root = tmp_path / "appdata"
    root.mkdir()
    monkeypatch.setenv("APP_DATA_DIR", str(root))
    app_paths.set_data_root(root)
    monkeypatch.setattr(init, "INIT_MARKER_FILE", None)
    monkeypatch.setattr(init, "_LEGACY_MARKER_DIRS", ())
    yield init
    app_paths.reset_cache()


def _database_says(monkeypatch, installed, has_users):
    """Make the PostgreSQL probe answer ``(installed, has_users)``."""
    monkeypatch.setattr(
        setup_routes, "_database_initialization_state",
        lambda: None if installed is None else (installed, has_users),
    )


# ── The authority order ─────────────────────────────────────────────────────


def test_an_installed_database_counts_even_without_a_marker(marker, monkeypatch):
    """The audit's reproduction: schema present, marker gone, no lockout."""
    _database_says(monkeypatch, installed=True, has_users=True)

    assert marker.is_system_initialized() is False
    state = setup_routes.initialization_state()
    assert state["initialized"] is True
    assert state["source"] == "database"
    assert setup_routes._setup_needed() is False


def test_the_lost_marker_is_recreated_on_read(marker, monkeypatch):
    """Healing keeps the fast path warm for the next process (and install.py)."""
    _database_says(monkeypatch, installed=True, has_users=True)

    setup_routes.initialization_state()

    assert marker.is_system_initialized() is True, (
        "a missing marker was acted upon instead of healed")


def test_an_empty_database_keeps_the_wizard_open(marker, monkeypatch):
    _database_says(monkeypatch, installed=False, has_users=False)

    assert setup_routes._setup_needed() is True
    assert marker.is_system_initialized() is False, (
        "the marker must not be written for a database that is not installed")


def test_a_schema_without_an_account_still_needs_the_wizard(marker, monkeypatch):
    _database_says(monkeypatch, installed=True, has_users=False)

    assert setup_routes.initialization_state()["initialized"] is True
    assert setup_routes._setup_needed() is True


def test_an_unreadable_accounts_table_fails_closed(marker, monkeypatch):
    """Schema present but the accounts probe failed: treat as installed + used."""
    _database_says(monkeypatch, installed=True, has_users=None)

    state = setup_routes.initialization_state()
    assert state["has_users"] is True
    assert setup_routes._setup_needed() is False, (
        "an unreadable accounts table reopened the installation endpoints")


# ── Database unreachable: marker is the fallback ───────────────────────────


def test_unreachable_database_with_a_marker_keeps_serving_the_application(marker, monkeypatch):
    marker.mark_system_initialized()
    _database_says(monkeypatch, installed=None, has_users=None)

    state = setup_routes.initialization_state()
    assert state["initialized"] is True
    assert state["source"] == "marker"
    assert setup_routes._setup_needed() is False


def test_unreachable_database_without_a_marker_points_at_the_wizard(marker, monkeypatch):
    _database_says(monkeypatch, installed=None, has_users=None)

    state = setup_routes.initialization_state()
    assert state["initialized"] is False
    assert state["source"] == "none"
    assert setup_routes._setup_needed() is True


# ── The guard on the mutating endpoints ────────────────────────────────────


@pytest.fixture
def client():
    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"),
                static_folder=str(PROJECT_ROOT / "static"))
    app.config.update(TESTING=True, SECRET_KEY="setup-gate-test")
    from flask_babel import Babel

    Babel(app)
    app.jinja_env.globals.update(_=lambda message, **values: message,
                                 csrf_token=lambda: "csrf", current_language="en")
    app.register_blueprint(setup_routes.setup_bp)
    app.add_url_rule("/", endpoint="index", view_func=lambda: "home")
    return app.test_client()


def test_install_is_refused_on_an_installed_system(client, marker, monkeypatch):
    _database_says(monkeypatch, installed=True, has_users=True)

    response = client.post("/api/setup/install", json={})

    assert response.status_code == 409
    assert response.get_json()["ok"] is False


def test_install_is_refused_when_the_state_cannot_be_determined(client, marker, monkeypatch):
    def _boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(setup_routes, "_setup_needed", _boom)

    response = client.post("/api/setup/install", json={})

    assert response.status_code == 503
    assert "Cannot verify system state" in response.get_json()["error"]


def test_the_diagnostics_are_public_only_in_the_setup_window(client, marker, monkeypatch):
    """AUDIT-SETUP-01: anonymous host fingerprinting/network probing after install."""
    _database_says(monkeypatch, installed=True, has_users=True)

    assert client.post("/api/setup/test-database", json={}).status_code == 401
    assert client.get("/api/setup/system-check").status_code == 401


# ── The global hook ────────────────────────────────────────────────────────


def _gate(app, marker, monkeypatch, installed, has_users, path="/auth/login"):
    app.add_url_rule("/", endpoint="index", view_func=lambda: "home")
    app.add_url_rule("/auth/login", endpoint="login", view_func=lambda: "login page")
    app.add_url_rule("/api/words", endpoint="api_words", view_func=lambda: "{}")
    _database_says(monkeypatch, installed=installed, has_users=has_users)
    setup_routes.reset_initialization_cache()
    setup_routes.register_setup_routes(app)
    return app.test_client().get(path, follow_redirects=False)


def test_login_is_not_hijacked_when_the_marker_is_gone(client, marker, monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="x")
    response = _gate(app, marker, monkeypatch, installed=True, has_users=True)
    assert response.status_code == 200, (
        f"an installed system redirected sign-in to {response.headers.get('Location')}")


def test_the_api_is_not_hijacked_when_the_marker_is_gone(marker, monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="x")
    response = _gate(app, marker, monkeypatch, installed=True, has_users=True,
                     path="/api/words")
    assert response.status_code == 200


def test_pages_are_sent_to_the_wizard_when_no_account_exists(marker, monkeypatch):
    """Schema installed by a restore, zero accounts: someone must create one."""
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="x")
    response = _gate(app, marker, monkeypatch, installed=True, has_users=False,
                     path="/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/setup")


def test_sign_in_is_never_hijacked_even_in_the_zero_account_state(marker, monkeypatch):
    """Deliberate asymmetry: the wizard is reachable, sign-in is untouched.

    With no account nobody can sign in anyway, so redirecting ``/auth/login``
    would buy nothing - and if the accounts probe is *wrongly* empty (a config
    change pointing at a fresh database, a restore mid-flight) a redirect here
    is exactly the lockout this fix removes. ``/auth/*`` and ``/api/*`` are
    therefore never redirected; every other page is.
    """
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="x")
    response = _gate(app, marker, monkeypatch, installed=True, has_users=False,
                     path="/auth/login")
    assert response.status_code == 200


def test_a_fresh_install_is_sent_to_the_wizard(marker, monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="x")
    response = _gate(app, marker, monkeypatch, installed=False, has_users=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/setup")


def test_the_setup_page_renders_when_the_wizard_is_needed(client, marker, monkeypatch):
    _database_says(monkeypatch, installed=False, has_users=False)
    assert client.get("/setup").status_code == 200


def test_the_setup_page_does_not_render_once_installation_is_complete(client, marker, monkeypatch):
    _database_says(monkeypatch, installed=True, has_users=True)
    response = client.get("/setup", follow_redirects=False)
    assert response.status_code == 302
    assert not response.headers["Location"].endswith("/setup")


def test_the_check_endpoint_reports_the_database_answer(client, marker, monkeypatch):
    _database_says(monkeypatch, installed=True, has_users=True)
    assert client.get("/api/setup/check").get_json()["initialized"] is True


def test_the_cached_answer_is_dropped_after_installation(client, marker, monkeypatch):
    """The wizard must not keep using the pre-install answer for 5 seconds."""
    _database_says(monkeypatch, installed=False, has_users=False)
    assert client.get("/api/setup/check").get_json()["initialized"] is False

    _database_says(monkeypatch, installed=True, has_users=True)
    setup_routes.reset_initialization_cache()
    assert client.get("/api/setup/check").get_json()["initialized"] is True
