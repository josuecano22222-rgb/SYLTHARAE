"""A client mistake must stay a client error, even inside a route's net.

Every API route in this tree wraps its body in ``try/except Exception`` and
answers with ``client_error(exc, status=500)``. That is right for a database
outage, but it also swallowed ``werkzeug``'s HTTP exceptions: a POST without a
JSON content type raised ``UnsupportedMediaType`` (415), the net turned it into
"An internal error occurred" with status 500, and the audit's route matrix saw
eight endpoints reporting a server fault for a request the client can fix.
"""

from __future__ import annotations

import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.security


@pytest.fixture()
def app_client(app, admin_client):
    return admin_client


@pytest.mark.parametrize("path", ["/api/search", "/api/words"])
def test_a_request_without_json_is_a_client_error_not_a_server_error(
        app_client, path):
    """No Content-Type → 415, never 500, and never a leaked internals message."""
    resp = app_client.post(path)  # deliberately no content type, no body
    assert resp.status_code != 500, (
        f"{path} answered 500 for a request the client can fix: "
        f"{resp.get_data(as_text=True)[:200]}")
    assert resp.status_code in (400, 415, 422)


def test_the_payload_names_the_problem_without_internals(app_client):
    resp = app_client.post("/api/search")
    body = resp.get_json() or {}
    assert body.get("error")
    assert "Traceback" not in str(body)
    assert "werkzeug" not in str(body).lower()


def test_client_error_keeps_the_http_status_it_was_given():
    """The unit-level contract the routes rely on."""
    from flask import Flask

    from core.errors import client_error
    from werkzeug.exceptions import UnsupportedMediaType

    app = Flask(__name__)
    with app.app_context():
        response, status = client_error(UnsupportedMediaType(), status=500)
        assert status == 415
        assert response.get_json()["error"]


def test_the_translation_catalog_endpoint_answers(app_client):
    """``/api/translations`` must return a catalogue, not a 500.

    Babel's catalogue carries plural entries under tuple keys
    (``('%(n)s file', 0)``). Copying those into a dict put a tuple where a
    string key belonged, and ``jsonify`` failed while sorting the keys - the
    endpoint answered "internal error" for every locale that has plurals.
    """
    resp = app_client.get("/api/translations", query_string={"locale": "en"})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
    body = resp.get_json()
    assert body["success"] is True
    assert isinstance(body["translations"], dict) and body["translations"]
    assert all(isinstance(k, str) for k in body["translations"])


def test_batch_import_without_inputs_is_a_400(app_client):
    """A missing form field is the client's mistake, not a server fault.

    ``int(request.form.get('source_id'))`` raised ``TypeError`` on ``None`` and
    the route's net reported it as a 500.
    """
    resp = app_client.post("/api/import-export/batch-import", data={})
    assert resp.status_code == 400, (resp.status_code,
                                     resp.get_data(as_text=True)[:200])
    assert (resp.get_json() or {}).get("error")
