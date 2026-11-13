"""Safe SQL building blocks shared by every consumer of search criteria.

These helpers used to live privately inside ``Api/services/search_service.py``
(and a second copy of ``_escape_like`` inside ``analyst_categories.py``). They
are the *predicate vocabulary* of the product: how a literal term is matched,
how a boolean expression is parsed, how an id filter is expressed. Search,
saved searches, monitoring rules, reports and exports must all mean the same
thing by "matches", so the vocabulary lives here, once, in the pure domain
layer, and ``search_service`` re-exports the old private names for backward
compatibility.

Every function returns SQL *fragments with placeholders* plus the values to
bind. No user value is ever interpolated into SQL text.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple


def escape_like(value: Optional[str]) -> str:
    """Escape LIKE/ILIKE wildcards so user text is matched literally.

    ``%`` and ``_`` in user input must not act as wildcards (``%%`` used to
    return the whole corpus - SEC-05/API-05). Backslash is PostgreSQL's
    default LIKE escape character, so it is doubled first. Quotes are left
    alone: values are bound parameters, never spliced into SQL text.
    """
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("%", "\\%")
    text = text.replace("_", "\\_")
    return text


def escape_postgres_regex(value: str) -> str:
    """Escape user text for a PostgreSQL ARE regular-expression literal."""
    special = set(r"\.^$|?*+(){}[]")
    return "".join(("\\" + char) if char in special else char for char in str(value))


def postgres_word_pattern(value: str) -> str:
    """Build a literal PostgreSQL regex requiring word boundaries."""
    parts = [part for part in re.split(r"\s+", str(value).strip()) if part]
    if not parts:
        return r"(?!)"
    phrase = r"[[:space:]]+".join(escape_postgres_regex(part) for part in parts)
    return r"(^|[^[:alnum:]_])" + phrase + r"([^[:alnum:]_]|$)"


def text_match_clause(field: str, value: str, *, case_sensitive: bool = False,
                      whole_word: bool = False) -> Tuple[str, str]:
    """Return a safe SQL predicate and its bound pattern for a literal text field.

    ``field`` is always a trusted, code-supplied column expression - never
    user input.
    """
    if whole_word:
        operator = "~" if case_sensitive else "~*"
        return f"{field} {operator} %s", postgres_word_pattern(value)
    operator = "LIKE" if case_sensitive else "ILIKE"
    return f"{field} {operator} %s", f"%{escape_like(value)}%"


def parse_boolean_expression(query: str):
    """Parse literals, quotes, parentheses, AND/OR/NOT into a small AST.

    AND binds more tightly than OR; adjacent operands imply AND, and a binary
    NOT means AND NOT (``alpha NOT beta``). All leaves remain literal user
    strings and are bound by the SQL builder.

    AST shapes: ``('term', str)``, ``('not', node)``, ``('and', l, r)``,
    ``('or', l, r)``. Returns ``None`` for an empty or malformed expression.
    """
    token_re = re.compile(
        r'"([^"]*)"|(\bAND\b|\bOR\b|\bNOT\b)|([()])|([^\s()"]+)',
        re.IGNORECASE,
    )
    tokens: list = []
    for match in token_re.finditer(str(query or "")):
        if match.group(1) is not None:
            value = match.group(1).strip()
            if value:
                tokens.append(("term", value))
        elif match.group(2) is not None:
            tokens.append(match.group(2).upper())
        elif match.group(3) is not None:
            tokens.append(match.group(3))
        elif match.group(4) is not None:
            tokens.append(("term", match.group(4)))
    if not tokens:
        return None

    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def parse_primary():
        nonlocal position
        token = peek()
        if token == "NOT":
            position += 1
            operand = parse_primary()
            return ("not", operand) if operand is not None else None
        if token == "(":
            position += 1
            node = parse_or()
            if node is None or peek() != ")":
                return None
            position += 1
            return node
        if isinstance(token, tuple) and token[0] == "term":
            position += 1
            return token
        return None

    def parse_and():
        nonlocal position
        node = parse_primary()
        if node is None:
            return None
        while position < len(tokens):
            token = peek()
            if token == "OR" or token == ")":
                break
            if token == "AND":
                position += 1
                right = parse_primary()
                if right is None:
                    return None
                node = ("and", node, right)
            elif token == "NOT":
                position += 1
                right = parse_primary()
                if right is None:
                    return None
                node = ("and", node, ("not", right))
            elif isinstance(token, tuple) and token[0] == "term" or token == "(":
                right = parse_primary()
                if right is None:
                    return None
                node = ("and", node, right)
            else:
                return None
        return node

    def parse_or():
        nonlocal position
        node = parse_and()
        if node is None:
            return None
        while peek() == "OR":
            position += 1
            right = parse_and()
            if right is None:
                return None
            node = ("or", node, right)
        return node

    expression = parse_or()
    return expression if expression is not None and position == len(tokens) else None


# ---------------------------------------------------------------------------
# Predicates over the canonical join:
#
#     paths p
#     LEFT JOIN hash_contexts hc ON p.context_id = hc.id
#     LEFT JOIN hashs h ON hc.hash_id = h.id
#
# Every consumer (interactive search, compiled criteria) uses this join and
# these aliases, so the fragments below are interchangeable between them.
# ---------------------------------------------------------------------------

CANONICAL_FROM = (
    "paths p "
    "LEFT JOIN hash_contexts hc ON p.context_id = hc.id "
    "LEFT JOIN hashs h ON hc.hash_id = h.id"
)


def literal_field_matches(value: str, *, case_sensitive: bool = False,
                          whole_word: bool = False) -> Tuple[List[str], List[str]]:
    """Clauses (OR-ed by the caller) that make one literal term match a path.

    Searches indexed text plus searchable file/provenance metadata: file
    name, file type, source name, side name, structured raw text, and - when
    case preservation is not requested - the token table (legacy records may
    not have a raw-text row; token tables cannot prove original case).
    """
    clauses: List[str] = []
    match_params: List[str] = []
    for field in ("p.file_name", "p.file_type"):
        clause, pattern = text_match_clause(
            field, value, case_sensitive=case_sensitive, whole_word=whole_word)
        clauses.append(clause)
        match_params.append(pattern)
    for table_alias, source_column, id_column in (
        ("_search_source", "name", "hc.source_id"),
        ("_search_side", "name", "hc.side_id"),
    ):
        clause, pattern = text_match_clause(
            f"{table_alias}.{source_column}", value,
            case_sensitive=case_sensitive, whole_word=whole_word)
        table = "sources" if table_alias == "_search_source" else "sides"
        clauses.append(
            f"EXISTS (SELECT 1 FROM {table} {table_alias} "  # nosec B608 # fixed table/alias/column literals, whitelisted date columns and fixed operators; values are bound parameters
            f"WHERE {table_alias}.id = {id_column} AND {clause})")
        match_params.append(pattern)
    content_clause, content_pattern = text_match_clause(
        "cr.content", value, case_sensitive=case_sensitive, whole_word=whole_word)
    clauses.append(
        "EXISTS (SELECT 1 FROM contents_raw cr "  # nosec B608 # fixed table/alias/column literals, whitelisted date columns and fixed operators; values are bound parameters
        f"WHERE cr.hash_id = h.id AND {content_clause})")
    match_params.append(content_pattern)
    if not case_sensitive:
        word_clause, word_pattern = text_match_clause(
            "w.word", value, case_sensitive=False, whole_word=whole_word)
        clauses.append(
            "EXISTS (SELECT 1 FROM words_hashs _search_wp "  # nosec B608 # fixed table/alias/column literals, whitelisted date columns and fixed operators; values are bound parameters
            "JOIN words w ON _search_wp.word_id = w.id "
            "WHERE _search_wp.hash_id = h.id AND "
            f"{word_clause})")
        match_params.append(word_pattern)
    return clauses, match_params


def literal_expression_sql(expression, *, case_sensitive: bool = False,
                           whole_word: bool = False) -> Tuple[str, List[str]]:
    """Compile a parsed boolean AST (see ``parse_boolean_expression``)."""
    kind = expression[0]
    if kind == "term":
        clauses, values = literal_field_matches(
            expression[1], case_sensitive=case_sensitive, whole_word=whole_word)
        return f"({' OR '.join(clauses)})", values
    if kind == "not":
        child_sql, child_values = literal_expression_sql(
            expression[1], case_sensitive=case_sensitive, whole_word=whole_word)
        return f"NOT ({child_sql})", child_values
    left_sql, left_values = literal_expression_sql(
        expression[1], case_sensitive=case_sensitive, whole_word=whole_word)
    right_sql, right_values = literal_expression_sql(
        expression[2], case_sensitive=case_sensitive, whole_word=whole_word)
    operator = "AND" if kind == "and" else "OR"
    return f"({left_sql} {operator} {right_sql})", left_values + right_values


def id_filter(column: str, values: Sequence) -> Tuple[Optional[str], list]:
    """``column IN (...)`` for a non-empty id list; ``(None, [])`` otherwise."""
    values = list(values or [])
    if not values:
        return None, []
    if len(values) == 1:
        return f"{column} = %s", values
    placeholders = ",".join(["%s"] * len(values))
    return f"{column} IN ({placeholders})", values


VALID_FILE_STATUSES = ("Read", "Unread")


def filter_predicates(
    *,
    source_ids: Sequence[int] = (),
    side_ids: Sequence[int] = (),
    file_types: Sequence[str] = (),
    path_ids: Sequence[int] = (),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date_column: str = "p.file_date",
    file_statuses: Optional[Sequence[str]] = None,
    category_ids: Sequence[int] = (),
    analyst_category_ids: Sequence[int] = (),
) -> Tuple[List[str], list]:
    """The structured (non-text) filters, as ``(conditions, params)``.

    The single implementation behind interactive advanced search and every
    compiled Criteria consumer. Semantics:

    * sources / sides / file types / categories: OR within a dimension,
      AND across dimensions;
    * ``path_ids``: exact ``paths.id`` selection (File Manager pick); AND
      with every other dimension;
    * smart categories match when the content contains any member word of
      the category (``words_hashs`` x ``words_categorys``);
    * analyst categories read ONLY ``analyst_file_categories`` and are never
      satisfied by a smart category id (FR-1.4);
    * ``file_statuses=None`` leaves status unfiltered; an empty list matches
      nothing (an explicit "neither" is a real, empty answer).
    """
    conditions: List[str] = []
    params: list = []

    for column, values in (("hc.source_id", source_ids), ("hc.side_id", side_ids),
                           ("p.file_type", file_types), ("p.id", path_ids)):
        clause, bound = id_filter(column, values)
        if clause:
            conditions.append(clause)
            params.extend(bound)

    if date_column not in ("p.file_date", "ct.content_date", "hc.date_creation"):
        raise ValueError(f"unsupported date column: {date_column}")
    if date_column == "ct.content_date":
        if date_from:
            conditions.append(
                "EXISTS (SELECT 1 FROM contents ct WHERE ct.hash_id = hc.hash_id "
                "AND ct.content_date >= %s)")
            params.append(date_from)
        if date_to:
            conditions.append(
                "EXISTS (SELECT 1 FROM contents ct WHERE ct.hash_id = hc.hash_id "
                "AND ct.content_date <= %s)")
            params.append(date_to)
    else:
        if date_from:
            conditions.append(f"{date_column} >= %s")
            params.append(date_from)
        if date_to:
            conditions.append(f"{date_column} <= %s")
            params.append(date_to)

    if file_statuses is not None:
        statuses = list(dict.fromkeys(file_statuses))
        if any(status not in VALID_FILE_STATUSES for status in statuses):
            raise ValueError("file_statuses must contain only 'Read' or 'Unread'")
        if not statuses:
            conditions.append("1=0")
        else:
            placeholders = ",".join(["%s"] * len(statuses))
            conditions.append(f"p.file_status IN ({placeholders})")
            params.extend(statuses)

    if category_ids:
        clause, bound = id_filter("wc.category_id", category_ids)
        conditions.append(
            "EXISTS (SELECT 1 FROM words_hashs wp2 "  # nosec B608 # fixed table/alias/column literals, whitelisted date columns and fixed operators; values are bound parameters
            "JOIN words_categorys wc ON wp2.word_id = wc.word_id "
            f"WHERE wp2.hash_id = hc.hash_id AND {clause})")
        params.extend(bound)

    if analyst_category_ids:
        placeholders = ",".join(["%s"] * len(analyst_category_ids))
        conditions.append(
            "EXISTS (SELECT 1 FROM analyst_file_categories _afc2 "  # nosec B608 # fixed table/alias/column literals, whitelisted date columns and fixed operators; values are bound parameters
            "WHERE _afc2.path_id = p.id "
            f"AND _afc2.category_id IN ({placeholders}))")
        params.extend(analyst_category_ids)

    return conditions, params


__all__ = [
    "CANONICAL_FROM",
    "VALID_FILE_STATUSES",
    "escape_like",
    "escape_postgres_regex",
    "filter_predicates",
    "id_filter",
    "literal_expression_sql",
    "literal_field_matches",
    "parse_boolean_expression",
    "postgres_word_pattern",
    "text_match_clause",
]
