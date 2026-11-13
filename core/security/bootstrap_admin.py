"""First-run administrator bootstrap (SEC-01).

On startup, when the ``users`` table exists and contains no accounts, an
initial administrator is created:

* Username from ``APP_ADMIN_USERNAME`` (default ``admin``).
* Password from ``APP_ADMIN_PASSWORD`` when provided; otherwise a random
  password is generated, written to ``<APP_DATA_DIR>/runtime/initial_admin_password.txt``
  with 0600 permissions and logged once. The operator must retrieve it there.

The password file is deleted by the bootstrap on subsequent startups once an
administrator exists, and the account is flagged ``must_change_password``.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

_INITIAL_PASSWORD_FILE = "initial_admin_password.txt"


def ensure_initial_admin(auth_service=None) -> bool:
    """Create the initial administrator if no users exist. Returns True if created."""
    try:
        if auth_service is None:
            from core.security.service import get_auth_service

            auth_service = get_auth_service()
        if auth_service.user_count() > 0:
            _remove_password_file()
            return False

        username = os.environ.get("APP_ADMIN_USERNAME", "admin").strip() or "admin"
        password = os.environ.get("APP_ADMIN_PASSWORD", "")

        generated = False
        if not password:
            password = secrets.token_urlsafe(16)
            generated = True

        user = auth_service.create_user(
            username=username, password=password, role="admin", must_change_password=True
        )
        auth_service.audit("bootstrap.initial_admin_created", user_id=user.id,
                           username=username)
        if generated:
            _write_password_file(username, password)
            logger.warning(
                "Created initial administrator %r. Password written to runtime "
                "directory (initial_admin_password.txt) - retrieve it and change "
                "it on first login.",
                username,
            )
        else:
            logger.info("Created initial administrator %r from APP_ADMIN_PASSWORD", username)
        return True
    except Exception:
        # Never block application startup on admin bootstrap; the first-admin
        # web flow remains available as a fallback.
        logger.exception("Initial administrator bootstrap failed")
        return False


def _password_file_path() -> Path:
    from core.app_paths import get_runtime_dir

    return get_runtime_dir() / _INITIAL_PASSWORD_FILE


def _write_password_file(username: str, password: str) -> None:
    path = _password_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"username={username}\npassword={password}\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - windows
        pass


def _remove_password_file() -> None:
    try:
        path = _password_file_path()
        if path.exists():
            path.unlink()
    except OSError:
        pass
