"""Bounded keyword search: one page of matches without loading every match.

A keyword's text is not stored as text. The ``keywords.keyword`` column holds a
packed list of word ids, and the text is those words joined by spaces in that
order. So the database cannot match the query text, and the match has to be
made in Python. The earlier search path loaded every keyword, with its usage
and category, decoded all of them, then sliced one page. Memory therefore grew
with the whole table.

This module streams the keyword ids in fixed chunks (keyset on ``k.id``, which
is deterministic and does not skip or repeat rows within one stream). It
decodes and matches each chunk, then:

* pass 1 (count only): counts the matches. Memory is one chunk.
* pass 2 (page): keeps only the best ``page * size`` candidates in a heap and
  returns the requested slice. Memory is the page depth, never the total.

A page past the end is not clamped here. It returns no rows, and the caller's
page model (``core.table_paging.fetch_page``) decides between an empty batch
and a clamp to the last page. So an absurd page number costs only the count
pass.

Ordering is the existing order, including the tie-break each caller had. The
HTML page breaks ties by id in the sort direction. The API breaks them by id
ascending. Each caller passes its own rank function, so neither changes.

The database function is passed in (``run``) so the module has no import
cycle with ``Api.utils`` and can be tested directly. ``run`` must raise on a
database error; this module never turns an error into an empty result.
"""
from __future__ import annotations

import heapq
import logging
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

from core.serialization import unpack_int_list
from core.table_paging import total_pages_for

logger = logging.getLogger(__name__)

#: Keywords decoded per chunk. Memory per scan is one chunk of rows plus the
#: candidates kept for the page.
CHUNK = 1000

# Candidate tuple: (id, keyword_bytes, category_id, text, usage_count)
Candidate = Tuple[int, Any, Any, str, int]
Run = Callable[..., Any]


class Rank:
    """A sort key: a sequence of (value, descending) parts, compared in order.

    ``heapq`` keeps the smallest keys, so a descending part is compared with
    the inequality reversed. Values in one part are never mixed across rows.
    """
    __slots__ = ("parts",)

    def __init__(self, parts: Sequence[Tuple[Any, bool]]):
        self.parts = tuple(parts)

    def __lt__(self, other: "Rank") -> bool:
        for (a, desc), (b, _desc) in zip(self.parts, other.parts):
            if a != b:
                return a > b if desc else a < b
        return False


def html_rank(sort_by: str, descending: bool) -> Callable[[Candidate], Rank]:
    """The HTML keywords page: ties follow the sort direction (by id)."""
    def rank(c: Candidate) -> Rank:
        kid, text, usage = c[0], c[3], c[4]
        if sort_by == "text":
            return Rank([(text.lower(), descending), (kid, descending)])
        if sort_by == "id":
            return Rank([(kid, descending)])
        if sort_by == "status":
            return Rank([(usage > 0, descending), (usage, descending), (kid, descending)])
        return Rank([(usage, descending), (kid, descending)])  # usage_count
    return rank


def api_rank(sort_by: str, descending: bool) -> Callable[[Candidate], Rank]:
    """The /api/keywords endpoint: ties are always id ascending."""
    def rank(c: Candidate) -> Rank:
        kid, text, usage = c[0], c[3], c[4]
        if sort_by == "usage_count":
            return Rank([(usage, descending), (kid, False)])
        if sort_by == "text":
            return Rank([(text.lower(), descending), (kid, False)])
        if sort_by == "id":
            return Rank([(kid, descending)])
        if sort_by == "status":
            return Rank([(usage > 0, descending), (usage, descending), (kid, False)])
        return Rank([(kid, False)])  # not sorted: id ascending, as before
    return rank


_WORDS_SQL = "SELECT id, word FROM words WHERE id = ANY(%s)"
_USAGE_SQL = (
    "SELECT kp.keyword_id, COUNT(DISTINCT p.id) "
    "FROM keywords_hashs kp "
    "JOIN hash_contexts hc ON hc.hash_id = kp.hash_id "
    "JOIN paths p ON p.context_id = hc.id "
    "WHERE kp.keyword_id = ANY(%s) GROUP BY kp.keyword_id"
)
_CATEGORY_SQL = (
    "SELECT cat.id, w.word FROM categorys cat "
    "LEFT JOIN words w ON cat.word_id = w.id WHERE cat.id = ANY(%s)"
)


def decode_texts(run: Run, rows: Sequence[Tuple[Any, Any, Any]]) -> dict:
    """Keyword id -> text for these rows. Mirrors ``batch_load_keywords_from_rows``
    (same words, same order, same skips), but a database error raises."""
    word_ids_by_kw = {}
    for kid, blob, _cat in rows:
        if not kid or not blob:
            continue
        try:
            ids = unpack_int_list(blob)
        except Exception as exc:  # undecodable blob: no text, as before
            logger.warning("keyword %s: cannot unpack word ids: %s", kid, exc)
            continue
        if ids and isinstance(ids, list):
            word_ids_by_kw[kid] = ids
    all_word_ids = {w for ids in word_ids_by_kw.values() for w in ids}
    word_map = {}
    if all_word_ids:
        for wid, word in run(_WORDS_SQL, (list(all_word_ids),), fetch="all") or []:
            word_map[wid] = word
    texts = {}
    for kid, ids in word_ids_by_kw.items():
        words = [word_map[w] for w in ids if w in word_map]
        if words:
            texts[kid] = " ".join(words)
    return texts


def stream_keyword_rows(run: Run, conditions: Sequence[str], params: Sequence[Any],
                        chunk: int = CHUNK) -> Iterator[List[Tuple[Any, Any, Any]]]:
    """Keyword rows (id, keyword_bytes, category_id) in id order, ``chunk`` at a
    time. Keyset on ``k.id``: each chunk starts after the last id of the one
    before, so no row is repeated and none is skipped within one stream."""
    where = "".join(f" AND {c}" for c in conditions)
    sql = (f"SELECT k.id, k.keyword, k.category_id FROM keywords k "
           f"WHERE k.id > %s{where} ORDER BY k.id LIMIT %s")
    after = 0
    while True:
        rows = run(sql, (after, *params, chunk), fetch="all") or []
        if not rows:
            return
        yield rows
        after = rows[-1][0]
        if len(rows) < chunk:
            return


def usage_counts(run: Run, ids: Sequence[int]) -> dict:
    """Distinct paths per keyword for these ids. Same count as the old LEFT JOIN
    (NULL paths are not counted); a keyword with no paths is absent (use 0)."""
    if not ids:
        return {}
    return {kid: n for kid, n in run(_USAGE_SQL, (list(ids),), fetch="all") or []}


def _scan(run: Run, *, needle: Optional[str], conditions: Sequence[str],
          params: Sequence[Any], rank: Optional[Callable[[Candidate], Rank]],
          keep: int, chunk: int,
          needle_ids: Optional[Sequence[int]] = None) -> Tuple[List[Candidate], int]:
    """One streamed pass. ``keep == 0`` counts only. Otherwise returns the
    ``keep`` best candidates in rank order, plus the total number of matches.

    ``needle`` is the lower-case text to find. ``None`` means every keyword
    counts, including one with no text (the sort-by-text listing does that).

    ``needle_ids`` are the word ids a query resolves to through its translations
    (``services/i18n/term_labels.py``). A keyword matches if its text contains
    the needle **or** any of the words it is made of is one of those ids, so a
    keyword written ``political stability`` is found by ``الاستقرار السياسي``."""
    total = 0
    wanted = set(int(w) for w in (needle_ids or ()))

    def matches() -> Iterator[Candidate]:
        nonlocal total
        for rows in stream_keyword_rows(run, conditions, params, chunk):
            texts = decode_texts(run, rows)
            hits = []
            for kid, blob, cat in rows:
                text = texts.get(kid)
                if needle is None:
                    hits.append((kid, blob, cat, text or ""))
                elif text and needle in text.lower():
                    hits.append((kid, blob, cat, text))
                elif wanted and _shares_a_word(blob, wanted):
                    hits.append((kid, blob, cat, text or ""))
            total += len(hits)
            if keep == 0:
                continue
            usage = usage_counts(run, [h[0] for h in hits])
            for kid, blob, cat, text in hits:
                yield (kid, blob, cat, text, usage.get(kid, 0))

    if keep == 0:
        for _ in matches():
            pass
        return [], total
    return heapq.nsmallest(keep, matches(), key=rank), total


def keyword_page(run: Run, *, page: int, size: int, needle: Optional[str],
                 conditions: Sequence[str], params: Sequence[Any],
                 rank: Callable[[Candidate], Rank],
                 chunk: int = CHUNK,
                 needle_ids: Optional[Sequence[int]] = None) -> Tuple[List[Candidate], int]:
    """``(rows_for_page, total)``. ``rows_for_page`` is empty for a page past the
    end; the caller's page model decides what that means."""
    _, total = _scan(run, needle=needle, conditions=conditions, params=params,
                     rank=None, keep=0, chunk=chunk, needle_ids=needle_ids)
    if page > total_pages_for(total, size):
        return [], total
    top, _ = _scan(run, needle=needle, conditions=conditions, params=params,
                   rank=rank, keep=page * size, chunk=chunk, needle_ids=needle_ids)
    return top[(page - 1) * size:], total


def _shares_a_word(blob, wanted: set) -> bool:
    """Whether this keyword's packed word list intersects ``wanted``.

    An undecodable blob is not a match: the same rule ``decode_texts`` applies,
    so a corrupt row cannot be reported as relevant to a query."""
    if not blob:
        return False
    try:
        ids = unpack_int_list(blob)
    except Exception:  # noqa: BLE001 - undecodable is not a match
        return False
    return any(word_id in wanted for word_id in ids or ())


def category_names(run: Run, category_ids: Sequence[Any]) -> dict:
    """Category id -> display name for these ids (None where there is none)."""
    ids = [c for c in set(category_ids) if c is not None]
    if not ids:
        return {}
    return {cid: word for cid, word in run(_CATEGORY_SQL, (ids,), fetch="all") or []}
