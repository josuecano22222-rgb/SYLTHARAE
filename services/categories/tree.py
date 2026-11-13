"""Category hierarchy: every read and write against ``categorys.parent_id``.

No new tables. The tree is derived with recursive CTEs over the two columns the
frozen schema already has (``categorys.parent_id`` from ``m0002``, and the name
in ``words.word``), and the cycle rule is enforced in two places: by the
database trigger (``m0004``) and, with a readable message, here.

The one query
=============

``fetch_tree`` answers with one prepared statement. For every node it returns
``depth``, the materialised ``sort_key`` (zero-padded id path) and ``id_path``,
``child_count`` (direct children), ``own_terms`` (words assigned to the node),
``descendant_count`` and ``total_terms`` (own plus every descendant's own). The
subtree totals come from an ancestor/descendant closure - one row per
(node, ancestor) pair - so the cost is ``sum(subtree size)``, not a per-node
walk, and a 100 000-node tree is measured in
``tests/integration/test_category_hierarchy_scale.py``.

Pagination is keyset on ``sort_key``: stable under concurrent inserts (a new
node sorts next to its parent, it does not shift a page), and it never asks
PostgreSQL to walk and discard ``OFFSET`` rows.

Names are unique per sibling, case-insensitively. Note what the schema does on
its own: a category name *is* a ``words`` row (``categorys.word_id``), and
``words.word`` is UNIQUE across the table, so two categories can never share a
name even in different subtrees. ``sibling_name_taken`` and
``name_in_use`` therefore report two different things - the first is the rule
this module enforces, the second is the consequence of the schema, and both are
refusals rather than silent renames.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from services.i18n.term_search import expand_for_search

logger = logging.getLogger(__name__)

#: The most rows one page may ask for.
MAX_PAGE = 500
#: Default page size for the tree endpoint.
DEFAULT_PAGE = 200
#: A node's name: trimmed, at least one visible character.
MAX_NAME = 255

#: ``sort_key`` is a dotted path of zero-padded ids, so plain text ordering is
#: tree order (parents before children, siblings by id) at any depth.
_ROW_SQL = """
WITH RECURSIVE tree AS (
    SELECT c.id, c.parent_id, w.word AS name,
           1 AS depth,
           ARRAY[c.id] AS id_path,
           ARRAY[w.word] AS name_path,
           lpad(c.id::text, 12, '0') AS sort_key
      FROM categorys c
      JOIN words w ON w.id = c.word_id
     WHERE c.parent_id IS NULL
    UNION ALL
    SELECT c.id, c.parent_id, w.word,
           p.depth + 1,
           p.id_path || c.id,
           p.name_path || w.word,
           p.sort_key || '.' || lpad(c.id::text, 12, '0')
      FROM categorys c
      JOIN words w ON w.id = c.word_id
      JOIN tree p ON c.parent_id = p.id
), terms AS (
    SELECT wc.category_id AS id, count(*) AS own_terms
      FROM words_categorys wc
     GROUP BY wc.category_id
), kids AS (
    SELECT parent_id, count(*) AS child_count
      FROM tree WHERE parent_id IS NOT NULL
     GROUP BY parent_id
), closure AS (
    SELECT id AS ancestor_id, id AS descendant_id FROM tree
    UNION ALL
    SELECT cl.ancestor_id, t.id
      FROM closure cl
      JOIN tree t ON t.parent_id = cl.descendant_id
), rolls AS (
    SELECT cl.ancestor_id AS id,
           count(*) FILTER (WHERE cl.descendant_id <> cl.ancestor_id) AS descendant_count,
           COALESCE(sum(tm.own_terms), 0) AS total_terms
      FROM closure cl
      LEFT JOIN terms tm ON tm.id = cl.descendant_id
     GROUP BY cl.ancestor_id
)
SELECT t.id, t.parent_id, t.name, t.depth, t.id_path, t.name_path, t.sort_key,
       COALESCE(k.child_count, 0)   AS child_count,
       COALESCE(tm.own_terms, 0)    AS own_terms,
       COALESCE(r.descendant_count, 0) AS descendant_count,
       COALESCE(r.total_terms, 0)   AS total_terms
  FROM tree t
  LEFT JOIN kids  k  ON k.parent_id = t.id
  LEFT JOIN terms tm ON tm.id = t.id
  LEFT JOIN rolls r  ON r.id = t.id
"""

_SEARCH_SQL = """
WITH RECURSIVE tree AS (
    SELECT c.id, c.parent_id, w.word AS name, 1 AS depth,
           ARRAY[c.id] AS id_path, lpad(c.id::text, 12, '0') AS sort_key
      FROM categorys c JOIN words w ON w.id = c.word_id
     WHERE c.parent_id IS NULL
    UNION ALL
    SELECT c.id, c.parent_id, w.word, p.depth + 1,
           p.id_path || c.id,
           p.sort_key || '.' || lpad(c.id::text, 12, '0')
      FROM categorys c
      JOIN words w ON w.id = c.word_id
      JOIN tree p ON c.parent_id = p.id
), matched AS (
    SELECT t.id, t.id_path FROM tree t WHERE t.name ILIKE %s
), keep AS (
    SELECT id FROM matched
    UNION
    SELECT unnest(id_path) FROM matched
)
SELECT t.id, t.parent_id, t.name, t.depth, t.id_path, t.name_path, t.sort_key,
       COALESCE(k.child_count, 0) AS child_count,
       COALESCE(tm.own_terms, 0) AS own_terms,
       COALESCE(r.descendant_count, 0) AS descendant_count,
       COALESCE(r.total_terms, 0) AS total_terms,
       (t.id IN (SELECT id FROM matched)) AS is_match
  FROM tree t
  LEFT JOIN kids k ON k.parent_id = t.id
  LEFT JOIN terms tm ON tm.id = t.id
  LEFT JOIN rolls r ON r.id = t.id
 WHERE t.id IN (SELECT id FROM keep)
"""

_SEARCH_CTES = """
WITH RECURSIVE tree AS (
    SELECT c.id, c.parent_id, w.word AS name, 1 AS depth,
           ARRAY[c.id] AS id_path, ARRAY[w.word] AS name_path,
           lpad(c.id::text, 12, '0') AS sort_key
      FROM categorys c JOIN words w ON w.id = c.word_id
     WHERE c.parent_id IS NULL
    UNION ALL
    SELECT c.id, c.parent_id, w.word, p.depth + 1,
           p.id_path || c.id, p.name_path || w.word,
           p.sort_key || '.' || lpad(c.id::text, 12, '0')
      FROM categorys c
      JOIN words w ON w.id = c.word_id
      JOIN tree p ON c.parent_id = p.id
), terms AS (
    SELECT wc.category_id AS id, count(*) AS own_terms
      FROM words_categorys wc GROUP BY wc.category_id
), kids AS (
    SELECT parent_id, count(*) AS child_count FROM tree
     WHERE parent_id IS NOT NULL GROUP BY parent_id
), closure AS (
    SELECT id AS ancestor_id, id AS descendant_id FROM tree
    UNION ALL
    SELECT cl.ancestor_id, t.id FROM closure cl JOIN tree t ON t.parent_id = cl.descendant_id
), rolls AS (
    SELECT cl.ancestor_id AS id,
           count(*) FILTER (WHERE cl.descendant_id <> cl.ancestor_id) AS descendant_count,
           COALESCE(sum(tm.own_terms), 0) AS total_terms
      FROM closure cl LEFT JOIN terms tm ON tm.id = cl.descendant_id
     GROUP BY cl.ancestor_id
), matched AS (
    SELECT t.id, t.id_path FROM tree t WHERE t.name ILIKE %s
), keep AS (
    SELECT id FROM matched UNION SELECT unnest(id_path) FROM matched
)
SELECT t.id, t.parent_id, t.name, t.depth, t.id_path, t.name_path, t.sort_key,
       COALESCE(k.child_count, 0) AS child_count,
       COALESCE(tm.own_terms, 0) AS own_terms,
       COALESCE(r.descendant_count, 0) AS descendant_count,
       COALESCE(r.total_terms, 0) AS total_terms,
       (t.id IN (SELECT id FROM matched)) AS is_match
  FROM tree t
  LEFT JOIN kids k ON k.parent_id = t.id
  LEFT JOIN terms tm ON tm.id = t.id
  LEFT JOIN rolls r ON r.id = t.id
 WHERE t.id IN (SELECT id FROM keep)
"""


class CategoryTreeError(Exception):
    """A refused operation, with the HTTP shape the API returns."""

    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def clean_name(raw: Any) -> str:
    """A category name: trimmed, whitespace collapsed, non-empty, bounded.

    Control characters are refused here rather than left to the driver. A NUL
    byte cannot be stored by PostgreSQL at all (psycopg2 raises while building
    the statement, which surfaced as a 500 from ``probe_malformed``), and the
    rest - bell, backspace, raw newlines - would be invisible in the tree and
    break the breadcrumb and the CSV export.
    """
    if not isinstance(raw, str):
        raise CategoryTreeError("invalid_name", "name must be a string", 400)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise CategoryTreeError(
            "invalid_characters",
            "name must not contain control characters (NUL, bell, newline, ...)",
            400)
    name = " ".join(raw.split())
    if not name:
        raise CategoryTreeError("invalid_name", "name is required", 400)
    if len(name) > MAX_NAME:
        raise CategoryTreeError(
            "invalid_name", f"name must be at most {MAX_NAME} characters", 400)
    return name


def _row_to_dict(row) -> Dict[str, Any]:
    (node_id, parent_id, name, depth, id_path, name_path, sort_key,
     child_count, own_terms, descendant_count, total_terms) = row[:11]
    is_match = row[11] if len(row) > 11 else None
    payload = {
        "id": node_id,
        "parent_id": parent_id,
        "name": name,
        "depth": depth,
        "path": " / ".join(name_path),
        "path_ids": list(id_path),
        "child_count": int(child_count),
        "own_terms": int(own_terms),
        "descendant_count": int(descendant_count),
        "total_terms": int(total_terms),
        "sort_key": sort_key,
    }
    if is_match is not None:
        payload["is_match"] = bool(is_match)
    return payload


def fetch_tree(cur, *, search: Optional[str] = None, cursor: Optional[str] = None,
               limit: int = DEFAULT_PAGE, with_total: bool = True) -> Dict[str, Any]:
    """One page of the whole tree, ordered by path.

    ``search`` returns matching nodes *and their ancestors*, each row flagged
    ``is_match``. That is what lets the tree auto-expand to a hit: the ancestors
    are already in the payload, so the client does not have to fetch them.

    The search path does **not** build the closure or the whole recursive tree:
    it finds the matches by name, then walks upwards from each one to collect
    the ancestor rows. At a hundred thousand nodes the closure version took
    3.9 s; the walk takes the size of the answer, not the size of the tree.
    """
    limit = max(1, min(int(limit), MAX_PAGE))
    needle = f"%{search.strip()}%" if search and search.strip() else None

    if needle:
        # Cross-language recall: the query may name a node in any supported
        # language, so the match set includes the words whose translation
        # normalises to it. No match leaves the predicate as it was.
        expansion = expand_for_search(search, run=_run_from_cursor(cur))
        return _search_rows(cur, needle, limit=limit, search=search,
                            word_ids=expansion.word_ids)

    base = _ROW_SQL
    where, params = "", []
    if cursor:
        where = " WHERE x.sort_key > %s"
        params.append(cursor)

    sql = f"SELECT * FROM ({base}) x{where} ORDER BY x.sort_key LIMIT %s"
    cur.execute(sql, tuple(params) + (limit + 1,))
    rows = cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_row_to_dict(row) for row in rows]
    next_cursor = items[-1]["sort_key"] if items and has_more else None

    total = None
    if with_total:
        cur.execute(f"SELECT count(*) FROM ({_ROW_SQL}) x")
        total = int(cur.fetchone()[0])

    payload = {"rows": items, "next_cursor": next_cursor, "total": total,
               "limit": limit, "search": None}
    if total is not None and total > FULL_TREE_WARN:
        payload["warning"] = (
            f"this tree has {total} categories; the whole-tree endpoint is not "
            f"meant for painting a page - use /api/categories/tree/children")
    return payload


#: How many matches a search may expand into ancestors. Beyond this the answer
#: stops being a "find a node" answer; the caller should narrow the search.
SEARCH_MATCH_CAP = 500


def _run_from_cursor(cur):
    """Adapt a psycopg2 cursor to the ``run(sql, params, fetch=...)`` shape the
    resolver expects, so the tree search can expand without opening a second
    connection (and therefore without a second transaction)."""
    def run(sql, params=None, fetch="all"):
        cur.execute(sql, params)
        return cur.fetchall()
    return run


def _search_rows(cur, needle: str, *, limit: int, search: str,
                 word_ids: Iterable[int] = ()) -> Dict[str, Any]:
    """Matches plus their ancestors, without the closure or the full recursion."""
    ids = [int(w) for w in word_ids]
    if ids:
        cur.execute(
            "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
            " WHERE w.word ILIKE %s OR c.word_id = ANY(%s)"
            " ORDER BY lower(w.word), c.id LIMIT %s",
            (needle, ids, SEARCH_MATCH_CAP + 1))
    else:
        cur.execute(
            "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
            " WHERE w.word ILIKE %s ORDER BY lower(w.word), c.id LIMIT %s",
            (needle, SEARCH_MATCH_CAP + 1))
    match_ids = [row[0] for row in cur.fetchall()]
    truncated = len(match_ids) > SEARCH_MATCH_CAP
    match_ids = match_ids[:SEARCH_MATCH_CAP]
    matches = set(match_ids)

    chain = _chain(cur, match_ids)
    children, terms = _counts_for(cur, list(chain))

    def depth_and_path(node_id):
        names, current, depth = [], node_id, 1
        while current is not None:
            parent, name = chain[current]
            names.append(name)
            if parent is None:
                break
            current = parent
            depth += 1
        return depth, list(reversed(names))

    items = []
    for node_id, (parent, name) in chain.items():
        depth, names = depth_and_path(node_id)
        items.append({
            "id": node_id, "parent_id": parent, "name": name, "depth": depth,
            "path": " / ".join(names), "path_ids": [],
            "child_count": children.get(node_id, 0),
            "own_terms": terms.get(node_id, 0),
            "descendant_count": 0, "total_terms": 0,
            "sort_key": "", "is_match": node_id in matches,
        })
    items.sort(key=lambda row: (row["path"].lower(), row["id"]))

    payload = {"rows": items[:limit], "next_cursor": None, "total": len(items),
               "limit": limit, "search": search, "matches": len(matches)}
    if truncated:
        payload["warning"] = (
            f"more than {SEARCH_MATCH_CAP} categories match; only the first "
            f"{SEARCH_MATCH_CAP} are expanded into their ancestors")
    return payload


def parse_node_id(value: Any, field: str = "id") -> int:
    """A path id as an integer, or a readable 400 (never the HTML 404 page)."""
    return _coerce_id(value, field)


def fetch_node(cur, node_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(f"SELECT * FROM ({_ROW_SQL}) x WHERE x.id = %s", (node_id,))
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def _lock_siblings(cur, parent_id: Optional[int]) -> None:
    """Serialise sibling-name decisions for one parent.

    Two concurrent creates under the same parent would otherwise both see "no
    such sibling" and both insert. The lock is held for the transaction and
    keyed on the parent, so different subtrees do not wait on each other.
    """
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"categorys:parent:{parent_id if parent_id is not None else 0}",))


def _require_node(cur, node_id: int, *, field: str = "id") -> Dict[str, Any]:
    if not isinstance(node_id, int) or isinstance(node_id, bool):
        raise CategoryTreeError("invalid_id", f"{field} must be an integer", 400)
    cur.execute("SELECT id, parent_id FROM categorys WHERE id = %s", (node_id,))
    row = cur.fetchone()
    if row is None:
        raise CategoryTreeError("not_found", f"no category with id {node_id}", 404)
    return {"id": row[0], "parent_id": row[1]}


def _sibling_with_name(cur, parent_id: Optional[int], name: str,
                       *, exclude_id: Optional[int] = None) -> Optional[int]:
    cur.execute(
        "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
        " WHERE lower(w.word) = lower(%s)"
        "   AND ((%s::int IS NULL AND c.parent_id IS NULL) OR c.parent_id = %s::int)"
        "   AND (%s::int IS NULL OR c.id <> %s::int)"
        " LIMIT 1",
        (name, parent_id, parent_id, exclude_id, exclude_id))
    row = cur.fetchone()
    return row[0] if row else None


def _word_for_new_category(cur, name: str) -> int:
    """The ``words`` row a new category will point at.

    Reuses a word that no category owns (a deleted category leaves its word
    behind) and refuses a word another category already uses: ``words.word`` and
    ``categorys.word_id`` are both UNIQUE, so the failure would otherwise surface
    as a driver error.
    """
    cur.execute("SELECT id FROM words WHERE word = %s", (name,))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (name,))
        return cur.fetchone()[0]
    word_id = row[0]
    cur.execute("SELECT id FROM categorys WHERE word_id = %s", (word_id,))
    owner = cur.fetchone()
    if owner is not None:
        raise CategoryTreeError(
            "name_in_use",
            f'a category named "{name}" already exists elsewhere (id {owner[0]}); '
            f"category names share one vocabulary and cannot repeat",
            409, existing_id=owner[0])
    return word_id


def _insert_category(cur, name: str, parent_id: Optional[int]) -> int:
    if parent_id is not None:
        _require_node(cur, parent_id, field="parent_id")
    _lock_siblings(cur, parent_id)
    clash = _sibling_with_name(cur, parent_id, name)
    if clash is not None:
        raise CategoryTreeError(
            "sibling_name_taken",
            f'this parent already has a category named "{name}" (id {clash})',
            409, existing_id=clash)
    word_id = _word_for_new_category(cur, name)
    cur.execute(
        "INSERT INTO categorys (word_id, parent_id) VALUES (%s, %s) RETURNING id",
        (word_id, parent_id))
    return cur.fetchone()[0]


def create_node(cur, *, name: Any, mode: str = "root",
                parent_id: Optional[int] = None,
                children: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    """Create a root, a child, or a node with several children at once."""
    name = clean_name(name)
    if mode not in ("root", "child"):
        raise CategoryTreeError("invalid_mode", "mode must be 'root' or 'child'", 400)
    if mode == "root":
        parent_id = None
    elif parent_id is None:
        raise CategoryTreeError("missing_parent", "a child needs a parent_id", 400)
    else:
        parent_id = _coerce_id(parent_id, "parent_id")

    child_names = []
    if children is not None:
        if isinstance(children, (str, bytes)) or not isinstance(children, Iterable):
            raise CategoryTreeError("invalid_children", "children must be a list of names", 400)
        for entry in children:
            child_names.append(clean_name(entry))
        if len(child_names) > 100:
            raise CategoryTreeError(
                "too_many_children", "at most 100 children can be created at once", 400)

    node_id = _insert_category(cur, name, parent_id)
    created_children = []
    for child_name in child_names:
        created_children.append(
            {"id": _insert_category(cur, child_name, node_id), "name": child_name})

    node = fetch_node(cur, node_id)
    return {"node": node, "children": created_children}


def _coerce_id(value: Any, field: str, *, allow_none: bool = False) -> Optional[int]:
    if value is None:
        if allow_none:
            return None
        raise CategoryTreeError("invalid_id", f"{field} is required", 400)
    if isinstance(value, bool):
        raise CategoryTreeError("invalid_id", f"{field} must be an integer", 400)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise CategoryTreeError("invalid_id", f"{field} must be an integer", 400)


def create_parent_above(cur, *, name: Any, child_ids: Any,
                        parent_id: Optional[int] = None) -> Dict[str, Any]:
    """Create a node and move the chosen categories under it, in one transaction."""
    name = clean_name(name)
    if not isinstance(child_ids, list) or not child_ids:
        raise CategoryTreeError(
            "invalid_children", "child_ids must be a non-empty list of ids", 400)
    ids = []
    for raw in child_ids:
        node_id = _coerce_id(raw, "child_ids")
        if node_id in ids:
            raise CategoryTreeError(
                "duplicate_child", f"category {node_id} is listed twice", 400)
        _require_node(cur, node_id, field="child_ids")
        ids.append(node_id)

    parent_id = None if parent_id is None else _coerce_id(parent_id, "parent_id")
    if parent_id is not None:
        if parent_id in ids:
            raise CategoryTreeError(
                "invalid_parent", "the new node cannot be placed under a category "
                                  "it is about to adopt", 400)
        _require_node(cur, parent_id, field="parent_id")

    new_id = _insert_category(cur, name, parent_id)
    for node_id in ids:
        cur.execute("UPDATE categorys SET parent_id = %s WHERE id = %s", (new_id, node_id))
    return {"node": fetch_node(cur, new_id), "moved": ids}


def _descendants(cur, node_id: int) -> List[int]:
    cur.execute(
        "WITH RECURSIVE down AS ("
        "  SELECT id FROM categorys WHERE parent_id = %s"
        "  UNION ALL"
        "  SELECT c.id FROM categorys c JOIN down d ON c.parent_id = d.id)"
        " SELECT id FROM down", (node_id,))
    return [row[0] for row in cur.fetchall()]


def move_node(cur, *, node_id: Any, parent_id: Any) -> Dict[str, Any]:
    """Re-parent one node (the subtree follows it)."""
    node_id = _coerce_id(node_id, "id")
    node = _require_node(cur, node_id)
    target = None if parent_id is None else _coerce_id(parent_id, "parent_id")
    if target is not None:
        if target == node_id:
            raise CategoryTreeError(
                "cycle", "a category cannot be moved under itself", 409)
        _require_node(cur, target, field="parent_id")

    # Readable refusal before the trigger fires: the user gets the path that
    # would have been closed, not a constraint name.
    if target is not None and target in _descendants(cur, node_id):
        raise CategoryTreeError(
            "cycle",
            f"category {target} is inside the subtree of {node_id}; "
            f"moving {node_id} under it would create a cycle",
            409)

    if target != node["parent_id"]:
        _lock_siblings(cur, target)
        _lock_siblings(cur, node["parent_id"])
        cur.execute(
            "SELECT w.word, c.id FROM categorys c JOIN words w ON w.id = c.word_id"
            " WHERE c.id = %s", (node_id,))
        name, _ = cur.fetchone()
        clash = _sibling_with_name(cur, target, name, exclude_id=node_id)
        if clash is not None:
            raise CategoryTreeError(
                "sibling_name_taken",
                f'the destination already has a category named "{name}" (id {clash})',
                409, existing_id=clash)
        cur.execute("UPDATE categorys SET parent_id = %s WHERE id = %s", (target, node_id))
    return {"node": fetch_node(cur, node_id), "previous_parent_id": node["parent_id"]}


def delete_node(cur, *, node_id: Any, mode: str = "block",
                reparent_to: Any = None) -> Dict[str, Any]:
    """Delete a node, either refusing when it has children or re-homing them."""
    node_id = _coerce_id(node_id, "id")
    node = _require_node(cur, node_id)
    if mode not in ("block", "reparent"):
        raise CategoryTreeError("invalid_mode", "mode must be 'block' or 'reparent'", 400)

    cur.execute("SELECT count(*) FROM categorys WHERE parent_id = %s", (node_id,))
    child_count = int(cur.fetchone()[0])
    if mode == "block" and child_count:
        raise CategoryTreeError(
            "has_children",
            f"category {node_id} has {child_count} child categor"
            f"{'y' if child_count == 1 else 'ies'}; move them first or delete "
            f"with mode=reparent",
            409, child_count=child_count)

    destination = node["parent_id"]
    if mode == "reparent":
        if reparent_to is not None:
            destination = _coerce_id(reparent_to, "reparent_to")
            if destination == node_id:
                raise CategoryTreeError(
                    "cycle", "children cannot be re-homed to the category being deleted", 409)
            _require_node(cur, destination, field="reparent_to")
            if destination in _descendants(cur, node_id):
                raise CategoryTreeError(
                    "cycle",
                    f"category {destination} is a descendant of {node_id}; "
                    f"re-homing there would create a cycle", 409)
        if child_count:
            _lock_siblings(cur, destination)
            cur.execute("UPDATE categorys SET parent_id = %s WHERE parent_id = %s",
                        (destination, node_id))

    try:
        cur.execute("DELETE FROM categorys WHERE id = %s", (node_id,))
    except Exception as exc:  # noqa: BLE001 - translated below
        if "foreign key" in str(exc).lower():
            raise CategoryTreeError(
                "in_use",
                f"category {node_id} is referenced by a place (geo_places.category_id); "
                f"re-assign or retire those places first",
                409) from exc
        raise
    return {"deleted": node_id, "children_moved_to": destination if child_count else None,
            "children_moved": child_count if mode == "reparent" else 0}


# ---------------------------------------------------------------------------
# Lazy loading: one level at a time
# ---------------------------------------------------------------------------
#
# The whole-tree query above is linear in the size of the tree and is built for
# the CSV export and for search. Painting a page does not need it: the browser
# only has to draw the roots, then the children of whatever is expanded. These
# helpers answer exactly that and nothing else, so the cost of the first paint
# is the cost of one level, not of the catalogue.
#
# Each row carries the two counts that are cheap for a node - how many children
# it has and how many words are assigned to it directly - and nothing that needs
# a subtree walk. ``descendant_count`` and ``total_terms`` are deliberately
# absent: computing them for the root of a hundred-thousand-node tree costs the
# whole tree, which is the thing being avoided. ``node_stats`` answers those for
# one node when the operator asks for them.

#: Above this many nodes the whole-tree endpoint says so in its payload; the
#: page shows the warning instead of pretending the export is free.
FULL_TREE_WARN = 5_000

_LEVEL_ROW_SQL = """
SELECT c.id, c.parent_id, w.word AS name
  FROM categorys c
  JOIN words w ON w.id = c.word_id
 WHERE {predicate}
 ORDER BY lower(w.word), c.id
"""


def _encode_cursor(word: str, node_id: int) -> str:
    return f"{word.lower()}|{node_id}"


def _decode_cursor(cursor: str):
    word, _, raw = cursor.rpartition("|")
    if not word or not raw.isdigit():
        raise CategoryTreeError("invalid_cursor", "cursor is not a cursor this endpoint issued", 400)
    return f"{word}|{int(raw)}", int(raw)


def _counts_for(cur, ids):
    """child_count and own_terms for a set of ids, in two grouped lookups."""
    if not ids:
        return {}, {}
    cur.execute("SELECT parent_id, count(*) FROM categorys"
                " WHERE parent_id = ANY(%s) GROUP BY parent_id", (list(ids),))
    children = {row[0]: int(row[1]) for row in cur.fetchall()}
    cur.execute("SELECT category_id, count(*) FROM words_categorys"
                " WHERE category_id = ANY(%s) GROUP BY category_id", (list(ids),))
    terms = {row[0]: int(row[1]) for row in cur.fetchall()}
    return children, terms


def _chain(cur, ids):
    """Every ancestor of *ids* as ``{id: (parent_id, name)}``.

    One recursive walk upwards; the depth of a category tree is small, so this
    costs a handful of rows instead of the closure the whole-tree query builds.
    """
    if not ids:
        return {}
    cur.execute(
        "WITH RECURSIVE up AS ("
        "  SELECT c.id, c.parent_id FROM categorys c WHERE c.id = ANY(%s)"
        "  UNION"
        "  SELECT c.id, c.parent_id FROM categorys c JOIN up ON c.id = up.parent_id)"
        " SELECT u.id, u.parent_id, w.word FROM up u"
        "   JOIN categorys c ON c.id = u.id"
        "   JOIN words w ON w.id = c.word_id", (list(ids),))
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def fetch_level(cur, *, parent_id: Optional[int], limit: int = 200,
                cursor: Optional[str] = None) -> Dict[str, Any]:
    """One level of the tree: the roots, or the children of one node."""
    limit = max(1, min(int(limit), MAX_PAGE))
    if parent_id is not None:
        _require_node(cur, parent_id, field="parent_id")

    predicate = "c.parent_id IS NULL"
    params: list = []
    if parent_id is not None:
        predicate = "c.parent_id = %s"
        params.append(parent_id)
    if cursor:
        bound_key, bound_id = _decode_cursor(cursor)
        predicate += " AND (lower(w.word), c.id) > (%s, %s)"
        params.extend([bound_key.split("|")[0], bound_id])

    cur.execute(_LEVEL_ROW_SQL.format(predicate=predicate) + " LIMIT %s",
                tuple(params) + (limit + 1,))
    rows = cur.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]

    ids = [row[0] for row in rows]
    children, terms = _counts_for(cur, ids)
    chain = _chain(cur, ids)

    built = []
    for node_id, _parent, name in rows:
        names = [name]
        current = chain[node_id][0]
        depth = 1
        while current is not None:
            parent_of_parent, parent_name = chain[current]
            names.append(parent_name)
            depth += 1
            current = parent_of_parent
        names.reverse()
        built.append({
            "id": node_id,
            "parent_id": chain[node_id][0],
            "name": name,
            "depth": depth,
            "path": " / ".join(names),
            "child_count": children.get(node_id, 0),
            "own_terms": terms.get(node_id, 0),
        })

    next_cursor = None
    if has_more and built:
        next_cursor = _encode_cursor(built[-1]["name"], built[-1]["id"])
    return {"rows": built, "next_cursor": next_cursor, "level_of": parent_id,
            "limit": limit}


def node_stats(cur, node_id: int) -> Dict[str, Any]:
    """descendant_count and total_terms for one node (one subtree walk)."""
    node = _require_node(cur, node_id)
    cur.execute(
        "WITH RECURSIVE down AS ("
        "  SELECT id FROM categorys WHERE id = %s"
        "  UNION ALL"
        "  SELECT c.id FROM categorys c JOIN down d ON c.parent_id = d.id)"
        " SELECT count(*) - 1,"
        "        (SELECT count(*) FROM words_categorys wc"
        "          WHERE wc.category_id IN (SELECT id FROM down))"
        "   FROM down", (node_id,))
    descendants, total_terms = cur.fetchone()
    children, terms = _counts_for(cur, [node_id])
    return {"id": node_id, "parent_id": node["parent_id"],
            "descendant_count": int(descendants), "total_terms": int(total_terms),
            "child_count": children.get(node_id, 0),
            "own_terms": terms.get(node_id, 0)}
