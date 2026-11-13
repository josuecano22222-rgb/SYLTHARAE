"""Flask integration for authentication & authorization (SEC-01, SEC-02).

* ``init_auth(app)`` registers the before-request authentication middleware.
* ``@login_required`` - any authenticated user.
* ``@roles_required('admin')`` - server-side role enforcement.
* Session cookie carries an opaque token; the authoritative state is the
  database session row (revocable, expiring).
* Public endpoints must be explicitly allow-listed in ``PUBLIC_ENDPOINTS``.
"""

from __future__ import annotations

import json
import logging
from functools import wraps

from urllib.parse import unquote

from flask import g, jsonify, redirect, request, session

from .service import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_ANALYST,
    get_auth_service,
    User,
)

logger = logging.getLogger(__name__)

#: Session keys
_SESSION_TOKEN_KEY = "auth_token"
_SESSION_USER_KEY = "auth_user_id"

#: Endpoints that never require authentication. Keep this list minimal and
#: reviewed; everything else requires a valid session.
PUBLIC_ENDPOINTS = frozenset(
    {
        "static",
        "auth.login",
        "auth.logout",  # harmless without a session; kept public for UX
        "auth.me",  # returns anonymous state; used by the UI shell
        "auth.first_admin_page",
        "auth.first_admin_create",  # gated server-side: only while zero users exist
        "auth.login_page",
        "get_csrf_token",  # app-level /api/csrf-token (needed to obtain tokens)
        "favicon",
        "health.health",
        "translations.get_i18n_catalog",  # read-only UI string catalog (no config)
        "setup.setup_page",  # gated: only functional before DB initialization
        "setup.system_check",
        "setup.test_database",
        "setup.run_installation",
        "setup.check_setup_status",
        "setup.set_setup_language",  # gated: only functional before DB initialization
        "setup.dependency_report",  # gated: pre-install, or administrator afterwards
        "setup.dependency_install",  # gated: pre-install, or administrator afterwards
        "setup.dependency_job",  # gated: pre-install, or administrator afterwards
    }
)

#: Endpoints that require an authenticated admin regardless of method.
ADMIN_ONLY_ENDPOINTS = frozenset(
    {
        # populated in code below; see require_admin decorator usage
    }
)


class AnonymousUser:
    id = None
    username = None
    role = None
    is_active = False
    must_change_password = False

    @property
    def is_authenticated(self) -> bool:
        return False

    @property
    def is_admin(self) -> bool:
        return False

    def has_role(self, *roles: str) -> bool:
        return False

    def to_safe_dict(self) -> dict:
        return {"id": None, "username": None, "role": None, "authenticated": False}


def init_auth(app) -> None:
    """Attach authentication middleware to the Flask app."""
    app.config.setdefault("AUTH_PUBLIC_ENDPOINTS", PUBLIC_ENDPOINTS)

    #: Blueprints whose non-safe methods always require the admin role
    #: (system configuration, import/restore, user administration).
    #: import_export: backup restore / batch import on the legacy surface.
    app.config.setdefault(
        "AUTH_ADMIN_BLUEPRINTS",
        frozenset({"settings", "settings_api", "setup", "concurrency",
                   "error_dashboard", "translations", "import_export"}),
    )
    #: Blueprints whose *safe* methods also require the admin role, because
    #: they return system configuration (DB host/user, storage, secrets flags).
    app.config.setdefault(
        "AUTH_ADMIN_READ_BLUEPRINTS",
        # AUTHZ-01: ``concurrency`` and ``error_dashboard`` are operator
        # diagnostics that expose internal runtime state (thread, process and
        # pool metrics, captured error payloads). They are already admin-only
        # for writes and have no navigation entry, but their *reads* were open
        # to every authenticated role by direct URL, so a ``viewer`` could pull
        # /concurrency/ or /api/errors/recent. Aligned with the settings
        # blueprints, which are admin-read for the same reason.
        frozenset({"settings", "settings_api", "concurrency", "error_dashboard"}),
    )
    #: Individual endpoints outside those blueprints that expose configuration.
    app.config.setdefault(
        "AUTH_ADMIN_READ_ENDPOINTS",
        frozenset({
            "settings_page_direct",
            # AUTHZ-02: the legacy import-export surface returns the same
            # three configuration blocks as /api/settings/export, which is
            # admin-read. It lives on the import_export blueprint, so the
            # admin-*read* set (settings, settings_api, concurrency,
            # error_dashboard) did not cover it and a viewer downloaded
            # system configuration the settings page refuses them.
            "import_export.export_settings",
        }),
    )
    #: Settings paths every authenticated user may *read*.
    #: ``/api/settings/theme`` is rendered inline into base.html for every
    #: user already, so gating it buys no confidentiality - but static/js/
    #: modules/ui/theme-manager.js fetches it on every page, so gating it
    #: does break theming for non-admins.
    #:
    #: ``/api/settings/system`` is the same case and was missed: the shell
    #: (static/js/modules/core/system-settings.js) reads it on every page to
    #: apply the app name, date format and page size, so an analyst was handed
    #: a 403 and a console error on every page and silently got compiled
    #: defaults instead of the configured values. The route answers a non-admin
    #: with the *presentation* block only - the operator keys (log path, debug
    #: flag, log level, screen inspector) stay administrator-only, which
    #: tests/integration/test_settings_shell_read.py asserts in both directions.
    app.config.setdefault(
        "AUTH_SETTINGS_ANY_USER_READ_PATHS",
        frozenset({"/api/settings/theme", "/api/settings/system"}),
    )
    #: Settings mutations every authenticated user may perform. Only paths
    #: whose *identical* effect is already reachable through a non-settings
    #: route may be listed. ``/set_language/<lang>`` (app-level, GET) already
    #: performs this exact global mutation for any authenticated user, so
    #: gating the API equivalent would break the sidebar language switcher
    #: without adding any security.
    app.config.setdefault(
        "AUTH_SETTINGS_ANY_USER_WRITE_PATHS",
        frozenset({"/api/settings/system/language"}),
    )
    #: Self-service mutations every authenticated user may perform on their
    #: own account. ``/auth/change-password`` operates exclusively on
    #: ``current_user().id`` (Api/routes/auth.py) and is reachable from the
    #: must-change-password banner and the user menu for *every* role, so
    #: gating it behind the analyst check left viewers permanently stuck with
    #: a banner they could never clear (AUTH-PW-01).
    app.config.setdefault(
        "AUTH_ANY_USER_WRITE_PATHS",
        frozenset({"/auth/change-password"}),
    )
    #: Non-safe (mutating) methods requiring analyst/admin by default.
    app.config.setdefault("AUTH_WRITE_METHODS", frozenset({"POST", "PUT", "PATCH", "DELETE"}))
    #: Endpoints whose JSON body is legitimately a list. None today - every
    #: handler reads named keys from a mapping - but the escape hatch exists so
    #: one future array endpoint does not weaken the rule for all of them.
    app.config.setdefault("JSON_ARRAY_BODY_ENDPOINTS", frozenset())

    @app.before_request
    def _load_current_user():  # pragma: no cover - exercised via tests
        g.user = AnonymousUser()
        #: True when the session could not be *checked* (the store raised), as
        #: opposed to the store answering "no such session". The difference is
        #: visible to the client: one means "sign in", the other means "retry".
        g.session_store_unavailable = False
        token = session.get(_SESSION_TOKEN_KEY)
        if token:
            try:
                result = get_auth_service().validate_session(token)
            except Exception:
                logger.exception("Session validation failed")
                g.session_store_unavailable = True
                result = None
            if result is not None:
                user, _record = result
                g.user = user
                g.session_record = _record
                session[_SESSION_USER_KEY] = user.id
            elif not g.session_store_unavailable:
                # Only drop the cookie when the store positively said the
                # session is gone. Discarding it on a store failure signed the
                # user out for good because of a database that came back later.
                session.pop(_SESSION_TOKEN_KEY, None)
                session.pop(_SESSION_USER_KEY, None)

        endpoint = (request.endpoint or "").split(".")[0]
        full_endpoint = request.endpoint or ""

        if full_endpoint in app.config["AUTH_PUBLIC_ENDPOINTS"]:
            return None
        # Blueprint-wide public check for health
        if full_endpoint.startswith("health."):
            return None
        if g.user.is_authenticated:
            return None
        # Unauthenticated: API gets 401 JSON, pages get a redirect to login.
        if (
            request.path.startswith("/api/")
            or request.is_json
            or request.accept_mimetypes.best == "application/json"
        ):
            if g.session_store_unavailable:
                # Fail closed, but say which failure it is: signing in cannot
                # work while the session store is unreachable, so a bare
                # "unauthenticated" sent the user to a password prompt that
                # could not succeed.
                return (
                    jsonify({
                        "error": "Authentication is temporarily unavailable: "
                                 "the session store could not be reached",
                        "code": "authentication_unavailable",
                        "reason": "session store unavailable; retry this request shortly",
                        "retry_after_seconds": 5,
                    }),
                    401,
                )
            return (
                jsonify({"error": "Authentication required", "code": "unauthenticated"}),
                401,
            )
        return redirect("/auth/login?next=" + request.path)

    @app.before_request
    def _enforce_role_policy():
        """SEC-02: server-side authorization for every endpoint.

        Policy (documented in docs/SECURITY.md):
        * safe methods (GET/HEAD/OPTIONS) - any authenticated user
        * mutating methods (POST/PUT/PATCH/DELETE) - analyst or admin
        * mutating methods on admin blueprints - admin only
        Endpoint-level decorators can tighten but never loosen this.
        """
        full_endpoint = request.endpoint or ""
        if full_endpoint in app.config["AUTH_PUBLIC_ENDPOINTS"]:
            return None
        if full_endpoint.startswith("health."):
            return None
        if not getattr(g.get("user"), "is_authenticated", False):
            return None  # unauthenticated handled by the auth hook above

        if getattr(g.user, "must_change_password", False):
            gate = _password_change_gate(app, full_endpoint)
            if gate is not None:
                return gate

        if request.method in ("GET", "HEAD", "OPTIONS"):
            # Settings pages/config can expose system configuration.
            # AUDIT (SEC-03): the original test was
            # ``full_endpoint.startswith("settings.")``, but the settings
            # blueprint is registered as ``settings_api`` and the settings page
            # is an app-level ``settings_page_direct`` route - so the check
            # never matched anything. Any authenticated user (including a
            # ``viewer``) could read /settings and /api/settings/database, and
            # any ``analyst`` could POST /api/settings/* (app name, DB
            # credentials, custom CSS). Match on the configured sets instead.
            blueprint = full_endpoint.split(".")[0]
            if (
                blueprint in app.config["AUTH_ADMIN_READ_BLUEPRINTS"]
                or full_endpoint in app.config["AUTH_ADMIN_READ_ENDPOINTS"]
                or full_endpoint.startswith("settings.")
                or full_endpoint.startswith("settings_api.")
            ):
                if request.path not in app.config["AUTH_SETTINGS_ANY_USER_READ_PATHS"]:
                    if not g.user.has_role(ROLE_ADMIN):
                        return _forbidden()
            return None

        if request.method not in app.config["AUTH_WRITE_METHODS"]:
            return None

        if request.path in app.config["AUTH_ANY_USER_WRITE_PATHS"]:
            return None

        blueprint = full_endpoint.split(".")[0]
        if blueprint in app.config["AUTH_ADMIN_BLUEPRINTS"]:
            exempt = (
                blueprint in ("settings", "settings_api")
                and request.path in app.config["AUTH_SETTINGS_ANY_USER_WRITE_PATHS"]
            )
            if exempt:
                # AUTH-01: paths in AUTH_SETTINGS_ANY_USER_WRITE_PATHS are
                # documented as reachable by *any* authenticated user, because
                # an equivalent non-settings route already performs the same
                # mutation for them. Returning here is what actually implements
                # that; falling through to the analyst check below silently
                # downgraded the exemption to "analyst or admin" and broke the
                # sidebar language switcher (static/js/modules/core/
                # language-switcher.js) for viewers with a 403.
                return None
            if not g.user.has_role(ROLE_ADMIN):
                return _forbidden()
        if not g.user.has_role(ROLE_ADMIN, ROLE_ANALYST):
            return _forbidden()
        return None

    @app.before_request
    def _reject_control_characters():
        """A NUL byte in the request target is malformed, not a value.

        PostgreSQL cannot store it and psycopg2 raises while the statement is
        built, so ``?q=a%00b`` turned several read endpoints into 500s. The
        request line is the right place to refuse it: a NUL cannot be meant in
        a URL, and refusing it says so instead of silently searching "ab".
        """
        raw_query = request.query_string or b""
        # ``%00`` arrives as three harmless-looking bytes and only becomes a NUL
        # after percent-decoding, which is when it reaches the driver.
        decoded = unquote(raw_query.decode("latin-1"))
        if (b"\x00" in raw_query or "\x00" in decoded
                or "\x00" in (request.path or "")):
            return (
                jsonify({
                    "error": "The request contains a NUL character, which is not "
                             "valid in a URL",
                    "code": "invalid_request_target",
                }),
                400,
            )
        return None

    @app.before_request
    def _reject_non_object_json_body():
        """A JSON body that is not an object is a client mistake, not a crash.

        Every mutating view reads its input as a mapping (``data.get(...)``).
        When the client sends a scalar, a string, a list or ``null`` the view
        raised ``AttributeError`` and the caller got a 500 with a correlation
        id - 68 routes did, measured by ``tools/audit/probe_malformed.py``.
        The shape is checked once here, after authentication (so an
        unauthenticated caller still gets 401, not a hint about the body) and
        before the view runs.

        A body that does not parse at all is left to the view: Flask already
        answers 400 for it, and so do the views that parse it themselves.
        """
        if request.method not in app.config["AUTH_WRITE_METHODS"]:
            return None
        if not request.is_json:
            return None
        if request.endpoint in app.config["JSON_ARRAY_BODY_ENDPOINTS"]:
            return None
        raw = request.get_data(cache=True)
        if not raw.strip():
            return None                      # no body at all: the view decides
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None                      # unparsable: the view answers 400
        if isinstance(payload, dict):
            return None
        kind = {list: "a list", str: "a string", int: "a number",
                float: "a number", bool: "a boolean"}.get(type(payload),
                                                          "not an object")
        return (
            jsonify({
                "error": f"Request body must be a JSON object, not {kind}",
                "code": "invalid_json_body",
            }),
            400,
        )

    @app.context_processor
    def _inject_user():  # pragma: no cover - template helper
        return {"current_user": g.get("user", AnonymousUser())}


# ----------------------------------------------------------------------
# Decorators (server-side enforcement - never rely on hiding UI elements)
# ----------------------------------------------------------------------
def login_required(fn):
    """Require any authenticated user."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _is_authenticated():
            return _unauthorized()
        return fn(*args, **kwargs)

    wrapper.__auth_required__ = True  # type: ignore[attr-defined]
    return wrapper


def roles_required(*roles: str):
    """Require an authenticated user with one of *roles* (server-side)."""
    for role in roles:
        if role not in ALL_ROLES:
            raise ValueError(f"Unknown role: {role}")

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = g.get("user")
            if not _is_authenticated():
                return _unauthorized()
            if not user.has_role(*roles):
                logger.warning(
                    "Authorization denied: user=%s role=%s needs=%s endpoint=%s",
                    user.username, user.role, roles, request.endpoint,
                )
                return _forbidden()
            return fn(*args, **kwargs)

        wrapper.__auth_roles__ = roles  # type: ignore[attr-defined]
        return wrapper

    return decorator


def admin_required(fn):
    """Shortcut for roles_required(ROLE_ADMIN)."""
    return roles_required(ROLE_ADMIN)(fn)


def write_access_required(fn):
    """Analyst-or-admin: anything that mutates application data."""
    return roles_required(ROLE_ADMIN, ROLE_ANALYST)(fn)


def roles_or_setup_required(*roles: str):
    """Allow access for listed roles OR during first-run (no users exist).

    Used by the setup endpoints: while the system has zero user accounts the
    setup flow must be reachable to create the initial admin; afterwards it is
    admin-only. CSRF still applies.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                if get_auth_service().user_count() == 0:
                    return fn(*args, **kwargs)
            except Exception:
                logger.exception("user_count check failed during setup gating")
            if not _is_authenticated():
                return _unauthorized()
            if roles and not g.get("user").has_role(*roles):
                return _forbidden()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ----------------------------------------------------------------------
# Session helpers used by auth routes
# ----------------------------------------------------------------------
def establish_session(user: User, raw_token: str) -> None:
    session.clear()
    session[_SESSION_TOKEN_KEY] = raw_token
    session[_SESSION_USER_KEY] = user.id
    session.permanent = True


def destroy_session() -> None:
    token = session.get(_SESSION_TOKEN_KEY)
    if token:
        try:
            get_auth_service().revoke_session(token)
        except Exception:
            logger.exception("Failed to revoke session server-side")
    session.clear()


def _is_authenticated() -> bool:
    return bool(g.get("user") and g.get("user").is_authenticated)


def current_user():
    return g.get("user", AnonymousUser())


def is_authenticated() -> bool:
    return _is_authenticated()


def _unauthorized():
    if request.path.startswith("/api/") or request.is_json:
        if g.get("session_store_unavailable"):
            return jsonify({
                "error": "Authentication is temporarily unavailable: "
                         "the session store could not be reached",
                "code": "authentication_unavailable",
                "reason": "session store unavailable; retry this request shortly",
                "retry_after_seconds": 5,
            }), 401
        return jsonify({"error": "Authentication required", "code": "unauthenticated"}), 401
    return redirect("/auth/login?next=" + request.path)


#: What an account flagged ``must_change_password`` may still reach: the
#: change-password flow itself, its own identity, sign-out, and the page shell
#: that carries the change-password banner.
PASSWORD_CHANGE_ALLOWED_ENDPOINTS = frozenset(
    {"auth.change_password", "auth.me", "auth.logout", "index"}
)


def _password_change_gate(app, full_endpoint: str):
    """AUDIT-AUTH-01: a temporary password must be replaced before use.

    ``must_change_password`` is set on the generated first-run password, on
    accounts an administrator creates, on admin resets and on recovery
    passwords, and every one of those paths promises the change is *forced*.
    It used to be a banner only: the temporary credential worked indefinitely
    for every page and API. Now the account can reach the change-password
    flow and the shell page that hosts it, and nothing else, until it has.
    """
    if full_endpoint in PASSWORD_CHANGE_ALLOWED_ENDPOINTS:
        return None
    if (request.method in ("GET", "HEAD")
            and request.path in app.config["AUTH_SETTINGS_ANY_USER_READ_PATHS"]):
        return None  # the theme the shell page loads
    if request.path.startswith("/api/") or request.is_json or request.method not in ("GET", "HEAD"):
        return jsonify({
            "error": "You must change your temporary password before continuing.",
            "code": "password_change_required",
        }), 403
    return redirect("/")


def _forbidden():
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Insufficient permissions", "code": "forbidden"}), 403
    from flask import render_template

    return render_template("403.html"), 403
