"""An unreachable session store must not look like "you are not signed in".

Measured during the round-4 outage injection: with PostgreSQL stopped, every
API request answered ``401`` ``{"code": "unauthenticated"}`` in about 2 ms, even
for a client holding a valid session cookie — and the cookie had been discarded,
so the user was signed out for good by a database that came back a minute later.

The distinction matters to a client: ``unauthenticated`` means "sign in", and
signing in cannot work while the database is down. This file pins down:

* when the session lookup *fails* (raises) rather than answering "no session",
  the response is still 401 but carries ``authentication_unavailable`` and a
  ``reason`` naming the cause, so the client can retry instead of asking the
  user for a password they cannot possibly use;
* the client's session cookie is kept, so the request succeeds again once the
  store is back;
* a genuinely absent or invalid session still answers plain
  ``unauthenticated``.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def store_down(monkeypatch):
    """Make the session store fail rather than answer "no such session"."""
    from core.security import flask_ext

    class _Broken:
        def validate_session(self, _token):
            raise ConnectionError("connection to server failed")

    monkeypatch.setattr(flask_ext, "get_auth_service", lambda: _Broken())


def test_a_failing_session_store_says_why_it_could_not_authenticate(
        admin_client, store_down):
    response = admin_client.get("/api/sources")

    assert response.status_code == 401
    payload = response.get_json()
    assert payload["code"] == "authentication_unavailable"
    assert "retry" in payload["reason"].lower() or "store" in payload["reason"].lower()


def test_the_session_cookie_survives_a_store_outage(admin_client, store_down, monkeypatch):
    from core.security import flask_ext

    assert admin_client.get("/api/sources").status_code == 401

    # the store comes back: the same client, with the same cookie, works again
    monkeypatch.undo()
    response = admin_client.get("/api/sources")
    assert response.status_code == 200, response.get_data(as_text=True)


def test_no_session_at_all_is_still_plain_unauthenticated(client):
    response = client.get("/api/sources")

    assert response.status_code == 401
    assert response.get_json()["code"] == "unauthenticated"
