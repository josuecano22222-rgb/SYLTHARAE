"""Term labels and query expansion: the two places a language meets a term.

Two questions, one module, because they must agree. If the label layer thinks
``political`` is ``سياسي`` and the search layer disagrees, the interface shows
one thing and finds another.

Display: :func:`labels_for`
===========================

One batched query for a page of word ids, and a fallback chain that never
returns an empty string:

    requested locale -> its base language -> the default language -> the canonical word

``ar-EG`` is not a stored language, so its base language ``ar`` is tried before
the default. Every label says where it came from - ``translated``,
``base-language``, ``default-language`` or ``canonical`` - and the caller surfaces
that, so a reader can see that a name has not been translated yet instead of
seeing an English word presented as Arabic.

Search: :func:`expand_query`
============================

The typed query is normalised by :func:`core.i18n.term_key.key_variants` and
matched **exactly** against ``term_names.match_key``. There is no prefix, no
similarity and no stemming: a short query is a real risk of matching half the
taxonomy, and "close enough" is not a property a legal taxonomy should have.

Three guards, each measured in ``tests/integration/test_term_names_search.py``:

* ``MIN_QUERY_CHARS`` - a query shorter than this expands to nothing, and the
  caller's ordinary text search runs unchanged. One Arabic letter is not a term.
* ``ID_SET_CAP`` - at most this many word ids are handed to SQL. A query that
  matches more is truncated and says so (``truncated=True``), which the caller
  reports rather than silently narrowing.
* ambiguity is returned, never resolved: a ``match_key`` shared by several words
  comes back as all of them, with ``ambiguous=True`` and the candidates, so the
  caller can offer a disambiguation list instead of guessing.

The caller decides what to do with an empty expansion (nothing) or a truncated
one (search, and say so).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from core.i18n.term_key import key_variants, term_key

#: Language used when the requested one has no name and the base language does not
#: either. This is the language every canonical ``words.word`` is written in.
DEFAULT_LANGUAGE = "en"

#: A query shorter than this (after normalisation) does not expand at all.
MIN_QUERY_CHARS = 3

#: The most word ids one query may contribute to a predicate.
ID_SET_CAP = 500

#: The most candidates reported back for one ambiguous key.
MAX_CANDIDATES = 20

Run = Callable[..., Any]


@dataclass(frozen=True)
class Label:
    """One word's name in one language, and where that name came from."""

    word_id: int
    name: str
    language: Optional[str]
    #: ``translated`` | ``base-language`` | ``default-language`` | ``canonical``
    source: str

    @property
    def translated(self) -> bool:
        return self.source == "translated"


@dataclass(frozen=True)
class CategoryLabel:
    """A category's displayed name, which is its word's label."""

    category_id: int
    name: str
    language: Optional[str]
    source: str

    @property
    def translated(self) -> bool:
        return self.source in ("translated", "base-language")


@dataclass(frozen=True)
class Candidate:
    word_id: int
    name: str
    language: str


@dataclass(frozen=True)
class Expansion:
    """The word ids a query resolves to, and everything the caller must know."""

    query: str
    key: str
    word_ids: Tuple[int, ...] = ()
    truncated: bool = False
    too_short: bool = False
    candidates: Tuple[Candidate, ...] = ()
    #: True when one key resolved to more than one word.
    ambiguous: bool = False
    #: True when the number of candidates exceeds :data:`MAX_CANDIDATES`.
    candidates_truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.word_ids)


def language_chain(locale: Optional[str]) -> Tuple[str, ...]:
    """The languages to try for ``locale``, most specific first.

    ``ar-EG`` -> ``('ar-eg', 'ar', 'en')``; ``en`` -> ``('en',)``; ``None`` ->
    ``('en',)``. Duplicates are removed, so ``en-GB`` does not try ``en`` twice.
    """
    ordered: List[str] = []
    if locale:
        lowered = str(locale).strip().lower().replace("_", "-")
        if lowered:
            ordered.append(lowered)
            base = lowered.split("-", 1)[0]
            if base and base not in ordered:
                ordered.append(base)
    if DEFAULT_LANGUAGE not in ordered:
        ordered.append(DEFAULT_LANGUAGE)
    return tuple(ordered)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
#: Best name first: the language's position *in the request's chain*, then
#: name type, then the oldest row. Ordering by ``language`` instead looks right
#: and is wrong - alphabetically ``en`` sorts before ``he``, so an Arabic reader
#: was handed the English name whenever both rows existed.
_LABELS_SQL = (
    "SELECT word_id, language, name_type, name FROM term_names "
    "WHERE word_id = ANY(%s) AND language = ANY(%s) "
    "ORDER BY word_id, array_position(%s::text[], language), CASE name_type "
    "WHEN 'preferred' THEN 0 WHEN 'alternate' THEN 1 ELSE 2 END, id"
)

_CANONICAL_SQL = "SELECT id, word FROM words WHERE id = ANY(%s)"


def _source_for(language: str, locale_chain: Sequence[str]) -> str:
    if locale_chain and language == locale_chain[0]:
        return "translated"
    if len(locale_chain) > 1 and language == locale_chain[1] and language != DEFAULT_LANGUAGE:
        return "base-language"
    if language == DEFAULT_LANGUAGE:
        return "default-language"
    return "translated"


def labels_for(run: Run, word_ids: Iterable[int], locale: Optional[str] = None) -> Dict[int, Label]:
    """Labels for a page of word ids, in one query plus the canonical fallback.

    ``run(sql, params, fetch='all')`` must raise on a database error; this
    function never turns an error into a silent fallback, because "the name is
    missing" and "the database is down" must not look the same.
    """
    ids = sorted({int(w) for w in word_ids if w is not None})
    if not ids:
        return {}

    chain = language_chain(locale)
    rows = run(_LABELS_SQL, (ids, list(chain), list(chain)), fetch="all") or []

    chosen: Dict[int, Label] = {}
    for word_id, language, _name_type, name in rows:
        if word_id in chosen:
            continue  # the ORDER BY already put the best name first
        chosen[word_id] = Label(
            word_id=int(word_id), name=name, language=language,
            source=_source_for(language, chain),
        )

    missing = [w for w in ids if w not in chosen]
    if missing:
        for word_id, word in run(_CANONICAL_SQL, (missing,), fetch="all") or []:
            chosen[int(word_id)] = Label(word_id=int(word_id), name=word,
                                         language=None, source="canonical")
    return chosen


_CATEGORY_LABELS_SQL = """
SELECT c.id, w.word, t.language, t.name
  FROM categorys c
  JOIN words w ON w.id = c.word_id
  LEFT JOIN LATERAL (
       SELECT language, name FROM term_names
        WHERE word_id = w.id AND language = ANY(%s)
        ORDER BY array_position(%s::text[], language),
                 CASE name_type WHEN 'preferred' THEN 0
                                WHEN 'alternate' THEN 1 ELSE 2 END, id
        LIMIT 1) t ON TRUE
 WHERE c.id = ANY(%s)
"""


def labels_for_categories(run: Run, category_ids: Iterable[int],
                          locale: Optional[str] = None) -> Dict[int, CategoryLabel]:
    """Category id -> label, in **one** query.

    The category list already selects ``c.id, w.word``; asking the resolver for
    the word ids first would be a second query for data the first one had. The
    lateral sub-select picks the best name per word: the most specific language
    in the chain, ``preferred`` before ``alternate``, then the lowest id.
    """
    ids = sorted({int(c) for c in category_ids if c is not None})
    if not ids:
        return {}
    chain = language_chain(locale)
    out: Dict[int, CategoryLabel] = {}
    for category_id, word, language, name in run(
            _CATEGORY_LABELS_SQL, (list(chain), list(chain), ids), fetch="all") or []:
        if language is None:
            out[int(category_id)] = CategoryLabel(
                category_id=int(category_id), name=word, language=None,
                source="canonical")
        else:
            out[int(category_id)] = CategoryLabel(
                category_id=int(category_id), name=name, language=language,
                source=_source_for(language, chain))
    return out


def label_map(run: Run, word_ids: Iterable[int], locale: Optional[str] = None) -> Dict[int, str]:
    """``{word_id: name}`` - the shape a JSON dict builder wants."""
    return {wid: label.name for wid, label in labels_for(run, word_ids, locale).items()}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
_EXPAND_SQL = (
    "SELECT t.word_id, w.word, t.language FROM term_names t "
    "JOIN words w ON w.id = t.word_id "
    "WHERE t.match_key = ANY(%s) "
    "ORDER BY t.word_id, t.language LIMIT %s"
)


def expand_query(run: Run, query: Optional[str], *, cap: int = ID_SET_CAP,
                 languages: Optional[Sequence[str]] = None) -> Expansion:
    """The word ids that ``query`` names, in any language.

    ``languages=None`` means every language, which is what makes an Arabic query
    find an English-named category and the reverse. ``cap`` bounds the id set
    handed to the caller's predicate.
    """
    text = (query or "").strip()
    variants = key_variants(text)
    key = variants[0] if variants else ""
    if not variants or len(key) < MIN_QUERY_CHARS:
        return Expansion(query=text, key=key, too_short=bool(key))

    sql = _EXPAND_SQL
    params: List[Any] = [list(variants), cap + MAX_CANDIDATES + 1]
    if languages:
        sql = sql.replace(" WHERE ", " WHERE t.language = ANY(%s) AND ", 1)
        params = [list(variants), list(languages), cap + MAX_CANDIDATES + 1]

    rows = run(sql, tuple(params), fetch="all") or []
    word_ids: List[int] = []
    seen = set()
    candidates: List[Candidate] = []
    for word_id, word, language in rows:
        if word_id not in seen:
            seen.add(word_id)
            word_ids.append(int(word_id))
        if len(candidates) < MAX_CANDIDATES:
            candidates.append(Candidate(word_id=int(word_id), name=word, language=language))

    truncated = len(word_ids) > cap
    candidates_truncated = len(candidates) >= MAX_CANDIDATES and len(rows) > len(candidates)
    return Expansion(
        query=text,
        key=key,
        word_ids=tuple(word_ids[:cap]),
        truncated=truncated,
        ambiguous=len(word_ids) > 1,
        candidates=tuple(candidates),
        candidates_truncated=candidates_truncated,
    )


def describe(expansion: Expansion) -> Dict[str, Any]:
    """The JSON shape an endpoint reports for an expansion it acted on."""
    if expansion.too_short:
        return {"query": expansion.query,
                "note": f"queries shorter than {MIN_QUERY_CHARS} characters are not expanded"}
    payload: Dict[str, Any] = {
        "query": expansion.query,
        "key": expansion.key,
        "words": len(expansion.word_ids),
    }
    if expansion.ambiguous:
        payload["ambiguous"] = True
        payload["candidates"] = [{"word_id": c.word_id, "name": c.name,
                                  "language": c.language}
                                 for c in expansion.candidates]
        if expansion.candidates_truncated:
            payload["candidates_truncated"] = True
    if expansion.truncated:
        payload["truncated"] = True
        payload["note"] = f"more than {ID_SET_CAP} words matched; only the first {ID_SET_CAP} are searched"
    return payload
