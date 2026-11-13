"""Integration tests: authentication service (SEC-01)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

from core.security.service import (
    AuthService,
    AccountDisabled,
    AccountLocked,
    AuthError,
    InvalidCredentials,
)


@pytest.fixture()
def auth(pg_db):
    """AuthService bound to the disposable DB with a unique user prefix."""
    import psycopg2

    factory = lambda: psycopg2.connect(  # noqa: E731
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"], connect_timeout=10,
    )
    return AuthService(factory)


def unique_name(base, return_id=[0]):
    return_id[0] += 1
    import os

    return f"{base}_{os.getpid()}_{return_id[0]}"


class TestUserLifecycle:
    def test_create_and_get(self, auth):
        username = unique_name("user_a")
        user = auth.create_user(username, "long-enough-password-123", role="analyst")
        assert user.id > 0
        assert user.role == "analyst"
        fetched = auth.get_user_by_username(username)
        assert fetched is not None
        assert fetched.id == user.id

    def test_duplicate_username_rejected(self, auth):
        username = unique_name("user_dup")
        auth.create_user(username, "long-enough-password-123")
        with pytest.raises(AuthError):
            auth.create_user(username, "another-long-password")

    def test_weak_password_rejected(self, auth):
        with pytest.raises(AuthError):
            auth.create_user(unique_name("user_weak"), "short")

    def test_invalid_role_rejected(self, auth):
        with pytest.raises(AuthError):
            auth.create_user(unique_name("user_role"), "long-enough-password-123", role="superuser")

    def test_password_not_stored_plaintext(self, auth, pg_db):
        import psycopg2

        username = unique_name("user_plain")
        pw = "plaintext-check-123"
        auth.create_user(username, pw)
        with psycopg2.connect(
            host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
            password=pg_db["password"], dbname=pg_db["database"],
        ) as conn, conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
            stored = cur.fetchone()[0]
        assert pw not in stored
        assert stored.startswith("scrypt:")


class TestLogin:
    def test_valid_login_creates_session(self, auth):
        username, pw = unique_name("login_ok"), "long-enough-password-123"
        auth.create_user(username, pw)
        user, session = auth.login(username, pw)
        assert user.username == username
        assert session.raw_token  # type: ignore[attr-defined]

    def test_wrong_password_rejected_uniformly(self, auth):
        username, pw = unique_name("login_bad"), "long-enough-password-123"
        auth.create_user(username, pw)
        with pytest.raises(InvalidCredentials):
            auth.login(username, "wrong-password-xxx")
        # Unknown user produces the SAME error type (no enumeration).
        with pytest.raises(InvalidCredentials):
            auth.login(unique_name("no_such_user"), "whatever-password")

    def test_lockout_after_repeated_failures(self, auth):
        username, pw = unique_name("login_lock"), "long-enough-password-123"
        auth.create_user(username, pw)
        for _ in range(4):
            with pytest.raises(InvalidCredentials):
                auth.login(username, "wrong-password-xxx")
        # 5th consecutive failure triggers the lockout
        with pytest.raises(AccountLocked):
            auth.login(username, "wrong-password-xxx")
        # Even the CORRECT password is refused while locked.
        with pytest.raises(AccountLocked):
            auth.login(username, pw)

    def test_successful_login_resets_failure_count(self, auth):
        username, pw = unique_name("login_reset"), "long-enough-password-123"
        auth.create_user(username, pw)
        for _ in range(3):
            with pytest.raises(InvalidCredentials):
                auth.login(username, "wrong-password-xxx")
        user, _ = auth.login(username, pw)
        fresh = auth.get_user_by_username(username)
        assert fresh.failed_login_count == 0

    def test_disabled_account_rejected(self, auth):
        username, pw = unique_name("login_disabled"), "long-enough-password-123"
        user = auth.create_user(username, pw)
        auth.set_active(user.id, False)
        with pytest.raises(AccountDisabled):
            auth.login(username, pw)


class TestSessions:
    def test_validate_and_revoke(self, auth):
        username, pw = unique_name("sess_rev"), "long-enough-password-123"
        auth.create_user(username, pw)
        user, session = auth.login(username, pw)
        token = session.raw_token  # type: ignore[attr-defined]
        assert auth.validate_session(token) is not None
        auth.revoke_session(token)
        assert auth.validate_session(token) is None

    def test_password_reset_revokes_sessions(self, auth):
        username, pw = unique_name("sess_reset"), "long-enough-password-123"
        user = auth.create_user(username, pw)
        _, session = auth.login(username, pw)
        token = session.raw_token  # type: ignore[attr-defined]
        assert auth.validate_session(token) is not None
        auth.set_password(user.id, "new-long-password-456")
        assert auth.validate_session(token) is None

    def test_garbage_token_rejected(self, auth):
        assert auth.validate_session("garbage-token") is None
        assert auth.validate_session("") is None


class TestAudit:
    def test_audit_written(self, auth, pg_db):
        import psycopg2

        username = unique_name("audit_user")
        user = auth.create_user(username, "long-enough-password-123")
        auth.audit("test.action", user_id=user.id, username=username, resource="res1")
        with psycopg2.connect(
            host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
            password=pg_db["password"], dbname=pg_db["database"],
        ) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = %s AND username = %s",
                ("test.action", username),
            )
            assert cur.fetchone()[0] >= 1
