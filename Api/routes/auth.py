"""Authentication routes (SEC-01).

Endpoints:
    GET  /auth/login         login page
    POST /auth/login         authenticate (JSON or form)
    POST /auth/logout        revoke session (CSRF-protected)
    GET  /auth/me            current identity (anonymous-safe)
    GET  /auth/first-admin   first-run admin creation (only when no users exist)
    POST /auth/first-admin   create the initial administrator
    POST /auth/change-password  change own password (authenticated; needs the
                                current password - RES-AUTH-02)

Admin account management:
    GET    /api/auth/users            list users (admin)
    POST   /api/auth/users            create user (admin)
    PATCH  /api/auth/users/<id>       set role / active (admin)
    POST   /api/auth/users/<id>/reset-password  admin password reset (admin)

All handlers return client-safe errors (SEC-08) and write audit records.
"""

from __future__ import annotations

import logging

import os

from flask import (
    Blueprint,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from core.security import (
    ROLE_ADMIN,
    get_auth_service,
    destroy_session,
    establish_session,
    current_user,
    is_authenticated,
)
from core.security.service import (
    AccountDisabled, AccountLocked, AuthError, InvalidCredentials, InvalidCurrentPassword,
)
from core.errors import client_error
from core.validation import PayloadError, text_field
from core.security.rate_limit import limiter

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


def _client_ip() -> str:
    """The client address for the audit log.

    ``request.remote_addr`` is the only trustworthy source: ProxyFix rewrites
    it from X-Forwarded-For when (and only when) TRUSTED_PROXY_COUNT declares
    a proxy. Reading the header here instead let any client write an address
    of its choosing into the audit log (found by
    tests/security/test_proxy_deployment.py).
    """
    return request.remote_addr or ""


@auth_bp.route("/auth/login", methods=["GET"])
def login_page():
    if is_authenticated():
        return redirect("/")
    return render_template("auth/login.html")


def _login_rate_limit() -> str:
    """Sign-in rate limit, env-overridable for the runtime-acceptance
    harness (which signs in several roles across the chain checks in one
    server session). Production default stays 10/minute; the override is
    read at request time and still bounded."""
    per_minute = os.environ.get("RATE_LIMIT_LOGIN_PER_MINUTE", "10")
    try:
        value = max(1, int(per_minute))
    except ValueError:
        value = 10
    return f"{value} per minute"


@auth_bp.route("/auth/login", methods=["POST"])
@limiter.limit(lambda: _login_rate_limit())
def login():
    data = request.get_json(silent=True) or request.form or {}
    try:
        # A list or a number where a name belongs used to reach ``.strip()``
        # and answer 500 on an endpoint anyone can call.
        username = text_field(data, "username", max_length=150, trim=True)
        password = text_field(data, "password", max_length=256)
    except PayloadError as exc:
        return jsonify(exc.to_dict()), 400
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    auth = get_auth_service()
    try:
        user, session_record = auth.login(username, password)
    except AccountLocked as exc:
        auth.audit("login.locked", username=username, ip_address=_client_ip())
        return jsonify({"error": str(exc), "code": exc.code}), 429
    except (InvalidCredentials, AccountDisabled):
        auth.audit("login.failed", username=username, ip_address=_client_ip())
        # Uniform message: never reveal whether the account exists.
        return jsonify({"error": "Invalid username or password", "code": "invalid_credentials"}), 401
    except AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400

    establish_session(user, session_record.raw_token)  # type: ignore[attr-defined]
    auth.audit(
        "login.success",
        user_id=user.id,
        username=user.username,
        ip_address=_client_ip(),
    )
    next_url = request.args.get("next") or request.form.get("next") or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"  # prevent open redirect
    if request.is_json or request.accept_mimetypes.best == "application/json":
        return jsonify({"success": True, "user": user.to_safe_dict(), "redirect": next_url})
    return redirect(next_url)


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    user = current_user()
    if is_authenticated():
        get_auth_service().audit(
            "logout", user_id=user.id, username=user.username, ip_address=_client_ip()
        )
    destroy_session()
    if request.is_json:
        return jsonify({"success": True})
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/auth/me", methods=["GET"])
def me():
    user = current_user()
    if not is_authenticated():
        return jsonify({"authenticated": False, "user": None})
    return jsonify({"authenticated": True, "user": user.to_safe_dict()})


@auth_bp.route("/auth/change-password", methods=["POST"])
@limiter.limit("10 per minute")
def change_password():
    """Change the signed-in user's password; the current password is required.

    A wrong current password counts towards the sign-in lockout; when it
    locks the account, every session - including this one - is ended. On
    success the account's other sessions are revoked and this one continues.
    """
    if not is_authenticated():
        return jsonify({"error": "Authentication required"}), 401
    data = request.get_json(silent=True) or request.form or {}
    try:
        current_password = text_field(data, "current_password", max_length=256)
        new_password = text_field(data, "new_password", max_length=256)
    except PayloadError as exc:
        return jsonify(exc.to_dict()), 400
    user = current_user()
    if not current_password:
        return jsonify({"error": "Enter your current password",
                        "code": "current_password_required"}), 400
    auth = get_auth_service()
    record = g.get("session_record")
    try:
        auth.change_own_password(user.id, current_password, new_password,
                                 keep_session_id=getattr(record, "session_id", None))
    except AccountLocked as exc:
        auth.audit("password.change_locked", user_id=user.id, username=user.username,
                   ip_address=_client_ip())
        destroy_session()
        return jsonify({"error": str(exc), "code": exc.code}), 429
    except InvalidCurrentPassword as exc:
        auth.audit("password.change_failed", user_id=user.id, username=user.username,
                   ip_address=_client_ip())
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except Exception as exc:
        return client_error(exc, subsystem="auth")
    auth.audit("password.change", user_id=user.id, username=user.username,
               ip_address=_client_ip())
    return jsonify({"success": True})


# ----------------------------------------------------------------------
# First-run administrator creation
# ----------------------------------------------------------------------
def _no_users_yet() -> bool:
    try:
        return get_auth_service().user_count() == 0
    except Exception:
        logger.exception("user_count failed during first-run gating")
        return False


@auth_bp.route("/auth/first-admin", methods=["GET"])
def first_admin_page():
    if not _no_users_yet():
        return redirect("/auth/login")
    return render_template("auth/first_admin.html")


@auth_bp.route("/auth/first-admin", methods=["POST"])
def first_admin_create():
    """Create the initial administrator. Only functional while zero users exist."""
    if not _no_users_yet():
        return jsonify({"error": "An administrator already exists. Sign in instead."}), 403
    data = request.get_json(silent=True) or request.form or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    try:
        user = get_auth_service().create_user(username, password, role=ROLE_ADMIN)
    except AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except Exception as exc:
        return client_error(exc, subsystem="auth")
    get_auth_service().audit(
        "user.first_admin_created", user_id=user.id, username=user.username,
        ip_address=_client_ip(),
    )
    # Log the new admin straight in.
    _, session_record = get_auth_service().login(username, password)
    establish_session(user, session_record.raw_token)  # type: ignore[attr-defined]
    return jsonify({"success": True, "user": user.to_safe_dict(), "redirect": "/"})


# ----------------------------------------------------------------------
# Admin user management API
# ----------------------------------------------------------------------
@auth_bp.route("/api/auth/users", methods=["GET"])
def api_list_users():
    if not (is_authenticated() and current_user().is_admin):
        return jsonify({"error": "Insufficient permissions"}), 403
    return jsonify({"users": get_auth_service().list_users()})


@auth_bp.route("/api/auth/users", methods=["POST"])
def api_create_user():
    if not (is_authenticated() and current_user().is_admin):
        return jsonify({"error": "Insufficient permissions"}), 403
    data = request.get_json(silent=True) or {}
    try:
        username = text_field(data, "username", max_length=150, trim=True)
        password = text_field(data, "password", max_length=256)
        role = text_field(data, "role", default="analyst", max_length=32) or "analyst"
    except PayloadError as exc:
        return jsonify(exc.to_dict()), 400
    try:
        user = get_auth_service().create_user(
            username=username,
            password=password,
            role=role,
            must_change_password=bool(data.get("must_change_password", True)),
        )
    except AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except Exception as exc:
        return client_error(exc, subsystem="auth")
    get_auth_service().audit(
        "user.create", user_id=current_user().id, username=current_user().username,
        resource=f"user:{user.id}", detail={"role": user.role}, ip_address=_client_ip(),
    )
    return jsonify({"success": True, "user": user.to_safe_dict()}), 201


@auth_bp.route("/api/auth/users/<int:user_id>", methods=["PATCH"])
def api_update_user(user_id: int):
    if not (is_authenticated() and current_user().is_admin):
        return jsonify({"error": "Insufficient permissions"}), 403
    data = request.get_json(silent=True) or {}
    auth = get_auth_service()
    # AUDIT-01: ``UPDATE ... WHERE id = %s`` against a missing id affects zero
    # rows and raises nothing, so the handler used to answer
    # ``200 {"success": true, "user": null}`` - reporting success for a change
    # that was never applied. Resolve the target first and 404 if absent.
    target = auth.get_user_by_id(user_id)
    if target is None:
        return jsonify({"error": "User not found", "code": "user_not_found"}), 404
    # AUTH-02: mirror the DELETE guards for the update path. The UI blocks
    # these client-side; without server-side guards a direct API call could
    # demote or deactivate the sole (or self) administrator and leave the
    # system with no way to administer users.
    loses_admin = (
        ("role" in data and data["role"] != ROLE_ADMIN)
        or (data.get("is_active") is False)
    )
    if loses_admin:
        if user_id == current_user().id:
            return jsonify({
                "error": "You cannot demote or deactivate your own account",
                "code": "self_management",
            }), 400
        if target.is_admin and target.is_active:
            active_admins = [u for u in auth.list_users()
                             if u.get("role") == ROLE_ADMIN and u.get("is_active", True)]
            if len(active_admins) <= 1:
                return jsonify({
                    "error": "Cannot demote or deactivate the last active administrator",
                    "code": "last_admin",
                }), 400
    try:
        if "role" in data:
            auth.set_role(user_id, data["role"])
        if "is_active" in data:
            auth.set_active(user_id, bool(data["is_active"]))
    except AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except Exception as exc:
        return client_error(exc, subsystem="auth")
    auth.audit(
        "user.update", user_id=current_user().id, username=current_user().username,
        resource=f"user:{user_id}", detail=data, ip_address=_client_ip(),
    )
    updated = auth.get_user_by_id(user_id)
    return jsonify({"success": True, "user": updated.to_safe_dict() if updated else None})


@auth_bp.route("/api/auth/users/<int:user_id>", methods=["DELETE"])
def api_delete_user(user_id: int):
    """Permanently delete a user account and revoke all of its sessions.

    Guards: an admin cannot delete themselves, and the last active admin
    cannot be deleted (that would lock every admin out of user management).
    """
    if not (is_authenticated() and current_user().is_admin):
        return jsonify({"error": "Insufficient permissions"}), 403
    auth = get_auth_service()
    target = auth.get_user_by_id(user_id)
    if target is None:
        return jsonify({"error": "User not found", "code": "user_not_found"}), 404
    if user_id == current_user().id:
        return jsonify({"error": "You cannot delete your own account", "code": "self_delete"}), 400
    if target.is_admin:
        active_admins = [u for u in auth.list_users()
                         if u.get("role") == ROLE_ADMIN and u.get("is_active", True)]
        if len(active_admins) <= 1:
            return jsonify({"error": "Cannot delete the last active administrator",
                            "code": "last_admin"}), 400
    try:
        auth.delete_user(user_id)
    except Exception as exc:
        return client_error(exc, subsystem="auth")
    auth.audit(
        "user.delete", user_id=current_user().id, username=current_user().username,
        resource=f"user:{user_id}", detail={"username": target.username},
        ip_address=_client_ip(),
    )
    return jsonify({"success": True})


@auth_bp.route("/api/auth/users/<int:user_id>/reset-password", methods=["POST"])
def api_reset_password(user_id: int):
    """Admin-initiated password reset. Generates a random password and returns
    it exactly once. All of the user's sessions are revoked."""
    if not (is_authenticated() and current_user().is_admin):
        return jsonify({"error": "Insufficient permissions"}), 403
    import secrets as _secrets

    # AUDIT-01: same zero-row hazard - never mint and return a temporary
    # password for an account that does not exist.
    if get_auth_service().get_user_by_id(user_id) is None:
        return jsonify({"error": "User not found", "code": "user_not_found"}), 404

    temporary = _secrets.token_urlsafe(12)
    try:
        get_auth_service().set_password(user_id, temporary, require_change=True)
    except AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except Exception as exc:
        return client_error(exc, subsystem="auth")
    get_auth_service().audit(
        "user.password_reset", user_id=current_user().id, username=current_user().username,
        resource=f"user:{user_id}", ip_address=_client_ip(),
    )
    return jsonify({"success": True, "temporary_password": temporary})


def register_auth_routes(app):
    app.register_blueprint(auth_bp)

    @app.route("/users")
    def users_page():
        """AUTH-UI: User Management page (admin-only).

        The /api/auth/users endpoints existed without any front end; this
        provides the missing management surface (list, create, role/active,
        password reset) plus a static role-capability matrix.
        """
        if not (is_authenticated() and current_user().is_admin):
            return render_template("403.html"), 403
        return render_template("auth/users.html")
