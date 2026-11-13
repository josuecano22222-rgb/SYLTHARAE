"""The manual translation service: memory first, engine second, nothing stored.

The order of operations for one request
=======================================

1. the text is normalised and split into engine-sized segments
   (:mod:`services.translation.text`);
2. the source language is the analyst's declaration when there is one, and the
   detected language otherwise (:mod:`services.translation.language`);
3. each segment is looked up in the translation memory - an exact hit costs no
   engine call;
4. the misses go to the engine **in one call** (engines translate a batch, and
   one call per sentence would be both slower and more expensive);
5. every fresh translation is written to the memory as ``origin = 'engine'``
   with the engine id and version;
6. the glossary is applied to the translations, and the terms that were applied
   are reported per segment.

Nothing here writes into the stored corpus - the tables that hold paths,
contents, signals or the search index. A translation is a reading of stored
material, delivered to the client that asked for it and remembered (if at all)
in the memory file.

This module is only ever called by ``Api/routes/translation_api.py`` - a human
clicking translate. It is deliberately not imported from ingestion, indexing or
detection, and ``tests/unit/test_translation_no_ingest_coupling.py`` fails if
that ever changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import engines as engine_registry
from .language import TRANSLATION_LANGUAGES, resolve_languages
from .memory import MemoryUnavailable, TranslationMemory
from .text import MAX_CHARS, MAX_REQUEST_CHARS, MAX_TOKENS, estimate_tokens, split_segments

logger = logging.getLogger(__name__)

__all__ = [
    "DISCLAIMER",
    "DocumentNotFound",
    "MemoryUnavailable",
    "MAX_DOCUMENTS_PER_JOB",
    "TranslationJobResult",
    "TranslationService",
    "configure",
    "get_service",
    "reset",
]

#: What every response says, in the words the interface shows.
DISCLAIMER = "machine translation, not verified"

#: Documents per batch job.
MAX_DOCUMENTS_PER_JOB = 50


class DocumentNotFound(LookupError):
    """The path id the request named is not stored here."""


@dataclass
class TranslationJobResult:
    """What a batch job returns to the job manager."""

    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cancelled: bool = False
    paused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"result_summary": dict(self.stats)}


class TranslationService:
    def __init__(self, memory: Optional[TranslationMemory] = None,
                 engine=None):
        self.memory = memory if memory is not None else TranslationMemory()
        self._engine = engine

    # -- engine resolution -------------------------------------------------
    def _resolve_engine(self, engine_id: Optional[str]):
        if self._engine is not None:
            return self._engine
        if engine_id:
            engine = engine_registry.get_engine(engine_id)
            available, reason = engine.availability()
            if not available:
                raise engine_registry.EngineUnavailable(
                    reason or f"{engine_id} is not installed",
                    [engine_registry.describe_engine(entry)
                     for entry in engine_registry.builtin_engines()])
            return engine
        return engine_registry.default_engine()

    # -- capabilities ------------------------------------------------------
    def capabilities(self) -> Dict[str, Any]:
        described = engine_registry.describe_available()
        if self._engine is not None:
            injected = engine_registry.describe_engine(self._engine)
            described = {
                "engines": [injected] + [engine for engine in described["engines"]
                                         if engine["id"] != injected["id"]],
                "available": described["available"] + (
                    [] if injected["id"] in described["available"] else [injected["id"]]),
                "default": described["default"] or injected["id"],
            }
        return {
            "languages": list(TRANSLATION_LANGUAGES),
            "max_tokens": MAX_TOKENS,
            "max_chars": MAX_CHARS,
            "max_request_chars": MAX_REQUEST_CHARS,
            "max_documents_per_job": MAX_DOCUMENTS_PER_JOB,
            "engines": described["engines"],
            "default_engine": described["default"],
            "disclaimer": DISCLAIMER,
            "memory": self.memory.stats(),
        }

    # -- text --------------------------------------------------------------
    def translate_text(self, text: str, *, target_lang: str,
                       source_lang: Optional[str] = None,
                       engine: Optional[str] = None,
                       actor: Optional[str] = None) -> Dict[str, Any]:
        source, detected, overridden = resolve_languages(text, target_lang, source_lang)
        segments = split_segments(text)
        if not segments:
            raise ValueError("text must contain something to translate")

        glossary = self.memory.glossary(source_lang=source, target_lang=target_lang)
        results: List[Dict[str, Any]] = []
        pending: List[int] = []
        for index, segment in enumerate(segments):
            hit = self.memory.lookup(source_lang=source, target_lang=target_lang,
                                     source_text=segment)
            if hit is None:
                results.append({"source": segment, "target": None,
                                "origin": None, "engine": None,
                                "engine_version": None, "from_memory": False})
                pending.append(index)
                continue
            results.append({
                "source": segment,
                "target": hit["target_text"],
                "origin": "user" if hit["origin"] == "user" else "memory",
                "engine": hit["engine"],
                "engine_version": hit["engine_version"],
                "from_memory": True,
            })

        engine_calls = 0
        used_engine = None
        if pending:
            used_engine = self._resolve_engine(engine)
            available, reason = used_engine.availability()
            if not available:
                raise engine_registry.EngineUnavailable(
                    reason or "no translation engine is installed",
                    [engine_registry.describe_engine(entry)
                     for entry in engine_registry.builtin_engines()])
            engine_calls = 1
            try:
                translated = used_engine.translate(
                    [results[index]["source"] for index in pending], source, target_lang)
            except engine_registry.EngineUnavailable:
                raise
            except Exception as exc:
                # A model that cannot be fetched, a backend that is not there, a
                # pair the engine does not carry: the analyst is told which
                # engine failed and in whose words, not handed a correlation id.
                logger.exception("translation engine %s failed for %s->%s",
                                 used_engine.id, source, target_lang)
                raise engine_registry.EngineUnavailable(
                    f"{used_engine.id} could not translate {source}->{target_lang}: {exc}",
                    [engine_registry.describe_engine(used_engine)],
                    code="engine_failed") from exc
            for index, output in zip(pending, translated):
                results[index]["target"] = output
                results[index]["origin"] = "engine"
                results[index]["engine"] = used_engine.id
                results[index]["engine_version"] = used_engine.version
                self.memory.remember(
                    source_lang=source, target_lang=target_lang,
                    source_text=results[index]["source"], target_text=output,
                    origin="engine", engine=used_engine.id,
                    engine_version=used_engine.version, actor=actor)

        applied_terms = set()
        for entry in results:
            # The glossary is written in the *source* language and applied to
            # the translation: "The door" -> "Vrata" only means anything once
            # the engine has produced text to put it in.
            terms = self.memory.apply_glossary_terms(entry["source"], glossary)
            if terms:
                entry["target"] = self.memory.apply_glossary(entry["target"], glossary)
            entry["glossary"] = terms
            entry["tokens"] = estimate_tokens(entry["source"])
            applied_terms.update(terms)

        selected_engine = None
        if used_engine is not None:
            selected_engine = engine_registry.describe_engine(used_engine)
        else:
            engine_hits = [entry for entry in results
                           if entry["origin"] in ("memory", "user")]
            selected_engine = {
                "id": engine_hits[0]["engine"] if engine_hits else None,
                "version": engine_hits[0]["engine_version"] if engine_hits else None,
                "available": True, "served_from_memory": True,
            }

        return {
            "success": True,
            "source_lang": source,
            "target_lang": target_lang,
            "detected_language": detected,
            "source_language_override": overridden,
            "engine": selected_engine,
            "engine_calls": engine_calls,
            "segments": results,
            "segment_count": len(results),
            "glossary_terms": len(applied_terms),
            "machine_translation": True,
            "verified": False,
            "disclaimer": DISCLAIMER,
            "stored_elsewhere": False,
        }

    # -- documents ---------------------------------------------------------
    def document_text(self, file_id: int) -> Dict[str, Any]:
        """The stored text of one path, or :class:`DocumentNotFound`."""
        from Api.utils.utils import execute_query

        rows = execute_query(
            "SELECT id, file_name, file_path FROM paths WHERE id = %s",
            (file_id,), fetch="all") or []
        if not rows:
            raise DocumentNotFound(f"no stored file with id {file_id}")
        row = rows[0]
        name = row[1] if not isinstance(row, dict) else row.get("file_name")
        from database import get_file_content

        text = get_file_content(file_id) or ""
        if not text.strip():
            name = name or f"file {file_id}"
        return {"file_id": int(file_id), "file_name": name, "text": text}

    def translate_document(self, file_id: int, *, target_lang: str,
                           source_lang: Optional[str] = None,
                           engine: Optional[str] = None,
                           actor: Optional[str] = None) -> Dict[str, Any]:
        document = self.document_text(file_id)
        text = document["text"]
        if len(text) > MAX_REQUEST_CHARS:
            # A document is translated in full, in segments; the request
            # ceiling applies to a *selection*, not to a stored file.
            text = text[:MAX_REQUEST_CHARS]
            truncated = True
        else:
            truncated = False
        result = self.translate_text(text, target_lang=target_lang,
                                     source_lang=source_lang, engine=engine,
                                     actor=actor)
        result.update({
            "file_id": document["file_id"],
            "file_name": document["file_name"],
            "truncated": truncated,
            "document_chars": len(document["text"]),
        })
        return result

    def translate_documents_job(self, file_ids: List[int], *, target_lang: str,
                                source_lang: Optional[str] = None,
                                engine: Optional[str] = None,
                                actor: Optional[str] = None) -> TranslationJobResult:
        result = TranslationJobResult(stats={"files_total": len(file_ids),
                                             "files_done": 0,
                                             "segments": 0,
                                             "engine_calls": 0})
        for position, file_id in enumerate(file_ids, start=1):
            try:
                translated = self.translate_document(
                    file_id, target_lang=target_lang, source_lang=source_lang,
                    engine=engine, actor=actor)
            except DocumentNotFound:
                result.warnings.append(f"file {file_id} is not stored here")
            except engine_registry.EngineUnavailable as exc:
                result.errors.append(str(exc))
                break
            except Exception as exc:                       # noqa: BLE001
                logger.exception("translation job failed for file %s", file_id)
                result.errors.append(f"file {file_id}: {exc.__class__.__name__}")
            else:
                result.stats["segments"] += translated["segment_count"]
                result.stats["engine_calls"] += translated["engine_calls"]
            result.stats["files_done"] = position
        return result

    # -- memory ------------------------------------------------------------
    def correct(self, *, source_text: str, target_text: str, source_lang: str,
                target_lang: str, actor: Optional[str] = None) -> Dict[str, Any]:
        row = self.memory.correct(source_lang=source_lang, target_lang=target_lang,
                                  source_text=source_text, target_text=target_text,
                                  actor=actor)
        return {"success": True, "correction": {
            "source_text": row["source_text"], "target_text": row["target_text"],
            "origin": row["origin"], "actor": row["actor"]}}

    def set_glossary_term(self, *, term: str, translation: str, source_lang: str,
                          target_lang: str, actor: Optional[str] = None) -> Dict[str, Any]:
        stored = self.memory.set_glossary_term(
            source_lang=source_lang, target_lang=target_lang, term=term,
            translation=translation, actor=actor)
        return {"success": True, "term": stored}

    def suggestions(self, *, text: str, target_lang: str,
                    source_lang: Optional[str] = None,
                    limit: int = 5) -> Dict[str, Any]:
        source, detected, overridden = resolve_languages(text, target_lang, source_lang)
        exact = self.memory.lookup(source_lang=source, target_lang=target_lang,
                                   source_text=text)
        fuzzy = self.memory.suggest(source_lang=source, target_lang=target_lang,
                                    source_text=text, limit=limit)
        return {
            "success": True,
            "phase": "fuzzy_suggestions",
            "source_lang": source,
            "target_lang": target_lang,
            "detected_language": detected,
            "source_language_override": overridden,
            "exact_match": exact,
            "suggestions": [
                {"source_text": row["source_text"], "target_text": row["target_text"],
                 "score": row["score"], "origin": row["origin"],
                 "engine": row["engine"], "engine_version": row["engine_version"]}
                for row in fuzzy],
            "disclaimer": DISCLAIMER,
        }


_service: Optional[TranslationService] = None


def get_service() -> TranslationService:
    global _service
    if _service is None:
        _service = TranslationService()
    return _service


def configure(memory: Optional[TranslationMemory] = None, engine=None) -> TranslationService:
    """Replace the singleton (tests, and a future per-installation engine)."""
    global _service
    _service = TranslationService(memory=memory, engine=engine)
    return _service


def reset() -> None:
    global _service
    _service = None
