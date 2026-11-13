"""
Lineage Service - reconstruct parent/child relationships for a stored file.

Every extracted object (archive member, email attachment, embedded document,
OCR derivative) is stored as its own ``paths`` row and linked to the object it
came from through ``paths.parent_path_id`` (migration 0007). This module turns
that into the ancestry and descendant sets the File Details view needs.

Two rules this module exists to enforce:

* The relationship is read from ``parent_path_id``, never inferred from
  filenames or filesystem paths. Two members of different archives can share a
  name; only the stored ids identify which object a derivative came from.
* ``hierarchy_path`` is returned as a *rendering* of the chain, not used to
  build it. If the text and the ids disagree, the ids win.

Both queries are depth-bounded. ``MAX_RECURSION_DEPTH`` in the router already
caps nesting at ingest, but the bound here is independent of that: a cycle
written by a bug or a restored backup must not hang the request.
"""

import logging
from typing import Any, Dict, List, Optional

from Api.utils import execute_query

logger = logging.getLogger(__name__)

#: Guard for the recursive walks. Generous against the ingest-time cap of 5,
#: but finite, so corrupt parent links cannot make the query run unbounded.
MAX_LINEAGE_DEPTH = 20

#: Hard cap on the descendants returned. A large archive can legitimately hold
#: tens of thousands of members; File Details shows a page, not all of them.
MAX_DESCENDANTS = 500


def get_ancestors(file_id: int, limit: int = MAX_LINEAGE_DEPTH) -> List[Dict[str, Any]]:
    """Walk ``parent_path_id`` upward from ``file_id``.

    Returns the chain ordered root-first, so index 0 is the outermost container
    and the last element is the immediate parent. ``depth`` counts the hops
    *from* ``file_id``, so the immediate parent has depth 1.

    An empty list means this row has no recorded parent - it is a top-level
    input. That is a normal state, not an error.
    """
    rows = execute_query(
        """
        WITH RECURSIVE lineage AS (
            SELECT id, file_name, parent_path_id, hierarchy_path, 0 AS depth
            FROM paths
            WHERE id = %s
            UNION ALL
            SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,
                   l.depth + 1
            FROM paths p
            JOIN lineage l ON l.parent_path_id = p.id
            WHERE l.depth < %s
        )
        SELECT id, file_name, hierarchy_path, depth
        FROM lineage
        WHERE depth > 0
        ORDER BY depth DESC
        """,
        (file_id, limit),
        fetch="all",
    ) or []
    return [
        {
            "id": row[0],
            "name": row[1],
            "hierarchy_path": row[2],
            "depth": row[3],
        }
        for row in rows
    ]


def get_descendants(file_id: int, limit: int = MAX_DESCENDANTS) -> List[Dict[str, Any]]:
    """Walk ``parent_path_id`` downward from ``file_id``.

    Returns every descendant, not just direct children, ordered shallow-first
    then by id so siblings appear in ingest order. ``depth`` 1 is a direct
    child.
    """
    rows = execute_query(
        """
        WITH RECURSIVE tree AS (
            SELECT id, file_name, parent_path_id, hierarchy_path, 1 AS depth
            FROM paths
            WHERE parent_path_id = %s
            UNION ALL
            SELECT p.id, p.file_name, p.parent_path_id, p.hierarchy_path,
                   t.depth + 1
            FROM paths p
            JOIN tree t ON p.parent_path_id = t.id
            WHERE t.depth < %s
        )
        SELECT id, file_name, parent_path_id, hierarchy_path, depth
        FROM tree
        ORDER BY depth, id
        LIMIT %s
        """,
        (file_id, MAX_LINEAGE_DEPTH, limit),
        fetch="all",
    ) or []
    return [
        {
            "id": row[0],
            "name": row[1],
            "parent_path_id": row[2],
            "hierarchy_path": row[3],
            "depth": row[4],
        }
        for row in rows
    ]


def get_file_lineage(file_id: int) -> Dict[str, Any]:
    """Full lineage block for File Details.

    Never raises: a failed lineage query must not take the whole File Details
    view down. The failure is logged and the block reports what it could not
    load, so the UI can say "lineage unavailable" instead of silently showing a
    file that appears to have no origin.
    """
    ancestors: List[Dict[str, Any]] = []
    descendants: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        ancestors = get_ancestors(file_id)
    except Exception as exc:  # noqa: BLE001 - boundary: report, do not propagate
        logger.warning("Could not load ancestors for file_id=%s: %s", file_id, exc)
        errors.append("ancestors")

    try:
        descendants = get_descendants(file_id)
    except Exception as exc:  # noqa: BLE001 - boundary: report, do not propagate
        logger.warning("Could not load descendants for file_id=%s: %s", file_id, exc)
        errors.append("descendants")

    return {
        "parent_path_id": ancestors[-1]["id"] if ancestors else None,
        "immediate_parent": ancestors[-1] if ancestors else None,
        "root": ancestors[0] if ancestors else None,
        "ancestors": ancestors,
        "descendants": descendants,
        "depth_from_root": len(ancestors),
        "descendant_count": len(descendants),
        "is_top_level": not ancestors,
        "errors": errors,
    }


def normalise_provenance(raw: Any) -> Optional[Dict[str, Any]]:
    """Coerce ``paths.extraction_provenance`` to a dict or None.

    psycopg2 decodes ``jsonb`` to a dict, but a driver configured with a raw
    loader, a restored backup, or a NULL column can each produce a different
    type. Returning None rather than a half-parsed value keeps the frontend
    from rendering the string "None" as though it were a provenance record.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            import json

            parsed = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("extraction_provenance is not valid JSON: %r", raw[:200])
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
