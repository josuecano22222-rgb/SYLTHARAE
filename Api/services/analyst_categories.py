"""
Analyst-Driven Manual Categorization service.

FRS: "Analyst-Driven Manual Categorization with Scoped Search Control".

This module implements the manual categorization layer. It is architecturally
and functionally INDEPENDENT from the system-generated ("smart")
classification taxonomy (``categorys`` / ``words_categorys`` / ``keywords``):

* Analyst categories live in ``analyst_categories`` and are assigned through
  ``analyst_file_categories`` - separate tables, separate namespace, separate
  UI controls. They are never merged with, or selectable from, smart-category
  controls (FR-1.4).
* A file can hold a smart category and an analyst category simultaneously;
  neither layer can overwrite the other (NFR-2).
* Every assign/remove/create action is written to ``analyst_categorization_log``
  with analyst identity, timestamp, originating search query, category and the
  affected file ids (FR-1.5).
* The search scope filter (FR-2.x) operates exclusively on analyst
  categorization status and is expressed as pure SQL EXISTS/NOT EXISTS
  fragments so it can be pushed into the existing search queries for
  performance (NFR-4). Smart categorization status is never consulted.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from Api.utils import execute_query, invalidate_query_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Search scopes (FR-2.2)
# ---------------------------------------------------------------------------

SCOPE_UNCATEGORIZED = "uncategorized"   # default (FR-2.1)
SCOPE_CATEGORIZED = "categorized"       # analyst-categorized files only
SCOPE_ALL = "all"                       # entire dataset

VALID_SCOPES = (SCOPE_UNCATEGORIZED, SCOPE_CATEGORIZED, SCOPE_ALL)
DEFAULT_SCOPE = SCOPE_UNCATEGORIZED

# Session key used to remember the analyst's last-selected scope (FR-2.3).
SCOPE_SESSION_KEY = "analyst_search_scope"


# One LIKE-escaping implementation for the whole product (core/criteria/sql.py).
from core.criteria.sql import escape_like as _escape_like  # noqa: E402
from core.criteria.model import normalize_analyst_scope as _normalize_analyst_scope  # noqa: E402


def normalize_scope(scope: Optional[str]) -> str:
    """Return a validated search scope, defaulting to uncategorized (FR-2.1).

    Delegates to the shared normaliser so search, saved-search mapping,
    reports and monitoring can never disagree about what a scope means.
    """
    return _normalize_analyst_scope(scope)


def scope_condition(scope: Optional[str], alias: str = "p") -> Optional[str]:
    """SQL fragment implementing the analyst-categorization scope filter.

    The condition references ONLY ``analyst_file_categories`` - smart
    categorization status is deliberately never consulted (FR-2.4). The
    returned fragment contains no parameters, so callers can splice it into
    their WHERE clauses without touching the parameter list (NFR-4: the
    EXISTS probe is served by ``idx_afc_path_id``).

    Returns ``None`` for the "all files" scope (no filtering).
    """
    scope = normalize_scope(scope)
    if scope == SCOPE_ALL:
        return None
    if scope == SCOPE_CATEGORIZED:
        return (
            f"EXISTS (SELECT 1 FROM analyst_file_categories _afc "
            f"WHERE _afc.path_id = {alias}.id)"
        )
    # Default: uncategorized files only (FR-2.1)
    return (
        f"NOT EXISTS (SELECT 1 FROM analyst_file_categories _afc "
        f"WHERE _afc.path_id = {alias}.id)"
    )


class AnalystCategoryService:
    """All analyst-categorization persistence and query logic."""

    # ------------------------------------------------------------------
    # Category management (analyst-defined labels only)
    # ------------------------------------------------------------------

    @staticmethod
    def list_categories(include_counts: bool = True) -> List[Dict[str, Any]]:
        """List analyst-defined categories. Never touches smart categories."""
        try:
            if include_counts:
                rows = execute_query(
                    """
                    SELECT ac.id, ac.name, ac.description, ac.color,
                           COUNT(afc.id) AS file_count,
                           MAX(afc.assigned_at) AS last_assigned_at
                    FROM analyst_categories ac
                    LEFT JOIN analyst_file_categories afc ON afc.category_id = ac.id
                    GROUP BY ac.id
                    ORDER BY LOWER(ac.name)
                    """,
                    fetch="all",
                )
            else:
                rows = execute_query(
                    """
                    SELECT ac.id, ac.name, ac.description, ac.color,
                           0, NULL
                    FROM analyst_categories ac
                    ORDER BY LOWER(ac.name)
                    """,
                    fetch="all",
                )
            categories = []
            for row in rows or []:
                categories.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "color": row[3],
                    "file_count": int(row[4] or 0),
                    "last_assigned_at": row[5].isoformat() if row[5] else None,
                })
            return categories
        except Exception as e:
            logger.error(f"Error listing analyst categories: {e}", exc_info=True)
            return []

    @staticmethod
    def get_category(category_id: int) -> Optional[Dict[str, Any]]:
        try:
            row = execute_query(
                """
                SELECT ac.id, ac.name, ac.description, ac.color,
                       (SELECT COUNT(*) FROM analyst_file_categories afc
                        WHERE afc.category_id = ac.id) AS file_count
                FROM analyst_categories ac
                WHERE ac.id = %s
                """,
                (category_id,),
                fetch="one",
            )
            if not row:
                return None
            return {
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "color": row[3],
                "file_count": int(row[4] or 0),
            }
        except Exception as e:
            logger.error(f"Error getting analyst category {category_id}: {e}", exc_info=True)
            return None

    @staticmethod
    def create_category(
        name: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
        user: Optional[Any] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Create an analyst-defined category (FR-1.3 'create at assignment').

        Returns (category_id, error). ``user`` may be a core.security User or
        None; the action is audit-logged either way (FR-1.5).
        """
        name = (name or "").strip()
        if not name:
            return None, "Category name is required"
        if len(name) > 255:
            return None, "Category name must be at most 255 characters"
        if color is not None:
            color = color.strip() or None

        user_id = getattr(user, "id", None)

        try:
            existing = execute_query(
                "SELECT id FROM analyst_categories WHERE LOWER(name) = LOWER(%s)",
                (name,),
                fetch="one",
            )
            if existing:
                return None, f'Analyst category "{name}" already exists'

            row = execute_query(
                """
                INSERT INTO analyst_categories (name, description, color, created_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (name, description, color, user_id),
                fetch="one",
            )
            category_id = row[0] if row else None
            if category_id is None:
                return None, "Failed to create analyst category"

            # AUDIT (cache): the scoped search paths (e.g.
            # get_optimized_search_results) serve their main query through
            # the shared query cache. Without invalidation a newly created
            # category (and, more importantly, new assignments) would keep
            # serving stale result sets for up to the cache TTL.
            invalidate_query_cache()

            AnalystCategoryService._log_action(
                action="category_created",
                user=user,
                category_id=category_id,
                category_name=name,
                source_query=None,
                path_ids=[],
            )
            return category_id, None
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                return None, f'Analyst category "{name}" already exists'
            logger.error(f"Error creating analyst category: {e}", exc_info=True)
            return None, "Failed to create analyst category"

    @staticmethod
    def update_category(
        category_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Rename / edit an analyst category. Smart categories are untouched."""
        try:
            existing = AnalystCategoryService.get_category(category_id)
            if not existing:
                return False, "Analyst category not found"

            sets: List[str] = []
            params: List[Any] = []
            if name is not None:
                name = name.strip()
                if not name:
                    return False, "Category name is required"
                sets.append("name = %s")
                params.append(name)
            if description is not None:
                sets.append("description = %s")
                params.append(description)
            if color is not None:
                sets.append("color = %s")
                params.append(color.strip() or None)

            if not sets:
                return True, None

            params.append(category_id)
            execute_query(
                f"UPDATE analyst_categories SET {', '.join(sets)} WHERE id = %s",
                tuple(params),
                fetch=None,
            )
            # Category renames surface in filter dropdowns / result badges
            # that may be cached - see create_category.
            invalidate_query_cache()
            return True, None
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                return False, "An analyst category with this name already exists"
            logger.error(f"Error updating analyst category {category_id}: {e}", exc_info=True)
            return False, "Failed to update analyst category"

    @staticmethod
    def delete_category(category_id: int) -> Tuple[bool, Optional[str]]:
        """Delete an analyst category; its assignments cascade away.

        Files simply return to analyst-uncategorized status for scope
        purposes (FR-2.1 / NFR-3). Smart categories are unaffected.
        """
        try:
            existing = AnalystCategoryService.get_category(category_id)
            if not existing:
                return False, "Analyst category not found"
            execute_query(
                "DELETE FROM analyst_categories WHERE id = %s",
                (category_id,),
                fetch=None,
            )
            # Deleting removes assignments -> scope eligibility of the
            # affected files changes; cached scoped searches must not keep
            # the stale view (see create_category).
            invalidate_query_cache()
            return True, None
        except Exception as e:
            logger.error(f"Error deleting analyst category {category_id}: {e}", exc_info=True)
            return False, "Failed to delete analyst category"

    # ------------------------------------------------------------------
    # Assignment / removal (FR-1.3, NFR-3)
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_path_ids(path_ids: Sequence[Any]) -> List[int]:
        cleaned: List[int] = []
        seen = set()
        for value in path_ids or []:
            try:
                pid = int(value)
            except (TypeError, ValueError):
                continue
            if pid > 0 and pid not in seen:
                seen.add(pid)
                cleaned.append(pid)
        return cleaned

    @staticmethod
    def assign(
        path_ids: Sequence[int],
        category_id: Optional[int] = None,
        category_name: Optional[str] = None,
        create_category: bool = False,
        source_query: Optional[str] = None,
        user: Optional[Any] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Assign one analyst category to a set of files (FR-1.3, FR-1.5).

        ``source_query`` is the search query that surfaced the files
        (traceability, FR-1.5). If ``create_category`` is true and
        ``category_name`` is given, the category is created at the point of
        assignment.

        Returns (result dict, error).
        """
        cleaned_ids = AnalystCategoryService._clean_path_ids(path_ids)
        if not cleaned_ids:
            return None, "No valid file ids supplied"
        if not category_id and not category_name:
            return None, "A category must be selected or named"

        user_id = getattr(user, "id", None)
        username = getattr(user, "username", None)

        # Optionally create the category at the point of assignment
        if not category_id and category_name:
            if not create_category:
                existing = execute_query(
                    "SELECT id FROM analyst_categories WHERE LOWER(name) = LOWER(%s)",
                    (category_name.strip(),),
                    fetch="one",
                )
                if existing:
                    category_id = existing[0]
                else:
                    return None, f'Analyst category "{category_name}" does not exist'
            else:
                category_id, err = AnalystCategoryService.create_category(
                    name=category_name, user=user
                )
                if err:
                    return None, err

        category = AnalystCategoryService.get_category(category_id)
        if not category:
            return None, "Analyst category not found"

        try:
            placeholders = ",".join(["%s"] * len(cleaned_ids))
            rows = execute_query(
                f"""
                INSERT INTO analyst_file_categories
                    (path_id, category_id, assigned_by, assigned_by_username, source_query)
                SELECT pid, %s, %s, %s, %s
                FROM unnest(ARRAY[{placeholders}]::INTEGER[]) AS pid
                ON CONFLICT (path_id, category_id) DO NOTHING
                RETURNING path_id
                """,
                tuple(
                    [category_id, user_id, username, source_query]
                    + cleaned_ids
                ),
                fetch="all",
            )
            inserted = [r[0] for r in (rows or [])]

            # Scope correctness: assigning an analyst category changes the
            # file's eligibility for the default ("uncategorized") search
            # scope. The scoped search paths serve cached result sets, so
            # without invalidation a just-categorized file could keep
            # appearing under the uncategorized scope for up to the cache
            # TTL (5 minutes by default).
            invalidate_query_cache()

            AnalystCategoryService._log_action(
                action="assign",
                user=user,
                category_id=category_id,
                category_name=category["name"],
                source_query=source_query,
                path_ids=inserted,
            )

            return (
                {
                    "category_id": category_id,
                    "category_name": category["name"],
                    "requested": len(cleaned_ids),
                    "assigned": len(inserted),
                    "already_assigned": len(cleaned_ids) - len(inserted),
                    "path_ids": inserted,
                },
                None,
            )
        except Exception as e:
            logger.error(f"Error assigning analyst category: {e}", exc_info=True)
            return None, "Failed to assign analyst category"

    @staticmethod
    def remove(
        path_ids: Sequence[int],
        category_ids: Optional[Sequence[int]] = None,
        source_query: Optional[str] = None,
        user: Optional[Any] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Remove analyst categories from files (NFR-3 - reversibility).

        With ``category_ids`` only those categories are removed; without it
        ALL analyst categories are cleared from the given files. Removing an
        assignment returns the file to "uncategorized" for search-scope
        purposes. Smart categories are never touched (NFR-2).
        """
        cleaned_ids = AnalystCategoryService._clean_path_ids(path_ids)
        if not cleaned_ids:
            return None, "No valid file ids supplied"

        clean_cat_ids = AnalystCategoryService._clean_path_ids(category_ids or [])

        try:
            path_placeholders = ",".join(["%s"] * len(cleaned_ids))
            params: List[Any] = list(cleaned_ids)

            if clean_cat_ids:
                cat_placeholders = ",".join(["%s"] * len(clean_cat_ids))
                query = f"""
                    DELETE FROM analyst_file_categories
                    WHERE path_id IN ({path_placeholders})
                      AND category_id IN ({cat_placeholders})
                    RETURNING path_id, category_id
                """
                params.extend(clean_cat_ids)
            else:
                query = f"""
                    DELETE FROM analyst_file_categories
                    WHERE path_id IN ({path_placeholders})
                    RETURNING path_id, category_id
                """

            rows = execute_query(query, tuple(params), fetch="all")
            removed = rows or []

            # Scope correctness: removal restores uncategorized eligibility
            # immediately - cached scoped searches must not serve the stale
            # view (see assign).
            invalidate_query_cache()

            affected_paths = sorted({r[0] for r in removed})
            AnalystCategoryService._log_action(
                action="remove",
                user=user,
                category_id=clean_cat_ids[0] if len(clean_cat_ids) == 1 else None,
                category_name=None,
                source_query=source_query,
                path_ids=affected_paths,
            )

            return (
                {
                    "requested_files": len(cleaned_ids),
                    "removed_assignments": len(removed),
                    "affected_paths": affected_paths,
                },
                None,
            )
        except Exception as e:
            logger.error(f"Error removing analyst categories: {e}", exc_info=True)
            return None, "Failed to remove analyst categories"

    # ------------------------------------------------------------------
    # Audit log (FR-1.5)
    # ------------------------------------------------------------------

    @staticmethod
    def _log_action(
        action: str,
        user: Optional[Any],
        category_id: Optional[int],
        category_name: Optional[str],
        source_query: Optional[str],
        path_ids: Sequence[int],
    ) -> None:
        """Write one audit row. Failures never break the main action."""
        try:
            import json

            execute_query(
                """
                INSERT INTO analyst_categorization_log
                    (analyst_id, analyst_username, action, category_id,
                     category_name, source_query, path_ids, path_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    getattr(user, "id", None),
                    getattr(user, "username", None),
                    action,
                    category_id,
                    category_name,
                    source_query,
                    json.dumps([int(p) for p in path_ids]),
                    len(path_ids),
                ),
                fetch=None,
            )
        except Exception as e:
            logger.error(f"Failed to write analyst categorization audit log: {e}")

    @staticmethod
    def list_log(
        action: Optional[str] = None,
        analyst_id: Optional[int] = None,
        category_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Browse the audit trail (FR-1.5 / FR-4.2 traceability)."""
        try:
            conditions = []
            params: List[Any] = []
            if action:
                conditions.append("action = %s")
                params.append(action)
            if analyst_id:
                conditions.append("analyst_id = %s")
                params.append(analyst_id)
            if category_id:
                conditions.append("category_id = %s")
                params.append(category_id)
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

            total_row = execute_query(
                f"SELECT COUNT(*) FROM analyst_categorization_log{where}",
                tuple(params),
                fetch="one",
            )
            total = total_row[0] if total_row else 0

            rows = execute_query(
                f"""
                SELECT id, analyst_id, analyst_username, action, category_id,
                       category_name, source_query, path_ids, path_count, created_at
                FROM analyst_categorization_log{where}
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [limit, offset]),
                fetch="all",
            )

            entries = []
            for row in rows or []:
                entries.append({
                    "id": row[0],
                    "analyst_id": row[1],
                    "analyst_username": row[2],
                    "action": row[3],
                    "category_id": row[4],
                    "category_name": row[5],
                    "source_query": row[6],
                    "path_ids": row[7] if isinstance(row[7], list) else [],
                    "path_count": row[8],
                    "created_at": row[9].isoformat() if row[9] else None,
                })
            return entries, total
        except Exception as e:
            logger.error(f"Error listing analyst categorization log: {e}", exc_info=True)
            return [], 0

    # ------------------------------------------------------------------
    # Analyst Categorization View (FR-4)
    # ------------------------------------------------------------------

    @staticmethod
    def list_analysts() -> List[Dict[str, Any]]:
        """Distinct analysts who have performed at least one assignment."""
        try:
            rows = execute_query(
                """
                SELECT afc.assigned_by, MAX(afc.assigned_by_username), COUNT(*)
                FROM analyst_file_categories afc
                GROUP BY afc.assigned_by
                ORDER BY 2 ASC
                """,
                fetch="all",
            )
            analysts = []
            for row in rows or []:
                analysts.append({
                    "id": row[0],
                    "username": row[1] or f"analyst #{row[0]}" if row[0] else "unknown",
                    "assignment_count": int(row[2] or 0),
                })
            return analysts
        except Exception as e:
            logger.error(f"Error listing analysts: {e}", exc_info=True)
            return []

    @staticmethod
    def list_assignments(
        category_id: Optional[int] = None,
        analyst_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        query: Optional[str] = None,
        file_query: Optional[str] = None,
        file_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Filtered, paginated assignment rows for the Analyst View (FR-4.2).

        Filters: analyst category, analyst, date range, originating search
        query text, (optionally) file name/path text, and an exact
        ``file_id`` (used by the content display surfaces to reflect the
        categories of the file being read).
        """
        try:
            conditions = []
            params: List[Any] = []

            if category_id:
                conditions.append("afc.category_id = %s")
                params.append(category_id)
            if analyst_id:
                conditions.append("afc.assigned_by = %s")
                params.append(analyst_id)
            if file_id:
                conditions.append("afc.path_id = %s")
                params.append(file_id)
            if date_from:
                conditions.append("afc.assigned_at >= %s::date")
                params.append(date_from)
            if date_to:
                conditions.append("afc.assigned_at < (%s::date + INTERVAL '1 day')")
                params.append(date_to)
            if query:
                conditions.append("afc.source_query ILIKE %s")
                params.append(f"%{_escape_like(query)}%")
            if file_query:
                conditions.append("(p.file_name ILIKE %s OR p.file_path ILIKE %s)")
                params.extend([f"%{_escape_like(file_query)}%", f"%{_escape_like(file_query)}%"])

            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

            total_row = execute_query(
                f"""
                SELECT COUNT(*)
                FROM analyst_file_categories afc
                JOIN analyst_categories ac ON ac.id = afc.category_id
                LEFT JOIN paths p ON p.id = afc.path_id
                {where}
                """,
                tuple(params),
                fetch="one",
            )
            total = total_row[0] if total_row else 0

            rows = execute_query(
                f"""
                SELECT afc.id, afc.path_id, p.file_name, p.file_path, p.file_type,
                       ac.id, ac.name, ac.color,
                       afc.assigned_by, afc.assigned_by_username,
                       afc.source_query, afc.assigned_at,
                       src.name, sd.name
                FROM analyst_file_categories afc
                JOIN analyst_categories ac ON ac.id = afc.category_id
                LEFT JOIN paths p ON p.id = afc.path_id
                LEFT JOIN hash_contexts hc ON hc.id = p.context_id
                LEFT JOIN sources src ON src.id = hc.source_id
                LEFT JOIN sides sd ON sd.id = hc.side_id
                {where}
                ORDER BY afc.assigned_at DESC, afc.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [limit, offset]),
                fetch="all",
            )

            assignments = []
            for row in rows or []:
                assignments.append({
                    "id": row[0],
                    "path_id": row[1],
                    "file_name": row[2] or "(missing file)",
                    "file_path": row[3] or "",
                    "file_type": row[4] or "",
                    "category_id": row[5],
                    "category_name": row[6],
                    "category_color": row[7],
                    "assigned_by": row[8],
                    "assigned_by_username": row[9] or "unknown",
                    "source_query": row[10],
                    "assigned_at": row[11].isoformat() if row[11] else None,
                    # Where the file came from: the source and side of its
                    # content identity (paths -> hash_contexts -> sources/sides).
                    "source_name": row[12],
                    "side_name": row[13],
                })
            return assignments, total
        except Exception as e:
            logger.error(f"Error listing analyst assignments: {e}", exc_info=True)
            return [], 0

    @staticmethod
    def get_stats() -> Dict[str, Any]:
        """Summary counts for the Analyst Categorization View header."""
        try:
            row = execute_query(
                """
                SELECT
                    (SELECT COUNT(*) FROM analyst_categories) AS category_count,
                    (SELECT COUNT(*) FROM analyst_file_categories) AS assignment_count,
                    (SELECT COUNT(DISTINCT path_id) FROM analyst_file_categories) AS categorized_files,
                    (SELECT COUNT(*) FROM paths) AS total_files,
                    (SELECT COUNT(DISTINCT assigned_by) FROM analyst_file_categories
                        WHERE assigned_by IS NOT NULL) AS analyst_count
                """,
                fetch="one",
            )
            return {
                "category_count": int(row[0] or 0) if row else 0,
                "assignment_count": int(row[1] or 0) if row else 0,
                "categorized_files": int(row[2] or 0) if row else 0,
                "total_files": int(row[3] or 0) if row else 0,
                "analyst_count": int(row[4] or 0) if row else 0,
            }
        except Exception as e:
            logger.error(f"Error computing analyst categorization stats: {e}", exc_info=True)
            return {
                "category_count": 0,
                "assignment_count": 0,
                "categorized_files": 0,
                "total_files": 0,
                "analyst_count": 0,
            }

    # ------------------------------------------------------------------
    # Search-result enrichment (display separation, FR-1.4)
    # ------------------------------------------------------------------

    @staticmethod
    def categories_for_files(path_ids: Sequence[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Map path_id -> [{id, name, color}] for the given files.

        Reads ONLY analyst tables. Used to decorate search results and file
        detail views so analyst categories render in their own, visibly
        separate namespace - never merged with smart categories.
        """
        cleaned = AnalystCategoryService._clean_path_ids(path_ids)
        if not cleaned:
            return {}
        try:
            placeholders = ",".join(["%s"] * len(cleaned))
            rows = execute_query(
                f"""
                SELECT afc.path_id, ac.id, ac.name, ac.color
                FROM analyst_file_categories afc
                JOIN analyst_categories ac ON ac.id = afc.category_id
                WHERE afc.path_id IN ({placeholders})
                ORDER BY LOWER(ac.name)
                """,
                tuple(cleaned),
                fetch="all",
            )
            mapping: Dict[int, List[Dict[str, Any]]] = {}
            for row in rows or []:
                mapping.setdefault(row[0], []).append({
                    "id": row[1],
                    "name": row[2],
                    "color": row[3],
                })
            return mapping
        except Exception as e:
            logger.error(f"Error fetching analyst categories for files: {e}", exc_info=True)
            return {}

    @staticmethod
    def attach_categories_to_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add an ``analyst_categories`` key to each search-result dict.

        The smart taxonomy, when present on a result, keeps its own key
        (``categories``); the two never merge (FR-1.4).
        """
        if not results:
            return results
        ids = [r.get("id") for r in results if r.get("id") is not None]
        mapping = AnalystCategoryService.categories_for_files(ids)
        for result in results:
            cats = mapping.get(result.get("id"), [])
            result["analyst_categories"] = [c["name"] for c in cats]
            result["analyst_category_details"] = cats
        return results

    # ------------------------------------------------------------------
    # Export (FR-1.4 - independent reporting/export fields)
    # ------------------------------------------------------------------

    #: Allowlisted CSV columns: ``(header, row accessor key or callable)``.
    ANALYST_CSV_COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("assignment_id", "id"),
        ("file_id", "path_id"),
        ("file_name", "file_name"),
        ("file_path", "file_path"),
        ("file_type", "file_type"),
        ("source", "source_name"),
        ("side", "side_name"),
        ("analyst_category", "category_name"),
        ("analyst_category_id", "category_id"),
        ("category_color", "category_color"),
        ("assigned_by", "assigned_by_username"),
        ("assigned_by_id", "assigned_by"),
        ("assigned_at", "assigned_at"),
        ("originating_search_query", "source_query"),
    )

    @staticmethod
    def resolve_export_columns(requested: Optional[str]) -> List[Tuple[str, str]]:
        """Validate and order CSV columns from a comma-separated key list."""
        allowed = {key: key for key, _field in AnalystCategoryService.ANALYST_CSV_COLUMNS}
        order = [key for key, _field in AnalystCategoryService.ANALYST_CSV_COLUMNS]
        if not requested or not str(requested).strip():
            return list(AnalystCategoryService.ANALYST_CSV_COLUMNS)
        picked: List[str] = []
        for raw in str(requested).split(","):
            key = raw.strip()
            if not key:
                continue
            if key not in allowed:
                raise ValueError(f"Unknown export column: {key}")
            if key not in picked:
                picked.append(key)
        if not picked:
            raise ValueError("Choose at least one export column")
        field_by_key = dict(AnalystCategoryService.ANALYST_CSV_COLUMNS)
        return [(key, field_by_key[key]) for key in order if key in picked]

    @staticmethod
    def export_cell(row: Dict[str, Any], field: str) -> Any:
        return row.get(field)

    @staticmethod
    def export_rows(
        category_id: Optional[int] = None,
        analyst_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        query: Optional[str] = None,
        file_query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Flat rows for CSV export of the Analyst Categorization View."""
        assignments, _total = AnalystCategoryService.list_assignments(
            category_id=category_id,
            analyst_id=analyst_id,
            date_from=date_from,
            date_to=date_to,
            query=query,
            file_query=file_query,
            limit=100000,
            offset=0,
        )
        return assignments
