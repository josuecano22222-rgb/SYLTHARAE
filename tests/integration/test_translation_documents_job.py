"""Several documents are a job, not a long request.

Translating a batch is the one operation here that can take minutes, so it goes
through the project's existing job manager: the request returns a ``job_id``
immediately, the work happens in the queue, and each document's translated
segments land in the translation memory - never in ``paths``/``contents``.

The documents are seeded with ``tests/integration/_seed.py`` (there is no
ingest-a-text API in this installation), and the job manager is put in
synchronous mode so create-run-finish happens in one call, without a thread
race.
"""

import uuid

import pytest

from tests.integration import _seed


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


@pytest.fixture()
def synchronous_jobs():
    from services.jobs.manager import JobManager

    manager = JobManager.get_instance()
    previous = manager.synchronous
    manager.synchronous = True
    try:
        yield manager
    finally:
        manager.synchronous = previous


@pytest.fixture()
def documents(pg_db):
    """Two stored documents, with their text, through the seed helper."""
    conn = _seed.connect(pg_db)
    ledger = _seed.SeedLedger()
    marker = uuid.uuid4().hex[:8]
    try:
        with conn, conn.cursor() as cur:
            ledger.sources.append(_seed.source(cur, f"tx_{marker}"))
            ledger.sides.append(_seed.side(cur, f"tx_{marker}"))
            ids = []
            for index in range(2):
                path_id, hash_id, context_id = _seed.document(
                    cur, source_id=ledger.sources[-1], side_id=ledger.sides[-1],
                    text=f"Document {index} {marker}. The door is open.",
                    file_name=f"txjob_{marker}_{index}.txt")
                ids.append(path_id)
                ledger.paths.append(path_id)
                ledger.hashes.append(hash_id)
                ledger.contexts.append(context_id)
    finally:
        conn.close()
    try:
        yield ids
    finally:
        _seed.remove_seeded(pg_db, ledger)


def test_a_document_that_does_not_exist_is_a_404(
        app, admin_client, stub_engine, translation_memory):
    response = admin_client.post("/api/translation/document", json={
        "file_id": 987654, "target_lang": "he", "source_lang": "en", "engine": "stub"})
    assert response.status_code == 404, response.get_data(as_text=True)


def test_one_document_is_translated_and_nothing_is_written_back(
        app, admin_client, stub_engine, translation_memory, documents):
    from Api.utils.utils import execute_query

    path_id = documents[0]
    before = (execute_query("SELECT count(*) FROM paths", fetch="one") or [0])[0]

    response = admin_client.post("/api/translation/document", json={
        "file_id": path_id, "target_lang": "he", "source_lang": "en", "engine": "stub"})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["file_id"] == path_id
    assert body["segments"], "a stored document should produce segments"
    assert body["stored_elsewhere"] is False
    assert body["disclaimer"] == "machine translation, not verified"

    after = (execute_query("SELECT count(*) FROM paths", fetch="one") or [0])[0]
    assert after == before, "translating must not create path rows"
    stored = translation_memory.lookup(source_lang="en", target_lang="he",
                                       source_text="The door is open.")
    assert stored is not None, "the document's segments did not reach the memory"


def test_several_documents_become_one_job(
        app, admin_client, stub_engine, translation_memory, synchronous_jobs,
        documents):
    from Api.utils.utils import execute_query

    before = (execute_query("SELECT count(*) FROM paths", fetch="one") or [0])[0]
    response = admin_client.post("/api/translation/documents", json={
        "file_ids": documents, "target_lang": "he", "source_lang": "en", "engine": "stub"})
    assert response.status_code == 202, response.get_data(as_text=True)
    body = response.get_json()
    assert body["job_id"]
    assert body["file_count"] == len(documents)

    job = synchronous_jobs.repo.get(body["job_id"])
    assert job["job_type"] == "translation"
    assert job["status"] != "FAILED", job.get("errors")
    assert job["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")

    after = (execute_query("SELECT count(*) FROM paths", fetch="one") or [0])[0]
    assert after == before, "a translation job must not create path rows"

    stored = translation_memory.lookup(source_lang="en", target_lang="he",
                                       source_text="The door is open.")
    assert stored is not None, "the job's translations were not stored in the memory"
