"""One error envelope, readable by both client shapes.

AUDIT-ENV-01/02: API failures were returned in two shapes - a string
``error`` (about forty modules) and ``{"success": false, "error": {code,
message, request_id}}`` (nine modules plus the framework handlers). A client
written for one shape rendered the other as ``[object Object]`` or
``undefined``; the browser harness reproduced both.

Every API error now carries the structured object *and* the top-level
``code``/``message``/``request_id``, and framework-level answers (404, 405, 500,
CSRF) are JSON for API callers instead of an HTML page that a ``fetch()``
cannot parse.
"""
from __future__ import annotations

import pytest
from flask import Flask

from Api.utils.envelope import error_payload, render_error


def test_the_envelope_carries_both_shapes():
    body = error_payload("not_found", "The requested resource does not exist.")
    assert body["success"] is False
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "The requested resource does not exist."
    assert body["error"]["request_id"]
    # Compatibility keys, so a client that reads a string error keeps working.
    assert body["code"] == "not_found"
    assert body["message"] == body["error"]["message"]
    assert body["request_id"] == body["error"]["request_id"]


def test_details_are_attached_to_the_error_object():
    body = error_payload("invalid", "Bad input.", details={"field": "name"})
    assert body["error"]["details"] == {"field": "name"}


def test_render_error_returns_a_json_response_and_the_status():
    app = Flask(__name__)
    with app.app_context():
        response, status = render_error("conflict", "Already exists.", 409)
    assert status == 409
    assert response.get_json()["error"]["code"] == "conflict"


def test_client_error_carries_message_and_request_id():
    """``core.errors.client_error`` is the 5xx/4xx path used by most routes."""
    from core.errors import client_error

    app = Flask(__name__)
    with app.test_request_context("/api/words", headers={"Accept": "application/json"}):
        response, status = client_error(RuntimeError("boom"), status=500,
                                        public_message="An internal error occurred",
                                        code="internal_error")
    body = response.get_json()
    assert status == 500
    assert body["error"] == "An internal error occurred"
    assert body["message"] == "An internal error occurred"
    assert body["code"] == "internal_error"
    assert body["request_id"] == body["correlation_id"]
    assert "boom" not in str(body), "the exception text leaked to the client"


def test_a_client_fixable_http_error_keeps_its_status():
    from werkzeug.exceptions import BadRequest

    from core.errors import client_error

    app = Flask(__name__)
    with app.test_request_context("/api/words"):
        response, status = client_error(BadRequest(), status=500)
    assert status == 400
    assert response.get_json()["message"]


@pytest.mark.parametrize("path,accept,expected", [
    ("/api/words", "text/html", True),
    ("/words", "application/json", True),
    ("/words", "text/html,application/xhtml+xml", False),
])
def test_only_api_or_json_requests_get_json_errors(path, accept, expected):
    from apps.web.app import _wants_json_error

    app = Flask(__name__)
    with app.test_request_context(path, headers={"Accept": accept}):
        assert _wants_json_error() is expected


def test_the_framework_handlers_answer_json_for_api_paths():
    """A 404 under /api/ must not be an HTML page (the fetch() cannot parse it)."""
    from apps.web.app import _json_error

    app = Flask(__name__)
    with app.test_request_context("/api/does-not-exist",
                                  headers={"Accept": "application/json"}):
        response, status = _json_error("not_found", "The requested resource does not exist.", 404)
    assert status == 404
    assert response.is_json
    assert response.get_json()["error"]["code"] == "not_found"
