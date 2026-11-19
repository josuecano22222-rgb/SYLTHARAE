"""The one JSON error envelope for API routes.

AUDIT-ENV-01/02: the application answered API failures in two shapes.

* the majority (about forty modules): ``{"error": "<sentence>", "code": "...",
  "correlation_id": "..."}``;
* nine route modules (audit, places, preferences, reports, retention,
  scenarios, schedules, signals, taxonomy) plus the framework handlers:
  ``{"success": false, "error": {"code", "message", "request_id"}}``.

A client written against one shape reads the other as ``[object Object]`` (an
object interpolated into a toast) or ``undefined`` (``payload.error.message`` on
a string). Both were reproduced in the browser harness.

During the migration every API error therefore carries **both**: the structured
``error`` object with the detail, and ``code``/``message``/``request_id`` at the
top level. New code should read ``error.message`` and the top-level ``code``;
the duplicate keys are what let the forty string-shaped endpoints and the nine
object-shaped ones converge without a flag day.

``render_error`` is the single implementation - the nine routes above import it
instead of each keeping a copy of the same three lines.
"""

from __future__ import annotations

import uuid

from flask import jsonify

__all__ = ["error_payload", "render_error"]


def error_payload(code: str, message: str, request_id: str | None = None,
                  details=None) -> dict:
    """The envelope body, without a Flask response around it."""
    request_id = request_id or uuid.uuid4().hex[:12]
    error = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        error["details"] = details
    return {
        "success": False,
        "error": error,
        # Compatibility keys - see the module docstring.
        "code": code,
        "message": message,
        "request_id": request_id,
    }


def render_error(code: str, message: str, status: int = 400, details=None):
    """``(response, status)`` with the envelope above."""
    return jsonify(error_payload(code, message, details=details)), status
