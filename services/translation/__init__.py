"""Manual translation of stored material - never automatic.

What this package is
====================

A human selects text in the file viewer or in a search result and asks for a
translation. The result is shown next to the original, labelled *machine
translation, not verified*, and the analyst can accept it or correct it. The
correction is remembered.

What it is not
==============

It is not part of ingestion, indexing, classification or detection. Nothing in
this package is imported by a module that runs unattended, and nothing here
writes a translation back into the stored corpus (paths, contents, signals,
search index) - a machine translation stored as content would be
indistinguishable from the analyst's source material afterwards. Two tests hold
that line: ``tests/unit/test_translation_no_ingest_coupling.py`` (the import
graph, both directions) and the ``NothingIsStored`` cases in
``tests/integration/test_translation_api.py``.

Layout
======

``text``      sentence splitting and the 512-token / character ceilings
``language``  script and stop-word detection, and the user's override
``engines``   the adapter contract and the three adapters (Argos, Opus-MT,
              NLLB-200); nothing imports an engine backend until it is used
``memory``    the translation memory: a SQLite file under ``APP_DATA_DIR``
              (translation memory is not derivable, so it is not in PostgreSQL)
``service``   the order of operations for one request, and the batch job

Licensing of the engines
========================

=================================  =========================  ===========
engine                             weights licence            default
=================================  =========================  ===========
Argos Translate                    MIT                        yes
Opus-MT (Helsinki-NLP via Marian)  CC-BY-4.0                  yes
NLLB-200                           CC-BY-NC-4.0 (non-commercial)  no
=================================  =========================  ===========

NLLB-200 is optional, is never selected by default, is never used for
documents, and must be configured (``SYLTHARAE_NLLB_MODEL``) and requested per
call. Its weights are non-commercial and its model card excludes some
documentation use cases; enabling it is a decision for the installation, not
for this package.
"""

from .service import (  # noqa: F401
    DISCLAIMER,
    MAX_DOCUMENTS_PER_JOB,
    DocumentNotFound,
    TranslationService,
    configure,
    get_service,
    reset,
)
from .text import MAX_CHARS, MAX_REQUEST_CHARS, MAX_TOKENS, split_segments  # noqa: F401
from .language import TRANSLATION_LANGUAGES, detect_language, resolve_languages  # noqa: F401

__all__ = [
    "DISCLAIMER",
    "MAX_CHARS",
    "MAX_DOCUMENTS_PER_JOB",
    "MAX_REQUEST_CHARS",
    "MAX_TOKENS",
    "TRANSLATION_LANGUAGES",
    "DocumentNotFound",
    "TranslationService",
    "configure",
    "detect_language",
    "get_service",
    "reset",
    "resolve_languages",
    "split_segments",
]
