"""User/account service backed by PostgreSQL (SEC-01, SEC-02).

Authoritative account store and session manager.  All SQL is parameterized.
Sessions are tracked server-side so they can be revoked and expired.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from .passwords import hash_password, verify_password

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_ANALYST = "analyst"
ROLE_VIEWER = "viewer"
ALL_ROLES = (ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER)


def _get_security_config() -> Dict[str, int]:
    """Read security configuration at call time — never frozen at import.

    This ensures that values from .env (loaded after module import) are
    always used.  Called on every login/session operation, not once at import.
    """
    import os
    return {
        "max_failed_logins": int(os.environ.get("SECURITY_MAX_FAILED_LOGINS", "5")),
        "lockout_minutes": int(os.environ.get("SECURITY_LOCKOUT_MINUTES", "15")),
        "session_hours": int(os.environ.get("SECURITY_SESSION_HOURS", "12")),
        "session_idle_hours": int(os.environ.get("SECURITY_SESSION_IDLE_HOURS", "6")),
    }


class AuthError(Exception):
    """Client-safe authentication error."""

    def __init__(self, message: str, code: str = "auth_error"):
        super().__init__(message)
        self.code = code


class InvalidCredentials(AuthError):
    def __init__(self):
        super().__init__("Invalid username or password", "invalid_credentials")


class InvalidCurrentPassword(AuthError):
    def __init__(self):
        super().__init__("Current password is incorrect", "invalid_current_password")


class AccountLocked(AuthError):
    def __init__(self, remaining_minutes: int):
        super().__init__(
            f"Account locked due to failed logins. Try again in {remaining_minutes} minutes",
            "account_locked",
        )
        self.remaining_minutes = remaining_minutes


class AccountDisabled(AuthError):
    def __init__(self):
        super().__init__("Account is disabled", "account_disabled")


@dataclass
class User:
    id: int
    username: str
    role: str
    is_active: bool
    failed_login_count: int = 0
    locked_until: Optional[datetime] = None
    must_change_password: bool = False
    created_at: Optional[datetime] = None

    @property
    def is_authenticated(self) -> bool:
        """Flask middleware compatibility: a loaded User is authenticated."""
        return True

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
        }


@dataclass
class SessionRecord:
    session_id: str
    user_id: int
    expires_at: datetime
    revoked_at: Optional[datetime] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    # Only the hash of the session token is persisted.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    """Database-backed user account and session service.

    A connection factory is injected (no import-time connections).
    """

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    def _conn(self):
        return self._connection_factory()

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, username, password_hash, role, is_active, failed_login_count,"
                " locked_until, must_change_password, created_at"
                " FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, username, password_hash, role, is_active, failed_login_count,"
                " locked_until, must_change_password, created_at"
                " FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        return self._row_to_user(row) if row else None

    @staticmethod
    def _row_to_user(row: Dict[str, Any]) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            role=row["role"],
            is_active=row["is_active"],
            failed_login_count=row.get("failed_login_count", 0),
            locked_until=row.get("locked_until"),
            must_change_password=row.get("must_change_password", False),
            created_at=row.get("created_at"),
        )

    def create_user(
        self,
        username: str,
        password: str,
        role: str = ROLE_ANALYST,
        must_change_password: bool = False,
    ) -> User:
        """Create a user account. Password is hashed before storage."""
        username = (username or "").strip()
        if not (1 <= len(username) <= 64):
            raise AuthError("Username must be 1-64 characters", "invalid_username")
        if role not in ALL_ROLES:
            raise AuthError(f"Role must be one of {ALL_ROLES}", "invalid_role")
        self._validate_password_strength(password)
        pw_hash = hash_password(password)
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, must_change_password)"
                    " VALUES (%s, %s, %s, %s) RETURNING id",
                    (username, pw_hash, role, must_change_password),
                )
                user_id = cur.fetchone()[0]
                conn.commit()
        except psycopg2.errors.UniqueViolation:
            raise AuthError("Username already exists", "duplicate_username")
        logger.info("Created user account id=%s role=%s", user_id, role)
        return self.get_user_by_id(user_id)  # type: ignore[return-value]

    @staticmethod
    def _validate_password_strength(password: str) -> None:
        import os as _os
        try:
            min_len = int(_os.environ.get("PASSWORD_MIN_LENGTH", "12"))
        except (TypeError, ValueError):
            min_len = 12
        # Match the installation wizard's supported range. Invalid persisted
        # values must not silently weaken password enforcement or make the
        # settings guidance disagree with the actual authentication policy.
        if not 8 <= min_len <= 128:
            min_len = 12
        if not isinstance(password, str) or len(password) < min_len:
            raise AuthError(f"Password must be at least {min_len} characters", "weak_password")
        if len(password) > 256:
            raise AuthError("Password too long", "invalid_password")

    def set_password(self, user_id: int, new_password: str, require_change: bool = False) -> None:
        """Admin/initiated password reset. Revokes all sessions for the user."""
        self._validate_password_strength(new_password)
        pw_hash = hash_password(new_password)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, must_change_password = %s,"
                " failed_login_count = 0, locked_until = NULL, updated_at = NOW() WHERE id = %s",
                (pw_hash, require_change, user_id),
            )
            cur.execute(
                "UPDATE sessions SET revoked_at = NOW() WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )
            conn.commit()

    def change_own_password(self, user_id: int, current_password: str, new_password: str,
                            keep_session_id: Optional[str] = None) -> None:
        """A signed-in user changing their own password (RES-AUTH-02).

        A session alone is not enough: the current password must be given, so
        an unattended browser or a stolen session cannot take the account
        over. A wrong current password counts towards the same lockout as a
        failed sign-in; if it locks the account, every session of the account
        is revoked (the caller must also end the current one). On success the
        account's other sessions are revoked; ``keep_session_id`` (the hashed
        id of the session making the change) stays signed in.
        """
        user_row = self._get_user_row(user_id)
        if not user_row:
            raise InvalidCurrentPassword()
        now = _utcnow()
        remaining = self._remaining_lock_minutes(user_row, now)
        if remaining:
            raise AccountLocked(remaining)
        if not verify_password(current_password or "", user_row["password_hash"]):
            if self._record_failed_attempt(user_row, now):
                self.revoke_all_sessions(user_id)
                raise AccountLocked(_get_security_config()["lockout_minutes"])
            raise InvalidCurrentPassword()
        if new_password == current_password:
            raise AuthError("The new password must be different from the current one",
                            "password_unchanged")
        self._validate_password_strength(new_password)
        pw_hash = hash_password(new_password)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, must_change_password = FALSE,"
                " password_changed_at = NOW(), failed_login_count = 0, locked_until = NULL,"
                " updated_at = NOW() WHERE id = %s",
                (pw_hash, user_id),
            )
            cur.execute(
                "UPDATE sessions SET revoked_at = NOW() WHERE user_id = %s"
                " AND revoked_at IS NULL AND session_id IS DISTINCT FROM %s",
                (user_id, keep_session_id),
            )
            conn.commit()

    def set_role(self, user_id: int, role: str) -> None:
        if role not in ALL_ROLES:
            raise AuthError(f"Role must be one of {ALL_ROLES}", "invalid_role")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = %s, updated_at = NOW() WHERE id = %s", (role, user_id)
            )
            conn.commit()

    def set_active(self, user_id: int, is_active: bool) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = %s, updated_at = NOW() WHERE id = %s",
                (is_active, user_id),
            )
            if not is_active:
                cur.execute(
                    "UPDATE sessions SET revoked_at = NOW() WHERE user_id = %s"
                    " AND revoked_at IS NULL",
                    (user_id,),
                )
            conn.commit()

    def delete_user(self, user_id: int) -> None:
        """Permanently remove a user account.

        Referential integrity: ``sessions.user_id`` cascades (m0003_auth_tables),
        and ``audit_log.user_id`` is deliberately FK-free so the audit trail
        survives the delete with its username snapshot intact. Callers are
        responsible for the self-delete / last-admin guards (Api/routes/auth.py).
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            if cur.rowcount == 0:
                raise AuthError("User not found", "user_not_found")
            conn.commit()

    def list_users(self) -> List[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, username, role, is_active, must_change_password, created_at"
                " FROM users ORDER BY id"
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def user_count(self) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            return int(cur.fetchone()[0])

    def _get_user_row(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Login / sessions
    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> Tuple[User, SessionRecord]:
        """Authenticate a user and create a session record.

        Returns ``(user, session)`` where ``session.session_token`` must be
        stored in the client's cookie.  Raises client-safe AuthError types.
        """
        username = (username or "").strip()
        user_row = self._get_user_row_by_username(username)
        if user_row is None:
            # Uniform failure: do not reveal whether the account exists.
            # Burn comparable time to reduce username enumeration timing.
            verify_password(password or "", hash_password("timing-equalizer"))
            raise InvalidCredentials()

        now = _utcnow()
        locked_until = user_row.get("locked_until")
        if locked_until is not None:
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if locked_until > now:
                raise AccountLocked(int((locked_until - now).total_seconds() // 60) + 1)

        if not user_row["is_active"]:
            raise AccountDisabled()

        if verify_password(password or "", user_row["password_hash"]):
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET failed_login_count = 0, locked_until = NULL,"
                    " last_login_at = NOW(), updated_at = NOW() WHERE id = %s",
                    (user_row["id"],),
                )
                conn.commit()
            user = self._row_to_user(user_row)
            session = self.create_session(user.id)
            return user, session

        if self._record_failed_attempt(user_row, now):
            raise AccountLocked(_get_security_config()["lockout_minutes"])
        raise InvalidCredentials()

    def _record_failed_attempt(self, user_row: Dict[str, Any], now: datetime) -> bool:
        """Count a wrong password (sign-in or change); True when it locks the account."""
        failed = (user_row.get("failed_login_count") or 0) + 1
        locked_until = None
        sec = _get_security_config()
        if failed >= sec["max_failed_logins"]:
            locked_until = now + timedelta(minutes=sec["lockout_minutes"])
            logger.warning(
                "Account %s locked for %s minutes after %d failed password attempts",
                user_row.get("username"), sec["lockout_minutes"], failed,
            )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET failed_login_count = %s, locked_until = %s,"
                " updated_at = NOW() WHERE id = %s",
                (failed, locked_until, user_row["id"]),
            )
            conn.commit()
        return locked_until is not None

    @staticmethod
    def _remaining_lock_minutes(user_row: Dict[str, Any], now: datetime) -> int:
        """Minutes left on a lockout, or 0 when the account is not locked."""
        locked_until = user_row.get("locked_until")
        if locked_until is None:
            return 0
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until <= now:
            return 0
        return int((locked_until - now).total_seconds() // 60) + 1

    def _get_user_row_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
        return dict(row) if row else None

    def create_session(self, user_id: int, hours: Optional[int] = None) -> SessionRecord:
        """Create a session; returns the record whose ``session_id`` attribute
        is the **hashed** token; the raw token is provided as ``raw_token``.

        Session duration is read from environment at call time (not frozen at import).
        """
        if hours is None:
            hours = _get_security_config()["session_hours"]
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        expires = _utcnow() + timedelta(hours=hours)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (session_id, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_hash, user_id, expires),
            )
            conn.commit()
        record = SessionRecord(session_id=token_hash, user_id=user_id, expires_at=expires)
        record.raw_token = raw_token  # type: ignore[attr-defined]
        return record

    def validate_session(self, raw_token: str) -> Optional[Tuple[User, SessionRecord]]:
        """Return ``(user, session)`` if the token is valid, else ``None``.

        Validation includes: record exists, not revoked, not expired, user is
        still active. Expired rows are lazily cleaned up.
        """
        if not raw_token:
            return None
        token_hash = _hash_token(raw_token)
        now = _utcnow()
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at,"
                " u.id AS uid, u.username, u.role, u.is_active, u.failed_login_count,"
                " u.locked_until, u.must_change_password, u.created_at"
                " FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.session_id = %s",
                (token_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            expires = row["expires_at"]
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if row["revoked_at"] is not None or expires < now or not row["is_active"]:
                return None
            # Sliding expiry: extend while the user remains active.
            idle_hours = _get_security_config()["session_idle_hours"]
            new_expiry = min(
                now + timedelta(hours=idle_hours),
                expires if expires > now else expires,
            )
            cur.execute(
                "UPDATE sessions SET expires_at = %s WHERE session_id = %s",
                (new_expiry, token_hash),
            )
            conn.commit()
        user = User(
            id=row["uid"],
            username=row["username"],
            role=row["role"],
            is_active=row["is_active"],
            failed_login_count=row.get("failed_login_count") or 0,
            locked_until=row.get("locked_until"),
            must_change_password=row.get("must_change_password") or False,
            created_at=row.get("created_at"),
        )
        return user, SessionRecord(row["session_id"], row["user_id"], expires)

    def revoke_session(self, raw_token: str) -> None:
        if not raw_token:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked_at = NOW() WHERE session_id = %s"
                " AND revoked_at IS NULL",
                (_hash_token(raw_token),),
            )
            conn.commit()

    def revoke_all_sessions(self, user_id: Optional[int] = None) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            if user_id is None:
                cur.execute(
                    "UPDATE sessions SET revoked_at = NOW() WHERE revoked_at IS NULL"
                )
            else:
                cur.execute(
                    "UPDATE sessions SET revoked_at = NOW() WHERE user_id = %s"
                    " AND revoked_at IS NULL",
                    (user_id,),
                )
            conn.commit()

    def purge_expired_sessions(self) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE expires_at < NOW() - INTERVAL '7 days'"
                " OR (revoked_at IS NOT NULL AND revoked_at < NOW() - INTERVAL '7 days')"
            )
            count = cur.rowcount
            conn.commit()
        return count

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------
    def audit(
        self,
        action: str,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        resource: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        """Append a tamper-evident-encouraging audit record. Never raises."""
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log (user_id, username, action, resource, detail,"
                    " ip_address) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        user_id,
                        username,
                        action,
                        resource,
                        psycopg2.extras.Json(detail) if detail else None,
                        ip_address,
                    ),
                )
                conn.commit()
        except Exception:
            logger.exception("Failed to write audit record for action=%s", action)

    def audit_strict(
        self,
        action: str,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        resource: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """Append an audit record and return its id - or raise.

        ``audit()`` never raises, which is right for login bookkeeping but
        wrong for events whose *absence* is itself a governance failure. A
        data disclosure (``DATA_EXPORTED``) must not happen unrecorded, so the
        disclosure hook uses this variant and refuses the export when the
        record cannot be written.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (user_id, username, action, resource, detail,"
                " ip_address) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    user_id,
                    username,
                    action,
                    (resource or "")[:255] or None,
                    psycopg2.extras.Json(detail) if detail else None,
                    ip_address,
                ),
            )
            audit_id = cur.fetchone()
            conn.commit()
        audit_id = audit_id[0] if not isinstance(audit_id, dict) else audit_id["id"]
        return int(audit_id)


_service: Optional[AuthService] = None


def _default_connection_factory():
    """Connection factory based on the unified settings (lazy, per-call)."""
    from settings.config import get_db_config

    cfg = get_db_config()
    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=10,
    )


def get_auth_service() -> AuthService:
    """Return the process-wide auth service (created lazily, DI-friendly)."""
    global _service
    if _service is None:
        _service = AuthService(_default_connection_factory)
    return _service


def set_auth_service(service: Optional[AuthService]) -> None:
    """Replace the process-wide auth service (used by tests for DI)."""
    global _service
    _service = service
