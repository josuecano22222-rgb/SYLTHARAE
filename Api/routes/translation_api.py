"""Manual translation API - POST only, every request audited.

=======  =====================================  ============================
method   path                                   what it does
=======  =====================================  ============================
POST     ``/api/translation/capabilities``      engines, languages, limits,
                                                memory statistics
POST     ``/api/translation/text``              translate one selected passage
POST     ``/api/translation/document``          translate one stored document
POST     ``/api/translation/documents``         a batch job through the job
                                                manager (returns ``job_id``)
POST     ``/api/translation/memory/correct``    an analyst's correction
POST     ``/api/translation/memory/suggest``    glossary/fuzzy suggestions
POST     ``/api/translation/glossary/term``     add or update a glossary term
=======  =====================================  ============================

Why POST for everything, including the two read-shaped calls: a translation is
work, and work behind a GET is work any prefetch, crawler or link preview can
trigger. ``capabilities`` is a POST for the same reason the rest are - it is
part of one vocabulary, and the CSRF token applies to all of it uniformly.

Every accepted request writes one ``audit_log`` row (``translation.*``) naming
who asked, for which language pair, how much text, and - for a document - which
file. A translation is a derived reading of stored material; "who asked for
which document in which language" is exactly the question the log has to be
able to answer.

Nothing in this module writes to the corpus. The translations go to the client
and, as memory, to a SQLite file under ``APP_DATA_DIR``
(``services/translation/memory.py``).
"""

from __future__ import annotations

import logging
import re

from flask import jsonify, request

from core.security.flask_ext import current_user, login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from core.validation import PayloadError, int_field, list_field, text_field
from services.translation import engines as engine_registry
from services.translation.language import TRANSLATION_LANGUAGES
from services.translation.service import (
    MAX_DOCUMENTS_PER_JOB,
    DocumentNotFound,
    MemoryUnavailable,
    get_service,
)
from services.translation.text import MAX_REQUEST_CHARS

logger = logging.getLogger(__name__)

_DEFAULT_SUGGESTIONS = 5
_MAX_SUGGESTIONS = 20


def _error(code: str, message: str, status: int, **extra):
    payload = {"success": False, "code": code, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def _body() -> dict:
    """The request body as a dict, or a 400 already formatted."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise PayloadError("a JSON object is required")
    return data


def _language(data: dict, key: str, *, required: bool) -> str | None:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise PayloadError(f"{key} is required", key)
        return None
    if not isinstance(value, str):
        raise PayloadError(f"{key} must be a language code", key)
    code = value.strip().lower()
    if code not in TRANSLATION_LANGUAGES:
        raise PayloadError(
            f"{key} must be one of {', '.join(TRANSLATION_LANGUAGES)}", key)
    return code


def _text(data: dict, key: str, *, max_length: int = MAX_REQUEST_CHARS) -> str:
    value = text_field(data, key, required=True, trim=False)
    if len(value) > max_length:
        raise PayloadError(
            f"{key} is longer than the {max_length} character limit for one "
            f"request ({len(value)} characters)", key)
    return value


def _actor():
    user = current_user()
    return getattr(user, "username", None), getattr(user, "id", None)


def _audit(action: str, detail: dict, resource: str = "translation") -> None:
    from core.security.service import get_auth_service

    username, user_id = _actor()
    get_auth_service().audit(action, user_id=user_id, username=username,
                             resource=resource, detail=detail,
                             ip_address=request.remote_addr)


def _engine_unavailable(exc) -> tuple:
    """503 with the reason and everything that was looked for.

    Two causes, one shape: nothing installed (``engine_not_installed``) and an
    engine that is installed but failed to produce a translation
    (``engine_failed`` - typically a model that cannot be fetched).
    """
    engines = getattr(exc, "engines", None) or [
        engine_registry.describe_engine(entry)
        for entry in engine_registry.builtin_engines()]
    return _error(getattr(exc, "code", "engine_not_installed"), str(exc), 503,
                  engines=engines)


def register_translation_routes(app):
    """Register the manual translation endpoints on *app*."""
    # The panel is included by two pages that know nothing about languages, so
    # the content-language vocabulary is an environment global (the same
    # pattern as feature_enabled/interface_for in Api/routes/common.py).
    app.jinja_env.globals.setdefault("translation_languages", dict(TRANSLATION_LANGUAGES))

    @app.route("/api/translation/capabilities", methods=["POST"])
    @login_required
    def translation_capabilities():
        return jsonify({"success": True, **get_service().capabilities()})

    # -- one selection -----------------------------------------------------
    @app.route("/api/translation/text", methods=["POST"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def translation_text():
        try:
            data = _body()
            text = _text(data, "text")
            target_lang = _language(data, "target_lang", required=True)
            source_lang = _language(data, "source_lang", required=False)
            engine = data.get("engine") or None
            if engine is not None and not isinstance(engine, str):
                raise PayloadError("engine must be an engine id", "engine")
        except PayloadError as exc:
            too_large = exc.field == "text" and "character limit" in exc.message
            return _error("text_too_large" if too_large else "VALIDATION_FAILED",
                          exc.message, 413 if too_large else 400,
                          field=exc.field)

        if target_lang == source_lang:
            return _error("SAME_LANGUAGE",
                          "source_lang and target_lang are the same language", 400)

        username, _user_id = _actor()
        try:
            result = get_service().translate_text(
                text, target_lang=target_lang, source_lang=source_lang,
                engine=engine, actor=username)
        except MemoryUnavailable as exc:
            return _error("memory_unavailable", str(exc), 503)
        except engine_registry.EngineUnavailable as exc:
            _audit("translation.text.requested",
                   {"target_lang": target_lang, "source_lang": source_lang,
                    "chars": len(text), "outcome": "engine_not_installed"})
            return _engine_unavailable(exc)
        except ValueError as exc:
            return _error("VALIDATION_FAILED", str(exc), 400)

        _audit("translation.text.requested", {
            "target_lang": result["target_lang"],
            "source_lang": result["source_lang"],
            "source_language_override": result["source_language_override"],
            "chars": len(text),
            "segments": result["segment_count"],
            "engine": (result["engine"] or {}).get("id"),
            "engine_calls": result["engine_calls"],
            "outcome": "translated",
        })
        return jsonify(result)

    # -- one document ------------------------------------------------------
    @app.route("/api/translation/document", methods=["POST"])
    @login_required
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    def translation_document():
        try:
            data = _body()
            file_id = int_field(data, "file_id", minimum=1)
            if file_id is None:
                raise PayloadError("file_id is required", "file_id")
            target_lang = _language(data, "target_lang", required=True)
            source_lang = _language(data, "source_lang", required=False)
            engine = data.get("engine") or None
            if engine is not None and not isinstance(engine, str):
                raise PayloadError("engine must be an engine id", "engine")
        except PayloadError as exc:
            return _error("VALIDATION_FAILED", exc.message, 400, field=exc.field)

        username, _user_id = _actor()
        try:
            result = get_service().translate_document(
                file_id, target_lang=target_lang, source_lang=source_lang,
                engine=engine, actor=username)
        except DocumentNotFound as exc:
            return _error("NOT_FOUND", str(exc), 404)
        except MemoryUnavailable as exc:
            return _error("memory_unavailable", str(exc), 503)
        except engine_registry.EngineUnavailable as exc:
            _audit("translation.document.requested",
                   {"file_id": file_id, "target_lang": target_lang,
                    "outcome": "engine_not_installed"})
            return _engine_unavailable(exc)

        _audit("translation.document.requested", {
            "file_id": file_id,
            "target_lang": result["target_lang"],
            "source_lang": result["source_lang"],
            "segments": result["segment_count"],
            "document_chars": result["document_chars"],
            "truncated": result["truncated"],
            "engine": (result["engine"] or {}).get("id"),
            "outcome": "translated",
        })
        return jsonify(result)

    # -- several documents: a job -----------------------------------------
    @app.route("/api/translation/documents", methods=["POST"])
    @login_required
    @limiter.limit("10 per minute")
    def translation_documents():
        try:
            data = _body()
            raw_ids = list_field(data, "file_ids")
            if raw_ids is None:
                raise PayloadError("file_ids is required", "file_ids")
            target_lang = _language(data, "target_lang", required=True)
            source_lang = _language(data, "source_lang", required=False)
            engine = data.get("engine") or None
        except PayloadError as exc:
            return _error("VALIDATION_FAILED", exc.message, 400, field=exc.field)

        file_ids = []
        for value in raw_ids:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return _error("VALIDATION_FAILED",
                              "file_ids must be a list of positive integers", 400,
                              field="file_ids")
            file_ids.append(int(value))
        file_ids = sorted(set(file_ids))
        if not file_ids:
            return _error("VALIDATION_FAILED", "file_ids must not be empty", 400,
                          field="file_ids")
        if len(file_ids) > MAX_DOCUMENTS_PER_JOB:
            return _error("VALIDATION_FAILED",
                          f"at most {MAX_DOCUMENTS_PER_JOB} documents per job "
                          f"({len(file_ids)} requested)", 400, field="file_ids")

        from services.jobs.manager import JobManager

        username, _user_id = _actor()
        service = get_service()

        def _starter(context):
            emit = context["emit"]
            emit("PHASE_STARTED", {"phase": "translating"})
            job_result = service.translate_documents_job(
                file_ids, target_lang=target_lang, source_lang=source_lang,
                engine=engine, actor=username)
            emit("PHASE_COMPLETED", {"phase": "translating",
                                     "segments": job_result.stats.get("segments", 0)})
            return job_result

        try:
            job = JobManager.get_instance().create_job(
                "translation",
                source=f"translation:{target_lang}:{len(file_ids)}",
                options={"file_ids": file_ids, "target_lang": target_lang,
                         "source_lang": source_lang, "engine": engine},
                created_by=username or "system",
                starter=_starter)
        except Exception:
            logger.exception("translation batch job creation failed")
            return _error("JOB_CREATE_FAILED",
                          "the translation job could not be created", 500)

        _audit("translation.documents.requested", {
            "job_id": job["job_id"], "file_ids": file_ids,
            "target_lang": target_lang, "source_lang": source_lang,
            "engine": engine, "outcome": "queued"})
        return jsonify({"success": True, "job_id": job["job_id"],
                        "file_count": len(file_ids), "status": job["status"]}), 202

    # -- memory ------------------------------------------------------------
    @app.route("/api/translation/memory/correct", methods=["POST"])
    @login_required
    def translation_memory_correct():
        try:
            data = _body()
            source_text = _text(data, "source_text")
            target_text = _text(data, "target_text")
            target_lang = _language(data, "target_lang", required=True)
            source_lang = _language(data, "source_lang", required=True)
        except PayloadError as exc:
            return _error("VALIDATION_FAILED", exc.message, 400, field=exc.field)

        if source_lang == target_lang:
            return _error("SAME_LANGUAGE",
                          "source_lang and target_lang are the same language", 400)

        username, _user_id = _actor()
        try:
            result = get_service().correct(source_text=source_text,
                                           target_text=target_text,
                                           source_lang=source_lang,
                                           target_lang=target_lang, actor=username)
        except MemoryUnavailable as exc:
            return _error("memory_unavailable", str(exc), 503)
        _audit("translation.memory.corrected", {
            "source_lang": source_lang, "target_lang": target_lang,
            "chars": len(source_text), "outcome": "corrected"})
        return jsonify(result)

    @app.route("/api/translation/memory/suggest", methods=["POST"])
    @login_required
    def translation_memory_suggest():
        try:
            data = _body()
            text = _text(data, "text")
            target_lang = _language(data, "target_lang", required=True)
            source_lang = _language(data, "source_lang", required=False)
            limit = int_field(data, "limit", default=_DEFAULT_SUGGESTIONS,
                              minimum=1, maximum=_MAX_SUGGESTIONS)
        except PayloadError as exc:
            return _error("VALIDATION_FAILED", exc.message, 400, field=exc.field)

        try:
            result = get_service().suggestions(text=text, target_lang=target_lang,
                                               source_lang=source_lang, limit=limit)
        except MemoryUnavailable as exc:
            return _error("memory_unavailable", str(exc), 503)
        _audit("translation.memory.suggested", {
            "target_lang": result["target_lang"],
            "source_lang": result["source_lang"],
            "chars": len(text), "suggestions": len(result["suggestions"]),
            "exact_match": result["exact_match"] is not None})
        return jsonify(result)

    # -- glossary ----------------------------------------------------------
    @app.route("/api/translation/glossary/term", methods=["POST"])
    @login_required
    def translation_glossary_term():
        try:
            data = _body()
            term = text_field(data, "term", required=True, trim=True, max_length=200)
            translation = text_field(data, "translation", required=True, trim=True,
                                     max_length=200)
            target_lang = _language(data, "target_lang", required=True)
            source_lang = _language(data, "source_lang", required=True)
        except PayloadError as exc:
            return _error("VALIDATION_FAILED", exc.message, 400, field=exc.field)

        if not re.search(r"\w", term):
            return _error("VALIDATION_FAILED",
                          "term must contain at least one letter or digit", 400,
                          field="term")
        username, _user_id = _actor()
        try:
            result = get_service().set_glossary_term(
                term=term, translation=translation, source_lang=source_lang,
                target_lang=target_lang, actor=username)
        except MemoryUnavailable as exc:
            return _error("memory_unavailable", str(exc), 503)
        _audit("translation.glossary.term_set", {
            "source_lang": source_lang, "target_lang": target_lang,
            "term": term[:80]})
        return jsonify(result)
