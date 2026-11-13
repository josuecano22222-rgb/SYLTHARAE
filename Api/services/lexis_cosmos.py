"""LEXIS Cosmos - the service layer behind ``/lexis``.

The interface is a galaxy view over the *existing* taxonomy and keyword index:

    root -> lens (words | keywords) -> main category -> subcategory
         -> term -> file

Nothing here invents a second taxonomy. Reads go through the statements in
:mod:`Api.services.lexis_queries`; writes go through the same statements the
Manage modal needs, and every write is recorded in ``audit_log`` (Q11.2) so the
Activity tab and the existing audit viewer agree.

Design notes
------------

**Node identifiers.** The UI addresses a node with a short string, exactly as
the specification does::

    root
    lens:words                    lens:keywords
    cat:words:<category_id>       cat:keywords:<category_id>
    sub:words:<category_id>       sub:keywords:<category_id>
    term:w_<word_id>              term:k_<keyword_id>
    file:<path_id>

``parse_node`` is the only place that string is interpreted, so a malformed id
is rejected once instead of in every endpoint.

**Scope resolution.** The sidebar search and every export take a *scope*: the
basket when it is non-empty, otherwise the focused node. ``resolve_scope``
turns that into ``(word_ids, keyword_ids)``; ``None`` means "this lens is not
part of the scope", an empty list means "part of the scope, but empty". That
distinction matters: Q4.2 matches only hashes carrying at least one of the
supplied ids, and a lens excluded on purpose must not accidentally become
"all words".

**Keyword expressions are packed blobs.** ``keywords.keyword`` is a packed list
of ``words.id`` values (see ``core.serialization``), not text. Display text is
rebuilt through Q2.8 and cached per request. Keyword *writes* accept a phrase,
upsert its words through Q8.1 and pack the ids - the same representation the
ingestion pipeline writes, so both sides keep matching.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2.extras

from core.criteria import CRITERIA_SCHEMA_VERSION, CriteriaError, from_legacy_search
from core.serialization import pack_int_list, unpack_int_list

from Api.services import lexis_queries as Q

logger = logging.getLogger(__name__)

#: File types accepted by the ingestion pipeline, reused so the LEXIS filter
#: offers the same vocabulary the data was written with.
SORT_KEYS = ("hits", "date", "name", "type")
SORT_DIRECTIONS = ("asc", "desc")
LENSES = ("words", "keywords")
MAX_PAGE_SIZE = 500
#: Farthest page the offset query will look at (see core.table_paging.MAX_PAGE).
MAX_PAGE_PAGES = 1_000_000
MAX_FILES_PER_TERM_NODE = 250
MAX_BASKET_TERMS = 400

#: Node id grammar (see the module docstring).
NODE_PATTERNS = (
    ("lens", re.compile(r"^lens:(words|keywords)$")),
    ("cat", re.compile(r"^cat:(words|keywords):(\d+)$")),
    ("sub", re.compile(r"^sub:(words|keywords):(\d+)$")),
    ("term", re.compile(r"^term:([wk])_(\d+)$")),
    ("file", re.compile(r"^file:(\d+)$")),
    ("root", re.compile(r"^root$")),
)


class LexisError(ValueError):
    """A request the service refuses, with a message safe to show a user."""


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Convert a psycopg2 value into something ``jsonify`` accepts."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        # Keyword blobs are packed int lists: never expose raw bytes to the UI.
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: _json_safe(value) for key, value in dict(row).items()}


def _int_list(value: Any, *, upper: int = MAX_BASKET_TERMS) -> Optional[List[int]]:
    """A validated int array parameter (``None`` stays ``NULL`` in SQL)."""
    if value is None:
        return None
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise LexisError("expected a list of identifiers")
    out: List[int] = []
    for item in value:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            raise LexisError("identifiers must be whole numbers")
        if parsed not in out:
            out.append(parsed)
    if len(out) > upper:
        raise LexisError("too many identifiers in one request")
    return out


def _str_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise LexisError("expected a list of values")
    out = [str(item).strip() for item in value if str(item).strip()]
    return out or None


def _date_param(value: Any, label: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise LexisError("%s must be a date (YYYY-MM-DD)" % label)
    return text


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

def _default_connection():
    from Api.utils import get_connection

    return get_connection()


class LexisCosmosService:
    """Every read and write the LEXIS Cosmos interface performs."""

    def __init__(self, connection_factory: Optional[Callable] = None):
        self._connection = connection_factory or _default_connection

    # -- plumbing ---------------------------------------------------------

    def _read(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params or {})
                    return [dict(row) for row in cur.fetchall()]
            finally:
                try:
                    conn.rollback()
                except Exception:  # pragma: no cover - connection already gone
                    pass

    def _read_one(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        rows = self._read(sql, params)
        return rows[0] if rows else None

    def _write(self, statements: Sequence[Tuple[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Run statements in one transaction; return the RETURNING rows."""
        results: List[Dict[str, Any]] = []
        with self._connection() as conn:
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    for sql, params in statements:
                        cur.execute(sql, params or {})
                        if cur.description:
                            results.append(dict(cur.fetchone() or {}))
                        else:
                            results.append({})
                conn.commit()
                return results
            except Exception:
                conn.rollback()
                raise

    def _invalidate(self) -> None:
        try:
            from Api.utils import invalidate_query_cache

            invalidate_query_cache()
        except Exception:  # pragma: no cover - cache is an optimisation
            logger.debug("LEXIS: could not clear the query cache", exc_info=True)

    # -- node identifiers -------------------------------------------------

    @staticmethod
    def parse_node(node: Optional[str]) -> Dict[str, Any]:
        """Interpret a node id; unknown ids are refused, never guessed."""
        text = (node or "root").strip()
        for kind, pattern in NODE_PATTERNS:
            match = pattern.match(text)
            if not match:
                continue
            if kind == "root":
                return {"kind": "root", "node": "root"}
            if kind == "lens":
                return {"kind": "lens", "lens": match.group(1), "node": text}
            if kind in ("cat", "sub"):
                return {"kind": kind, "lens": match.group(1),
                        "category_id": int(match.group(2)), "node": text}
            if kind == "term":
                prefix = match.group(1)
                return {"kind": "term",
                        "lens": "words" if prefix == "w" else "keywords",
                        "term_id": int(match.group(2)), "node": text}
            if kind == "file":
                return {"kind": "file", "path_id": int(match.group(1)), "node": text}
        raise LexisError("unknown node: %s" % text)

    # -- filters ----------------------------------------------------------

    @staticmethod
    def normalise_filters(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The one filter envelope shared by the tree, search and exports."""
        data = dict(raw or {})
        return {
            "file_types": _str_list(data.get("file_types")),
            "source_ids": _int_list(data.get("source_ids")),
            "side_ids": _int_list(data.get("side_ids")),
            "date_from": _date_param(data.get("date_from"), "date_from"),
            "date_to": _date_param(data.get("date_to"), "date_to"),
            "q": (str(data.get("q")).strip() or None) if data.get("q") else None,
        }

    @staticmethod
    def _search_filters(filters: Dict[str, Any], raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """The sidebar search filters: source/side by *name* (Q4.2/Q4.3/Q6.1)."""
        data = raw or {}
        return {
            "file_types": filters["file_types"],
            "sources": _str_list(data.get("sources")),
            "sides": _str_list(data.get("sides")),
            "date_from": filters["date_from"],
            "date_to": filters["date_to"],
            "q": filters["q"],
        }

    # ==================================================================
    # 1. Health
    # ==================================================================

    def health(self) -> Dict[str, Any]:
        """Q1.1 - global counts for the root node and the status bar."""
        row = self._read_one(Q.Q1_1_HEALTH) or {}
        return {key: _json_safe(value) for key, value in row.items()}

    # ==================================================================
    # 2. The galaxy tree
    # ==================================================================

    def lens_summary(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Q2.1 - per-lens term and file counts for the two galaxy nodes."""
        params = self.normalise_filters(filters)
        rows = self._read(Q.Q2_1_LENS_SUMMARY, params)
        found = {row["lens"]: row for row in rows}
        out = []
        for lens in LENSES:
            row = found.get(lens, {})
            out.append({
                "lens": lens,
                "node": "lens:%s" % lens,
                "term_count": int(row.get("term_count") or 0),
                "file_count": int(row.get("file_count") or 0),
            })
        return out

    def categories(self, lens: str, filters: Optional[Dict[str, Any]] = None,
                   hide_empty: bool = True) -> List[Dict[str, Any]]:
        """Q2.2 / Q2.3 - main categories under a lens."""
        if lens not in LENSES:
            raise LexisError("lens must be 'words' or 'keywords'")
        params = self.normalise_filters(filters)
        params["hide_empty"] = bool(hide_empty)
        sql = Q.Q2_2_WORD_CATEGORIES if lens == "words" else Q.Q2_3_KEYWORD_CATEGORIES
        rows = self._read(sql, params)
        return [{
            "node": "cat:%s:%s" % (lens, row["id"]),
            "kind": "cat",
            "lens": lens,
            "id": row["id"],
            "name": row["name"],
            "color": row.get("color"),
            "subcategory_count": int(row.get("subcategory_count") or 0),
            "term_count": int(row.get("term_count") or 0),
            "file_count": int(row.get("file_count") or 0),
        } for row in rows]

    def subcategories(self, lens: str, main_category_id: int,
                      filters: Optional[Dict[str, Any]] = None,
                      hide_empty: bool = True) -> List[Dict[str, Any]]:
        """Q2.4 / Q2.5 - subcategories of one main category."""
        if lens not in LENSES:
            raise LexisError("lens must be 'words' or 'keywords'")
        params = self.normalise_filters(filters)
        params["main_category_id"] = int(main_category_id)
        params["hide_empty"] = bool(hide_empty)
        sql = Q.Q2_4_WORD_SUBCATEGORIES if lens == "words" else Q.Q2_5_KEYWORD_SUBCATEGORIES
        rows = self._read(sql, params)
        return [{
            "node": "sub:%s:%s" % (lens, row["id"]),
            "kind": "sub",
            "lens": lens,
            "id": row["id"],
            "name": row["name"],
            "parent_id": row.get("parent_id"),
            "term_count": int(row.get("term_count") or 0),
            "file_count": int(row.get("file_count") or 0),
        } for row in rows]

    def terms(self, lens: str, category_id: int,
              filters: Optional[Dict[str, Any]] = None,
              hide_empty: bool = True) -> List[Dict[str, Any]]:
        """Q2.6 / Q2.7 - the terms of a subcategory (or a term-bearing category).

        Keyword terms carry a packed blob in ``keywords.keyword``; their display
        label is rebuilt from the packed word ids (Q2.8).
        """
        if lens not in LENSES:
            raise LexisError("lens must be 'words' or 'keywords'")
        params = self.normalise_filters(filters)
        params["category_id"] = int(category_id)
        params["hide_empty"] = bool(hide_empty)
        if lens == "words":
            rows = self._read(Q.Q2_6_WORD_TERMS, params)
            return [{
                "node": "term:w_%s" % row["word_id"],
                "kind": "term",
                "lens": "words",
                "id": row["word_id"],
                "label": row["label"],
                "subcategory_id": row.get("subcategory_id"),
                "main_category_id": row.get("main_category_id"),
                "file_count": int(row.get("file_count") or 0),
                "total_hits": int(row.get("total_hits") or 0),
            } for row in rows]

        rows = self._read(Q.Q2_7_KEYWORD_TERMS, params)
        blobs = {row["keyword_id"]: row.get("keyword_blob") for row in rows}
        labels = self.keyword_labels(list(blobs))
        return [{
            "node": "term:k_%s" % row["keyword_id"],
            "kind": "term",
            "lens": "keywords",
            "id": row["keyword_id"],
            "label": labels.get(row["keyword_id"], ""),
            "subcategory_id": row.get("subcategory_id"),
            "main_category_id": row.get("main_category_id"),
            "file_count": int(row.get("file_count") or 0),
            "total_hits": int(row.get("total_hits") or 0),
            # The packed expression stays here: a psycopg2 memoryview is not
            # JSON, and one leaking into the tree payload turned every keyword
            # subcategory into a 500.
        } for row in rows]

    def term_files(self, lens: str, term_id: int,
                   filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Q2.9 / Q2.10 - the file stack under one term."""
        if lens not in LENSES:
            raise LexisError("lens must be 'words' or 'keywords'")
        params = self.normalise_filters(filters)
        if lens == "words":
            params["word_id"] = int(term_id)
            rows = self._read(Q.Q2_9_WORD_TERM_FILES, params)
        else:
            params["keyword_id"] = int(term_id)
            rows = self._read(Q.Q2_10_KEYWORD_TERM_FILES, params)
        return [{
            "node": "file:%s" % row["file_id"],
            "kind": "file",
            "id": row["file_id"],
            "name": row["name"],
            "type": row["type"],
            "date": _json_safe(row.get("date")),
            "source": row.get("source"),
            "side": row.get("side"),
            "hits": int(row.get("hits") or 0),
        } for row in rows[:MAX_FILES_PER_TERM_NODE]]

    def outline(self) -> List[Dict[str, Any]]:
        """Q2.11 - the full taxonomy with per-term file counts."""
        rows = self._read(Q.Q2_11_OUTLINE_WORDS) + self._read(Q.Q2_11_OUTLINE_KEYWORDS)
        keyword_ids = [row["term_id"] for row in rows if row.get("keyword_blob") is not None]
        labels = self.keyword_labels(keyword_ids)
        out = []
        for row in rows:
            lens = row["lens"]
            term_id = row.get("term_id")
            label = row.get("term_label")
            if lens == "keywords":
                label = labels.get(term_id, "")
            out.append({
                "main_category_id": row.get("main_category_id"),
                "main_category_name": row.get("main_category_name"),
                "subcategory_id": row.get("subcategory_id"),
                "subcategory_name": row.get("subcategory_name"),
                "lens": lens,
                "term_id": term_id,
                "term_label": label,
                "node": ("term:w_%s" % term_id) if lens == "words" and term_id
                        else ("term:k_%s" % term_id if term_id else None),
                "file_count": int(row.get("file_count") or 0),
            })
        out.sort(key=lambda item: (
            (item["main_category_name"] or "").lower(),
            (item["subcategory_name"] or "") == "",
            (item["subcategory_name"] or "").lower(),
            item["lens"],
            item["term_id"] or 0,
        ))
        return out

    def children(self, node: Optional[str], filters: Optional[Dict[str, Any]] = None,
                 hide_empty: bool = True) -> Dict[str, Any]:
        """One lazy step of the galaxy: the children of ``node``."""
        target = self.parse_node(node)
        kind = target["kind"]
        if kind == "root":
            items = [{
                "node": "lens:%s" % row["lens"],
                "kind": "lens",
                "lens": row["lens"],
                "label": row["lens"],
                "term_count": row["term_count"],
                "file_count": row["file_count"],
                "expandable": True,
            } for row in self.lens_summary(filters)]
        elif kind == "lens":
            items = [dict(item, label=item["name"], expandable=True)
                     for item in self.categories(target["lens"], filters, hide_empty)]
        elif kind == "cat":
            items = [dict(item, label=item["name"], expandable=True)
                     for item in self.subcategories(target["lens"], target["category_id"],
                                                    filters, hide_empty)]
            # A main category may hold terms directly (Q2.2 target_category_id is
            # COALESCE(sub_c.id, main_c.id)); show those alongside the subs.
            direct = self.terms(target["lens"], target["category_id"], filters, hide_empty)
            items.extend(dict(item, expandable=item["file_count"] > 0) for item in direct)
        elif kind == "sub":
            items = [dict(item, expandable=item["file_count"] > 0)
                     for item in self.terms(target["lens"], target["category_id"],
                                            filters, hide_empty)]
        elif kind == "term":
            files = self.term_files(target["lens"], target["term_id"], filters)
            items = [dict(item, label=item["name"], expandable=False) for item in files]
        else:
            raise LexisError("a file node has no children")
        return {"node": target["node"], "kind": kind, "children": items}

    # -- keyword label resolution -----------------------------------------

    def keyword_labels(self, keyword_ids: Sequence[int]) -> Dict[int, str]:
        """Display text for keyword expressions (Q2.8 + the packed blob).

        ``keywords.keyword`` stores packed ``words.id`` values. The client is
        never shown the blob; the phrase is rebuilt here and cached for the
        lifetime of the request.
        """
        wanted = [int(k) for k in keyword_ids if k is not None]
        if not wanted:
            return {}
        cache = getattr(self, "_label_cache", None)
        if cache is None:
            cache = self._label_cache = {}
        missing = [key for key in dict.fromkeys(wanted) if key not in cache]
        if missing:
            blobs = self._read(Q.Q2_8_FETCH_KEYWORDS, {"keyword_ids": missing})
            for row in blobs:
                word_ids: List[int] = []
                try:
                    word_ids = unpack_int_list(row.get("keyword"))
                except Exception:
                    logger.warning("LEXIS: unreadable keyword blob %s", row.get("id"),
                                   exc_info=True)
                if word_ids:
                    words = self._read(Q.Q2_8_RESOLVE_KEYWORD_WORDS, {"word_ids": word_ids})
                    cache[row["id"]] = " ".join(part["word"] for part in words)
                else:
                    cache[row["id"]] = ""
        return {key: cache.get(key, "") for key in wanted}

    # ==================================================================
    # 3. Term finder
    # ==================================================================

    def find(self, query: str, limit: int = 9) -> Dict[str, Any]:
        """Q3.1 + Q3.2 - header typeahead over word and keyword terms.

        Q3.2 filters the stored expression by the raw text the operator typed.
        A keyword expression is a packed blob, so the match happens on the
        rebuilt phrase (Q2.8) rather than on the blob - otherwise every
        keyword would look identical to SQL.
        """
        text = (query or "").strip()
        if not text:
            return {"query": "", "words": [], "keywords": []}
        words = self._read(Q.Q3_1_FIND_WORDS, {"q": text})
        stored_keywords = self._read(Q.Q3_2_FIND_KEYWORDS)
        labels = self.keyword_labels([row["term_id"] for row in stored_keywords])
        needle = text.lower()
        keyword_rows = [dict(row, label=labels.get(row["term_id"], ""))
                        for row in stored_keywords
                        if needle in labels.get(row["term_id"], "").lower()]
        keyword_rows.sort(key=lambda row: (-int(row.get("file_count") or 0), row["term_id"]))

        def shape(row: Dict[str, Any], lens: str) -> Dict[str, Any]:
            label = row.get("label") if lens == "words" else labels.get(row["term_id"], "")
            return {
                "node": ("term:w_%s" % row["term_id"]) if lens == "words"
                        else ("term:k_%s" % row["term_id"]),
                "kind": "term",
                "lens": lens,
                "term_id": row["term_id"],
                "label": label,
                "cat_id": row.get("cat_id"),
                "cat_name": row.get("cat_name"),
                "sub_id": row.get("sub_id"),
                "sub_name": row.get("sub_name"),
                "file_count": int(row.get("file_count") or 0),
            }

        return {
            "query": text,
            "words": [shape(row, "words") for row in words[:limit]],
            "keywords": [shape(row, "keywords") for row in keyword_rows[:limit]],
        }

    # ==================================================================
    # 4. Sidebar search, facets & pagination
    # ==================================================================

    def resolve_scope(self, focus: Optional[str] = None,
                      basket: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Q4.1 - expand a focus node (or the basket) into term ids.

        Returns ``{"word_ids": [...] | None, "keyword_ids": [...] | None}``
        plus a description of what was resolved, so the caller can report the
        scope it searched.
        """
        basket_nodes = [str(item) for item in (basket or [])][:MAX_BASKET_TERMS]
        nodes = basket_nodes or [focus or "root"]
        word_ids: List[int] = []
        keyword_ids: List[int] = []
        include_words = False
        include_keywords = False

        for raw in nodes:
            target = self.parse_node(raw)
            kind = target["kind"]
            if kind == "root":
                include_words = include_keywords = True
                word_ids.extend(row["word_id"] for row in
                                self._read(Q.Q4_1_SCOPE_ALL_WORD_IDS))
                keyword_ids.extend(row["id"] for row in
                                   self._read(Q.Q4_1_SCOPE_ALL_KEYWORD_IDS))
            elif kind == "lens":
                if target["lens"] == "words":
                    include_words = True
                    word_ids.extend(row["word_id"] for row in
                                    self._read(Q.Q4_1_SCOPE_ALL_WORD_IDS))
                else:
                    include_keywords = True
                    keyword_ids.extend(row["id"] for row in
                                       self._read(Q.Q4_1_SCOPE_ALL_KEYWORD_IDS))
            elif kind in ("cat", "sub"):
                if target["lens"] == "words":
                    include_words = True
                    word_ids.extend(row["word_id"] for row in
                                    self._read(Q.Q4_1_SCOPE_WORD_IDS,
                                               {"category_id": target["category_id"]}))
                else:
                    include_keywords = True
                    keyword_ids.extend(row["keyword_id"] for row in
                                       self._read(Q.Q4_1_SCOPE_KEYWORD_IDS,
                                                  {"category_id": target["category_id"]}))
            elif kind == "term":
                if target["lens"] == "words":
                    include_words = True
                    word_ids.append(target["term_id"])
                else:
                    include_keywords = True
                    keyword_ids.append(target["term_id"])
            else:
                raise LexisError("a file node cannot define a search scope")

        def unique(values: Iterable[int]) -> List[int]:
            return list(dict.fromkeys(int(value) for value in values))

        words = unique(word_ids) if include_words else None
        keywords = unique(keyword_ids) if include_keywords else None
        return {
            "nodes": nodes,
            "word_ids": words,
            "keyword_ids": keywords,
            "term_count": len(words or []) + len(keywords or []),
        }

    def search(self, scope: Dict[str, Any], raw_filters: Optional[Dict[str, Any]] = None,
               sort_key: str = "hits", sort_dir: str = "desc",
               page: int = 1, size: int = 50,
               cursor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Q4.2 (offset paging) or Q4.3 (keyset paging) within a scope."""
        sort_key = sort_key if sort_key in SORT_KEYS else "hits"
        sort_dir = sort_dir if sort_dir in SORT_DIRECTIONS else "desc"
        size = max(1, min(int(size or 50), MAX_PAGE_SIZE))
        filters = self.normalise_filters(raw_filters)
        params = dict(self._search_filters(filters, raw_filters))
        params.update({
            "word_ids": scope.get("word_ids"),
            "keyword_ids": scope.get("keyword_ids"),
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "size": size,
        })

        total: Optional[int] = None
        if cursor and cursor.get("id") is not None:
            params.update({
                "after_id": int(cursor.get("id")),
                "after_hits": int(cursor.get("hits") or 0),
                "after_date": cursor.get("date") or None,
                "after_str": cursor.get("str") or "",
            })
            rows = self._read(Q.Q4_3_SEARCH_KEYSET, params)
        else:
            # A page number is an OFFSET multiplier and PostgreSQL's bigint
            # refuses anything past 2**63: ?page=99999999999999999999 raised
            # "bigint out of range" and answered 500. No list has a million
            # pages; clamp instead of failing.
            params["offset"] = max(0, (min(max(1, int(page)), MAX_PAGE_PAGES) - 1) * size)
            rows = self._read(Q.Q4_2_SEARCH_OFFSET, params)
            if rows:
                total = int(rows[0].get("total_count") or 0)
            else:
                total = 0

        keyword_ids = [int(key.split(":")[1]) for row in rows
                       for key in (row.get("keys") or []) if str(key).startswith("k:")]
        labels = self.keyword_labels(keyword_ids)
        files = []
        for row in rows:
            keys = [str(key) for key in (row.get("keys") or [])]
            matched = []
            raw_labels = list(row.get("matched") or [])
            for index, key in enumerate(keys):
                raw_label = raw_labels[index] if index < len(raw_labels) else key
                if key.startswith("k:"):
                    matched.append(labels.get(int(key.split(":")[1]), ""))
                else:
                    matched.append(raw_label)
            files.append({
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "source": row.get("source"),
                "date": _json_safe(row.get("date")),
                "side": row.get("side"),
                "status": row.get("status"),
                "size": row.get("size"),
                "hits": int(row.get("hits") or 0),
                "keys": keys,
                "matched": [label for label in matched if label],
            })

        next_cursor = None
        if files:
            tail = rows[-1]
            sort_value = {
                "hits": tail.get("hits"),
                "date": _json_safe(tail.get("date")),
                "name": tail.get("name"),
                "type": tail.get("type"),
            }.get(sort_key)
            next_cursor = {
                "id": tail["id"],
                "hits": int(tail.get("hits") or 0),
                "date": _json_safe(tail.get("date")),
                "str": str(sort_value) if sort_value is not None else "",
            }

        return {
            "files": files,
            "total": total,
            "size": size,
            "page": int(page),
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "cursor": next_cursor,
            "scope": {"nodes": scope.get("nodes"), "term_count": scope.get("term_count")},
        }

    def facets(self, scope: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """Q4.4 - facet counts inside the current scope, per facet."""
        rows = self._read(Q.Q4_4_FACETS, {
            "word_ids": scope.get("word_ids"),
            "keyword_ids": scope.get("keyword_ids"),
        })
        out: Dict[str, List[Dict[str, Any]]] = {"type": [], "source": [], "dim": []}
        for row in rows:
            out.setdefault(row["facet"], []).append({
                "value": row["value"],
                "count": int(row["file_count"] or 0),
            })
        return out

    def filter_options(self) -> Dict[str, List[Dict[str, Any]]]:
        """Q4.5 - the values the sidebar dropdowns offer."""
        types = [row["file_type"] for row in self._read(Q.Q4_5_FILE_TYPES)]
        sources = [{"id": row["id"], "name": row["name"]}
                   for row in self._read(Q.Q4_5_SOURCES)]
        sides = [{"id": row["id"], "name": row["name"],
                  "importance": _json_safe(row.get("importance"))}
                 for row in self._read(Q.Q4_5_SIDES)]
        return {"file_types": types, "sources": sources, "sides": sides}

    # ==================================================================
    # 5. Document viewer
    # ==================================================================

    def file_metadata(self, path_id: int) -> Dict[str, Any]:
        """Q5.1 - metadata and canonical content identity."""
        row = self._read_one(Q.Q5_1_FILE_METADATA, {"path_id": int(path_id)})
        if row is None:
            raise LexisError("that file is no longer in the library")
        return {key: _json_safe(value) for key, value in row.items()}

    def file_text(self, path_id: int) -> Dict[str, Any]:
        """Q5.2 with the Q5.3 compressed fallback.

        ``contents_raw`` is the readable projection of the content; when only
        the symbol-pair store (``contents.content_data``) exists, the text is
        reconstructed through the same reader the File Library uses, so the
        two views never disagree.
        """
        chunks = self._read(Q.Q5_2_FILE_TEXT, {"path_id": int(path_id)})
        if chunks:
            return {"text": "".join(row["content"] for row in chunks),
                    "chunks": len(chunks), "source": "contents_raw"}
        packed = self._read(Q.Q5_3_FILE_TEXT_COMPRESSED, {"path_id": int(path_id)})
        if packed:
            try:
                from Api.utils import load_text_content

                text = load_text_content(int(path_id)) or ""
            except Exception:
                logger.warning("LEXIS: could not reconstruct content for path %s",
                               path_id, exc_info=True)
                text = ""
            return {"text": text, "chunks": len(packed), "source": "contents"}
        return {"text": "", "chunks": 0, "source": "none"}

    def file_matches(self, path_id: int) -> List[Dict[str, Any]]:
        """Q5.4 - "why it matched": every term and expression, with its path."""
        rows = self._read(Q.Q5_4_FILE_MATCHES, {"path_id": int(path_id)})
        labels = self.keyword_labels([row["term_id"] for row in rows
                                      if row["lens"] == "keywords"])
        out = []
        for row in rows:
            lens = row["lens"]
            label = row.get("label") if lens == "words" else labels.get(row["term_id"], "")
            out.append({
                "lens": lens,
                "term_id": row["term_id"],
                "label": label or "",
                "node": ("term:w_%s" % row["term_id"]) if lens == "words"
                        else ("term:k_%s" % row["term_id"]),
                "cat_id": row.get("cat_id"),
                "cat_name": row.get("cat_name"),
                "sub_id": row.get("sub_id"),
                "sub_name": row.get("sub_name"),
                "hits": int(row.get("hits") or 0),
            })
        return out

    # ==================================================================
    # 6. Actions & exports
    # ==================================================================

    def review_rows(self, scope: Dict[str, Any], raw_filters: Optional[Dict[str, Any]],
                    selected_path_ids: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
        """Q6.1 - Action 2 (xCats): categories, terms and keywords per file."""
        filters = self.normalise_filters(raw_filters)
        params = self._search_filters(filters, raw_filters)
        params.update({
            "word_ids": scope.get("word_ids"),
            "keyword_ids": scope.get("keyword_ids"),
            "selected_path_ids": _int_list(selected_path_ids),
        })
        rows = self._read(Q.Q6_1_REVIEW, params)
        labels = self.keyword_labels([row["term_id"] for row in rows
                                      if row["lens"] == "keywords"])
        out = []
        for row in rows:
            lens = row["lens"]
            out.append({
                "file_id": row["file_id"],
                "file": row["file"],
                "lens": lens,
                "category": row.get("category"),
                "subcategory": row.get("subcategory"),
                "term_id": row["term_id"],
                "term": row.get("term") if lens == "words" else labels.get(row["term_id"], ""),
                "hits": int(row.get("hits") or 0),
            })
        return out

    def extracted_text(self, path_ids: Sequence[int]) -> List[Dict[str, Any]]:
        """Q6.2 - Actions 3 & 5 (xZipText, xDirText)."""
        ids = self._target_ids(path_ids)
        return [dict(_row(row), extracted_text=row.get("extracted_text") or "")
                for row in self._read(Q.Q6_2_EXTRACTED_TEXT, {"target_path_ids": ids})]

    def original_paths(self, path_ids: Sequence[int]) -> List[Dict[str, Any]]:
        """Q6.3 - Actions 4, 6, 9, 10 (original paths and metadata)."""
        ids = self._target_ids(path_ids)
        return [_row(row) for row in self._read(Q.Q6_3_ORIGINAL_PATHS,
                                                {"target_path_ids": ids})]

    def structured_rows(self, path_ids: Sequence[int], scope: Optional[Dict[str, Any]] = None,
                        raw_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Q6.4 - Action 7 (xStruct): structured export with the strongest term."""
        ids = self._target_ids(path_ids)
        rows = self._read(Q.Q6_4_STRUCTURED, {
            "target_path_ids": ids,
            "word_ids": (scope or {}).get("word_ids"),
            "keyword_ids": (scope or {}).get("keyword_ids"),
        })
        return [{key: _json_safe(value) for key, value in row.items()} for row in rows]

    def column_rows(self, path_ids: Sequence[int], scope: Optional[Dict[str, Any]] = None,
                    raw_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Q6.6 - Action 11 and the standard exporter."""
        ids = self._target_ids(path_ids)
        rows = self._read(Q.Q6_6_COLUMNS, {
            "target_path_ids": ids,
            "word_ids": (scope or {}).get("word_ids"),
        })
        return [{key: _json_safe(value) for key, value in row.items()} for row in rows]

    def rename_file(self, path_id: int, new_name: str, new_path: Optional[str],
                    user_id: Optional[int] = None) -> Dict[str, Any]:
        """Q6.5 - Action 8 (xRename), recording the path revision."""
        name = (new_name or "").strip()
        if not name:
            raise LexisError("a file name is required")
        if "/" in name or "\\" in name:
            raise LexisError("a file name cannot contain a path separator")
        current = self.file_metadata(int(path_id))
        target_path = (new_path or "").strip() or _replace_name(current["path"], name)
        results = self._write([
            (Q.Q6_5_RENAME_UPDATE, {
                "path_id": int(path_id), "new_file_name": name, "new_file_path": target_path,
            }),
            (Q.Q6_5_RENAME_REVISION, {
                "path_id": int(path_id),
                "user_id": user_id,
                "old_file_name": current["name"],
                "old_file_path": current["path"],
                "new_file_name": name,
                "new_file_path": target_path,
            }),
        ])
        self._invalidate()
        return {key: _json_safe(value) for key, value in (results[0] or {}).items()}

    def _target_ids(self, path_ids: Sequence[int]) -> List[int]:
        ids = _int_list(path_ids, upper=5000)
        if not ids:
            raise LexisError("no files were supplied")
        return ids

    # ==================================================================
    # 7. Dashboard & data health
    # ==================================================================

    def dashboard(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Q7.1-Q7.5 - KPIs, two bar charts and the data-health panels."""
        kpis = self._read_one(Q.Q7_1_DASHBOARD_KPIS, {"user_id": user_id}) or {}
        types = self._read(Q.Q7_2_FILES_BY_TYPE)
        sides = self._read(Q.Q7_3_FILES_BY_SIDE)
        orphans = self._read(Q.Q7_4_ORPHAN_TERMS)
        labels = self.keyword_labels([row["term_id"] for row in orphans
                                      if row["lens"] == "keywords"])
        uncategorised = self._read(Q.Q7_5_UNCATEGORISED_FILES)
        return {
            "kpis": {key: _json_safe(value) for key, value in kpis.items()},
            "by_type": [{"label": row["file_type"],
                         "count": int(row["file_count"] or 0)} for row in types],
            "by_side": [{"label": row["side_name"],
                         "count": int(row["file_count"] or 0)} for row in sides],
            "orphan_terms": [{
                "lens": row["lens"],
                "term_id": row["term_id"],
                "label": row.get("label") if row["lens"] == "words"
                         else labels.get(row["term_id"], ""),
                "category_id": row.get("category_id"),
            } for row in orphans],
            "uncategorised": [_row(row) for row in uncategorised],
        }

    # ==================================================================
    # 8. Taxonomy CRUD
    # ==================================================================

    def ensure_word(self, word: str) -> int:
        """Q8.1 - create or get a word id for a name."""
        text = (word or "").strip()
        if not text:
            raise LexisError("a name is required")
        if len(text) > 200:
            raise LexisError("a name cannot exceed 200 characters")
        row = self._write([(Q.Q8_1_UPSERT_WORD, {"word": text})])[0]
        return int(row["id"])

    def find_word(self, word: str) -> Optional[int]:
        """Q8.1b - look a word id up without creating it."""
        row = self._read_one(Q.Q8_1_FIND_WORD, {"word": (word or "").strip()})
        return int(row["id"]) if row else None

    def create_category(self, name: str, parent_id: Optional[int] = None,
                        color: Optional[str] = None, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Q8.2 / Q8.3 (+ Q8.5 for a colour) - create a category."""
        word_id = self.ensure_word(name)
        if parent_id in (None, "", 0):
            sql = Q.Q8_2_CREATE_MAIN_CATEGORY
            params = {"word_id": word_id}
            kind = "cat"
        else:
            parent = int(parent_id)
            parent_row = self._read_one(
                "SELECT id, parent_id FROM categorys WHERE id = %(id)s", {"id": parent})
            if parent_row is None:
                raise LexisError("the parent category does not exist")
            if parent_row.get("parent_id") is not None:
                raise LexisError("a subcategory cannot contain another subcategory")
            sql = Q.Q8_3_CREATE_SUBCATEGORY
            params = {"word_id": word_id, "parent_category_id": parent}
            kind = "sub"
        statements = [(sql, params)]
        if color:
            statements.append((Q.Q8_5_UPSERT_CATEGORY_COLOR,
                               {"name": name.strip(), "color": color, "user_id": user_id}))
        created = self._write(statements)[0]
        self._invalidate()
        return {"id": int(created["id"]), "kind": kind, "name": name.strip(),
                "parent_id": None if kind == "cat" else int(parent_id)}

    def rename_category(self, category_id: int, new_name: str,
                        color: Optional[str] = None,
                        user_id: Optional[int] = None) -> Dict[str, Any]:
        """Q8.4 (+ Q8.5) - rename a category or subcategory."""
        word_id = self.ensure_word(new_name)
        statements = [(Q.Q8_4_RENAME_CATEGORY,
                       {"new_word_id": word_id, "category_id": int(category_id)})]
        if color:
            statements.append((Q.Q8_5_UPSERT_CATEGORY_COLOR,
                               {"name": new_name.strip(), "color": color, "user_id": user_id}))
        updated = self._write(statements)[0]
        self._invalidate()
        return {"id": int(updated["id"]), "name": new_name.strip()}

    def set_category_color(self, name: str, color: str,
                           user_id: Optional[int] = None) -> Dict[str, Any]:
        """Q8.5 - upsert a category colour."""
        if not re.match(r"^#[0-9a-fA-F]{6}$", color or ""):
            raise LexisError("a colour must be written as #rrggbb")
        row = self._write([(Q.Q8_5_UPSERT_CATEGORY_COLOR,
                            {"name": name.strip(), "color": color, "user_id": user_id})])[0]
        self._invalidate()
        return {"id": int(row["id"]), "name": name.strip(), "color": color}

    def delete_category(self, category_id: int, is_main: bool) -> Dict[str, Any]:
        """Q8.6 / Q8.7 - delete a category (main categories take their subs)."""
        if is_main:
            results = self._write([
                (Q.Q8_6_DELETE_SUBCATEGORIES_OF, {"main_category_id": int(category_id)}),
                (Q.Q8_6_DELETE_MAIN_CATEGORY, {"main_category_id": int(category_id)}),
            ])
        else:
            results = self._write([
                (Q.Q8_7_DELETE_SUBCATEGORY, {"subcategory_id": int(category_id)}),
            ])
        self._invalidate()
        return {"deleted": int(category_id), "kind": "cat" if is_main else "sub"}

    def duplicate_term(self, label: str) -> Optional[Dict[str, Any]]:
        """Q8.8 - where a word term already lives, if it does."""
        row = self._read_one(Q.Q8_8_FIND_DUPLICATE_TERM, {"label": (label or "").strip()})
        return _row(row)

    def add_word_term(self, label: str, subcategory_id: int) -> Dict[str, Any]:
        """Q8.9 - add a word term to a subcategory."""
        word_id = self.ensure_word(label)
        rows = self._write([(Q.Q8_9_ADD_WORD_TERM,
                             {"word_id": word_id, "subcategory_id": int(subcategory_id)})])
        self._invalidate()
        return {"word_id": word_id, "subcategory_id": int(subcategory_id),
                "created": bool(rows and rows[0])}

    def edit_word_term(self, old_word_id: int, new_name: str,
                       subcategory_id: int) -> Dict[str, Any]:
        """Q8.10 - replace a word link with the new word's link."""
        new_word_id = self.ensure_word(new_name)
        self._write([
            (Q.Q8_10_REMOVE_OLD_WORD_TERM,
             {"old_word_id": int(old_word_id), "subcategory_id": int(subcategory_id)}),
            (Q.Q8_10_ADD_NEW_WORD_TERM,
             {"new_word_id": new_word_id, "subcategory_id": int(subcategory_id)}),
        ])
        self._invalidate()
        return {"word_id": new_word_id, "subcategory_id": int(subcategory_id)}

    def remove_word_term(self, word_id: int, subcategory_id: int) -> Dict[str, Any]:
        """Q8.11 - remove a word term from a subcategory."""
        self._write([(Q.Q8_11_REMOVE_WORD_TERM,
                      {"word_id": int(word_id), "subcategory_id": int(subcategory_id)})])
        self._invalidate()
        return {"word_id": int(word_id), "subcategory_id": int(subcategory_id)}

    def add_keyword(self, phrase: str, subcategory_id: int) -> Dict[str, Any]:
        """Q8.12 - add a keyword expression (packed word ids, as ingestion does)."""
        blob, words = self._pack_phrase(phrase)
        row = self._write([(Q.Q8_12_ADD_KEYWORD,
                            {"keyword_blob": blob, "subcategory_id": int(subcategory_id)})])[0]
        self._invalidate()
        return {"id": int(row["id"]), "label": " ".join(words),
                "subcategory_id": int(subcategory_id)}

    def edit_keyword(self, keyword_id: int, phrase: str,
                     subcategory_id: int) -> Dict[str, Any]:
        """Q8.13 - clear stale matches, then update expression and category."""
        blob, words = self._pack_phrase(phrase)
        row = self._write([
            (Q.Q8_13_CLEAR_KEYWORD_MATCHES, {"keyword_id": int(keyword_id)}),
            (Q.Q8_13_UPDATE_KEYWORD, {"keyword_blob": blob,
                                      "subcategory_id": int(subcategory_id),
                                      "keyword_id": int(keyword_id)}),
        ])[-1]
        self._invalidate()
        return {"id": int(row["id"]), "label": " ".join(words),
                "subcategory_id": int(subcategory_id)}

    def delete_keyword(self, keyword_id: int) -> Dict[str, Any]:
        """Q8.14 - delete a keyword expression."""
        self._write([(Q.Q8_14_DELETE_KEYWORD, {"keyword_id": int(keyword_id)})])
        self._invalidate()
        return {"id": int(keyword_id)}

    def _pack_phrase(self, phrase: str) -> Tuple[bytes, List[str]]:
        """Split a keyword expression into words, upsert them, pack their ids."""
        text = (phrase or "").strip()
        tokens = [token for token in re.split(r"\s+", text) if token]
        if not tokens:
            raise LexisError("a keyword expression is required")
        if len(tokens) > 20:
            raise LexisError("a keyword expression cannot exceed 20 words")
        ids: List[int] = []
        for token in tokens:
            clean = token.strip("\"'()").strip()
            if not clean:
                continue
            ids.append(self.ensure_word(clean))
        if not ids:
            raise LexisError("a keyword expression is required")
        return pack_int_list(ids), [token.strip("\"'()") for token in tokens if token.strip("\"'()")]

    # ==================================================================
    # 9. File manager & bulk operations
    # ==================================================================

    def list_files(self, file_type: Optional[str] = None, query: Optional[str] = None,
                   limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Q9.1 - the file manager list with snippet and matched-term count."""
        limit = max(1, min(int(limit or 50), MAX_PAGE_SIZE))
        params = {
            "file_type": (file_type or "").strip() or None,
            "mfq": (query or "").strip() or None,
            "limit": limit,
            "offset": max(0, int(offset or 0)),
        }
        rows = self._read(Q.Q9_1_FILE_MANAGER, params)
        total = int(rows[0]["total_count"]) if rows else 0
        files = [{key: _json_safe(value) for key, value in row.items()
                  if key != "total_count"} for row in rows]
        return {"files": files, "total": total, "limit": limit, "offset": params["offset"]}

    def ingest_file(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Q9.2 - register one file: content, context, path, hits.

        The statement set is the specification's ingestion sequence. Word and
        keyword hits are optional: when the caller supplies ``content`` the
        words already in the taxonomy are counted, which keeps the taxonomy
        honest without writing new vocabulary behind the operator's back.
        """
        data = dict(payload or {})
        name = (data.get("file_name") or "").strip()
        path = (data.get("file_path") or "").strip() or name
        content = data.get("content") or ""
        if not name:
            raise LexisError("a file name is required")
        file_type = (data.get("file_type") or "").strip() or "TXT"
        file_date = _date_param(data.get("file_date"), "file_date") or date.today().isoformat()
        size = data.get("file_size")
        try:
            size = int(size) if size not in (None, "") else len(content.encode("utf-8"))
        except (TypeError, ValueError):
            raise LexisError("file_size must be a whole number")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        statements: List[Tuple[str, Dict[str, Any]]] = [
            (Q.Q9_2_UPSERT_HASH, {"sha256_hex": digest}),
            (Q.Q9_2_UPSERT_SOURCE, {"source_name": (data.get("source") or "LEXIS").strip()}),
            (Q.Q9_2_UPSERT_SIDE, {"side_name": (data.get("side") or "Unknown").strip()}),
        ]
        hash_row, source_row, side_row = self._write(statements)
        statements = [
            (Q.Q9_2_UPSERT_CONTEXT, {"hash_id": hash_row["id"], "source_id": source_row["id"],
                                     "side_id": side_row["id"]}),
        ]
        context_row = self._write(statements)[0]
        statements = [
            (Q.Q9_2_INSERT_PATH, {
                "file_name": name, "file_path": path, "file_size": size,
                "file_type": file_type, "file_date": file_date,
                "context_id": context_row["id"],
            }),
            (Q.Q9_2_UPSERT_CONTENT, {"hash_id": hash_row["id"], "content_text": content}),
        ]
        hits = self._word_hits(content)
        for word_id, count, positions in hits.get("words", []):
            statements.append((Q.Q9_2_UPSERT_WORD_HITS, {
                "hash_id": hash_row["id"], "word_id": word_id, "word_count": count,
                "position_indexer": positions,
            }))
        for keyword_id, count in hits.get("keywords", []):
            statements.append((Q.Q9_2_UPSERT_KEYWORD_HITS, {
                "hash_id": hash_row["id"], "keyword_id": keyword_id, "word_count": count,
            }))
        results = self._write(statements)
        self._invalidate()
        return {
            "id": int(results[0]["id"]),
            "hash_id": int(hash_row["id"]),
            "context_id": int(context_row["id"]),
            "name": name,
            "word_hits": len(hits.get("words", [])),
            "keyword_hits": len(hits.get("keywords", [])),
        }

    def _word_hits(self, content: str) -> Dict[str, Any]:
        """Count taxonomy words and keyword expressions present in a text."""
        text = (content or "").lower()
        if not text.strip():
            return {"words": [], "keywords": []}
        tokens = sorted(set(re.findall(r"[a-z0-9][a-z0-9'\-]*", text)))
        if not tokens:
            return {"words": [], "keywords": []}
        rows = self._read(Q.Q9_2_LOOKUP_WORDS, {"tokens": tokens})
        words = []
        for row in rows:
            pattern = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(row["word"].lower()))
            count = len(pattern.findall(text))
            if count:
                words.append((row["id"], count, None))
        present = {word_id for word_id, _count, _pos in words}
        keywords: List[Tuple[int, int]] = []
        if present:
            stored = self._read(Q.Q9_2_LOOKUP_KEYWORDS)
            labels = self.keyword_labels([row["id"] for row in stored])
            for row in stored:
                phrase = labels.get(row["id"], "").lower()
                if not phrase or not set(phrase.split()) <= {
                        word["word"].lower() for word in rows}:
                    continue
                count = len(re.findall(
                    r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(phrase), text))
                if count:
                    keywords.append((row["id"], count))
        return {"words": words, "keywords": keywords}

    def duplicate_file(self, source_path_id: int, copy_name: Optional[str] = None) -> Dict[str, Any]:
        """Q9.3 - duplicate a file record, sharing context and content."""
        source = self.file_metadata(int(source_path_id))
        name = (copy_name or "").strip() or _copy_name(source["name"])
        row = self._write([(Q.Q9_3_DUPLICATE_PATH,
                            {"copy_file_name": name, "source_path_id": int(source_path_id)})])[0]
        self._invalidate()
        return {"id": int(row["id"]), "name": name}

    def bulk_set_side(self, path_ids: Sequence[int], new_side_id: int) -> Dict[str, Any]:
        """Q9.4 - move the selected files onto another side."""
        ids = self._target_ids(path_ids)
        rows = self._write([(Q.Q9_4_BULK_SET_SIDE,
                             {"path_ids": ids, "new_side_id": int(new_side_id)})])[0]
        self._invalidate()
        return {"updated": len(rows) if isinstance(rows, dict) and rows else 0,
                "path_ids": ids, "side_id": int(new_side_id)}

    def bulk_delete(self, path_ids: Sequence[int]) -> Dict[str, Any]:
        """Q9.5 - delete the selected files."""
        ids = self._target_ids(path_ids)
        rows = self._write([(Q.Q9_5_BULK_DELETE, {"path_ids": ids})])[0]
        self._invalidate()
        return {"deleted": len(ids), "path_ids": ids}

    # ==================================================================
    # 10. Saved views
    # ==================================================================

    def list_views(self, user_id: Optional[int]) -> List[Dict[str, Any]]:
        """Q10.1 - saved views visible to the current user."""
        rows = self._read(Q.Q10_1_LIST_VIEWS, {"user_id": user_id})
        return [{key: _json_safe(value) for key, value in row.items()} for row in rows]

    def save_view(self, user_id: Optional[int], name: str, query: str,
                  filters: Dict[str, Any]) -> Dict[str, Any]:
        """Q10.2 - store the current view (focus, basket, filters, side)."""
        label = (name or "").strip()
        if not label:
            raise LexisError("a view name is required")
        data = dict(filters or {})
        data.setdefault("focus", "root")
        data.setdefault("basket", [])
        criteria_json, fingerprint = self._criteria(query, data)
        params = {
            "user_id": user_id,
            "name": label,
            "query": (query or "").strip(),
            "filters_json": json.dumps(_json_safe(data), sort_keys=True),
            "criteria_json": json.dumps(criteria_json, sort_keys=True),
            "criteria_fingerprint": fingerprint,
            "criteria_schema_version": CRITERIA_SCHEMA_VERSION,
        }
        row = self._write([(Q.Q10_2_SAVE_VIEW, params)])[0]
        return {"id": int(row["id"]), "name": row["name"],
                "filters": _json_safe(row.get("filters")),
                "created_at": _json_safe(row.get("created_at"))}

    @staticmethod
    def _criteria(query: str, filters: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """Canonical criteria + fingerprint for a LEXIS view.

        ``saved_searches`` requires a criteria document, a SHA-256 fingerprint
        and a schema version. The shared mapper accepts the keys it knows and
        reports the rest; LEXIS-specific keys (``focus``, ``basket``,
        ``hide_empty``) live in ``filters`` where they are stored verbatim.
        """
        try:
            criteria = from_legacy_search(query, filters)
            return criteria.to_dict(), criteria.fingerprint()
        except (CriteriaError, Exception) as exc:  # pragma: no cover - defensive
            logger.warning("LEXIS: criteria mapping fallback: %s", exc)
            payload = {"text": (query or "").strip() or None,
                       "lexis_filters": _json_safe(filters)}
            blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            return payload, hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def rename_view(self, view_id: int, name: str, user_id: Optional[int]) -> Dict[str, Any]:
        """Q10.3 - rename a saved view."""
        label = (name or "").strip()
        if not label:
            raise LexisError("a view name is required")
        rows = self._write([(Q.Q10_3_RENAME_VIEW,
                             {"view_id": int(view_id), "new_name": label,
                              "user_id": user_id})])
        if not rows or not rows[0]:
            raise LexisError("that view does not exist or is not yours")
        return {"id": int(view_id), "name": label}

    def touch_view(self, view_id: int) -> Dict[str, Any]:
        """Q10.4 - record that a view was applied."""
        self._write([(Q.Q10_4_TOUCH_VIEW, {"view_id": int(view_id)})])
        return {"id": int(view_id)}

    def delete_view(self, view_id: int, user_id: Optional[int]) -> Dict[str, Any]:
        """Q10.5 - delete a saved view."""
        rows = self._write([(Q.Q10_5_DELETE_VIEW,
                             {"view_id": int(view_id), "user_id": user_id})])
        if not rows or not rows[0]:
            raise LexisError("that view does not exist or is not yours")
        return {"id": int(view_id)}

    # ==================================================================
    # 11. Activity log
    # ==================================================================

    def list_activity(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Q11.1 - the most recent 300 activity entries."""
        rows = self._read(Q.Q11_1_ACTIVITY, {"user_id": user_id})
        return [{key: _json_safe(value) for key, value in row.items()} for row in rows]

    def record_activity(self, action: str, resource: Optional[str], message: str,
                        user_id: Optional[int] = None, username: Optional[str] = None,
                        ip_address: Optional[str] = None) -> Dict[str, Any]:
        """Q11.2 - write an activity entry (mutations, undo, redo, backup)."""
        if not (action or "").strip():
            raise LexisError("an action is required")
        row = self._write([(Q.Q11_2_RECORD_ACTIVITY, {
            "user_id": user_id,
            "username": username,
            "action": action.strip()[:255],
            "resource": (resource or None) and str(resource)[:255],
            "message": (message or action)[:1000],
            "ip_address": (ip_address or None) and str(ip_address)[:64],
        })])[0]
        return {"id": int(row["id"]), "created_at": _json_safe(row.get("created_at"))}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _replace_name(path: str, name: str) -> str:
    """Swap the file name in a stored path, keeping its directory."""
    text = (path or "").replace("\\", "/")
    if "/" not in text:
        return name
    return text.rsplit("/", 1)[0] + "/" + name


def _copy_name(name: str) -> str:
    """``report.pdf`` -> ``report (copy).pdf``."""
    text = (name or "untitled").strip()
    if "." in text:
        stem, extension = text.rsplit(".", 1)
        return "%s (copy).%s" % (stem, extension)
    return "%s (copy)" % text


__all__ = ["LexisCosmosService", "LexisError", "SORT_KEYS", "SORT_DIRECTIONS",
           "LENSES", "MAX_PAGE_SIZE"]
