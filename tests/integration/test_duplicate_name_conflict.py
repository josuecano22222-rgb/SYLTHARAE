"""A duplicate name is a conflict, not a server fault.

``sources.name``, ``sides.name`` and ``words.word`` are UNIQUE in the frozen
baseline (m0001). Creating a second source - or renaming one - to an existing
name used to reach the INSERT, raise ``UniqueViolation`` from psycopg2, and
leave the client with ``500 {"error": "Failed to create source"}`` plus a
correlation id: the analyst sees "an internal error" for typing a name that is
already taken, and the server logs a traceback for a request that was simply
invalid.

The handlers now recognise a unique violation (``core.errors.
is_unique_violation`` walks the ``QueryError`` wrapper chain) and answer
``409 {"code": "already_exists", "field": "name"}``.
"""

import uuid

import pytest


def _name(prefix="dup"):
    return f"{prefix} {uuid.uuid4().hex[:10]}"


class TestDuplicateNames:
    def test_second_source_with_the_same_name_is_a_conflict(self, app, client_factory):
        client = client_factory("admin")
        name = _name("source")
        first = client.post("/api/sources", json={"name": name})
        assert first.status_code == 201, first.get_data(as_text=True)

        second = client.post("/api/sources", json={"name": name})
        assert second.status_code == 409, second.get_data(as_text=True)
        payload = second.get_json() or {}
        assert payload.get("code") == "already_exists"
        assert payload.get("field") == "name"

    def test_renaming_a_source_onto_an_existing_name_is_a_conflict(
            self, app, client_factory):
        client = client_factory("admin")
        taken, other = _name("taken"), _name("other")
        assert client.post("/api/sources", json={"name": taken}).status_code == 201
        created = client.post("/api/sources", json={"name": other})
        source_id = (created.get_json() or {}).get("id")
        assert source_id

        response = client.put(f"/api/sources/{source_id}", json={"name": taken})
        assert response.status_code == 409, response.get_data(as_text=True)
        assert (response.get_json() or {}).get("field") == "name"

    def test_second_side_with_the_same_name_is_a_conflict(self, app, client_factory):
        client = client_factory("admin")
        name = _name("side")
        assert client.post("/api/sides", json={"name": name}).status_code == 201
        second = client.post("/api/sides", json={"name": name})
        assert second.status_code == 409, second.get_data(as_text=True)

    def test_the_unique_violation_detector_sees_through_a_wrapper(self):
        """The repository wraps the driver error; the detector must follow it."""
        from core.errors import is_unique_violation

        class UniqueViolation(Exception):
            pass

        wrapped = RuntimeError("Query execution failed: duplicate key value "
                               "violates unique constraint \"sources_name_key\"")
        assert is_unique_violation(wrapped) is True

        inner = UniqueViolation("duplicate key value violates unique constraint")
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner
        assert is_unique_violation(outer) is True
        assert is_unique_violation(ValueError("nothing to do with the database")) is False


class TestCreateOrGetContracts:
    """The writers that report "already exists" must report the real id."""

    def test_creating_an_existing_word_returns_its_id_not_a_boolean(
            self, app, client_factory):
        """``word_exists`` answers True/False; ``id`` must be the row's id.

        The endpoint is create-or-get - its docstring promises "return existing
        word ID if it already exists" - and it used to answer
        ``{"id": true}``, so a client that followed the id got a boolean.
        """
        client = client_factory("admin")
        word = f"w{uuid.uuid4().hex[:10]}"

        created = client.post("/api/words", json={"word": word})
        assert created.status_code == 201, created.get_data(as_text=True)
        word_id = (created.get_json() or {}).get("id")
        assert isinstance(word_id, int)

        again = client.post("/api/words", json={"word": word})
        payload = again.get_json() or {}
        assert again.status_code == 200, again.get_data(as_text=True)
        assert payload.get("already_exists") is True
        assert payload.get("id") == word_id

    def test_a_duplicate_lexis_category_is_a_conflict(self, app, client_factory):
        client = client_factory("admin")
        name = _name("lexis")
        assert client.post("/api/lexis/taxonomy/categories",
                           json={"name": name}).status_code == 200
        duplicate = client.post("/api/lexis/taxonomy/categories", json={"name": name})
        assert duplicate.status_code == 409, duplicate.get_data(as_text=True)
        payload = duplicate.get_json() or {}
        assert payload.get("code") == "already_exists"
        assert payload.get("field") == "name"
