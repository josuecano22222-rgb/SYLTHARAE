"""Processing reports: one query over the stored paths.

The report answers "what did we ingest, and what happened to it" without a
report table, a summary table or any denormalised counter. Everything comes
from the rows the ingestion already writes:

* ``paths`` - one row per stored occurrence, with its processing status;
* ``hash_contexts`` - the source and the side (context/aspect) it came from;
* ``sources`` / ``sides`` - their names;
* ``words_hashs`` -> ``words_categorys`` -> ``categorys`` -> ``words`` - the
  classifications applied to the canonical content, and the category term
  (the word the category is named with);
* ``keywords_hashs`` -> ``keywords`` - the keywords found in it.

A single statement, one round trip, one row back: totals, per-file-type
success/failure, and the classifications, terms and keywords that occur in the
filtered population. Filters are the source, the side, a date range and a time
range - all optional and all bound parameters.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, Dict, Optional

#: Statuses counted as a success, a failure or a partial result. Anything not
#: listed is "not finished" (discovered/queued/processing/retrying) or
#: deliberately not processed (unsupported/skipped), which the report keeps
#: apart rather than calling it a failure.
SUCCESS_STATUSES = ("processed",)
PARTIAL_STATUSES = ("partially_processed",)
FAILED_STATUSES = ("failed",)
SKIPPED_STATUSES = ("unsupported", "skipped")

_REPORT_SQL = """
WITH f AS (
    SELECT p.id            AS path_id,
           p.file_type     AS file_type,
           p.processing_status AS processing_status,
           hc.hash_id      AS hash_id,
           hc.source_id    AS source_id,
           hc.side_id      AS side_id,
           s.name          AS source_name,
           sd.name         AS side_name
    FROM paths p
    LEFT JOIN hash_contexts hc ON hc.id = p.context_id
    LEFT JOIN sources s ON s.id = hc.source_id
    LEFT JOIN sides sd ON sd.id = hc.side_id
    WHERE (%(source_id)s::int IS NULL OR hc.source_id = %(source_id)s::int)
      AND (%(side_id)s::int IS NULL OR hc.side_id = %(side_id)s::int)
      AND (%(date_from)s::date IS NULL OR p.date_creation >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.date_creation <= %(date_to)s::date)
      AND (%(time_from)s::time IS NULL
           OR p.status_updated_at IS NOT NULL
          AND p.status_updated_at::time >= %(time_from)s::time)
      AND (%(time_to)s::time IS NULL
           OR p.status_updated_at IS NOT NULL
          AND p.status_updated_at::time <= %(time_to)s::time)
), totals AS (
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(success)s)) AS success,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(partial)s)) AS partial,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(failed)s))  AS failed,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(skipped)s)) AS skipped
    FROM f
), by_type AS (
    SELECT file_type,
           COUNT(*) AS total,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(success)s)) AS success,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(partial)s)) AS partial,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(failed)s))  AS failed,
           COUNT(*) FILTER (WHERE processing_status = ANY(%(skipped)s)) AS skipped
    FROM f
    GROUP BY file_type
), classifications AS (
    SELECT c.id  AS category_id,
           w.word AS category_term,
           COUNT(DISTINCT x.path_id) AS paths
    FROM f x
    JOIN words_hashs wh ON wh.hash_id = x.hash_id
    JOIN words_categorys wc ON wc.word_id = wh.word_id
    JOIN categorys c ON c.id = wc.category_id
    JOIN words w ON w.id = c.word_id
    GROUP BY c.id, w.word
), keywords AS (
    -- ``keywords.keyword`` is a packed list of word ids, not text: a keyword
    -- here is a phrase. The facet is grouped by id; the phrase is decoded in
    -- Python for the bounded top-N (see ``attach_keyword_phrases``).
    SELECT kh.keyword_id AS keyword_id, COUNT(DISTINCT x.path_id) AS paths
    FROM f x
    JOIN keywords_hashs kh ON kh.hash_id = x.hash_id
    GROUP BY kh.keyword_id
), by_source AS (
    SELECT source_id, source_name, COUNT(*) AS paths
    FROM f WHERE source_id IS NOT NULL
    GROUP BY source_id, source_name
), by_side AS (
    SELECT side_id, side_name, COUNT(*) AS paths
    FROM f WHERE side_id IS NOT NULL
    GROUP BY side_id, side_name
)
SELECT jsonb_build_object(
    'totals', (SELECT to_jsonb(t) FROM totals t),
    'by_file_type', COALESCE((SELECT jsonb_agg(to_jsonb(bt) ORDER BY bt.file_type)
                              FROM by_type bt), '[]'::jsonb),
    'classifications', COALESCE((SELECT jsonb_agg(to_jsonb(cls) ORDER BY cls.category_term)
                                 FROM classifications cls), '[]'::jsonb),
    'keywords', COALESCE((SELECT jsonb_agg(to_jsonb(kw) ORDER BY kw.paths DESC,
                                                         kw.keyword_id)
                          FROM keywords kw), '[]'::jsonb),
    'sources', COALESCE((SELECT jsonb_agg(to_jsonb(bs) ORDER BY bs.source_name)
                         FROM by_source bs), '[]'::jsonb),
    'sides', COALESCE((SELECT jsonb_agg(to_jsonb(bd) ORDER BY bd.side_name)
                       FROM by_side bd), '[]'::jsonb)
) AS report
"""


#: How many keyword phrases the report decodes. ``keywords.keyword`` holds a
#: packed word-id list, so the text is assembled in Python; the facet is
#: ordered by occurrence, and this bounds the work rather than the population.
MAX_KEYWORD_PHRASES = 200


def keyword_phrases(keyword_ids) -> Dict[int, str]:
    """``{keyword_id: "phrase text"}`` for the given keyword ids.

    The phrase is the words the keyword stores, in order: ``keywords.keyword``
    is a packed list of ``words.id`` values (the same encoding as stored
    content), so it is decoded with the shipped serializer and joined against
    ``words`` in one query. An undecodable row is reported as its id, never as
    a silent blank.
    """
    ids = [int(i) for i in keyword_ids if i is not None][:MAX_KEYWORD_PHRASES]
    if not ids:
        return {}
    from Api.utils import execute_query
    from core.serialization import unpack_int_list

    rows = execute_query(
        "SELECT id, keyword FROM keywords WHERE id = ANY(%s)", (ids,), fetch="all") or []
    word_ids: Dict[int, list] = {}
    for keyword_id, blob in rows:
        try:
            word_ids[keyword_id] = unpack_int_list(blob)
        except Exception:  # pragma: no cover - legacy/undecodable payload
            word_ids[keyword_id] = []
    wanted = sorted({w for words in word_ids.values() for w in words})
    texts: Dict[int, str] = {}
    if wanted:
        for word_id, word in execute_query(
                "SELECT id, word FROM words WHERE id = ANY(%s)",
                (wanted,), fetch="all") or []:
            texts[word_id] = word
    return {keyword_id: (" ".join(texts.get(w, str(w)) for w in words) or str(keyword_id))
            for keyword_id, words in word_ids.items()}


def attach_keyword_phrases(report: Dict[str, Any]) -> Dict[str, Any]:
    """Add ``keyword`` text to each keyword facet row, in place."""
    rows = report.get("keywords") or []
    phrases = keyword_phrases([r.get("keyword_id") for r in rows])
    for row in rows:
        row["keyword"] = phrases.get(row.get("keyword_id"), str(row.get("keyword_id")))
    return report


def _params(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    filters = filters or {}
    return {
        "source_id": filters.get("source_id"),
        "side_id": filters.get("side_id"),
        "date_from": filters.get("date_from") or None,
        "date_to": filters.get("date_to") or None,
        "time_from": filters.get("time_from") or None,
        "time_to": filters.get("time_to") or None,
        "success": list(SUCCESS_STATUSES),
        "partial": list(PARTIAL_STATUSES),
        "failed": list(FAILED_STATUSES),
        "skipped": list(SKIPPED_STATUSES),
    }


def processing_report(cur, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The report for ``filters``, read through ``cur`` in one statement.

    The keyword facet carries ids only (the phrase is a packed word-id list);
    ``report_for_request`` is the form the pages use, with the text decoded.
    """
    cur.execute(_REPORT_SQL, _params(filters))
    row = cur.fetchone()
    report = row[0] if row else None
    if isinstance(report, str):  # pragma: no cover - drivers returning text
        import json

        report = json.loads(report)
    report = report or {}
    report.setdefault("totals", {"total": 0, "success": 0, "partial": 0,
                                 "failed": 0, "skipped": 0})
    for key in ("by_file_type", "classifications", "keywords", "sources", "sides"):
        report.setdefault(key, [])
    return report


def report_for_request(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``processing_report`` against the application's connection."""
    from Api.utils import execute_query

    row = execute_query(_REPORT_SQL, _params(filters), fetch="one")
    report = (row[0] if isinstance(row, (tuple, list)) else row) or {}
    if isinstance(report, str):  # pragma: no cover
        import json

        report = json.loads(report)
    report.setdefault("totals", {"total": 0, "success": 0, "partial": 0,
                                 "failed": 0, "skipped": 0})
    for key in ("by_file_type", "classifications", "keywords", "sources", "sides"):
        report.setdefault(key, [])
    # The statement returns keyword ids; the phrase text is decoded here (see
    # ``keyword_phrases``) because the encoding is a packed word-id list.
    return attach_keyword_phrases(report)


def _as_date(value):
    return value if isinstance(value, date) else None


def _as_time(value):
    return value if isinstance(value, time) else None
