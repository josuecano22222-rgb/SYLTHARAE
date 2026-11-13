"""The one query compiler: ``Criteria`` + ``AccessScope`` -> parameterised SQL.

Every non-interactive consumer - saved-search evaluation, monitoring rules,
report datasets, exports - obtains its matching set from here. Interactive
search uses the very same predicate builders (``core.criteria.sql``) and adds
ranking on top; it does not have a private notion of "matches".

Guarantees
----------
* **Parameterised only.** The SQL text is built exclusively from code
  constants; every user-derived value is a bound parameter.
* **Permission-aware.** ``AccessScope`` predicates are compiled into the
  WHERE clause, so rows a caller may not see are never fetched. (Today the
  schema has no per-source ACL, so ``allowed_source_ids=None`` - "all" - is
  the only value the application produces; the predicate path exists and is
  tested so a future ACL cannot be bolted on *after* retrieval.)
* **Stable ordering.** Every ORDER BY ends with a unique tie-breaker, so
  pagination never duplicates or skips rows.
* **Explainable.** ``CompiledQuery.explain`` states the criteria, the scope
  and the ordering in words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.criteria.model import Criteria, CriteriaError
from core.criteria.sql import (
    CANONICAL_FROM,
    filter_predicates,
    literal_expression_sql,
    literal_field_matches,
    parse_boolean_expression,
)

#: Hard ceiling for one page of compiled results.
MAX_PAGE_SIZE = 10_000

_DATE_COLUMN = {
    "file_date": "p.file_date",
    "content_date": "ct.content_date",
    "context_date": "hc.date_creation",
}

_SORT_COLUMN_PATH = {
    "date": "p.file_date",
    "name": "p.file_name",
    "type": "p.file_type",
    "size": "p.file_size",
    "id": "p.id",
}


@dataclass(frozen=True)
class AccessScope:
    """Who is asking, and what they may see.

    ``allowed_source_ids``: ``None`` means unrestricted; a tuple restricts the
    result to those sources (an empty tuple matches nothing).
    ``suppressed_source_count`` is carried so reports can print the
    ``ROLE_SCOPED`` note ("N sources are excluded for your role").
    """

    user_id: Optional[int] = None
    role: Optional[str] = None
    allowed_source_ids: Optional[Tuple[int, ...]] = None
    suppressed_source_count: int = 0

    @classmethod
    def unrestricted(cls, user_id: Optional[int] = None,
                     role: Optional[str] = None) -> "AccessScope":
        return cls(user_id=user_id, role=role)

    @classmethod
    def system(cls) -> "AccessScope":
        """Scope for system processes (detection, maintenance). Never used
        for anything a user will read - user-facing work always carries the
        user's own scope."""
        return cls(user_id=None, role="system")


@dataclass(frozen=True)
class CompiledQuery:
    criteria_fingerprint: str
    where_sql: str
    params: Tuple
    order_sql: str
    unit: str
    notes: Tuple[str, ...] = field(default_factory=tuple)
    scope: AccessScope = field(default_factory=AccessScope)

    # ------------------------------------------------------------------
    def count_sql(self) -> Tuple[str, Tuple]:
        target = "DISTINCT hc.hash_id" if self.unit == "hash" else "DISTINCT p.id"
        sql = f"SELECT COUNT({target}) FROM {CANONICAL_FROM} WHERE {self.where_sql}"  # nosec B608 # CANONICAL_FROM, compiler-built WHERE (fixed fragments with %s) and whitelisted ORDER BY; values are bound parameters
        return sql, self.params

    def ids_sql(self, limit: int, offset: int = 0) -> Tuple[str, Tuple]:
        """Page of matching ids (path ids or hash ids per ``unit``)."""
        limit = int(limit)
        offset = int(offset)
        if limit <= 0 or limit > MAX_PAGE_SIZE:
            raise CriteriaError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise CriteriaError("offset must be >= 0")
        if self.unit == "hash":
            # One row per content hash; order by the hash's earliest-ordered
            # path so the order is still meaningful and deterministic.
            sql = (
                "SELECT hash_id FROM ("  # nosec B608 # CANONICAL_FROM, compiler-built WHERE (fixed fragments with %s) and whitelisted ORDER BY; values are bound parameters
                f" SELECT hc.hash_id AS hash_id, MIN(p.id) AS first_path"
                f" FROM {CANONICAL_FROM} WHERE {self.where_sql}"
                "  AND hc.hash_id IS NOT NULL"
                " GROUP BY hc.hash_id"
                ") matched ORDER BY hash_id ASC LIMIT %s OFFSET %s"
            )
        else:
            sql = (f"SELECT p.id FROM {CANONICAL_FROM} WHERE {self.where_sql} "  # nosec B608 # CANONICAL_FROM, compiler-built WHERE (fixed fragments with %s) and whitelisted ORDER BY; values are bound parameters
                   f"ORDER BY {self.order_sql} LIMIT %s OFFSET %s")
        return sql, self.params + (limit, offset)

    def explain(self, criteria: Criteria) -> List[str]:
        lines = list(criteria.explain())
        if self.scope.allowed_source_ids is not None:
            lines.append(f"Access scope: restricted to {len(self.scope.allowed_source_ids)} "
                         f"source(s) for role {self.scope.role!r}")
        if self.scope.suppressed_source_count:
            lines.append(f"{self.scope.suppressed_source_count} source(s) excluded "
                         "for this role")
        lines.extend(self.notes)
        lines.append(f"Criteria fingerprint: {self.criteria_fingerprint}")
        return lines


def compile_criteria(criteria: Criteria, scope: AccessScope) -> CompiledQuery:
    """Compile criteria for a caller. Raises ``CriteriaError`` if unsatisfiable
    as written (e.g. a malformed boolean expression) - never silently widens."""
    if not isinstance(scope, AccessScope):
        raise CriteriaError("an AccessScope is required: compiled queries are "
                            "never run without knowing who is asking")
    conditions: List[str] = []
    params: list = []
    notes: List[str] = []

    # --- text expression --------------------------------------------------
    if criteria.text:
        expression = parse_boolean_expression(criteria.text)
        if expression is None:
            raise CriteriaError(f"text is not a valid expression: {criteria.text!r}")
        sql, values = literal_expression_sql(
            expression, case_sensitive=criteria.case_sensitive,
            whole_word=criteria.whole_word)
        conditions.append(sql)
        params.extend(values)
        if criteria.options.use_fuzzy or criteria.options.use_expansion:
            notes.append("Compiled matching is literal: fuzzy/expansion recall "
                         "options apply to interactive search only.")

    # --- phrases: every phrase must occur ------------------------------------
    for phrase in criteria.phrases:
        clauses, values = literal_field_matches(
            phrase, case_sensitive=criteria.case_sensitive, whole_word=True)
        conditions.append(f"({' OR '.join(clauses)})")
        params.extend(values)

    # --- structured filters (shared with interactive search) -----------------
    filt_sql, filt_params = filter_predicates(
        source_ids=criteria.sources,
        side_ids=criteria.sides,
        file_types=criteria.file_types,
        path_ids=criteria.path_ids,
        date_from=criteria.date_from,
        date_to=criteria.date_to,
        date_column=_DATE_COLUMN[criteria.date_field],
        file_statuses=criteria.file_statuses,
        category_ids=criteria.categories,
        analyst_category_ids=criteria.analyst_categories,
    )
    conditions.extend(filt_sql)
    params.extend(filt_params)

    # --- curated keywords ---------------------------------------------------
    if criteria.keywords:
        if criteria.keyword_logic == "OR":
            placeholders = ",".join(["%s"] * len(criteria.keywords))
            conditions.append(
                "EXISTS (SELECT 1 FROM keywords_hashs _kh WHERE _kh.hash_id = hc.hash_id "  # nosec B608 # CANONICAL_FROM, compiler-built WHERE (fixed fragments with %s) and whitelisted ORDER BY; values are bound parameters
                f"AND _kh.keyword_id IN ({placeholders}))")
            params.extend(criteria.keywords)
        elif criteria.keyword_logic == "NOT":
            placeholders = ",".join(["%s"] * len(criteria.keywords))
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM keywords_hashs _kh WHERE _kh.hash_id = hc.hash_id "  # nosec B608 # CANONICAL_FROM, compiler-built WHERE (fixed fragments with %s) and whitelisted ORDER BY; values are bound parameters
                f"AND _kh.keyword_id IN ({placeholders}))")
            params.extend(criteria.keywords)
        else:  # AND
            for kid in criteria.keywords:
                conditions.append(
                    "EXISTS (SELECT 1 FROM keywords_hashs _kh WHERE "
                    "_kh.hash_id = hc.hash_id AND _kh.keyword_id = %s)")
                params.append(kid)

    # --- analyst scope --------------------------------------------------------
    if criteria.analyst_scope == "categorized":
        conditions.append("EXISTS (SELECT 1 FROM analyst_file_categories _afc "
                          "WHERE _afc.path_id = p.id)")
    elif criteria.analyst_scope == "uncategorized":
        conditions.append("NOT EXISTS (SELECT 1 FROM analyst_file_categories _afc "
                          "WHERE _afc.path_id = p.id)")

    # --- access scope: applied in SQL, before retrieval ----------------------
    if scope.allowed_source_ids is not None:
        if not scope.allowed_source_ids:
            conditions.append("1=0")
        else:
            placeholders = ",".join(["%s"] * len(scope.allowed_source_ids))
            conditions.append(f"hc.source_id IN ({placeholders})")
            params.extend(scope.allowed_source_ids)

    where_sql = " AND ".join(conditions) if conditions else "TRUE"

    # --- ordering: deterministic, always ends with the unique id --------------
    order_parts: List[str] = []
    for s in criteria.sort:
        if s.field == "relevance":
            notes.append("Relevance ranking exists only in interactive search; "
                         "compiled results are ordered by file date (newest "
                         "first), then id.")
            order_parts.append("p.file_date DESC")
            continue
        order_parts.append(f"{_SORT_COLUMN_PATH[s.field]} {s.direction.upper()}")
    if not any(part.startswith("p.id ") for part in order_parts):
        order_parts.append("p.id ASC")
    order_sql = ", ".join(order_parts)

    if criteria.unit == "hash":
        notes.append("unit='hash': one row per content hash, ordered by hash id "
                     "(content has no single file date or name).")
    if criteria.hide_duplicates and criteria.unit == "path":
        notes.append("hide_duplicates is honoured by unit='hash'; with unit='path' "
                     "every occurrence is a separate row.")

    return CompiledQuery(
        criteria_fingerprint=criteria.fingerprint(),
        where_sql=where_sql,
        params=tuple(params),
        order_sql=order_sql,
        unit=criteria.unit,
        notes=tuple(dict.fromkeys(notes)),
        scope=scope,
    )
