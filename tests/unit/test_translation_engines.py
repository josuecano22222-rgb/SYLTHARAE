"""Engine adapters: permissive by default, NLLB-200 optional and flagged.

The engine is a swappable adapter, never an import at the top of the feature:

* the default engine is a permissive one - Argos Translate (MIT) or an Opus-MT
  (CC-BY-4.0) Marian model through ``transformers``;
* NLLB-200 is available only if the analyst asks for it, and it is flagged:
  its weights are CC-BY-NC-4.0 (non-commercial) and the model card excludes
  some documentation use cases, so it is not the default and the response says
  so;
* no engine is installed by default, so "not installed" has to be a clear,
  non-fatal answer rather than an ImportError traceback, and the model must
  load lazily - constructing the registry must not import torch.
"""

import sys

from services.translation import engines as engine_registry


def test_the_adapter_list_is_permissive_by_default():
    listed = {engine.id: engine for engine in engine_registry.builtin_engines()}
    assert {"argos", "opus_mt", "nllb"} <= set(listed)

    permissive = [engine.id for engine in listed.values() if engine.document_safe]
    assert "argos" in permissive
    assert "opus_mt" in permissive
    assert "nllb" not in permissive, "NLLB-200 must not be the document default"
    assert listed["nllb"].license == "CC-BY-NC-4.0"
    assert listed["nllb"].document_safe is False


def test_building_the_registry_does_not_import_an_engine_backend():
    before = set(sys.modules)
    engine_registry.builtin_engines()
    engine_registry.describe_available()
    imported = {name.split(".")[0] for name in set(sys.modules) - before}
    assert imported.isdisjoint({"torch", "transformers", "argos", "ctranslate2"})


def test_an_engine_that_is_not_installed_answers_why_instead_of_crashing():
    listed = {engine.id: engine for engine in engine_registry.builtin_engines()}
    available, reason = listed["argos"].availability()
    if available:
        # An installation that does ship Argos must still answer the contract.
        assert reason is None
    else:
        assert reason and "not installed" in reason.lower()


def test_the_default_engine_is_the_first_available_permissive_one(monkeypatch):
    from services.translation import engines as registry

    monkeypatch.setattr(registry.ArgosEngine, "availability", lambda self: (False, "not installed"))
    monkeypatch.setattr(registry.OpusMTEngine, "availability", lambda self: (True, None))
    chosen = registry.default_engine()
    assert chosen.id == "opus_mt"

    monkeypatch.setattr(registry.OpusMTEngine, "availability", lambda self: (False, "not installed"))
    try:
        registry.default_engine()
    except registry.EngineUnavailable as exc:
        assert "not installed" in str(exc).lower()
        assert exc.engines, "the error must say which engines were looked for"
    else:                                     # pragma: no cover - defensive
        raise AssertionError("default_engine() must raise when nothing is installed")


def test_nllb_is_only_used_when_named_and_says_what_its_licence_is():
    from services.translation.engines import describe

    summary = describe("nllb")
    assert summary["id"] == "nllb"
    assert summary["license"] == "CC-BY-NC-4.0"
    assert summary["document_safe"] is False
    assert summary["non_commercial"] is True


def test_a_registered_engine_can_stand_in_for_tests():
    class Echo:
        id = "echo"
        version = "echo-1"
        license = "test"
        document_safe = True

        def availability(self):
            return True, None

        def translate(self, texts, source_lang, target_lang):
            return [f"{target_lang}:{text}" for text in texts]

    engine_registry.register_engine(Echo())
    try:
        assert engine_registry.get_engine("echo").translate(
            ["hi"], "en", "he") == ["he:hi"]
        assert any(engine.id == "echo" for engine in engine_registry.builtin_engines())
    finally:
        engine_registry.unregister_engine("echo")
