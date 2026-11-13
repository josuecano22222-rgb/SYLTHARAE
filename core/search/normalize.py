"""Canonical query representation for caching and planner deduplication."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional


_WS = re.compile(r"\s+")


def normalize_query_text(query: str) -> str:
    """Collapse whitespace; lower-case for cache keys where case is not required."""
    text = _WS.sub(" ", str(query or "").strip())
    return text


def normalize_boolean_query(query: str) -> str:
    """Normalize boolean operators for equivalent-query detection."""
    text = normalize_query_text(query)
    text = re.sub(r"\bAND\b", "AND", text, flags=re.IGNORECASE)
    text = re.sub(r"\bOR\b", "OR", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNOT\b", "NOT", text, flags=re.IGNORECASE)
    return text


def search_cache_key(
    *,
    query: str,
    filters: Optional[Dict[str, Any]] = None,
    sort_by: str = "relevance",
    sort_order: str = "desc",
    scope: Optional[str] = None,
    search_mode: str = "advanced",
    index_version: int = 1,
    case_sensitive: bool = False,
) -> str:
    """Deterministic key for metadata/snippet caches."""
    payload = {
        "q": normalize_boolean_query(query) if not case_sensitive else normalize_query_text(query),
        "filters": filters or {},
        "sort_by": sort_by,
        "sort_order": sort_order,
        "scope": scope or "uncategorized",
        "mode": search_mode,
        "index_version": index_version,
        "case_sensitive": case_sensitive,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _norm_id_list(values: Any) -> list:
    if not values:
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    out: list = []
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _norm_file_types(file_type: Any) -> list:
    if not file_type:
        return []
    if isinstance(file_type, (list, tuple)):
        return sorted(str(x).strip().lower() for x in file_type if str(x).strip())
    return [str(file_type).strip().lower()]


def pagination_query_signature(
    *,
    query: str,
    file_type: Any = None,
    source_ids: Any = None,
    side_ids: Any = None,
    category_ids: Any = None,
    analyst_category_ids: Any = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    analyst_scope: Optional[str] = None,
    file_statuses: Any = None,
    hide_duplicates: bool = False,
    sort_by: str = "relevance",
    sort_order: str = "desc",
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> str:
    """Single signature for keyset cursors — must match between mint and validate."""
    statuses: Optional[list] = None
    if file_statuses is not None:
        if isinstance(file_statuses, (list, tuple)):
            statuses = sorted(str(s) for s in file_statuses if s is not None)
        else:
            statuses = [str(file_statuses)]
    filters: Dict[str, Any] = {
        "file_type": _norm_file_types(file_type),
        "source_ids": _norm_id_list(source_ids),
        "side_ids": _norm_id_list(side_ids),
        "category_ids": _norm_id_list(category_ids),
        "analyst_category_ids": _norm_id_list(analyst_category_ids),
        "date_from": date_from or None,
        "date_to": date_to or None,
        "file_statuses": statuses,
        "hide_duplicates": bool(hide_duplicates),
        "whole_word": bool(whole_word),
    }
    return search_cache_key(
        query=query,
        filters=filters,
        sort_by=sort_by,
        sort_order=sort_order,
        scope=analyst_scope or "uncategorized",
        case_sensitive=case_sensitive,
    )
