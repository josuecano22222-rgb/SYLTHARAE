"""The memory API answers 503 when the memory cannot be written.

The route already had that answer for translating text and documents
(``except MemoryUnavailable -> 503 memory_unavailable``), but the three memory
routes - correct, suggest, glossary term - did not, so a full disk reached the
analyst as "An internal error occurred" (HTTP 500, round-10 disk-full drill,
correlation id ERR-20261024-000001).

A read-only memory file stands in for the full volume: same failure class, no
root needed, deterministic.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


@pytest.fixture()
def broken_memory(tmp_path, monkeypatch):
    """A configured memory whose file cannot be written."""
    from services.translation import service as service_module
    from services.translation.memory import TranslationMemory

    memory = TranslationMemory(path=tmp_path / "translation" / "tm.db")
    memory.remember(source_lang="en", target_lang="ar", source_text="harbour",
                    target_text="مرفأ", origin="engine", engine="stub",
                    engine_version="1")
    monkeypatch.setattr(service_module, "_service",
                        service_module.TranslationService(memory=memory))
    os.chmod(memory.path, 0o444)
    os.chmod(memory.path.parent, 0o555)
    try:
        yield memory
    finally:
        os.chmod(memory.path.parent, 0o755)
        os.chmod(memory.path, 0o644)


def _assert_unavailable(response):
    """503 in *this* module's envelope.

    The translation routes still answer with the older flat shape
    ``{"success": false, "code": ..., "error": "<message>"}`` while the rest of
    the API uses ``{"error": {"code", "message"}}``; unifying them is a client
    contract change, so it is reported rather than done here.
    """
    body = response.get_json()
    assert response.status_code == 503, response.get_data(as_text=True)
    assert body["success"] is False
    assert body["code"] == "memory_unavailable"
    assert "translation memory" in body["error"]
    assert "correlation_id" not in body


def test_correct_reports_an_unwritable_memory_as_503(app, admin_client, broken_memory):
    _assert_unavailable(admin_client.post("/api/translation/memory/correct", json={
        "source_text": "harbour", "target_text": "مَرْفَأ",
        "source_lang": "en", "target_lang": "ar"}))


def test_suggest_reports_an_unwritable_memory_as_503(app, admin_client, broken_memory):
    _assert_unavailable(admin_client.post("/api/translation/memory/suggest", json={
        "text": "harbour", "source_lang": "en", "target_lang": "ar"}))


def test_glossary_term_reports_an_unwritable_memory_as_503(app, admin_client, broken_memory):
    _assert_unavailable(admin_client.post("/api/translation/glossary/term", json={
        "term": "harbour", "translation": "مرفأ",
        "source_lang": "en", "target_lang": "ar"}))
