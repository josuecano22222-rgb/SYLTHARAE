"""Engine adapters: permissive by default, NLLB-200 optional and flagged.

Nothing here imports an engine backend at module scope or at registry-build
time. Availability is decided with :func:`importlib.util.find_spec`, which
looks the package up without executing it, so ``/api/translation/capabilities``
answers on a machine where no engine is installed - which is the normal
installation, because the engines are an optional extra.

The adapters
============

=====  ====================  ======================  ==========  =============
id     backend               weights licence          document    default
=====  ====================  ======================  ==========  =============
argos  Argos Translate       MIT                     yes         yes
opus_mt Opus-MT (Marian) via CC-BY-4.0 (Helsinki-NLP  yes         yes
       ``transformers``      model cards)
nllb   NLLB-200 via          CC-BY-NC-4.0            **no**      never
       ``transformers``      (non-commercial)
=====  ====================  ======================  ==========  =============

NLLB-200 is not the default and is not used for documents. Its weights are
CC-BY-NC-4.0 - non-commercial - and the model card excludes some documentation
use cases; an installation that enables it is making a licensing decision, so
it must be asked for explicitly per request (``"engine": "nllb"``) and the
response repeats the licence and the ``document_safe: false`` flag.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "ArgosEngine",
    "Engine",
    "EngineUnavailable",
    "NLLBEngine",
    "OpusMTEngine",
    "builtin_engines",
    "default_engine",
    "describe",
    "describe_available",
    "describe_engine",
    "get_engine",
    "register_engine",
    "unregister_engine",
]


class EngineUnavailable(RuntimeError):
    """No usable engine for this request.

    ``engines`` carries the description of every engine that was considered, so
    the caller can answer "not installed, and here is what was looked for"
    instead of a traceback.
    """

    #: ``engine_not_installed`` - the package is absent or no model is installed;
    #: ``engine_failed`` - the engine is there but could not produce a
    #: translation (a model that cannot be fetched, a backend error).
    def __init__(self, message: str, engines: Optional[List[Dict]] = None,
                 code: str = "engine_not_installed"):
        super().__init__(message)
        self.engines = engines or []
        self.code = code


class Engine:
    """The adapter contract. Subclasses are cheap; models load lazily."""

    id = "engine"
    version = "0"
    license = "unknown"
    document_safe = True
    non_commercial = False
    requires = ("",)

    def availability(self) -> Tuple[bool, Optional[str]]:
        """``(available, reason)`` - must not import the backend."""
        missing = [package for package in self.requires
                   if package and importlib.util.find_spec(package) is None]
        if missing:
            return False, (f"{self.id} is not installed: missing "
                           f"{', '.join(sorted(missing))}")
        return True, None

    def translate(self, texts: List[str], source_lang: str,
                  target_lang: str) -> List[str]:          # pragma: no cover
        raise NotImplementedError

    # -- description -------------------------------------------------------
    def describe(self) -> Dict:
        available, reason = self.availability()
        return {
            "id": self.id,
            "version": self.version,
            "license": self.license,
            "document_safe": self.document_safe,
            "non_commercial": self.non_commercial,
            "available": available,
            "reason": reason,
        }


class ArgosEngine(Engine):
    """Argos Translate (MIT) - the permissive default."""

    id = "argos"
    license = "MIT"
    document_safe = True
    requires = ("argostranslate",)

    @property
    def version(self) -> str:                        # noqa: D401 - lazy
        spec = importlib.util.find_spec("argostranslate")
        if spec is None:
            return "not installed"
        try:
            from importlib.metadata import version as _version

            return f"argos-translate {_version('argostranslate')}"
        except Exception:                                # pragma: no cover
            return "argos-translate"

    def translate(self, texts, source_lang, target_lang):
        import argostranslate.translate as argos        # lazy: only on use

        installed = argos.get_installed_languages()
        source = next((lang for lang in installed if lang.code == source_lang), None)
        target = next((lang for lang in installed if lang.code == target_lang), None)
        if source is None or target is None:
            raise EngineUnavailable(
                f"argos has no installed model for {source_lang}->{target_lang}",
                [self.describe()])
        translation = source.get_translation(target)
        if translation is None:
            raise EngineUnavailable(
                f"argos has no translation path for {source_lang}->{target_lang}",
                [self.describe()])
        return [translation.translate(text) for text in texts]


class OpusMTEngine(Engine):
    """Opus-MT (Helsinki-NLP Marian models, CC-BY-4.0) through transformers."""

    id = "opus_mt"
    license = "CC-BY-4.0"
    document_safe = True
    requires = ("transformers", "torch")

    @property
    def version(self) -> str:
        spec = importlib.util.find_spec("transformers")
        if spec is None:
            return "not installed"
        try:
            from importlib.metadata import version as _version

            return f"transformers {_version('transformers')}"
        except Exception:                                # pragma: no cover
            return "transformers"

    def _model_name(self, source_lang: str, target_lang: str) -> str:
        #: ``SYLTHARAE_OPUS_MT_MODEL`` overrides; otherwise the Helsinki-NLP
        #: naming convention, which is what the model cards use.
        override = os.environ.get("SYLTHARAE_OPUS_MT_MODEL", "").strip()
        if override:
            return override
        return f"Helsinki-NLP/opus-mt-{source_lang}-{target_lang}"

    def translate(self, texts, source_lang, target_lang):
        from transformers import MarianMTModel, MarianTokenizer   # lazy

        name = self._model_name(source_lang, target_lang)
        tokenizer = MarianTokenizer.from_pretrained(name)
        model = MarianMTModel.from_pretrained(name)
        batch = tokenizer(texts, return_tensors="pt", padding=True, truncation=True,
                          max_length=512)
        generated = model.generate(**batch)
        return tokenizer.batch_decode(generated, skip_special_tokens=True)


class NLLBEngine(Engine):
    """NLLB-200 - optional, non-commercial weights, not for documents."""

    id = "nllb"
    license = "CC-BY-NC-4.0"
    document_safe = False
    non_commercial = True
    requires = ("transformers", "torch")

    @property
    def version(self) -> str:
        model = os.environ.get("SYLTHARAE_NLLB_MODEL", "").strip()
        if importlib.util.find_spec("transformers") is None:
            return "not installed"
        try:
            from importlib.metadata import version as _version

            return f"transformers {_version('transformers')}" + (f" ({model})" if model else "")
        except Exception:                                # pragma: no cover
            return "transformers"

    def availability(self):
        available, reason = super().availability()
        if not available:
            return available, reason
        if not os.environ.get("SYLTHARAE_NLLB_MODEL", "").strip():
            return False, ("nllb is optional and not configured: set "
                           "SYLTHARAE_NLLB_MODEL (weights are CC-BY-NC-4.0)")
        return True, None

    def translate(self, texts, source_lang, target_lang):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer   # lazy

        model_name = os.environ["SYLTHARAE_NLLB_MODEL"].strip()
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        tokenizer.src_lang = source_lang
        batch = tokenizer(texts, return_tensors="pt", padding=True, truncation=True,
                          max_length=512)
        generated = model.generate(
            **batch,
            forced_bos_token_id=tokenizer.convert_tokens_to_ids(target_lang))
        return tokenizer.batch_decode(generated, skip_special_tokens=True)


_BUILTIN_FACTORIES = (ArgosEngine, OpusMTEngine, NLLBEngine)

#: Engines registered at runtime (plugins, and test stand-ins).
_REGISTERED: Dict[str, Engine] = {}


def register_engine(engine: Engine) -> None:
    """Make *engine* selectable by its ``id`` (used by tests and plugins)."""
    _REGISTERED[engine.id] = engine


def unregister_engine(engine_id: str) -> None:
    _REGISTERED.pop(engine_id, None)


def builtin_engines() -> List[Engine]:
    return [factory() for factory in _BUILTIN_FACTORIES] + list(_REGISTERED.values())


def get_engine(engine_id: str) -> Engine:
    for engine in builtin_engines():
        if engine.id == engine_id:
            return engine
    raise EngineUnavailable(f"unknown translation engine: {engine_id}",
                            [engine.describe() for engine in builtin_engines()])


def describe_engine(engine) -> Dict:
    """Describe any object satisfying the adapter contract.

    ``Engine.describe()`` is the normal path; a stand-in (a test double, a
    plugin written against the three methods it actually needs) is described
    from its attributes so the response is shaped the same way either way.
    """
    describe_method = getattr(engine, "describe", None)
    if callable(describe_method):
        return describe_method()
    availability = getattr(engine, "availability", None)
    available, reason = (True, None)
    if callable(availability):
        available, reason = availability()
    return {
        "id": getattr(engine, "id", "engine"),
        "version": getattr(engine, "version", "0"),
        "license": getattr(engine, "license", "unknown"),
        "document_safe": bool(getattr(engine, "document_safe", True)),
        "non_commercial": bool(getattr(engine, "non_commercial", False)),
        "available": available,
        "reason": reason,
    }


def describe(engine_id: str) -> Dict:
    return describe_engine(get_engine(engine_id))


def describe_available() -> Dict:
    engines = [engine.describe() for engine in builtin_engines()]
    return {
        "engines": engines,
        "available": [engine["id"] for engine in engines if engine["available"]],
        "default": default_engine_id(),
    }


def default_engine_id() -> Optional[str]:
    """The engine a request gets when it names none: the first available
    permissive one, in declaration order. ``None`` when nothing is installed."""
    for engine in builtin_engines():
        if engine.document_safe and engine.availability()[0]:
            return engine.id
    return None


def default_engine() -> Engine:
    engine_id = default_engine_id()
    if engine_id is None:
        engines = [engine.describe() for engine in builtin_engines()]
        raise EngineUnavailable(
            "no translation engine is usable here: every engine is not "
            "installed or not configured. Install argos-translate or "
            "transformers+torch, or set SYLTHARAE_NLLB_MODEL for the optional "
            "non-commercial NLLB-200 engine.", engines)
    return get_engine(engine_id)
