"""Flat compilation for pure boolean OR (one contents_raw / words probe per path)."""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple

from core.criteria.sql import text_match_clause


def is_pure_disjunction(expression: Tuple) -> bool:
    kind = expression[0]
    if kind == "term":
        return True
    if kind == "or":
        return is_pure_disjunction(expression[1]) and is_pure_disjunction(expression[2])
    return False


def flatten_disjunction_terms(expression: Tuple) -> List[str]:
    kind = expression[0]
    if kind == "term":
        return [str(expression[1])]
    if kind == "or":
        merged: List[str] = []
        seen = set()
        for term in flatten_disjunction_terms(expression[1]) + flatten_disjunction_terms(expression[2]):
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                merged.append(term)
        return merged
    return []


def flat_or_literal_sql(
    terms: Sequence[str],
    *,
    case_sensitive: bool,
    whole_word: bool,
) -> Tuple[str, List[Any]]:
    """OR of term predicates with shared EXISTS blocks for content and token tables."""
    cleaned = [str(t).strip() for t in terms if str(t).strip()]
    if not cleaned:
        return "1=0", []

    params: List[Any] = []
    branches: List[str] = []

    for field in ("p.file_name", "p.file_type"):
        field_clauses: List[str] = []
        for term in cleaned:
            clause, value = text_match_clause(
                field, term, case_sensitive=case_sensitive, whole_word=whole_word
            )
            field_clauses.append(clause)
            params.append(value)
        branches.append(f"({' OR '.join(field_clauses)})")

    for table_alias, source_column, id_column in (
        ("_search_source", "name", "hc.source_id"),
        ("_search_side", "name", "hc.side_id"),
    ):
        exists_parts: List[str] = []
        for term in cleaned:
            clause, value = text_match_clause(
                f"{table_alias}.{source_column}",
                term,
                case_sensitive=case_sensitive,
                whole_word=whole_word,
            )
            table = "sources" if table_alias == "_search_source" else "sides"
            exists_parts.append(
                f"EXISTS (SELECT 1 FROM {table} {table_alias} "
                f"WHERE {table_alias}.id = {id_column} AND {clause})"
            )
            params.append(value)
        branches.append(f"({' OR '.join(exists_parts)})")

    content_clauses: List[str] = []
    for term in cleaned:
        clause, value = text_match_clause(
            "cr.content", term, case_sensitive=case_sensitive, whole_word=whole_word
        )
        content_clauses.append(clause)
        params.append(value)
    branches.append(
        "EXISTS (SELECT 1 FROM contents_raw cr "
        f"WHERE cr.hash_id = h.id AND ({' OR '.join(content_clauses)}))"
    )

    if not case_sensitive:
        word_clauses: List[str] = []
        for term in cleaned:
            clause, value = text_match_clause(
                "w.word", term, case_sensitive=False, whole_word=whole_word
            )
            word_clauses.append(clause)
            params.append(value)
        branches.append(
            "EXISTS (SELECT 1 FROM words_hashs _search_wp "
            "JOIN words w ON _search_wp.word_id = w.id "
            f"WHERE _search_wp.hash_id = h.id AND ({' OR '.join(word_clauses)}))"
        )

    return f"({' OR '.join(branches)})", params
