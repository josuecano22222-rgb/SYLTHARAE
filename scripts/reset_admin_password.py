#!/usr/bin/env python3
"""Administrator password recovery (local, offline, no other accounts touched).

Forgotten the administrator password? Run this on the machine that hosts the
application, while the server is stopped (it also works while the server is
running - see below):

    python scripts/reset_admin_password.py --list          # who can log in?
    python scripts/reset_admin_password.py                 # reset 'admin'
    python scripts/reset_admin_password.py --username administrator
    python scripts/reset_admin_password.py --password 'My own long password'

What it does, and what it deliberately does not:

* It changes the password of **one** account with the application's own
  password hashing (Werkzeug scrypt, the same function the login path verifies
  against). Nothing is written in plaintext to the database.
* The new password is a random one-time password unless you pass ``--password``;
  it is written to ``<APP_DATA_DIR>/runtime/recovery_admin_password.txt`` with
  ``0600`` permissions (the same convention as the first-run bootstrap file),
  and it is never echoed to the console or to the logs.
* The account is flagged ``must_change_password``, so the temporary password
  forces a change at the next login instead of becoming the permanent one.
* All sessions of that account are revoked, and lockout/failed-login counters
  are cleared, so a locked-out admin can get back in immediately. A running
  server picks this up on the next request; no restart is required.
* Every other account, its categories, assignments and the audit trail are left
  alone. The action is written to the audit log (``user.password_recovery``).

Safety rails - it refuses rather than guesses:

* The target must be an admin. Use ``--promote`` if the only account you can
  log in with was demoted (this is the recovery path for a lost admin role).
* The target must be active. Use ``--activate`` if it was deactivated.
* If no accounts exist at all, nothing is reset: start the server, and the
  first-run bootstrap creates the initial administrator (see docs/OPERATIONS.md, "First run").

Exit codes: 0 success, 2 account not found, 3 refused (role/inactive/other),
4 database not reachable, 1 unexpected error.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Windows-native console safety (redirected output uses legacy code pages).
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_RECOVERY_FILE = "recovery_admin_password.txt"


def _load_env_file() -> None:
    """Load <project root>/.env exactly like the application does.

    ``override=False`` keeps the documented precedence: real environment
    variables beat the file. Missing python-dotenv is not an error here.
    """
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False, interpolate=False)
    except ImportError:
        pass
    except Exception as exc:  # never block recovery on a config file issue
        print(f"  [warn] could not read {env_file}: {exc}")


def _password_file_path() -> Path:
    from core.app_paths import get_runtime_dir

    return get_runtime_dir() / _RECOVERY_FILE


def _write_password_file(username: str, password: str) -> Path:
    path = _password_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"username={username}\npassword={password}\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows without POSIX permissions
        pass
    return path


def _describe_target() -> None:
    """Print the database this run will touch (never the password)."""
    from settings.config import get_db_config

    try:
        cfg = get_db_config()
    except Exception:
        return
    print(
        "Database: {}@{}:{}/{}".format(
            cfg.get("user"), cfg.get("host"), cfg.get("port"), cfg.get("database")
        )
    )


def _print_accounts(service) -> None:
    users = service.list_users()
    if not users:
        print("No accounts exist yet - start the server and the first-run bootstrap")
        print("creates the initial administrator (see docs/OPERATIONS.md, 'First run').")
        return
    print(f"{'id':>4}  {'username':<24} {'role':<8} {'active':<7} must change")
    for user in users:
        print(
            "{:>4}  {:<24} {:<8} {:<7} {}".format(
                user["id"],
                str(user["username"])[:24],
                str(user["role"]),
                "yes" if user.get("is_active") else "no",
                "yes" if user.get("must_change_password") else "no",
            )
        )


def _reset(args) -> int:
    _load_env_file()
    _describe_target()

    try:
        from core.security.service import get_auth_service
    except Exception as exc:
        print(f"[error] the application code could not be imported: {exc}")
        print("        Run this script from the application folder with its Python environment.")
        return 1

    service = get_auth_service()

    try:
        account_count = service.user_count()
    except Exception as exc:
        print(f"[error] the database could not be reached: {exc}")
        print("        Check that PostgreSQL is running, and that the login the app uses")
        print("        (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD in .env) is correct.")
        return 4

    if args.list:
        _print_accounts(service)
        return 0

    username = args.username or os.environ.get("APP_ADMIN_USERNAME", "admin").strip() or "admin"

    if account_count == 0:
        print("[error] there are no accounts to recover.")
        print("        Start the server once: the first-run bootstrap creates the initial")
        print("        administrator (see docs/OPERATIONS.md, 'First run').")
        return 2

    user = service.get_user_by_username(username)
    if user is None:
        print(f"[error] no account named {username!r}.")
        print()
        _print_accounts(service)
        print()
        print("        Re-run with --username <name>, or leave it out to use 'admin'.")
        return 2

    if user.role != "admin" and not args.promote:
        print(f"[error] {username!r} is a {user.role}, not an admin - refusing to touch it.")
        print("        If this account is your way back in, re-run with --promote to")
        print("        make it the administrator (the change is audited).")
        return 3

    if not user.is_active and not args.activate:
        print(f"[error] {username!r} is deactivated, so a new password would not help.")
        print("        Re-run with --activate to enable it (the change is audited).")
        return 3

    print(
        "Account: {} (id {}, role {}, {}, {} failed login(s){})".format(
            user.username,
            user.id,
            user.role,
            "active" if user.is_active else "deactivated",
            user.failed_login_count,
            ", locked" if getattr(user, "locked_until", None) else "",
        )
    )

    password = args.password or secrets.token_urlsafe(12)
    require_change = not args.keep_password

    try:
        if user.role != "admin":
            service.set_role(user.id, "admin")
        if not user.is_active:
            service.set_active(user.id, True)
        service.set_password(user.id, password, require_change=require_change)
    except Exception as exc:
        print(f"[error] the password could not be set: {exc}")
        return 1

    service.audit(
        "user.password_recovery",
        user_id=None,
        username="local-recovery",
        resource=f"user:{user.id}",
        detail={
            "username": user.username,
            "role_before": user.role,
            "promoted": user.role != "admin",
            "activated": not user.is_active,
            "require_change": require_change,
            "source": "scripts/reset_admin_password.py",
        },
    )

    print()
    print(f"[ok] password reset for {user.username!r} (id {user.id}).")
    if user.role != "admin":
        print("     Role promoted to admin.")
    if not user.is_active:
        print("     Account activated.")
    print("     Sessions of that account were revoked; lockout state was cleared.")

    if args.password:
        print("     The password is the one you passed with --password.")
    else:
        path = _write_password_file(user.username, password)
        print(f"     Temporary password written to: {path}")
        print("     Read it now (it is a file only you can read):")
        print(f'       Windows: type "{path}"')
        print(f'       Linux/macOS: cat "{path}"')

    if require_change:
        print("     The app asks for a new password at the next login.")
    print("     Delete the password file after you have logged in.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset the administrator password of a local SYLTHARAE install.",
        epilog=(
            "Run with --list first if you are not sure of the account name. "
            "See docs/OPERATIONS.md (Recovering an administrator password) for the "
            "full procedure."
        ),
    )
    parser.add_argument(
        "--username",
        default=None,
        help="account to reset (default: APP_ADMIN_USERNAME from .env, else 'admin')",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="use this password instead of a generated one-time password "
        "(at least 12 characters)",
    )
    parser.add_argument(
        "--promote", action="store_true", help="also make the account an admin (audited)"
    )
    parser.add_argument(
        "--activate", action="store_true", help="also enable the account if it is disabled (audited)"
    )
    parser.add_argument(
        "--keep-password",
        action="store_true",
        help="do not force a password change at the next login",
    )
    parser.add_argument("--list", action="store_true", help="list accounts and exit")
    args = parser.parse_args(argv)

    if args.password is not None and len(args.password) < 12:
        print("[error] --password must be at least 12 characters.")
        return 3

    return _reset(args)


if __name__ == "__main__":
    sys.exit(main())
