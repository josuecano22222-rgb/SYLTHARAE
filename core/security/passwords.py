"""Password hashing for user accounts (SEC-01).

Uses Werkzeug's ``generate_password_hash`` (scrypt by default, which is
memory-hard) with a salt, and ``check_password_hash`` for verification.
Never store or log plaintext passwords.
"""

from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash

# scrypt (memory-hard); werkzeug >= 2.3 default. Method is embedded in the
# hash string so existing hashes keep verifying if the default changes.
_METHOD = "scrypt"
_SALT_SIZE = 16


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage. Returns ``method$salt$hash``."""
    if not isinstance(password, str) or len(password) < 1:
        raise ValueError("Password must be a non-empty string")
    return generate_password_hash(password, method=_METHOD, salt_length=_SALT_SIZE)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored hash (constant-time)."""
    if not password or not password_hash:
        return False
    try:
        return check_password_hash(password_hash, password)
    except Exception:
        # Malformed hash in DB: treat as failed verification, never crash auth.
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash should be upgraded (e.g. after parameter change)."""
    try:
        from werkzeug.security import __version__ as wz_version  # noqa: F401
    except ImportError:  # pragma: no cover
        return False
    try:
        # werkzeug >= 2.3 exposes this helper
        from werkzeug.security import check_password_hash as _c  # noqa: F401
        import werkzeug.security as _ws
        if hasattr(_ws, "needs_rehash"):  # type: ignore[attr-defined]
            return _ws.needs_rehash(password_hash, _METHOD)
    except Exception:
        pass
    return False
