"""Administrator password recovery (``scripts/reset_admin_password.py``).

The recovery path an operator uses when nobody can log in still has to behave
like the product does everywhere else: hashed (never plaintext) storage, the
temporary password written where only the machine's owner can read it, live
sessions revoked, lockout cleared, the action audited, and nothing that was not
asked for touched.

These tests run the real script as a subprocess against the real database, and
verify the outcome through the real login route. They create their own accounts
instead of the shared ``testadmin`` - the database is shared by the whole test
session, and a test that changes the administrator password must not change the
password every other test logs in with.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from core.security.passwords import verify_password
from core.security.service import get_auth_service

pytestmark = pytest.mark.integration

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "scripts", "reset_admin_password.py")


def _unique(prefix: str) -> str:
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex[:8]}"


def _make_account(prefix: str, password: str, role: str = "admin"):
    auth = get_auth_service()
    username = _unique(prefix)
    return auth.create_user(username, password, role=role)


def _run(*args, data_dir, extra_env=None):
    env = dict(os.environ)
    env["APP_DATA_DIR"] = str(data_dir)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, _SCRIPT, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _recovery_file(data_dir):
    return data_dir / "runtime" / "recovery_admin_password.txt"


def _password_from_file(data_dir):
    text = _recovery_file(data_dir).read_text(encoding="utf-8")
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return fields["username"], fields["password"]


def _stored_hash(username):
    with get_auth_service()._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
        return cur.fetchone()[0]


def _login(app, username, password, client=None):
    if client is not None:
        return client.post("/auth/login", json={"username": username, "password": password})
    with app.test_client() as c:
        return c.post("/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------------------
# Listing and targeting
# ---------------------------------------------------------------------------


def test_list_names_the_accounts_that_can_log_in(admin_credentials, tmp_path):
    result = _run("--list", data_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "testadmin" in result.stdout  # the seeded administrator
    assert "username" in result.stdout and "role" in result.stdout
    # Nothing derived from a password hash may be printed.
    assert "scrypt" not in result.stdout
    assert "test-admin-password-123" not in result.stdout


def test_an_unknown_account_is_reported_with_the_available_ones(admin_credentials, tmp_path):
    result = _run("--username", "no-such-account", data_dir=tmp_path)

    assert result.returncode == 2
    assert "no account named" in result.stdout
    assert "testadmin" in result.stdout  # so the operator can pick the right name
    assert not _recovery_file(tmp_path).exists()


def test_the_default_account_comes_from_the_app_configuration(admin_credentials, tmp_path):
    """Without --username the script uses APP_ADMIN_USERNAME, as the app does."""
    account = _make_account("recovery_default", "recovery-default-pass-123")

    result = _run(data_dir=tmp_path, extra_env={"APP_ADMIN_USERNAME": account.username})

    assert result.returncode == 0, result.stdout + result.stderr
    assert account.username in result.stdout
    username, temporary = _password_from_file(tmp_path)
    assert username == account.username
    stored = _stored_hash(account.username)
    assert temporary not in stored, "the password must be stored hashed, never as plaintext"
    assert verify_password(temporary, stored) is True


# ---------------------------------------------------------------------------
# The recovery itself
# ---------------------------------------------------------------------------


def test_recovery_issues_a_working_password_and_kills_live_sessions(
    app, admin_credentials, tmp_path
):
    account = _make_account("recovery_admin", "forgotten-admin-pass-123")

    with app.test_client() as live:
        assert _login(app, account.username, "forgotten-admin-pass-123", live).status_code == 200
        assert live.get("/archives").status_code == 200

        result = _run("--username", account.username, data_dir=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

        # The session that was live a moment ago is revoked, not merely stale.
        assert live.get("/archives").status_code == 302

    username, temporary = _password_from_file(tmp_path)
    assert username == account.username
    assert temporary not in result.stdout, "the password must not be printed to the console"

    user = get_auth_service().get_user_by_username(account.username)
    assert user.must_change_password is True, "a recovery password must be one-time"
    assert user.failed_login_count == 0
    assert user.locked_until is None

    # Logging in with the recovered password also proves the stored value is a
    # hash of it and not the plaintext (the login path verifies the hash).
    assert _login(app, account.username, temporary).status_code == 200
    assert _login(app, account.username, "forgotten-admin-pass-123").status_code == 401


def test_a_locked_out_account_can_get_back_in(app, admin_credentials, tmp_path):
    """Five wrong passwords lock the account; recovery has to clear that too."""
    account = _make_account("recovery_locked", "locked-admin-pass-123")

    for _ in range(6):
        _login(app, account.username, "definitely-the-wrong-password")
    assert get_auth_service().get_user_by_username(account.username).locked_until is not None

    result = _run("--username", account.username, data_dir=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

    _, temporary = _password_from_file(tmp_path)
    assert _login(app, account.username, temporary).status_code == 200

    user = get_auth_service().get_user_by_username(account.username)
    assert user.locked_until is None
    assert user.failed_login_count == 0


def test_the_recovery_is_audited(admin_credentials, tmp_path):
    account = _make_account("recovery_audited", "audited-admin-pass-123")

    assert _run("--username", account.username, data_dir=tmp_path).returncode == 0

    with get_auth_service()._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT username, resource, detail FROM audit_log"
            " WHERE action = 'user.password_recovery' AND resource = %s",
            (f"user:{account.id}",),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    actor, resource, detail = rows[0]
    assert actor == "local-recovery"
    assert detail["source"] == "scripts/reset_admin_password.py"
    assert detail["username"] == account.username


def test_the_temporary_password_file_is_private(admin_credentials, tmp_path):
    account = _make_account("recovery_private", "private-admin-pass-123")

    assert _run("--username", account.username, data_dir=tmp_path).returncode == 0

    path = _recovery_file(tmp_path)
    assert path.exists()
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# Operator-supplied password
# ---------------------------------------------------------------------------


def test_an_operator_supplied_password_is_used_verbatim(app, admin_credentials, tmp_path):
    account = _make_account("recovery_chosen", "chosen-admin-pass-123")
    chosen = "chosen-by-the-operator-123"

    result = _run(
        "--username", account.username, "--password", chosen, "--keep-password", data_dir=tmp_path
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # Nothing was generated, so there is no temporary password to write down.
    assert not _recovery_file(tmp_path).exists()
    assert _login(app, account.username, chosen).status_code == 200

    user = get_auth_service().get_user_by_username(account.username)
    assert user.must_change_password is False, "--keep-password keeps the password as chosen"


def test_a_short_operator_password_is_refused(app, admin_credentials, tmp_path):
    account = _make_account("recovery_short", "safe-password-123")

    result = _run("--username", account.username, "--password", "short", data_dir=tmp_path)

    assert result.returncode == 3
    assert "at least 12 characters" in result.stdout
    # The account keeps the password it had.
    assert _login(app, account.username, "safe-password-123").status_code == 200


# ---------------------------------------------------------------------------
# Refusals: never promote or activate unless asked
# ---------------------------------------------------------------------------


def test_a_non_admin_account_is_not_touched_by_accident(app, admin_credentials, tmp_path):
    account = _make_account("recovery_viewer", "viewer-password-123", role="viewer")

    result = _run("--username", account.username, data_dir=tmp_path)

    assert result.returncode == 3
    assert "viewer" in result.stdout and "--promote" in result.stdout
    assert get_auth_service().get_user_by_username(account.username).role == "viewer"
    assert _login(app, account.username, "viewer-password-123").status_code == 200


def test_promoting_is_explicit_and_does_not_touch_other_accounts(app, admin_credentials, tmp_path):
    account = _make_account("recovery_promote", "viewer-password-123", role="viewer")

    result = _run(
        "--username", account.username, "--promote",
        "--password", "promoted-recovery-123", "--keep-password", data_dir=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    user = get_auth_service().get_user_by_username(account.username)
    assert user.role == "admin"
    assert user.is_active is True
    assert _login(app, account.username, "promoted-recovery-123").status_code == 200
    # The original administrator keeps its own password: recovery is local and
    # scoped, not a global reset.
    assert _login(app, "testadmin", "test-admin-password-123").status_code == 200


def test_a_deactivated_account_needs_activate_explicitly(app, admin_credentials, tmp_path):
    account = _make_account("recovery_inactive", "inactive-password-123")
    get_auth_service().set_active(account.id, False)

    refused = _run("--username", account.username, "--keep-password", data_dir=tmp_path)
    assert refused.returncode == 3
    assert "--activate" in refused.stdout
    assert get_auth_service().get_user_by_username(account.username).is_active is False
    assert not _recovery_file(tmp_path).exists()

    done = _run(
        "--username", account.username, "--activate",
        "--password", "reactivated-recovery-123", "--keep-password", data_dir=tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr

    assert get_auth_service().get_user_by_username(account.username).is_active is True
    assert _login(app, account.username, "reactivated-recovery-123").status_code == 200


def test_the_script_reports_which_database_it_used(admin_credentials, tmp_path):
    """The operator must be able to see *which* database was touched."""
    result = _run("--list", data_dir=tmp_path)

    assert "Database:" in result.stdout
    assert os.environ["DB_NAME"] in result.stdout
    database_password = os.environ.get("DB_PASSWORD") or ""
    if database_password:
        assert database_password not in result.stdout
