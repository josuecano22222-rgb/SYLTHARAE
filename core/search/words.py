"""Indexed word resolution (query token → word_id without ILIKE scans)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from Api.utils import execute_query


def normalize_search_token(term: str) -> str:
    return str(term or "").strip().lower()


def resolve_word_ids_for_terms(
    terms: Sequence[str],
    *,
    cursor: Optional[Any] = None,
) -> Dict[str, int]:
    """Map normalized token → words.id for exact vocabulary hits."""
    keys = []
    seen = set()
    for term in terms:
        key = normalize_search_token(term)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    if not keys:
        return {}
    sql = "SELECT word, id FROM words WHERE word = ANY(%s)"
    if cursor is not None:
        cursor.execute(sql, (keys,))
        rows = cursor.fetchall() or []
    else:
        rows = execute_query(sql, (keys,), fetch="all") or []
    resolved: Dict[str, int] = {}
    for row in rows:
        if not row or row[0] is None:
            continue
        try:
            resolved[str(row[0])] = int(row[1])
        except (TypeError, ValueError, IndexError):
            continue
    return resolved


def inverted_index_and_clause(word_ids: Sequence[int]) -> Tuple[str, List[int]]:
    """Require every word_id on the same content hash (AND semantics)."""
    ids = [int(w) for w in word_ids if w is not None]
    if not ids:
        return "1=0", []
    parts = []
    params: List[int] = []
    for wid in ids:
        parts.append(
            "EXISTS (SELECT 1 FROM words_hashs _fast_wh "
            "WHERE _fast_wh.hash_id = h.id AND _fast_wh.word_id = %s)"
        )
        params.append(wid)
    return f"({' AND '.join(parts)})", params


def can_use_fast_inverted_path(
    terms: Sequence[str],
    *,
    case_sensitive: bool,
    whole_word: bool,
    has_boolean_ops: bool,
    has_quotes: bool,
    cursor: Optional[Any] = None,
) -> bool:
    if case_sensitive or whole_word or has_boolean_ops or has_quotes:
        return False
    if not terms or len(terms) > 8:
        return False
    resolved = resolve_word_ids_for_terms(terms, cursor=cursor)
    return len(resolved) == len([t for t in terms if normalize_search_token(t)])
