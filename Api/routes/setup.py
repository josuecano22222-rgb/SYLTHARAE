"""
Web-based installation wizard routes.

The page at /setup renders a multi-step wizard (install_wizard.html).
Each step calls an API endpoint that delegates to the shared installer
services in core.installer — no business logic lives here.

Endpoints:
    GET  /setup                  → wizard page
    GET  /api/setup/system-check → prerequisite checks
    POST /api/setup/test-database→ DB connectivity test
    POST /api/setup/install      → execute full installation
    GET  /api/setup/check        → is the system initialized?
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from flask_babel import gettext as _
import logging
import threading

logger = logging.getLogger(__name__)

setup_bp = Blueprint("setup", __name__)

# Installation lock: prevents concurrent installation requests from
# racing.  Only one installation can execute at a time across all
# threads.  The second request receives a deterministic 409 response.
_install_lock = threading.Lock()


def _text_field(data, key, label, default="", *, required=False, maximum=None, preserve=False):
    """Read one bounded text value from an untrusted setup JSON object."""
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(_("%(label)s must be text.", label=_(label)))
    if any(character in value for character in ("\r", "\n", "\0")):
        raise ValueError(_("%(label)s cannot contain line breaks or null characters.", label=_(label)))
    result = value if preserve else value.strip()
    if required and not result.strip():
        raise ValueError(_("%(label)s is required.", label=_(label)))
    if maximum is not None and len(result) > maximum:
        raise ValueError(_("%(label)s must be no more than %(n)s characters.", label=_(label), n=maximum))
    return result


def _integer_field(data, key, label, default, minimum, maximum):
    """Parse a JSON integer without accepting booleans or truncating floats."""
    value = data.get(key, default)
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(_("%(label)s must be a whole number.", label=_(label)))
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(_("%(label)s must be a whole number.", label=_(label))) from None
    if not minimum <= result <= maximum:
        raise ValueError(_("%(label)s must be between %(minimum)s and %(maximum)s.", label=_(label), minimum=minimum, maximum=maximum))
    return result


def _is_initialized():

    """Authoritative initialization-state check.

    The system is considered initialized when the filesystem marker exists
    AND the critical tables exist in the application database.  The
    filesystem marker is the fast-path; the table check is authoritative.

    Returns True if initialized, False if not, False on any error
    (fail-safe: an errored check should not block legitimate setup).
    """
    # Fast-path: filesystem marker (written at end of successful install)
    try:
        from core.initialization import is_system_initialized
        if not is_system_initialized():
            return False
        # Marker exists, but verify DB is actually ready too
    except Exception:
        return False

    # Authoritative check: do the critical tables exist?
    try:
        import psycopg2
        from database import get_db_config
        cfg = get_db_config()
        conn = psycopg2.connect(
            dbname=cfg.get("database", "analysis"),
            user=cfg.get("user", "postgres"),
            password=cfg.get("password", ""),
            host=cfg.get("host", "localhost"),
            port=cfg.get("port", 5432),
            connect_timeout=5,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND table_name IN ('words','paths','contents','users')")
            count = cur.fetchone()[0]
            cur.close()
            return count >= 4
        finally:
            conn.close()
    except Exception:
        # DB unreachable — if marker exists, trust it (partial recovery case)
        return True


def _reject_if_initialized():
    """Return an error response if the system is already initialized.

    Used as a guard on mutating setup endpoints.  Returns None if the
    system is NOT initialized (i.e., installation is allowed).
    """
    try:
        if _is_initialized():
            return jsonify({
                "ok": False,
                "error": _("System is already initialized. Re-installation is not permitted through this endpoint.")
            }), 409  # 409 Conflict
    except Exception as e:
        # If we cannot determine state, block installation as a safety measure.
        # The admin can always use CLI for recovery.
        logger.error("Initialization check failed: %s", e)
        return jsonify({
            "ok": False,
            "error": _("Cannot verify system state. Installation blocked for safety. Use CLI for recovery.")
        }), 503
    return None


def _require_setup_window_or_admin():
    """Gate the wizard's diagnostic endpoints once installation is complete.

    AUDIT-SETUP-01: ``/api/setup/system-check`` and ``/api/setup/test-database``
    are public so the first-run wizard can use them before any account exists.
    They were never closed afterwards, so on an installed system an anonymous
    visitor could fingerprint the host and make the server open PostgreSQL
    connections to arbitrary hosts and ports (network probing / credential
    guessing). After initialization they are administrator diagnostics.

    Returns ``None`` when the request may proceed, otherwise a response.
    """
    if not _is_initialized():
        return None
    from flask import g

    user = g.get("user")
    if not getattr(user, "is_authenticated", False):
        return jsonify({"ok": False, "error": _("Authentication required"),
                        "code": "unauthenticated"}), 401
    if not user.has_role("admin"):
        return jsonify({"ok": False, "error": _("Insufficient permissions"),
                        "code": "forbidden"}), 403
    return None


# ── Page ──
# Every string the wizard script displays. The page ships these translated
# (keyed by the English msgid) so the browser never holds untranslated copies.
# A test asserts this list matches the T('…') calls in install-wizard-page.js.
WIZARD_SCRIPT_STRING_KEYS = (
    '%(count)s existing tables',
    '%(mb)s MB free',
    'Address',
    'Admin',
    'Admin password must be at least %(n)s characters.',
    'Administrator',
    'Check the step results above for details.',
    'Checking dependencies…',
    'Checking…',
    'Choose at least one dependency to install.',
    'Compatible',
    'Complete',
    'Could not check dependencies.',
    'Could not reach the server:',
    'Created and migrated',
    'Database',
    'Database & schema',
    'Database connection failed.',
    'Dependency details',
    'Details',
    'Environment',
    'Failed',
    'Fair',
    'Finalize',
    'Go to Login',
    'Hide dependency details',
    'Hide details',
    'Idle timeout cannot exceed the session lifetime.',
    'Import failed',
    'Import name',
    'Incompatible version',
    'Inspect',
    'Install',
    'Installation complete!',
    'Installation failed.',
    'Installed %(version)s',
    'Installing selected packages…',
    'Installing…',
    'Listed in requirements',
    'Minimum %(n)s characters',
    'Missing',
    'Needs attention: %(names)s',
    'Network error:',
    'Next',
    'None (command-line tool)',
    'Not available',
    'Optional',
    'Optional gaps: %(count)s',
    'Passwords do not match.',
    'Please try again.',
    'Ready',
    'Redirecting to login…',
    'Required',
    'Required dependencies need attention: %(ready)s of %(total)s ready',
    'Required dependencies ready: %(ready)s of %(total)s',
    'Requirement',
    'Running',
    'Runtime directories',
    'See docs/OPERATIONS.md for help.',
    'Selected dependencies installed.',
    'Some checks failed.',
    'Some selected dependencies could not be installed. Review the details and retry.',
    'Source',
    'Step %(current)s of %(total)s',
    'Strong',
    'Test Connection',
    'Testing…',
    'The per-minute rate limit cannot exceed the per-hour rate limit.',
    'Unknown',
    'Weak',
    'Write configuration',
    'You can still proceed, but you may need to install missing components before the database step will succeed.',
)


def _wizard_script_strings():
    """Translated strings for the wizard script; missing translations fall back to English."""
    return {key: _(key) for key in WIZARD_SCRIPT_STRING_KEYS}


def _wizard_language_options():
    """Endonym-labelled language choices for the wizard selector (canonical registry)."""
    from settings.languages import SUPPORTED_LANGUAGES
    return list(SUPPORTED_LANGUAGES.items())


@setup_bp.route("/setup", methods=["GET"])
def setup_page():
    if _is_initialized():
        return redirect(url_for("index"))
    return render_template(
        "Setup/install_wizard.html",
        wizard_script_strings=_wizard_script_strings(),
        wizard_language_options=_wizard_language_options(),
    )


# ── Step 1: System check ──
@setup_bp.route("/api/setup/system-check", methods=["GET"])
def system_check():
    gate = _require_setup_window_or_admin()
    if gate is not None:
        return gate
    from core.installer import check_system
    checks = check_system()
    all_ok = all(c.get("ok", False) for c in checks.values())
    return jsonify({"all_ok": all_ok, "checks": checks})


# ── Step 2: Test database ──
@setup_bp.route("/api/setup/test-database", methods=["POST"])
def test_database():
    gate = _require_setup_window_or_admin()
    if gate is not None:
        return gate
    from core.installer import test_database_connection

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "message": _("Request body must be a JSON object.")}), 400

    try:
        port = _integer_field(data, "port", "Database port", 5432, 1, 65535)
        host = _text_field(data, "host", "Database host", "localhost", required=True, maximum=253)
        user = _text_field(data, "user", "Database username", "postgres", required=True, maximum=63)
        database = _text_field(data, "database", "Database name", "analysis", required=True, maximum=63)
        password = _text_field(data, "password", "Database password", "", required=True,
                               maximum=1024, preserve=True)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    result = test_database_connection(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )
    status = 200 if result.get("ok", True) else 400
    return jsonify(result), status


# ── Step 5: Full installation ──
@setup_bp.route("/api/setup/install", methods=["POST"])
def run_installation():
    # GUARD 1: reject if system is already initialized
    guard_response = _reject_if_initialized()
    if guard_response is not None:
        return guard_response

    # GUARD 2: serialize installation — only one request at a time.
    # Non-blocking acquire: if another request is already installing,
    # return 409 immediately rather than waiting or racing.
    acquired = _install_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({
            "ok": False,
            "error": _("Installation is already in progress. Please wait for it to complete.")
        }), 409

    try:
        from core.installer import run_installation as _run
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": _("Request body must be a JSON object.")}), 400

        try:
            db_host = _text_field(data, "db_host", "Database host", "localhost", required=True, maximum=253)
            db_user = _text_field(data, "db_user", "Database username", "postgres", required=True, maximum=63)
            db_password = _text_field(data, "db_password", "Database password", "", required=True,
                                      maximum=1024, preserve=True)
            db_name = _text_field(data, "db_name", "Database name", "analysis", required=True, maximum=63)
            admin_username = _text_field(data, "admin_username", "Admin username", "admin",
                                         required=True, maximum=64)
            admin_password = _text_field(data, "admin_password", "Admin password", "", required=True,
                                         maximum=256, preserve=True)
            environment = _text_field(data, "environment", "Environment", "production", required=True)
            flask_host = _text_field(data, "flask_host", "Bind address", "0.0.0.0", required=True, maximum=253)
            log_level = _text_field(data, "log_level", "Log level", "INFO", required=True)
            ingestion_roots = _text_field(data, "ingestion_roots", "Ingestion roots", "", maximum=4096)

            db_port = _integer_field(data, "db_port", "Database port", 5432, 1, 65535)
            flask_port = _integer_field(data, "flask_port", "Web port", 5000, 1, 65535)
            max_workers = _integer_field(data, "max_workers", "Maximum processing workers", 8, 1, 32)
            max_failed_logins = _integer_field(data, "max_failed_logins", "Maximum failed logins", 5, 1, 20)
            lockout_minutes = _integer_field(data, "lockout_minutes", "Lockout duration", 15, 1, 1440)
            session_hours = _integer_field(data, "session_hours", "Session lifetime", 12, 1, 720)
            session_idle_hours = _integer_field(data, "session_idle_hours", "Idle timeout", 6, 1, 720)
            pw_min = _integer_field(data, "password_min_length", "Minimum password length", 12, 8, 128)
            rate_per_minute = _integer_field(data, "rate_limit_per_minute", "Rate limit per minute", 60, 1, 1000000)
            rate_per_hour = _integer_field(data, "rate_limit_per_hour", "Rate limit per hour", 600, 1, 1000000)
            file_processing_timeout = _integer_field(
                data, "file_processing_timeout", "Processing timeout", 1200, 30, 7200)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        if len(admin_password) < pw_min:
            return jsonify({"ok": False,
                            "error": _("Admin password must be at least %(n)s characters.", n=pw_min)}), 400
        if environment not in {"production", "staging", "development"}:
            return jsonify({"ok": False, "error": _("Environment must be production, staging, or development.")}), 400
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            return jsonify({"ok": False, "error": _("Choose a supported log level.")}), 400
        if session_idle_hours > session_hours:
            return jsonify({"ok": False,
                            "error": _("Idle timeout cannot exceed the session lifetime.")}), 400
        if rate_per_minute > rate_per_hour:
            return jsonify({"ok": False,
                            "error": _("The per-minute rate limit cannot exceed the per-hour limit.")}), 400

        # Map the validated frontend fields to the installer configuration.
        config = {
            "DB_HOST": db_host,
            "DB_PORT": str(db_port),
            "DB_USER": db_user,
            "DB_PASSWORD": db_password,
            "DB_NAME": db_name,
            "APP_ADMIN_USERNAME": admin_username,
            "APP_ADMIN_PASSWORD": admin_password,
            "FLASK_ENV": environment,
            "FLASK_PORT": str(flask_port),
            "FLASK_HOST": flask_host,
            "MAX_WORKERS": str(max_workers),
            "LOG_LEVEL": log_level,
            "INGESTION_ROOTS": ingestion_roots,
            "SECURITY_MAX_FAILED_LOGINS": str(max_failed_logins),
            "SECURITY_LOCKOUT_MINUTES": str(lockout_minutes),
            "SECURITY_SESSION_HOURS": str(session_hours),
            "SECURITY_SESSION_IDLE_HOURS": str(session_idle_hours),
            "PASSWORD_MIN_LENGTH": str(pw_min),
            "RATE_LIMIT_PER_MINUTE": str(rate_per_minute),
            "RATE_LIMIT_PER_HOUR": str(rate_per_hour),
            "FILE_PROCESSING_TIMEOUT": str(file_processing_timeout),
        }

        # remote_addr is already proxy-corrected (TRUSTED_PROXY_COUNT).
        result = _run(config, client_ip=request.remote_addr or "")
        result["redirect"] = url_for("index")
        status = 200 if result.get("ok") else 500
        return jsonify(result), status
    finally:
        _install_lock.release()


# ── Language choice (first run) ──
@setup_bp.route("/api/setup/language", methods=["POST"])
def set_setup_language():
    """Store the language chosen on the wizard as ``system.language``.

    ``/set_language`` needs a signed-in user, so an anonymous visitor to a fresh
    install cannot use it. This endpoint is only open until the system is
    initialized, accepts only a registered language code, and sets the same
    ``user_language`` cookie the login screen uses for this browser.
    """
    guard_response = _reject_if_initialized()
    if guard_response is not None:
        return guard_response

    from settings.languages import SUPPORTED_LANGUAGES

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": _("Request body must be a JSON object.")}), 400
    code = data.get("language")
    if not isinstance(code, str) or code not in SUPPORTED_LANGUAGES:
        return jsonify({"ok": False, "error": _("Choose a supported language.")}), 400

    persisted = True
    try:
        from settings import get_settings
        get_settings().set_setting("system.language", code)
    except Exception as exc:  # the cookie still applies to this browser
        persisted = False
        logger.warning("Setup language could not be saved to system.language: %s", exc)

    # The session is updated too, so signed-in state and the cookie agree.
    session["language"] = code
    response = jsonify({"ok": True, "language": code, "persisted": persisted})
    response.set_cookie("user_language", code, max_age=31536000, samesite="Lax", path="/")
    return response


# ── Python dependency readiness ──
@setup_bp.route("/api/setup/dependencies", methods=["GET"])
def dependency_report():
    guard_response = _require_setup_window_or_admin()
    if guard_response is not None:
        return guard_response
    from core.dependency_readiness import build_report

    refresh = request.args.get("refresh") in ("1", "true", "yes")
    report = build_report(refresh=refresh)
    items = []
    for item in report["items"]:
        item = dict(item)
        localized = _localized_dependency_detail(item.get("detail_key"), item.get("detail_vars"))
        if localized is not None:
            item["detail"] = localized
        items.append(item)
    return jsonify({"ok": True, **report, "items": items}), 200


def _localized_dependency_detail(key, variables):
    """Translate a readiness detail message. Technical output (import errors, pip) stays as-is."""
    if not key:
        return None
    templates = {
        "Not installed": _("Not installed"),
        "Installed %(installed)s; requires %(requires)s": _("Installed %(installed)s; requires %(requires)s"),
        "Installed (command-line tool)": _("Installed (command-line tool)"),
        "Import did not finish within %(seconds)s seconds": _("Import did not finish within %(seconds)s seconds"),
        "Could not read package metadata: %(error)s": _("Could not read package metadata: %(error)s"),
        "pip exited with code %(code)s": _("pip exited with code %(code)s"),
    }
    text = templates.get(key)
    if text is None:
        return None
    try:
        return text % (variables or {})
    except (KeyError, TypeError, ValueError):
        return text


def _dependency_request_message(code, name=""):
    """Translated text for a rejected dependency install request."""
    if code == "none_chosen":
        return _("Choose at least one dependency to install.")
    if code == "too_many":
        return _("Too many dependencies in one request.")
    if code == "not_declared":
        return _("%(name)s is not a declared dependency.", name=name)
    return _("Invalid request.")


@setup_bp.route("/api/setup/dependencies/install", methods=["POST"])
def dependency_install():
    guard_response = _require_setup_window_or_admin()
    if guard_response is not None:
        return guard_response
    from core.dependency_readiness import (
        DependencyBusyError, DependencyRequestError, start_install,
    )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": _("Request body must be a JSON object.")}), 400
    try:
        job = start_install(data.get("names"))
    except DependencyRequestError as exc:
        return jsonify({"ok": False, "error": _dependency_request_message(exc.code, exc.name)}), 400
    except DependencyBusyError:
        return jsonify({"ok": False, "error": _("An installation is already running.")}), 409
    return jsonify({"ok": True, "job": job}), 202


@setup_bp.route("/api/setup/dependencies/jobs/<job_id>", methods=["GET"])
def dependency_job(job_id):
    guard_response = _require_setup_window_or_admin()
    if guard_response is not None:
        return guard_response
    from core.dependency_readiness import get_job

    job = get_job(job_id) if len(job_id) == 32 and job_id.isalnum() else None
    if job is None:
        return jsonify({"ok": False, "error": _("Installation job not found.")}), 404
    return jsonify({"ok": True, "job": job}), 200


# ── Status check ──
@setup_bp.route("/api/setup/check", methods=["GET"])
def check_setup_status():
    try:
        return jsonify({"initialized": _is_initialized()}), 200
    except Exception:
        return jsonify({"initialized": False}), 200


# ── Registration ──
def register_setup_routes(app):
    app.register_blueprint(setup_bp)

    @app.before_request
    def _check_database_setup():
        if request.endpoint in (
            "setup.setup_page", "setup.system_check",
            "setup.test_database", "setup.run_installation",
            "setup.check_setup_status", "static",
        ):
            return None
        if request.path.startswith("/api/setup/") or request.path.startswith("/static/"):
            return None
        if request.path == "/api/csrf-token":
            return None
        if request.endpoint in ("_internal_error", "not_found", "favicon"):
            return None
        try:
            if _is_initialized():
                return None
        except Exception:
            pass
        if request.path != "/setup":
            return redirect(url_for("setup.setup_page"))
        return None
