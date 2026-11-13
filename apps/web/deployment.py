"""Start-up validation of the deployment settings.

``run_web.py`` calls :func:`check_deployment` before serving, and
``verify_readiness.py`` reports the same findings. It is a pure function of
the environment, so every rule is unit-tested.

Two severities:

* **error**: the setting is invalid or dangerous, and the server refuses to
  start. Examples: debug mode in production, or a ``TRUSTED_PROXY_COUNT``
  that is not a number. The app would otherwise silently trust no proxy, and
  every client would share the proxy's address for rate limits and lockouts.
* **warning**: the setting is valid but unsafe for production. The server
  starts, and the warning says what to change. The development server in
  production is a warning, not an error, because ``FLASK_ENV`` defaults to
  ``production`` and ``WSGI_SERVER`` to ``flask``. Refusing would stop every
  existing default installation on upgrade.

See docs/OPERATIONS.md ("Start-up checks") for the operator-facing list.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import List, Mapping, Optional

LOOPBACK_HOSTS = {"localhost"}
MIN_SECRET_KEY_LENGTH = 32


@dataclass(frozen=True)
class Finding:
    level: str      # "error" | "warning"
    code: str
    message: str
    action: str = ""


class DeploymentError(RuntimeError):
    """Raised by :func:`enforce` when a finding is an error."""


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _is_loopback(host: str) -> bool:
    host = host.strip().strip("[]").lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_trusted_proxy_count(value: Optional[str]) -> int:
    """``TRUSTED_PROXY_COUNT`` as a non-negative int; ValueError otherwise."""
    text = (value or "").strip()
    if not text:
        return 0
    if not text.isdigit():
        raise ValueError(f"TRUSTED_PROXY_COUNT must be a whole number >= 0, not {text!r}")
    return int(text)


def _waitress_installed() -> bool:
    try:
        import waitress  # noqa: F401
    except ImportError:
        return False
    return True


def check_deployment(env: Optional[Mapping[str, str]] = None,
                     waitress_available: Optional[bool] = None) -> List[Finding]:
    """Every problem with the deployment settings in *env* (default os.environ)."""
    env = os.environ if env is None else env
    if waitress_available is None:
        waitress_available = _waitress_installed()
    findings: List[Finding] = []

    flask_env = (env.get("FLASK_ENV") or "production").strip().lower()
    production = flask_env == "production"
    debug = _truthy(env.get("FLASK_DEBUG"))
    host = (env.get("FLASK_HOST") or "0.0.0.0").strip()

    # -- errors: invalid or dangerous values --------------------------------
    if debug and production:
        findings.append(Finding(
            "error", "debug_in_production",
            "FLASK_DEBUG is enabled while FLASK_ENV=production. Debug mode runs the "
            "interactive debugger, which executes code sent by the browser.",
            "Set FLASK_DEBUG=false, or FLASK_ENV=development on a development machine."))

    try:
        proxies = parse_trusted_proxy_count(env.get("TRUSTED_PROXY_COUNT"))
    except ValueError as exc:
        proxies = None
        findings.append(Finding(
            "error", "invalid_trusted_proxy_count", str(exc),
            "Use 0 when clients connect directly, or 1 behind one reverse proxy."))

    port_text = (env.get("FLASK_PORT") or "5000").strip()
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        findings.append(Finding(
            "error", "invalid_port", f"FLASK_PORT must be a port number 1-65535, not {port_text!r}.",
            "Set FLASK_PORT, for example FLASK_PORT=5000."))

    threads = (env.get("WAITRESS_THREADS") or "").strip()
    if threads and not threads.isdigit():
        findings.append(Finding(
            "warning", "invalid_waitress_threads",
            f"WAITRESS_THREADS={threads!r} is not a number, so the default (32) is used.",
            "Set WAITRESS_THREADS to a whole number, 4 or more."))

    if not production:
        return findings

    # -- warnings: valid but unsafe in production ---------------------------
    server = (env.get("WSGI_SERVER") or "flask").strip().lower()
    if server != "waitress":
        findings.append(Finding(
            "warning", "development_server",
            "Production is being served by Flask's built-in development server, which "
            "is not hardened against slow or hostile clients.",
            'Install Waitress (pip install -e ".[server]") and set WSGI_SERVER=waitress.'))
    elif not waitress_available:
        findings.append(Finding(
            "warning", "waitress_missing",
            "WSGI_SERVER=waitress, but Waitress is not installed, so the built-in "
            "development server is used instead.",
            'pip install -e ".[server]" (or pip install "waitress>=3.0").'))

    if proxies and not _is_loopback(host):
        findings.append(Finding(
            "warning", "proxy_bypassable",
            f"TRUSTED_PROXY_COUNT={proxies}, but the app listens on {host}. Clients that "
            "connect to the app directly, bypassing the proxy, can forge "
            "X-Forwarded-For and so evade rate limits and lockouts, or pose as another "
            "address in the audit log.",
            "Set FLASK_HOST=127.0.0.1 when the proxy runs on the same machine, or "
            "firewall the app port so only the proxy can reach it."))
    elif proxies == 0 and not _is_loopback(host):
        cookie_secure_value = (env.get("SESSION_COOKIE_SECURE") or "").strip().lower()
        cookie_secure = (cookie_secure_value in {"1", "true", "yes", "on"}
                         if cookie_secure_value else True)
        if cookie_secure:
            message = (
                "Production with no reverse proxy (TRUSTED_PROXY_COUNT=0), listening on "
                f"{host}. Secure session cookies prevent sign-in over plain HTTP, while "
                "passwords would cross the network unencrypted."
            )
        else:
            message = (
                "Production with no reverse proxy (TRUSTED_PROXY_COUNT=0), listening on "
                f"{host} with SESSION_COOKIE_SECURE=false. HTTP sign-in is enabled, but "
                "credentials and session cookies cross the network unencrypted; restrict "
                "access to a trusted private network."
            )
        findings.append(Finding(
            "warning", "no_tls_proxy", message,
            "Prefer a TLS-terminating proxy (deploy/nginx/syltharae.conf) and set "
            "TRUSTED_PROXY_COUNT=1 and FLASK_HOST=127.0.0.1. For HTTP on a trusted LAN "
            "only, explicitly set SESSION_COOKIE_SECURE=false and SESSION_COOKIE_SAMESITE=Lax."))

    secret = env.get("FLASK_SECRET_KEY") or ""
    if secret and len(secret) < MIN_SECRET_KEY_LENGTH:
        findings.append(Finding(
            "warning", "short_secret_key",
            f"FLASK_SECRET_KEY is {len(secret)} characters. It signs sessions and CSRF "
            f"tokens, and should be at least {MIN_SECRET_KEY_LENGTH} random characters.",
            'python -c "import secrets; print(secrets.token_hex(32))", then restart.'))

    return findings


def format_findings(findings: List[Finding]) -> List[str]:
    lines = []
    for f in findings:
        lines.append(f"[{f.level.upper()}] {f.message}")
        if f.action:
            lines.append(f"[ACTION] {f.action}")
    return lines


def enforce(env: Optional[Mapping[str, str]] = None, printer=print,
            waitress_available: Optional[bool] = None) -> List[Finding]:
    """Print every finding; raise DeploymentError when any is an error."""
    findings = check_deployment(env, waitress_available=waitress_available)
    for line in format_findings(findings):
        printer(line)
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise DeploymentError("Refusing to start: " + " ".join(f.message for f in errors))
    return findings
