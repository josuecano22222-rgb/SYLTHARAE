"""Indexed-vocabulary short-circuit for provably empty plain/boolean AND queries.

A term missing from the indexed word vocabulary is **not** on its own proof
that a search is empty. Interactive search matches more than that vocabulary:
``core.criteria.sql.literal_field_matches`` - the single definition of
"matches", shared with saved searches, monitoring, reports and exports - also
matches the file name, the file type, the source and side names, the stored
raw text and the token table. A filename fragment, a code name or an
identifier that never entered the word table (``e04c07fc`` in
``alpha-report-e04c07fc.txt``) matched files plainly, yet the short-circuit
answered zero results without running a query.

So the short-circuit now proves its case against that whole predicate: the
missing terms must match no path at all. The bounded existence probe (one
``LIMIT 1`` query per unresolved term, no DISTINCT, no COUNT) is not the
candidate generation it replaces - a search that really is empty still skips
the DISTINCT/COUNT/ranking work - and it is the difference between a fast
answer and a wrong one.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.criteria.sql import literal_field_matches, parse_boolean_expression
from core.search.words import normalize_search_token, resolve_word_ids_for_terms

_REGEX_LIKE = re.compile(
    r"[\[\]\(\)\|\?\*\+\{\\]|\\b|\(\?[a-zA-Z]",
)


def looks_like_regex_pattern(query: str) -> bool:
    """Heuristic: do not treat regex/PCRE-style queries as indexed vocabulary."""
    text = str(query or "").strip()
    if not text:
        return False
    return bool(_REGEX_LIKE.search(text))


def collect_expression_terms(expression: Tuple) -> List[str]:
    kind = expression[0]
    if kind == "term":
        return [str(expression[1])]
    if kind in ("and", "or"):
        return collect_expression_terms(expression[1]) + collect_expression_terms(expression[2])
    if kind == "not":
        return collect_expression_terms(expression[1])
    return []


def indexed_expression_provably_empty(expression: Tuple, id_map: Dict[str, int]) -> bool:
    """True when the boolean tree cannot match indexed vocabulary (AND/OR rules)."""
    kind = expression[0]
    if kind == "term":
        return normalize_search_token(expression[1]) not in id_map
    if kind == "and":
        return indexed_expression_provably_empty(expression[1], id_map) or indexed_expression_provably_empty(
            expression[2], id_map
        )
    if kind == "or":
        return indexed_expression_provably_empty(expression[1], id_map) and indexed_expression_provably_empty(
            expression[2], id_map
        )
    if kind == "not":
        return False
    return False


def unresolved_mandatory_term_count(terms: Sequence[str], id_map: Dict[str, int]) -> int:
    count = 0
    for term in terms:
        key = normalize_search_token(term)
        if key and key not in id_map:
            count += 1
    return count


def unresolved_terms(terms: Sequence[str], id_map: Dict[str, int]) -> List[str]:
    """The terms the word vocabulary could not resolve, in query order."""
    out: List[str] = []
    for term in terms:
        key = normalize_search_token(term)
        if key and key not in id_map:
            out.append(term)
    return out


def term_matches_a_path(term: str, cursor: Any) -> bool:
    """Does this term match any path by the search's own definition of matching?

    One ``SELECT 1 ... LIMIT 1`` over ``literal_field_matches`` - the same
    predicate the search itself applies - so a term that lives only in a file
    name, a type, a source/side name or the stored raw text is found here and
    the caller does not short-circuit.
    """
    clauses, params = literal_field_matches(term, case_sensitive=False,
                                            whole_word=False)
    sql = (
        "SELECT 1 FROM paths p"
        " JOIN hash_contexts hc ON hc.id = p.context_id"
        " JOIN hashs h ON h.id = hc.hash_id"
        f" WHERE ({' OR '.join(clauses)}) LIMIT 1"
    )
    cursor.execute(sql, tuple(params))
    return bool(cursor.fetchall())


def any_unresolved_term_matches_a_path(terms: Sequence[str], cursor: Any) -> bool:
    """True as soon as one unresolved term matches anything the search can match."""
    for term in terms:
        if term_matches_a_path(term, cursor):
            return True
    return False


def assess_indexed_vocabulary_short_circuit(
    *,
    query: str,
    intent: Dict[str, Any],
    case_sensitive: bool,
    whole_word: bool,
    expansion_terms: Sequence[str],
    cursor: Any,
) -> Optional[Dict[str, Any]]:
    """
    Return telemetry dict when the query is provably empty on indexed vocabulary.

    Plain space-separated terms use AND semantics. Boolean OR only short-circuits
    when every branch is provably empty.
    """
    if case_sensitive or whole_word or expansion_terms:
        return None
    if intent.get("has_quotes"):
        return None
    if looks_like_regex_pattern(query):
        return None

    text = str(query or "").strip()
    if len(text) < 2:
        return None

    if intent.get("has_operators") or "(" in text or ")" in text:
        expression = parse_boolean_expression(text)
        if expression is None:
            return None
        terms = collect_expression_terms(expression)
        if not terms:
            return None
        id_map = resolve_word_ids_for_terms(terms, cursor=cursor)
        if not indexed_expression_provably_empty(expression, id_map):
            return None
        missing_terms = unresolved_terms(terms, id_map)
        # A term the vocabulary missed may still match a file name, a type, a
        # source/side name or the raw text; only the database can say that it
        # does not, and only then is the empty answer provable.
        if any_unresolved_term_matches_a_path(missing_terms, cursor):
            return None
        return {
            "short_circuit": True,
            "short_circuit_reason": "mandatory_term_unresolved",
            "unresolved_term_count": len(missing_terms),
        }

    if intent.get("has_operators"):
        return None

    search_terms = [t.strip() for t in text.split() if t.strip()]
    if not search_terms:
        return None
    id_map = resolve_word_ids_for_terms(search_terms, cursor=cursor)
    missing_terms = unresolved_terms(search_terms, id_map)
    if not missing_terms:
        return None
    # See the boolean branch: a vocabulary miss is not proof of no match.
    if any_unresolved_term_matches_a_path(missing_terms, cursor):
        return None
    return {
        "short_circuit": True,
        "short_circuit_reason": "mandatory_term_unresolved",
        "unresolved_term_count": len(missing_terms),
    }
