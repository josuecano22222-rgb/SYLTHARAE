"""Term-name services: display labels and query expansion.

``term_labels`` is the only module that reads ``term_names``; anything that
needs a translated label or a cross-language search goes through it, so the
fallback chain and the search guards exist in one place.
"""
from __future__ import annotations

from services.i18n.term_labels import (  # noqa: F401
    DEFAULT_LANGUAGE,
    ID_SET_CAP,
    MIN_QUERY_CHARS,
    Candidate,
    CategoryLabel,
    Expansion,
    Label,
    describe,
    expand_query,
    label_map,
    labels_for,
    labels_for_categories,
    language_chain,
)

__all__ = [
    "DEFAULT_LANGUAGE", "ID_SET_CAP", "MIN_QUERY_CHARS", "Candidate", "CategoryLabel", "Expansion",
    "Label", "describe", "expand_query", "label_map", "labels_for", "labels_for_categories", "language_chain",
]
