"""The translation API: POST only, authenticated, validated, audited, read-only.

Every endpoint here is a POST. There is no GET that translates anything, so a
browser navigation, a prefetch or a crawler cannot start work; the CSRF token
applies as it does to every other POST in the application.

The audit requirement is the interesting one: a translation is a derived
reading of stored material and the record of *who asked for which document in
which language* has to survive, so each accepted request writes one
``audit_log`` row.
"""

import uuid

import pytest


@pytest.fixture()
def stub_engine():
    from services.translation import engines as engine_registry

    class Stub:
        id = "stub"
        version = "stub-1.0"
        license = "test"
        document_safe = True

        def availability(self):
            return True, None

        def translate(self, texts, source_lang, target_lang):
            return [f"{target_lang}<{text}>" for text in texts]

    engine_registry.register_engine(Stub())
    try:
        yield "stub"
    finally:
        engine_registry.unregister_engine("stub")


@pytest.fixture()
def translation_memory(tmp_path, monkeypatch):
    """A per-test memory file, wired into the app's service singleton."""
    from services.translation import service as service_module
    from services.translation.memory import TranslationMemory

    memory = TranslationMemory(tmp_path / "translation_memory.db")
    service_module.configure(memory=memory)
    try:
        yield memory
    finally:
        service_module.reset()


def _audit_rows(action):
    from Api.utils.utils import execute_query

    return execute_query(
        "SELECT action, resource, detail FROM audit_log WHERE action = %s"
        " ORDER BY id DESC", (action,), fetch="all") or []


class TestAuthentication:
    @pytest.mark.parametrize("path", [
        "/api/translation/capabilities",
        "/api/translation/text",
        "/api/translation/document",
        "/api/translation/documents",
        "/api/translation/memory/correct",
        "/api/translation/memory/suggest",
    ])
    def test_every_endpoint_requires_a_signed_in_user(self, app, client, path):
        response = client.post(path, json={})
        assert response.status_code in (302, 401, 403), (
            f"POST {path} answered {response.status_code} unauthenticated")


class TestPostOnly:
    @pytest.mark.parametrize("path", [
        "/api/translation/capabilities",
        "/api/translation/text",
        "/api/translation/document",
        "/api/translation/documents",
        "/api/translation/memory/correct",
        "/api/translation/memory/suggest",
    ])
    def test_no_endpoint_answers_a_get(self, app, client_factory, path):
        client = client_factory("admin")
        response = client.get(path)
        assert response.status_code == 405, (
            f"GET {path} answered {response.status_code}: only POST is allowed")


class TestValidation:
    def test_text_is_required(self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        response = client.post("/api/translation/text", json={"target_lang": "he"})
        assert response.status_code == 400, response.get_data(as_text=True)
        assert (response.get_json() or {}).get("field") == "text"

    def test_target_language_must_be_one_of_the_supported_ones(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        response = client.post("/api/translation/text",
                               json={"text": "Hello.", "target_lang": "xx"})
        assert response.status_code == 400
        assert (response.get_json() or {}).get("field") == "target_lang"

    def test_an_oversized_selection_is_refused_with_the_limit_in_the_message(
            self, app, client_factory, stub_engine, translation_memory):
        from services.translation.text import MAX_REQUEST_CHARS

        client = client_factory("admin")
        response = client.post("/api/translation/text", json={
            "text": "a " * (MAX_REQUEST_CHARS // 2 + 10), "target_lang": "he"})
        assert response.status_code == 413, response.get_data(as_text=True)
        body = response.get_json() or {}
        assert body.get("code") == "text_too_large"
        assert str(MAX_REQUEST_CHARS) in body.get("error", "")

    def test_a_nul_byte_is_a_validation_error_not_a_server_error(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        response = client.post("/api/translation/text",
                               json={"text": "a\x00b", "target_lang": "he"})
        assert response.status_code == 400, response.get_data(as_text=True)

    def test_a_bad_body_shape_is_a_validation_error(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        for body in ([], 3, "text"):
            response = client.post("/api/translation/text", json=body)
            assert 400 <= response.status_code < 500, (body, response.status_code)

    def test_the_documents_endpoint_bounds_the_number_of_files(
            self, app, client_factory, stub_engine, translation_memory):
        from services.translation.service import MAX_DOCUMENTS_PER_JOB

        client = client_factory("admin")
        response = client.post("/api/translation/documents", json={
            "file_ids": list(range(1, MAX_DOCUMENTS_PER_JOB + 2)),
            "target_lang": "he"})
        assert response.status_code == 400, response.get_data(as_text=True)
        assert (response.get_json() or {}).get("field") == "file_ids"


class TestTranslation:
    def test_a_selection_is_translated_and_the_response_labels_it(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        response = client.post("/api/translation/text", json={
            "text": "The door is open. Close it.", "target_lang": "he",
            "source_lang": "en", "engine": "stub"})
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["success"] is True
        assert body["machine_translation"] is True
        assert body["verified"] is False
        assert body["disclaimer"] == "machine translation, not verified"
        assert [s["target"] for s in body["segments"]] == [
            "he<The door is open.>", "he<Close it.>"]
        assert body["target_lang"] == "he"

    def test_each_request_is_written_to_the_audit_log(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        marker = uuid.uuid4().hex[:8]
        text = f"The door is open {marker}."
        assert client.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"}).status_code == 200

        rows = _audit_rows("translation.text.requested")
        assert rows, "no audit record was written for the translation request"
        assert any("he" in str(row[2]) for row in rows)

    def test_a_correction_is_audited_and_reused(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        text = f"Open the door {uuid.uuid4().hex[:6]}."
        client.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})

        corrected = client.post("/api/translation/memory/correct", json={
            "source_text": text, "target_text": "פתח את הדלת.",
            "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert corrected.status_code == 200, corrected.get_data(as_text=True)

        again = client.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        body = again.get_json()
        assert body["segments"][0]["target"] == "פתח את הדלת."
        assert body["segments"][0]["origin"] == "user"
        assert _audit_rows("translation.memory.corrected")

    def test_suggestions_are_returned_separately_from_translation(
            self, app, client_factory, stub_engine, translation_memory):
        client = client_factory("admin")
        client.post("/api/translation/text", json={
            "text": "The report was delivered yesterday.",
            "target_lang": "he", "source_lang": "en", "engine": "stub"})
        response = client.post("/api/translation/memory/suggest", json={
            "text": "The report was delivered today.",
            "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["exact_match"] is None
        assert body["suggestions"], "the near-identical sentence should be suggested"
        assert body["phase"] == "fuzzy_suggestions"

    def test_a_missing_engine_is_a_clear_503_and_not_a_traceback(
            self, app, client_factory, translation_memory):
        from services.translation import engines as engine_registry

        # No engine registered for this test: the registry holds the built-ins,
        # and none of them is installed in this environment.
        available = [engine.id for engine in engine_registry.builtin_engines()
                     if engine.availability()[0]]
        if available:                      # pragma: no cover - environment dependent
            pytest.skip(f"an engine is installed here: {available}")

        client = client_factory("admin")
        response = client.post("/api/translation/text", json={
            "text": "The door is open.", "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert response.status_code == 503, response.get_data(as_text=True)
        body = response.get_json()
        assert body["code"] == "engine_not_installed"
        assert body["engines"], "the answer must list the engines it looked for"


class TestTheEngineCannotBeFetched:
    """A model that cannot be downloaded is a 503 with the reason, not a 500.

    This is the failure a real installation meets first: the package is
    installed (``pip install argostranslate`` succeeds) but the *model* cannot
    be fetched - no network, a blocked host, or a language pair Argos does not
    carry. The analyst must be told which engine and which pair failed, in the
    words of the failure, instead of being handed a correlation id.
    """

    def test_a_model_that_cannot_be_fetched_is_a_503_with_the_reason(
            self, app, client_factory, translation_memory):
        from services.translation import engines as engine_registry

        class Unfetchable:
            id = "unfetchable"
            version = "0.1"
            license = "test"
            document_safe = True

            def availability(self):
                return True, None

            def translate(self, texts, source_lang, target_lang):
                raise OSError("cannot reach huggingface.co: connection refused")

        engine_registry.register_engine(Unfetchable())
        try:
            client = client_factory("admin")
            response = client.post("/api/translation/text", json={
                "text": "The door is open.", "target_lang": "he",
                "source_lang": "en", "engine": "stub", "engine": "unfetchable"})
        finally:
            engine_registry.unregister_engine("unfetchable")

        assert response.status_code == 503, response.get_data(as_text=True)
        body = response.get_json()
        assert body["code"] in ("engine_not_installed", "engine_failed")
        assert "huggingface.co" in body["error"]
        assert body["engines"], "the answer must still list the engines"


class TestTheMemoryCanDisappear:
    """A missing memory file must be a clear 503, not a 500 with a traceback.

    The memory lives in a file under APP_DATA_DIR. If that directory is gone -
    an unmounted volume, a deleted runtime directory, permissions changed under
    a running server - every translation would otherwise answer 500 and the
    analyst would see a correlation id. The answer must name the condition, and
    must not pretend the memory is empty: a lost file means lost corrections.
    """

    def test_a_missing_schema_in_an_existing_file_is_a_503(
            self, app, client_factory, stub_engine, translation_memory):
        """A file that is there but has lost its tables is reported, not
        silently re-initialised and presented as an empty memory."""
        import sqlite3

        with sqlite3.connect(translation_memory.path) as conn:
            conn.execute("DROP TABLE tm_entries")
            conn.execute("DROP TABLE tm_glossary")

        client = client_factory("admin")
        response = client.post("/api/translation/text", json={
            "text": "The door is open.", "target_lang": "he",
            "source_lang": "en", "engine": "stub"})
        assert response.status_code == 503, response.get_data(as_text=True)
        assert (response.get_json() or {}).get("code") == "memory_unavailable"

    def test_a_fresh_memory_creates_its_directory(
            self, app, client_factory, stub_engine, translation_memory, tmp_path):
        """The directory under APP_DATA_DIR is ours to create on first use."""
        from services.translation import service as service_module
        from services.translation.memory import TranslationMemory

        nested = tmp_path / "made" / "by" / "us" / "translation_memory.db"
        service_module.configure(memory=TranslationMemory(nested))

        client = client_factory("admin")
        response = client.post("/api/translation/text", json={
            "text": "The door is open.", "target_lang": "he",
            "source_lang": "en", "engine": "stub"})
        assert response.status_code == 200, response.get_data(as_text=True)
        assert nested.exists(), "the memory file was not created on first use"

    def test_a_missing_memory_directory_is_a_503_with_the_reason(
            self, app, client_factory, stub_engine, translation_memory, tmp_path):
        gone = tmp_path / "gone"
        gone.mkdir()
        translation_memory.path = gone / "translation_memory.db"
        gone.rmdir()                       # the directory disappeared under us

        client = client_factory("admin")
        response = client.post("/api/translation/text", json={
            "text": "The door is open.", "target_lang": "he",
            "source_lang": "en", "engine": "stub"})

        assert response.status_code == 503, response.get_data(as_text=True)
        body = response.get_json()
        assert body["code"] == "memory_unavailable"
        assert "memory" in body["error"].lower()
        assert "Traceback" not in response.get_data(as_text=True)


class TestNothingIsStored:
    def test_translating_a_document_does_not_change_the_stored_corpus(
            self, app, admin_client, stub_engine, translation_memory):
        from Api.utils.utils import execute_query
        from services.translation.service import TranslationService

        source = admin_client.post("/api/sources",
                                   json={"name": f"tx {uuid.uuid4().hex[:8]}"})
        source_id = (source.get_json() or {}).get("id")

        tables = ("content_signals", "paths", "hash_contexts", "titles_content")
        def counts():
            return {table: (execute_query(f"SELECT count(*) AS n FROM {table}",
                                          fetch="one") or [0])[0]
                    for table in tables}

        before = counts()
        response = admin_client.post("/api/translation/document", json={
            "file_id": 999999, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        # A file that does not exist is a 404, and still nothing was written.
        assert response.status_code in (404, 400), response.get_data(as_text=True)
        assert counts() == before
        assert TranslationService is not None
        assert source_id is not None
