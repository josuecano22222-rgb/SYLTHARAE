"""A JSON body that is not an object must be refused, not crash the view.

The malformed-body sweep (``tools/audit/probe_malformed.py``) posted a scalar,
a string, a list and ``null`` to every mutating route and received 162 500s
across 68 routes: the views call ``data.get(...)`` on whatever
``request.get_json()`` returned, so a non-mapping body raises ``AttributeError``
inside the handler and the client gets "An internal error occurred" plus a
correlation id.

No route in this application takes a bare JSON array as its body (checked by
reading every ``get_json`` call site), so the shape can be enforced once, at the
request boundary, before the view runs. That is what
``core.security.flask_ext`` now does: a JSON request whose body parses to
anything other than an object answers 400 ``invalid_json_body``.

The list below is the set of routes that answered 500 to a non-object body
before the fix; it is deliberately a sample across blueprints, not the whole
set - the sweep is the exhaustive check.
"""

import uuid

import pytest

#: (method, path, role needed to reach the handler) - role ``None`` = public.
NON_OBJECT_TARGETS = [
    ("post", "/api/sources", "admin"),
    ("put", "/api/sources/1", "admin"),
    ("post", "/api/sides", "admin"),
    ("post", "/api/words", "admin"),
    ("post", "/category/add", "admin"),
    ("post", "/api/auth/users", "admin"),
    ("post", "/api/search/saved", "admin"),
    ("post", "/api/settings/reset", "admin"),
    ("post", "/api/settings/validate", "admin"),
    ("post", "/api/notifications/scan", "admin"),
    ("post", "/api/import/preview", "admin"),
    ("post", "/api/export/structured/plan", "admin"),
    ("post", "/api/analyst/categories", "admin"),
    ("post", "/files/export", "admin"),
    ("post", "/search/advanced", "admin"),
    ("post", "/auth/login", None),
    ("post", "/auth/change-password", None),
]

BODIES = {
    "scalar": b"7",
    "string": b'"text"',
    "list": b"[]",
    "null": b"null",
    "list-of-objects": b'[{"name": "x"}]',
}


def _client(client_factory):
    return client_factory("admin")


@pytest.mark.parametrize("body", sorted(BODIES))
@pytest.mark.parametrize("method,path,role", NON_OBJECT_TARGETS,
                         ids=[f"{m.upper()} {p}" for m, p, _ in NON_OBJECT_TARGETS])
def test_non_object_json_body_is_refused(client_factory, body, method, path, role):
    client = _client(client_factory)
    response = getattr(client, method)(
        path, data=BODIES[body], content_type="application/json")
    assert response.status_code < 500, (
        f"{method.upper()} {path} with a {body} body answered "
        f"{response.status_code}: {response.get_data(as_text=True)[:200]}")
    assert response.status_code == 400, (
        f"{method.upper()} {path} with a {body} body answered "
        f"{response.status_code}, expected 400: "
        f"{response.get_data(as_text=True)[:200]}")


def test_the_refusal_names_the_problem(client_factory):
    """A 400 that only says "bad request" tells the caller nothing."""
    response = _client(client_factory).post(
        "/api/sources", data=b'"just a string"', content_type="application/json")
    payload = response.get_json() or {}
    assert payload.get("code") == "invalid_json_body"
    assert "object" in (payload.get("error") or "").lower()


def test_an_object_body_still_reaches_the_view(client_factory):
    """The guard rejects the *shape*, never the request itself."""
    response = _client(client_factory).post(
        "/api/sources", json={"name": "", "ownership": "guard-check"})
    assert response.status_code == 400          # the view's own validation
    assert (response.get_json() or {}).get("code") != "invalid_json_body"


def test_form_encoded_and_multipart_bodies_are_untouched(client_factory):
    client = _client(client_factory)
    form = client.post("/api/sources", data={"name": ""})
    assert (form.get_json() or {}).get("code") != "invalid_json_body"
    plain = client.post("/api/words", data=b"not json", content_type="text/plain")
    assert (plain.get_json() or {}).get("code") != "invalid_json_body"


# ---------------------------------------------------------------------------
# The second half of the sweep: the body *is* an object, but a field in it has
# the wrong type, a NUL byte, or a length the column cannot hold.
# ---------------------------------------------------------------------------

WRONG_TYPE_BODIES = {
    "list-for-name": {"name": ["a"]},
    "dict-for-name": {"name": {"a": 1}},
    "number-for-name": {"name": 5},
    "nul-in-name": {"name": "before\x00after"},
    "oversized-name": {"name": "A" * 5000},
    "list-for-query": {"name": "ok", "query": ["x"]},
    "nested-filters": {"name": "ok", "query": "q", "filters": {"a": {"b": {"c": 1}}}},
}


def _unique(body):
    """Give each case its own name.

    ``sources.name`` and ``sides.name`` are UNIQUE, so a fixed name turns the
    second run of a case into a duplicate-key conflict and the test measures
    the wrong thing.
    """
    if isinstance(body.get("name"), str):
        body = dict(body)
        body["name"] = f"{body['name']} {uuid.uuid4().hex[:10]}"
    return body


@pytest.mark.parametrize("body", sorted(WRONG_TYPE_BODIES))
@pytest.mark.parametrize("method,path,role", NON_OBJECT_TARGETS,
                         ids=[f"{m.upper()} {p}" for m, p, _ in NON_OBJECT_TARGETS])
def test_a_field_of_the_wrong_type_is_refused(client_factory, body, method, path, role):
    client = _client(client_factory)
    response = getattr(client, method)(
        path, json=_unique(WRONG_TYPE_BODIES[body]))
    assert response.status_code < 500, (
        f"{method.upper()} {path} with a {body} field answered "
        f"{response.status_code}: {response.get_data(as_text=True)[:200]}")


@pytest.mark.parametrize("value", [["a"], {"a": 1}, 5, "before\x00after"])
def test_the_source_writer_names_the_field_it_rejected(app, client_factory, value):
    """A 400 that names the field is a fix; one that says "bad request" is not."""
    response = _client(client_factory).post("/api/sources", json={"name": value})
    assert response.status_code == 400
    payload = response.get_json() or {}
    assert payload.get("field") == "name", payload
    assert "name" in (payload.get("error") or "")
