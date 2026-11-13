"""The service: memory first, engine second, glossary on top, nothing stored.

These tests use a stub engine, so they run on a machine with no translation
engine installed - which is the default installation.
"""

import pytest

from services.translation.memory import TranslationMemory
from services.translation.service import TranslationService


class StubEngine:
    id = "stub"
    version = "stub-1.0"
    license = "test"
    document_safe = True

    def __init__(self):
        self.calls = []

    def availability(self):
        return True, None

    def translate(self, texts, source_lang, target_lang):
        self.calls.append((list(texts), source_lang, target_lang))
        return [f"{target_lang}<{text}>" for text in texts]


@pytest.fixture()
def service(tmp_path):
    engine = StubEngine()
    return TranslationService(memory=TranslationMemory(tmp_path / "tm.db"),
                              engine=engine), engine


def test_translating_a_selection_returns_segments_with_their_origin(service):
    translator, engine = service
    result = translator.translate_text("The door is open. Close it.",
                                       target_lang="he", source_lang="en")
    assert result["source_lang"] == "en"
    assert result["target_lang"] == "he"
    assert [segment["source"] for segment in result["segments"]] == [
        "The door is open.", "Close it."]
    assert all(segment["origin"] == "engine" for segment in result["segments"])
    assert all(segment["engine"] == "stub" for segment in result["segments"])
    assert all(segment["engine_version"] == "stub-1.0"
               for segment in result["segments"])
    assert result["disclaimer"] == "machine translation, not verified"
    assert result["machine_translation"] is True
    assert result["verified"] is False
    assert engine.calls, "the stub engine should have been asked"


def test_a_second_identical_request_is_served_from_memory_not_the_engine(service):
    translator, engine = service
    first = translator.translate_text("The door is open.", target_lang="he",
                                      source_lang="en")
    calls_after_first = len(engine.calls)
    second = translator.translate_text("The door is open.", target_lang="he",
                                       source_lang="en")
    assert second["segments"][0]["target"] == first["segments"][0]["target"]
    assert second["segments"][0]["origin"] == "memory"
    assert second["engine_calls"] == 0
    assert len(engine.calls) == calls_after_first


def test_a_corrected_segment_is_served_back_and_marked_as_the_analysts(service):
    translator, _engine = service
    translator.translate_text("The door is open.", target_lang="he", source_lang="en")
    translator.correct(source_text="The door is open.", target_text="הדלת פתוחה.",
                       source_lang="en", target_lang="he", actor="analyst1")
    again = translator.translate_text("The door is open.", target_lang="he",
                                      source_lang="en")
    segment = again["segments"][0]
    assert segment["target"] == "הדלת פתוחה."
    assert segment["origin"] == "user"
    assert again["engine_calls"] == 0


def test_the_glossary_is_applied_to_the_translation_and_the_source_is_untouched(service):
    translator, _engine = service
    translator.set_glossary_term(source_lang="en", target_lang="hr",
                                 term="The door", translation="Vrata", actor="analyst1")
    result = translator.translate_text("The door is open.", target_lang="hr",
                                       source_lang="en")
    segment = result["segments"][0]
    assert "Vrata" in segment["target"]
    assert segment["source"] == "The door is open."      # never rewritten
    assert segment["glossary"] == ["The door"]
    assert result["glossary_terms"] == 1


def test_language_detection_can_be_overridden_by_the_caller(service):
    translator, _engine = service
    result = translator.translate_text("Dobar dan.", target_lang="en",
                                       source_lang="hr")
    assert result["source_lang"] == "hr"
    assert result["source_language_override"] is True
    assert result["detected_language"] == "hr"

    detected = translator.translate_text("Dobar dan.", target_lang="en")
    assert detected["source_language_override"] is False


def test_a_translation_is_not_written_anywhere_but_the_memory(service, tmp_path):
    translator, _engine = service
    result = translator.translate_text("The door is open.", target_lang="he",
                                       source_lang="en")
    assert result["stored_elsewhere"] is False
    files = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert files == {"tm.db"}, f"the service wrote unexpected files: {sorted(files)}"


def test_capabilities_list_the_engines_and_the_limits(service):
    translator, _engine = service
    caps = translator.capabilities()
    assert caps["languages"] == ["en", "ar", "he", "fa", "hr"]
    assert caps["max_tokens"] == 512
    assert caps["max_chars"] >= 1000
    assert any(engine["id"] == "stub" for engine in caps["engines"])
    assert caps["engines"][0]["license"]


def test_the_engine_being_absent_is_a_clear_answer_not_a_crash(tmp_path):
    from services.translation.engines import EngineUnavailable

    translator = TranslationService(memory=TranslationMemory(tmp_path / "tm.db"),
                                    engine=None)
    with pytest.raises(EngineUnavailable) as caught:
        translator.translate_text("The door is open.", target_lang="he",
                                  source_lang="en")
    assert "not installed" in str(caught.value).lower()

    # ... but a memory hit needs no engine and must still be served.
    translator.memory.remember(source_lang="en", target_lang="he",
                               source_text="The door is open.",
                               target_text="הדלת פתוחה.", origin="user",
                               engine="user", engine_version="user",
                               actor="analyst1")
    result = translator.translate_text("The door is open.", target_lang="he",
                                       source_lang="en")
    assert result["segments"][0]["target"] == "הדלת פתוחה."
    assert result["engine_calls"] == 0
