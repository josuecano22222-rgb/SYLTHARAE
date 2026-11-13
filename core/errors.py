"""Secure error handling (SEC-08).

Raw exception text must never reach clients.  This module provides:

* ``client_error(...)``  -> builds a client-safe error payload with a
  correlation ID (``ERR-YYYYMMDD-NNNN``) and logs the full details
  server-side (traceback, user, route, subsystem).
* ``register_error_handlers(app)`` -> Flask handlers that replace the
  previous behaviour of returning ``str(exception)``.

Never expose: SQL, filesystem paths, credentials, connection strings, stack
traces, internal class names, or database details in responses.
"""

from __future__ import annotations

import logging
import itertools
import re
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException


class ClientSafeError(Exception):
    """An error whose ``str(exc)`` is written for the end user.

    Raise it (or a subclass) for *rejections* - invalid input, a missing file,
    a policy refusal - never for internal faults. Code that turns exceptions
    into user-visible messages (the job worker, ``services.jobs.manager``)
    shows the text of these as-is and logs them as warnings; every other
    exception is reported as ``Internal error (<correlation id>)`` with the
    detail kept server-side. The message must not contain secrets, SQL, paths
    outside the application or other user input echoed back unescaped.
    """

logger = logging.getLogger(__name__)

_counter_lock = threading.Lock()
_counter = itertools.count(1)

# Error categories -> whether the class of problem is security-relevant
STATUS_BY_EXCEPTION = {
    "psycopg2.errors.UniqueViolation": 409,
}


def new_correlation_id() -> str:
    """Generate a correlation id like ``ERR-20260910-000123``."""
    with _counter_lock:
        n = next(_counter)
    return f"ERR-{datetime.utcnow().strftime('%Y%m%d')}-{n:06d}"


#: A full connection string - ``scheme://rest-of-token``.
_DSN_RE = re.compile(r"\b[a-zA-Z][\w+.\-]*://\S+")
#: Credentials without a scheme - ``user:password@host``.
_CREDENTIALS_RE = re.compile(r"\b[\w.\-]+:[^\s/@:]+@[\w.\-]+")


def sanitize_message(message: str) -> str:
    """Best-effort scrubbing of low-level details from any text we must emit.

    Used on the rare messages that are shown to an operator but originate
    outside the application's own vocabulary. Connection strings are removed
    whole (their user, password, host and database included) rather than just
    their scheme, so a DSN cannot survive as a fragment.
    """
    if not message:
        return message
    scrubbed = _DSN_RE.sub("[removed]", message)
    scrubbed = _CREDENTIALS_RE.sub("[removed]", scrubbed)
    for token in ("Traceback (most recent call last)", "psql:"):
        scrubbed = scrubbed.replace(token, "[removed]")
    return scrubbed


def client_safe_message(
    exc: Exception,
    subsystem: str = "web",
) -> str:
    """SEC-08: log full detail server-side, return a client-safe message.

    For embedding in JSON payloads where ``client_error`` (which returns a
    full response) cannot be used, e.g. ``{'error': client_safe_message(e)}``.
    The returned message never contains exception text; the correlation id
    lets operators find the server-side detail.
    """
    correlation_id = new_correlation_id()
    log_server_error(exc, correlation_id, subsystem=subsystem)
    return f"Internal error ({correlation_id})"


def log_server_error(
    exc: Exception,
    correlation_id: str,
    subsystem: str = "web",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Log the full details server-side, tied to the correlation id.

    Safe to call from worker threads: request/user details are included
    only when a request context is actually active.
    """
    user_name = None
    route = path = method = None
    try:
        from flask import has_request_context

        if has_request_context():
            user = getattr(g, "user", None)
            user_name = getattr(user, "username", None) if user is not None else None
            route = request.endpoint if request else None
            path = request.path if request else None
            method = request.method if request else None
    except Exception:
        pass
    payload = {
        "correlation_id": correlation_id,
        "exception_type": exc.__class__.__name__,
        "route": route,
        "path": path,
        "method": method,
        "user": user_name,
        "subsystem": subsystem,
    }
    if extra:
        payload.update(extra)
    logger.error(
        "Unhandled exception [%s]: %s", correlation_id, payload, exc_info=exc
    )


def client_error(
    exc: Exception,
    status: int = 500,
    subsystem: str = "web",
    public_message: str = "An internal error occurred",
    extra: Optional[Dict[str, Any]] = None,
    success_key: Optional[str] = None,
    code: Optional[str] = None,
):
    """Log server-side and produce a client-safe JSON error response.

    ``success_key`` adds ``{"success": False}`` (or any key) for endpoints
    whose clients expect that shape.

    An ``HTTPException`` keeps its own status: routes wrap their body in
    ``except Exception`` and call this with ``status=500``, which turned a
    request the client can fix (no JSON content type, a bad parameter) into a
    reported server fault. The exception's code is the truth; ``status`` is
    only the default for genuine failures. Its description is a fixed,
    framework-authored sentence, so it is safe to return.
    """
    if isinstance(exc, HTTPException) and exc.code and exc.code < 500:
        body: Dict[str, Any] = {}
        if success_key:
            body[success_key] = False
        body["error"] = exc.description or public_message
        body["code"] = (exc.name or "error").lower().replace(" ", "_")
        response = jsonify(body)
        response.status_code = exc.code
        return response, exc.code

    correlation_id = new_correlation_id()
    log_server_error(exc, correlation_id, subsystem=subsystem, extra=extra)
    body = {}
    if success_key:
        body[success_key] = False
    body["error"] = public_message
    body["correlation_id"] = correlation_id
    # A stable, caller-branchable code. It is a constant from the call site,
    # never anything derived from the exception, so it cannot carry internals
    # the fixed sentence withholds.
    if code:
        body["code"] = code
    return jsonify(body), status


#: Substrings psycopg2 puts in a unique-constraint violation message. Used
#: only when the exception chain carries no psycopg2 class we can see (the
#: repository wraps the driver error in ``QueryError``).
_UNIQUE_MARKERS = (
    "duplicate key value violates unique constraint",
    "UniqueViolation",
)


def is_unique_violation(exc: BaseException) -> bool:
    """Did *exc* come from a UNIQUE constraint?

    The repositories wrap driver errors (``QueryError``), so the original
    ``psycopg2.errors.UniqueViolation`` is a ``__cause__`` away rather than the
    exception being caught. Walking the chain is what lets a handler answer
    "that name is taken" (409) instead of reporting a server fault for
    something the client typed.
    """
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in ("UniqueViolation", "IntegrityError"):
            return True
        text = str(current)
        if any(marker in text for marker in _UNIQUE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def conflict(message: str, *, field: str | None = None,
             success_key: str | None = None):
    """A 409 the client can act on: the value already exists."""
    body = {"error": message, "code": "already_exists"}
    if field:
        body["field"] = field
    if success_key:
        body[success_key] = False
    return body, 409


def register_error_handlers(app) -> None:
    """Install secure error handlers for all exceptions."""

    @app.errorhandler(403)
    def _forbidden_page(e):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "Insufficient permissions", "code": "forbidden"}), 403
        from flask import render_template

        return render_template("403.html"), 403

    @app.errorhandler(Exception)
    def _handle_any(e):
        # Let HTTP exceptions (404, 400, CSRFError...) keep their status but
        # ensure no raw internals leak for 5xx.
        if isinstance(e, HTTPException):
            if e.code >= 500:
                return client_error(e, status=e.code)
            return e
        return client_error(e, status=500)

    @app.errorhandler(500)
    def _handle_500(e):
        if request.path.startswith("/api/") or request.is_json:
            return client_error(e, status=500)
        from flask import render_template

        return render_template("500.html"), 500
