"""RES-AUTH-02: changing your own password requires the current password.

Before the fix a signed-in session was enough: anyone at an unattended
browser, or holding a stolen session cookie, could set a new password and
own the account for good. Now:

* the current password is required and verified;
* a wrong one counts towards the sign-in lockout, and when it locks the
  account every session of that account - including the caller's - ends;
* a successful change revokes the account's other sessions, keeps this one;
* the new password must differ from the current one and meet the policy.
"""
import uuid

import pytest

from core.security.service import get_auth_service

PASSWORD = "current-password-123"
NEW_PASSWORD = "a-new-password-456"


@pytest.fixture()
def account(app):
    """A fresh viewer account per test (lockout state never leaks)."""
    username = f"pwchange_{uuid.uuid4().hex[:10]}"
    get_auth_service().create_user(username, PASSWORD, role="viewer")
    return username


def _signed_in(app, username, password=PASSWORD):
    client = app.test_client()
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _can_sign_in(app, username, password):
    resp = app.test_client().post("/auth/login", json={"username": username, "password": password})
    return resp.status_code == 200


def _signed_in_still(client):
    return client.get("/auth/me").get_json()["authenticated"] is True


def _change(client, current, new=NEW_PASSWORD):
    return client.post("/auth/change-password",
                       json={"current_password": current, "new_password": new})


def test_a_session_alone_cannot_change_the_password(app, account):
    client = _signed_in(app, account)
    resp = client.post("/auth/change-password", json={"new_password": NEW_PASSWORD})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "current_password_required"
    assert _can_sign_in(app, account, PASSWORD)
    assert not _can_sign_in(app, account, NEW_PASSWORD)


def test_a_wrong_current_password_is_refused_and_counted(app, account):
    client = _signed_in(app, account)
    resp = _change(client, "not-my-password-000")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_current_password"
    row = get_auth_service()._get_user_row_by_username(account)
    assert row["failed_login_count"] == 1, "counted like a failed sign-in"
    assert _signed_in_still(client)
    assert _can_sign_in(app, account, PASSWORD), "the password did not change"


def test_the_right_current_password_changes_it(app, account):
    client = _signed_in(app, account)
    resp = _change(client, PASSWORD)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _can_sign_in(app, account, NEW_PASSWORD)
    assert not _can_sign_in(app, account, PASSWORD)
    assert _signed_in_still(client), "the session that made the change continues"


def test_other_sessions_are_signed_out_by_a_change(app, account):
    mine = _signed_in(app, account)
    elsewhere = _signed_in(app, account)          # e.g. a stolen session
    assert _signed_in_still(elsewhere)
    assert _change(mine, PASSWORD).status_code == 200
    assert _signed_in_still(mine)
    assert not _signed_in_still(elsewhere)
    assert elsewhere.get("/api/search", query_string={"query": "x"}).status_code == 401


def test_repeated_wrong_guesses_lock_the_account_and_end_every_session(app, account, monkeypatch):
    monkeypatch.setenv("SECURITY_MAX_FAILED_LOGINS", "3")
    thief = _signed_in(app, account)
    owner = _signed_in(app, account)
    codes = [_change(thief, f"guess-{i}-password").status_code for i in range(3)]
    assert codes == [400, 400, 429], codes
    assert not _signed_in_still(thief)
    assert not _signed_in_still(owner), "every session of a locked account ends"
    locked = app.test_client().post("/auth/login", json={"username": account, "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.get_json()["code"] == "account_locked"


def test_the_new_password_must_differ_and_meet_the_policy(app, account):
    client = _signed_in(app, account)
    same = _change(client, PASSWORD, new=PASSWORD)
    assert same.status_code == 400 and same.get_json()["code"] == "password_unchanged"
    weak = _change(client, PASSWORD, new="short")
    assert weak.status_code == 400 and weak.get_json()["code"] == "weak_password"
    assert _can_sign_in(app, account, PASSWORD)


def test_anonymous_requests_are_refused(app):
    resp = app.test_client().post("/auth/change-password",
                                  json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert resp.status_code == 401


def test_failures_and_changes_are_audited(app, account):
    client = _signed_in(app, account)
    _change(client, "not-my-password-000")
    _change(client, PASSWORD)
    user_id = get_auth_service()._get_user_row_by_username(account)["id"]
    with get_auth_service()._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT action FROM audit_log WHERE user_id = %s ORDER BY id", (user_id,))
        actions = [row[0] for row in cur.fetchall()]
    assert "password.change_failed" in actions
    assert actions.index("password.change_failed") < actions.index("password.change")


def test_the_form_asks_for_the_current_password():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="currentPasswordInput"' in base and 'autocomplete="current-password"' in base
    script = (root / "static" / "js" / "user-menu.js").read_text(encoding="utf-8")
    assert "current_password: currentPassword" in script


def test_security_doc_matches_the_implementation():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    doc = (root / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    route = (root / "Api" / "routes" / "auth.py").read_text(encoding="utf-8")
    assert "requires the\n  current password" in doc or "requires the current password" in doc
    for action in ("password.change", "password.change_failed", "password.change_locked"):
        assert f"`{action}`" in doc and f'"{action}"' in route, action
    assert '@auth_bp.route("/auth/change-password", methods=["POST"])\n@limiter.limit("10 per minute")' in route
