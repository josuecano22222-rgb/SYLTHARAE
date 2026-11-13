"""Behind a TLS proxy: what the app must see, and the failure when it does not.

A failure met in practice: through a proxy that did not pass the public host
name, every sign-in answered **400**. Over HTTPS, Flask-WTF checks that the
Referer is the same origin as the request. The browser's Referer names the
public host (https://syltharae.example.org/), but without Host or
X-Forwarded-Host the app thinks it is 127.0.0.1:5000, and the CSRF check
fails. deploy/nginx/syltharae.conf passes both headers. These tests use the
same ProxyFix the app installs for TRUSTED_PROXY_COUNT=1.
"""
import re

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

PUBLIC = "syltharae.example.org"


@pytest.fixture()
def proxied_app(app, admin_credentials, monkeypatch):
    """The app as run with TRUSTED_PROXY_COUNT=1, CSRF enforced."""
    monkeypatch.setattr(app, "wsgi_app", ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1))
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    return app


def _sign_in(app, forwarded, referer=f"https://{PUBLIC}/auth/login"):
    username, password = ("testadmin", "test-admin-password-123")
    client = app.test_client()
    page = client.get("/auth/login", headers=forwarded, base_url="http://127.0.0.1:5000")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)).group(1)
    return client.post("/auth/login", json={"username": username, "password": password},
                       headers={**forwarded, "X-CSRFToken": token, "Referer": referer},
                       base_url="http://127.0.0.1:5000")


def test_sign_in_through_a_correct_proxy_succeeds(proxied_app):
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": PUBLIC,
                 "X-Forwarded-For": "203.0.113.7"}
    resp = _sign_in(proxied_app, forwarded)
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_sign_in_through_a_proxy_that_drops_the_host_fails_with_400(proxied_app):
    """The failure mode the shipped nginx config prevents - kept as a guard."""
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"}
    resp = _sign_in(proxied_app, forwarded)
    assert resp.status_code == 400
    assert "csrf" in resp.get_data(as_text=True).lower()


def _last_login_ip(username="testadmin"):
    from core.security.service import get_auth_service
    with get_auth_service()._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ip_address FROM audit_log WHERE username = %s"
                    " AND action = 'login.success' ORDER BY id DESC LIMIT 1", (username,))
        return str(cur.fetchone()[0])


def test_the_app_records_the_real_client_address(proxied_app):
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": PUBLIC,
                 "X-Forwarded-For": "203.0.113.7"}
    assert _sign_in(proxied_app, forwarded).status_code == 200
    assert _last_login_ip() == "203.0.113.7"


def test_hsts_only_over_https_in_production(proxied_app, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    client = proxied_app.test_client()
    tls = client.get("/auth/login", headers={"X-Forwarded-Proto": "https",
                                             "X-Forwarded-Host": PUBLIC})
    plain = client.get("/auth/login", headers={"X-Forwarded-Host": PUBLIC})
    assert "max-age=" in tls.headers.get("Strict-Transport-Security", "")
    assert "Strict-Transport-Security" not in plain.headers


def test_same_origin_plain_http_login_works_without_disabling_csrf(app, admin_credentials, monkeypatch):
    username, password = admin_credentials
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SAMESITE", "Lax")
    client = app.test_client()
    page = client.get("/auth/login", base_url="http://lan-host:5000")
    token = re.search(r'name="csrf-token" content="([^"]+)"',
                      page.get_data(as_text=True)).group(1)
    resp = client.post(
        "/auth/login", json={"username": username, "password": password},
        headers={"X-CSRFToken": token, "Referer": "http://lan-host:5000/auth/login"},
        base_url="http://lan-host:5000")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True


def test_without_a_trusted_proxy_forwarded_headers_are_ignored(app, admin_credentials):
    username, password = admin_credentials
    resp = app.test_client().post(
        "/auth/login", json={"username": username, "password": password},
        headers={"X-Forwarded-For": "203.0.113.99"},
        environ_base={"REMOTE_ADDR": "198.51.100.9"})
    assert resp.status_code == 200
    assert _last_login_ip(username) == "198.51.100.9"
