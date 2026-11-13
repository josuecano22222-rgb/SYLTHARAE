"""Who may correct, who may shape the glossary, and what a correction means.

Three rules, each of which the feature would be worthless without:

* **authorisation** - a correction changes what every later translation of that
  sentence says, and a glossary term changes every translation that contains
  the term. A viewer is a reader; the write policy of this application is
  analyst-or-admin, and this test holds the translation endpoints to it rather
  than to ``login_required`` alone.
* **one memory, many analysts** - two people translating the same document are
  supposed to get the same answer, and a correction made by one is the answer
  the other gets next. The memory is a file under ``APP_DATA_DIR``, not a
  per-request object, and this is the test that says so.
* **a correction outranks the engine, and the row remembers which engine** -
  after a correction the next request makes no engine call at all, and every
  row carries the engine id and version that produced it (``user`` for a
  correction), because "which model said this" is part of the provenance.

The engines are stubs: this file is about the rules, not about a model.
"""

from __future__ import annotations

import pathlib
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
def translation_memory(tmp_path):
    from services.translation import service as service_module
    from services.translation.memory import TranslationMemory

    memory = TranslationMemory(tmp_path / "translation_memory.db")
    service_module.configure(memory=memory)
    try:
        yield memory
    finally:
        service_module.reset()


def _sentence():
    return f"Open the northern gate {uuid.uuid4().hex[:8]}."


class TestAuthorisation:
    """A viewer reads; the write policy is analyst or admin."""

    def test_a_viewer_cannot_correct_a_translation(
            self, app, client_factory, stub_engine, translation_memory):
        viewer = client_factory("viewer")
        response = viewer.post("/api/translation/memory/correct", json={
            "source_text": _sentence(), "target_text": "פתח את השער.",
            "source_lang": "en", "target_lang": "he"})
        assert response.status_code == 403, response.get_data(as_text=True)

    def test_a_viewer_cannot_add_a_glossary_term(
            self, app, client_factory, stub_engine, translation_memory):
        viewer = client_factory("viewer")
        response = viewer.post("/api/translation/glossary/term", json={
            "term": f"gate {uuid.uuid4().hex[:6]}", "translation": "שער",
            "source_lang": "en", "target_lang": "he"})
        assert response.status_code == 403, response.get_data(as_text=True)

    def test_a_viewer_cannot_translate_either(
            self, app, client_factory, stub_engine, translation_memory):
        """A consequence worth pinning: translation is a POST, so the write
        policy applies to reading the corpus through it as well. A viewer is
        refused the engine, not just the correction."""
        viewer = client_factory("viewer")
        response = viewer.post("/api/translation/text", json={
            "text": "The gate is open.", "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert response.status_code == 403, response.get_data(as_text=True)

    def test_an_analyst_may_correct_and_set_a_term(
            self, app, client_factory, stub_engine, translation_memory):
        analyst = client_factory("analyst")

        corrected = analyst.post("/api/translation/memory/correct", json={
            "source_text": _sentence(), "target_text": "פתח את השער.",
            "source_lang": "en", "target_lang": "he"})
        assert corrected.status_code == 200, corrected.get_data(as_text=True)

        term = analyst.post("/api/translation/glossary/term", json={
            "term": f"gate {uuid.uuid4().hex[:6]}", "translation": "שער",
            "source_lang": "en", "target_lang": "he"})
        assert term.status_code == 200, term.get_data(as_text=True)

    def test_an_admin_may_correct(
            self, app, client_factory, stub_engine, translation_memory):
        admin = client_factory("admin")
        response = admin.post("/api/translation/memory/correct", json={
            "source_text": _sentence(), "target_text": "פתח את השער.",
            "source_lang": "en", "target_lang": "he"})
        assert response.status_code == 200, response.get_data(as_text=True)


class TestSharedMemory:
    def test_two_users_get_the_same_answer_from_one_memory(
            self, app, client_factory, stub_engine, translation_memory):
        text = _sentence()
        admin = client_factory("admin")
        analyst = client_factory("analyst")

        first = admin.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert first.status_code == 200, first.get_data(as_text=True)
        assert first.get_json()["engine_calls"] == 1

        second = analyst.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert second.status_code == 200, second.get_data(as_text=True)
        body = second.get_json()
        # Nothing was asked of the engine: the sentence was already translated.
        assert body["engine_calls"] == 0
        assert body["segments"][0]["target"] == first.get_json()["segments"][0]["target"]

    def test_a_correction_by_one_user_is_the_answer_for_the_other(
            self, app, client_factory, stub_engine, translation_memory):
        text = _sentence()
        admin = client_factory("admin")
        analyst = client_factory("analyst")

        admin.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        corrected = admin.post("/api/translation/memory/correct", json={
            "source_text": text, "target_text": "השער הצפוני פתוח.",
            "source_lang": "en", "target_lang": "he"})
        assert corrected.status_code == 200, corrected.get_data(as_text=True)

        after = analyst.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        body = after.get_json()
        assert body["segments"][0]["target"] == "השער הצפוני פתוח."
        assert body["segments"][0]["origin"] == "user"


class TestTheMemoryIsAFile:
    """Not a table: the memory lives in SQLite under APP_DATA_DIR."""

    def test_the_default_memory_is_a_file_under_the_data_root(self):
        from core.app_paths import get_data_root
        from services.translation.memory import default_memory_path

        path = default_memory_path()
        root = pathlib.Path(get_data_root()).resolve()
        assert path.name == "translation_memory.db"
        assert root in path.resolve().parents, f"{path} is outside {root}"
        assert str(path).endswith(".db")

    def test_two_memory_objects_on_the_default_path_see_each_other(self):
        """Two processes (or two service instances) share one memory file."""
        from services.translation.memory import TranslationMemory

        sentence = _sentence()
        first = TranslationMemory()
        second = TranslationMemory()
        first.correct(source_lang="en", target_lang="he",
                      source_text=sentence, target_text="השער פתוח.",
                      actor="test_first")

        hit = second.lookup(source_lang="en", target_lang="he", source_text=sentence)
        assert hit is not None, "the second object cannot see the first one's row"
        assert hit["target_text"] == "השער פתוח."
        assert hit["origin"] == "user"

    def test_nothing_was_created_in_postgresql(self, pg_db):
        """The document memory is not a table.

        ``translation_overrides`` is the *interface* catalogue's table (msgid ->
        wording, a pre-existing part of the frozen baseline). Document
        translation memory is a different thing and must not appear in the
        schema: it is not derivable, so it lives in a file, and the schema is
        frozen.
        """
        from tests.integration import _seed

        interface_tables = {"translation_overrides"}
        conn = _seed.connect(pg_db)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND ("
                    " table_name ILIKE '%translat%' OR table_name ILIKE 'tm_%')"
                    " ORDER BY table_name")
                found = [row[0] for row in cur.fetchall()]
        finally:
            conn.close()
        unexpected = [name for name in found if name not in interface_tables]
        assert unexpected == [], (
            f"document translation state was put in PostgreSQL: {unexpected}")


class TestCorrectionBeatsTheEngine:
    def test_the_next_request_makes_no_engine_call(
            self, app, client_factory, stub_engine, translation_memory):
        text = _sentence()
        admin = client_factory("admin")

        admin.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        admin.post("/api/translation/memory/correct", json={
            "source_text": text, "target_text": "השער פתוח.",
            "source_lang": "en", "target_lang": "he"})

        again = admin.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        body = again.get_json()
        assert body["engine_calls"] == 0, "the engine was called despite a correction"
        assert body["segments"][0]["target"] == "השער פתוח."

    def test_an_engine_write_never_overwrites_a_correction(
            self, app, client_factory, stub_engine, translation_memory):
        """Asking for the sentence again after a correction must not undo it."""
        text = _sentence()
        admin = client_factory("admin")

        admin.post("/api/translation/memory/correct", json={
            "source_text": text, "target_text": "השער פתוח.",
            "source_lang": "en", "target_lang": "he"})
        for _ in range(2):
            admin.post("/api/translation/text", json={
                "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})

        rows = translation_memory.lookup(source_lang="en", target_lang="he",
                                         source_text=text)
        assert rows["origin"] == "user"
        assert rows["target_text"] == "השער פתוח."


class TestProvenance:
    def _rows(self, memory):
        import sqlite3

        with sqlite3.connect(memory.path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM tm_entries").fetchall()

    def test_every_row_records_the_engine_and_its_version(
            self, app, client_factory, stub_engine, translation_memory):
        admin = client_factory("admin")
        text = "The bridge is closed. The road is open."
        response = admin.post("/api/translation/text", json={
            "text": text, "target_lang": "he", "source_lang": "en", "engine": "stub"})
        assert response.status_code == 200, response.get_data(as_text=True)

        rows = self._rows(translation_memory)
        assert len(rows) == 2, "one row per sentence"
        for row in rows:
            assert row["origin"] == "engine"
            assert row["engine"] == "stub"
            assert row["engine_version"] == "stub-1.0"
            assert row["target_text"].startswith("he<")
            # ``actor`` names the person who *vouched* for the text, so it is
            # empty on an engine row by design; who asked for the translation is
            # in audit_log (translation.text.requested), not here.
            assert row["actor"] is None

    def test_a_correction_records_the_person_not_the_engine(
            self, app, client_factory, stub_engine, translation_memory):
        text = _sentence()
        analyst = client_factory("analyst")
        analyst.post("/api/translation/memory/correct", json={
            "source_text": text, "target_text": "השער פתוח.",
            "source_lang": "en", "target_lang": "he"})

        rows = self._rows(translation_memory)
        assert len(rows) == 1
        row = rows[0]
        assert row["origin"] == "user"
        assert row["actor"] == "testrole_analyst"
        assert row["target_text"] == "השער פתוח."

    def test_the_glossary_records_who_set_the_term(
            self, app, client_factory, stub_engine, translation_memory):
        term = f"checkpoint {uuid.uuid4().hex[:6]}"
        admin = client_factory("admin")
        response = admin.post("/api/translation/glossary/term", json={
            "term": term, "translation": "מחסום",
            "source_lang": "en", "target_lang": "he"})
        assert response.status_code == 200, response.get_data(as_text=True)

        import sqlite3

        with sqlite3.connect(translation_memory.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tm_glossary WHERE term = ?",
                               (term,)).fetchone()
        assert row is not None
        assert row["actor"] == "testrole_admin"
