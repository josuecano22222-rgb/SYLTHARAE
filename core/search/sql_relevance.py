"""SQL-side relevance scoring for paginated search (no Python BM25)."""

from __future__ import annotations

from typing import List, Sequence, Tuple

from core.criteria.sql import escape_like


def sql_relevance_score_expr(
    ranking_terms: Sequence[str],
    *,
    hash_ref: str = "h.id",
) -> Tuple[str, List[str]]:
    """Build a COALESCE(SUM(...),0) relevance expression and its bind params.

    Filename hits weigh 2.0; inverted-index (words_hashs) hits weigh 1.0.
    Uses ``hc.hash_id`` via the caller's join — no per-row path subquery.
    """
    terms = [str(t).strip() for t in ranking_terms if str(t).strip()]
    if not terms:
        return "0::float", []

    parts: List[str] = []
    params: List[str] = []
    for term in terms:
        pattern = f"%{escape_like(term)}%"
        parts.append(
            f"""
            CASE
                WHEN p.file_name ILIKE %s ESCAPE '\\' THEN 2.0
                WHEN EXISTS (
                    SELECT 1 FROM words_hashs _rel_wp
                    JOIN words _rel_w ON _rel_wp.word_id = _rel_w.id
                    WHERE _rel_wp.hash_id = {hash_ref}
                      AND _rel_w.word ILIKE %s ESCAPE '\\'
                ) THEN 1.0
                ELSE 0.0
            END
            """.strip()
        )
        params.extend([pattern, pattern])

    expr = " + ".join(f"({part})" for part in parts)
    return f"COALESCE(({expr}), 0)::float", params
