"""Authentication & authorization core (SEC-01, SEC-02).

Single authoritative implementation for:

* Password hashing (werkzeug scrypt/PBKDF2)
* User accounts and roles (admin / analyst / viewer)
* Login, logout, session records with server-side revocation and expiry
* Account lockout after repeated failures
* Route protection decorators used by every protected endpoint

Database tables used here (``users``, ``sessions``, ``audit_log``) are created
by the schema bootstrap/migration system - never at import time.
"""

from .passwords import hash_password, verify_password, needs_rehash
from .service import (
    AuthService,
    get_auth_service,
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_VIEWER,
    ALL_ROLES,
)
from .flask_ext import (
    init_auth,
    login_required,
    roles_required,
    roles_or_setup_required,
    current_user,
    is_authenticated,
    establish_session,
    destroy_session,
)

__all__ = [
    "hash_password",
    "verify_password",
    "needs_rehash",
    "AuthService",
    "get_auth_service",
    "ROLE_ADMIN",
    "ROLE_ANALYST",
    "ROLE_VIEWER",
    "ALL_ROLES",
    "init_auth",
    "login_required",
    "roles_required",
    "roles_or_setup_required",
    "current_user",
    "is_authenticated",
    "establish_session",
    "destroy_session",
]
