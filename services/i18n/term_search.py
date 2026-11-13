"""Glue between a typed search term and the predicates that already exist.

Six searches already match a typed string against a name: the category list, the
category API, the category tree, the word picker, the keyword table and the
archives/file-analysis facets. Adding cross-language recall to them is one
change in each, and this module is the one change:

    expansion = expand_for_search(request.args.get("search"))
    if expansion:
        where = "(w.word ILIKE %s OR w.id = ANY(%s))"
        params = [f"%{search}%", list(expansion.word_ids)]
    else:
        where = "w.word ILIKE %s"           # exactly today's behaviour
        params = [f"%{search}%"]

Why a helper rather than six copies: three details have to be identical in all
six places or the searches disagree - the guards (:mod:`services.i18n.term_labels`
owns them), the parameter form (a Python list, never a tuple, for ``= ANY``), and
what happens when the lookup fails. Here the answer is that a failed lookup
falls back to the text search and is logged: a translation table that cannot be
read must not make search answer nothing, but it must not be silent either.

Document-content search is deliberately **not** wired to this. Matching a
document because it contains a *translation* of one of its words would change
recall on the main search page without the reader asking for it; the plan keeps
it out of scope (``docs/plans/CATEGORY_TERM_I18N.md`` D3).
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from services.i18n.term_labels import ID_SET_CAP, Expansion, expand_query

logger = logging.getLogger(__name__)


def _execute(sql, params=None, fetch="all"):
    from Api.utils import execute_query
    return execute_query(sql, params, fetch=fetch)


def expand_for_search(query: Optional[str], *, run=None,
                      cap: int = ID_SET_CAP) -> Expansion:
    """The expansion for one query, or an empty one **and a log line**.

    Search must not fail because the resolver did: a database hiccup here has to
    degrade to the pre-existing text match, not turn a search page into a 500.
    The degradation is logged at ``warning`` with the query, so "the Arabic
    search found nothing" has an explanation in the log rather than a shrug.
    """
    text = (query or "").strip()
    if not text:
        return Expansion(query=text, key="")
    try:
        return expand_query(run or _execute, text, cap=cap)
    except Exception as exc:  # noqa: BLE001 - search degrades, it does not fail
        logger.warning("term expansion unavailable for %r: %s: %s",
                       text, type(exc).__name__, exc)
        return Expansion(query=text, key="")


def id_any_params(expansion: Expansion) -> List[Any]:
    """The parameter list for ``... = ANY(%s)`` - a ``list``, which psycopg2 maps
    to an array. A tuple would be sent as a row constructor and fail at runtime."""
    return [list(expansion.word_ids)]


def summary(expansion: Expansion) -> dict:
    """A small, JSON-safe report for a response body or a log line."""
    return {"key": expansion.key, "words": len(expansion.word_ids),
            "truncated": expansion.truncated,
            "ambiguous": expansion.ambiguous,
            "too_short": expansion.too_short}
