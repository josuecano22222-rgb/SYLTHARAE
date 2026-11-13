"""File Analysis API - the modern Category/Keywords/Titles/Sources/Sides/
Relations/Geolocation explorer (see docs on the ``File_Management_Analysis_System``
legacy page this replaces).

Design notes, since this sits next to the older ``Api/routes/archives*.py``
implementation it draws SQL patterns from:

* Every count here is real: "0 items" means the underlying join genuinely
  returns nothing (e.g. no analyst has curated any category/keyword yet),
  never a placeholder.
* Category/Keyword file counts join through ``words_categorys``/``keywords``
  -> ``*_hashs`` -> ``hash_contexts`` -> ``paths``, exactly like the legacy
  archives endpoints, because that is the real schema (a category or
  keyword is anchored to canonical content via ``hash_id``, not per path).
* Relations use a STRICTLY correct definition the legacy endpoint does not:
  a relation is content (one ``hashs`` row) that appears under 2+ DISTINCT
  (source, side) pairs. Two copies of the same file filed under the SAME
  source and side are ordinary duplicates, not a relation - see
  which makes ``COUNT(DISTINCT hash_contexts.id) > 1`` for a hash exactly
  equivalent to "spans >= 2 distinct (source, side) pairs".
* File lists for every facet are normalized into the exact shape
  ``/api/search`` returns (``file_name``, ``file_type``, ``source_id``,
  ...) so the frontend can drop them straight into the same store state
  the rest of the app already renders with (DataTable, ActionsMenu,
  RecordDetail, UniversalExportDialog) - no new file-list UI needed.
"""

from flask import Blueprint, request, jsonify
import logging
import zlib

from Api.utils import execute_query
from core.serialization import unpack_int_list
from services.i18n.term_search import expand_for_search
from core.errors import client_error
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from Api.services.geo_extraction_service import scan_and_tag_geolocations

logger = logging.getLogger(__name__)

file_analysis_bp = Blueprint('file_analysis', __name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# A relation is a hashs row whose content shows up in >= 2 distinct
# (source, side) contexts. hash_contexts carries UNIQUE(hash_id, source_id,
# side_id), so counting distinct hash_contexts rows for a hash is exactly
# counting distinct (source, side) pairs - never inflated by ordinary
# same-source/same-side duplicate paths.
_RELATION_HAVING = "COUNT(DISTINCT hc.id) > 1"


def _paginate_list(items, page, per_page):
    page = max(1, page)
    per_page = max(1, min(200, per_page))
    total = len(items)
    total_pages = (total + per_page - 1) // per_page if total else 1
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]
    return page_items, {
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
    }


def _int_arg(name, default=None):
    return request.args.get(name, default, type=int)


def _str_arg(name, default=''):
    return request.args.get(name, default).strip()


def _unpack_maybe_compressed(raw):
    """``unpack_int_list`` for both keyword blobs (plain JSON) and title
    blobs (``titles_content_repo.insert_titles_content`` zlib-compresses the
    packed ids before storing - see ``insert_title()``/``Get_titles_content``
    in that repo, which decompresses internally). The shared read helper
    ``Api.utils.utils.load_text_title``/the legacy archives routes call
    ``unpack_int_list`` directly on the still-compressed bytes, which raises
    and is swallowed, so title text has been silently empty there. Try a
    zlib decompress first (titles); fall back to the raw bytes (keywords,
    which are never compressed) so both blob families decode correctly.
    """
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    return unpack_int_list(raw)


def _decode_word_blob(blob, word_dict):
    if not blob:
        return ''
    try:
        if isinstance(blob, memoryview):
            blob = bytes(blob)
        elif not isinstance(blob, bytes):
            blob = bytes(blob)
        if len(blob) < 2:
            return ''
        word_ids = _unpack_maybe_compressed(blob)
        if not word_ids or not isinstance(word_ids, list):
            return ''
        words = [word_dict.get(wid, '') for wid in word_ids]
        return ' '.join(w for w in words if w).strip()
    except Exception:
        return ''


def _batch_words_for_blobs(blobs):
    """All the distinct word ids referenced by a set of packed keyword/title blobs."""
    all_ids = set()
    for blob in blobs:
        if not blob:
            continue
        try:
            raw = bytes(blob) if not isinstance(blob, bytes) else blob
            if len(raw) < 2:
                continue
            ids = _unpack_maybe_compressed(raw)
            if ids and isinstance(ids, list):
                all_ids.update(ids)
        except Exception:
            continue
    if not all_ids:
        return {}
    placeholders = ','.join(['%s'] * len(all_ids))
    rows = execute_query(f"SELECT id, word FROM words WHERE id IN ({placeholders})", list(all_ids), fetch="all")  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
    return {r[0]: r[1] for r in (rows or [])}


def _rows_to_search_shape(rows):
    """``rows``: iterable of (id, file_name, file_type, file_size, file_status,
    file_date, date_creation, source_name, side_name, source_id, side_id)
    -> the exact dict shape ``/api/search`` returns, with categories attached."""
    results = []
    for r in rows:
        results.append({
            'id': r[0],
            'file_name': r[1],
            'file_type': r[2],
            'file_size': r[3],
            'file_status': r[4],
            'file_date': r[5].isoformat() if r[5] else None,
            'date_creation': r[6].isoformat() if r[6] else None,
            'source_name': r[7] or 'Unknown',
            'side_name': r[8] or 'Unknown',
            'source_id': r[9],
            'side_id': r[10],
            'relevance_score': None,
        })
    try:
        from Api.services.analyst_categories import AnalystCategoryService
        results = AnalystCategoryService.attach_categories_to_results(results)
    except Exception as e:
        logger.warning(f"Could not attach analyst categories to file-analysis rows: {e}")
    return results


_FILE_COLUMNS = """
    p.id, p.file_name, p.file_type, p.file_size, p.file_status,
    p.file_date, p.date_creation,
    s.name AS source_name, si.name AS side_name, hc.source_id, hc.side_id
"""
_FILE_JOINS = """
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    LEFT JOIN sources s ON hc.source_id = s.id
    LEFT JOIN sides si ON hc.side_id = si.id
"""


# ---------------------------------------------------------------------------
# Overview (the 7-tile hub)
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/overview', methods=['GET'])
def fa_overview():
    try:
        counts = {}

        counts['categories'] = execute_query("""
            SELECT COUNT(DISTINCT c.id) FROM categorys c
            JOIN words_categorys wc ON wc.category_id = c.id
            JOIN words_hashs wp ON wp.word_id = wc.word_id
        """, fetch="one")[0]

        counts['keywords'] = execute_query("""
            SELECT COUNT(DISTINCT k.id) FROM keywords k
            JOIN keywords_hashs kp ON kp.keyword_id = k.id
        """, fetch="one")[0]

        counts['words'] = execute_query("""
            SELECT COUNT(DISTINCT wc.word_id) FROM words_categorys wc
            JOIN words_hashs wh ON wh.word_id = wc.word_id
        """, fetch="one")[0]

        counts['titles'] = execute_query("""
            SELECT COUNT(*) FROM titles_content WHERE title_status = 'Main'
        """, fetch="one")[0]

        counts['sources'] = execute_query("""
            SELECT COUNT(DISTINCT s.id) FROM sources s
            JOIN hash_contexts hc ON hc.source_id = s.id
            JOIN paths p ON p.context_id = hc.id
        """, fetch="one")[0]

        counts['sides'] = execute_query("""
            SELECT COUNT(DISTINCT si.id) FROM sides si
            JOIN hash_contexts hc ON hc.side_id = si.id
            JOIN paths p ON p.context_id = hc.id
        """, fetch="one")[0]

        rel = execute_query(f"""
            SELECT COUNT(*) FROM (
                SELECT h.id FROM hashs h JOIN hash_contexts hc ON hc.hash_id = h.id
                GROUP BY h.id HAVING {_RELATION_HAVING}
            ) relations
        """, fetch="one")  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
        counts['relations'] = rel[0] if rel else 0

        geo = execute_query("SELECT COUNT(DISTINCT place_name) FROM path_geo_mentions", fetch="one")
        counts['geolocation'] = geo[0] if geo else 0
        geo_files = execute_query("""
            SELECT COUNT(DISTINCT p.id) FROM paths p WHERE p.coordinates IS NOT NULL AND TRIM(p.coordinates) != ''
        """, fetch="one")
        counts['geolocation_files'] = geo_files[0] if geo_files else 0

        return jsonify({'success': True, 'counts': counts})
    except Exception as e:
        logger.error(f"Error in fa_overview: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Category -> Keywords -> Files
# ---------------------------------------------------------------------------


def _word_search_where(run, search):
    """``WHERE``/``params`` for a word-name facet search, with cross-language recall.

    A typed term may name the word in any supported language; the facet then
    matches the word id as well as the text. With no expansion the predicate is
    exactly the one this module always built.
    """
    if not search:
        return "", []
    expansion = expand_for_search(search, run=run)
    if expansion.word_ids:
        return "WHERE (w.word ILIKE %s OR w.id = ANY(%s))", [
            f"%{search}%", list(expansion.word_ids)]
    return "WHERE w.word ILIKE %s", [f"%{search}%"]


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/categories', methods=['GET'])
def fa_categories():
    """One row per category with FIVE distinct, DB-derived metrics -- never
    conflating the "exactly one word" and "2+ word phrase" tiers of the
    taxonomy (see module docstring):

    * word_count / keyword_count: how many distinct Category Words / how
      many distinct Keyword phrases are curated under this category.
    * file_count: distinct files matched by EITHER a category word OR a
      category keyword (the union - a file matched by both is counted once).
    * word_matches / keyword_matches: total occurrence counts (sum of the
      per-hash ``word_count`` tally in ``words_hashs``/``keywords_hashs``),
      i.e. "how many times", not just "how many files".

    Previously this single query LEFT JOINed words_categorys/words_hashs/
    paths together with an UNRELATED keywords join (no shared ON condition),
    which cartesian-multiplied rows per category before COUNT(DISTINCT ...)
    collapsed them back down -- correct by accident, but exactly the kind of
    join Postgres has to materialize and hash across before it can dedupe,
    which is why this endpoint measured ~1.0-1.1s (req #17). Three narrow,
    independently-aggregated CTEs joined by category_id are both correct by
    construction and cheap to plan.
    """
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        search = _str_arg('search')

        where, params = _word_search_where(execute_query, search)

        rows = execute_query(f"""
            WITH word_stats AS (
                SELECT wc.category_id,
                       COUNT(DISTINCT wc.word_id) AS word_count,
                       COALESCE(SUM(wh.word_count), 0) AS word_matches
                FROM words_categorys wc
                JOIN words_hashs wh ON wh.word_id = wc.word_id
                GROUP BY wc.category_id
            ),
            keyword_stats AS (
                SELECT k.category_id,
                       COUNT(DISTINCT k.id) AS keyword_count,
                       COALESCE(SUM(kh.word_count), 0) AS keyword_matches
                FROM keywords k
                JOIN keywords_hashs kh ON kh.keyword_id = k.id
                GROUP BY k.category_id
            ),
            word_files AS (
                SELECT wc.category_id, p.id AS file_id
                FROM words_categorys wc
                JOIN words_hashs wh ON wh.word_id = wc.word_id
                JOIN hash_contexts hc ON hc.hash_id = wh.hash_id
                JOIN paths p ON p.context_id = hc.id
            ),
            keyword_files AS (
                SELECT k.category_id, p.id AS file_id
                FROM keywords k
                JOIN keywords_hashs kh ON kh.keyword_id = k.id
                JOIN hash_contexts hc ON hc.hash_id = kh.hash_id
                JOIN paths p ON p.context_id = hc.id
            ),
            file_stats AS (
                SELECT category_id, COUNT(DISTINCT file_id) AS file_count
                FROM (SELECT * FROM word_files UNION SELECT * FROM keyword_files) u
                GROUP BY category_id
            )
            SELECT c.id, w.word,
                   COALESCE(ws.word_count, 0), COALESCE(ks.keyword_count, 0),
                   COALESCE(fs.file_count, 0),
                   COALESCE(ws.word_matches, 0), COALESCE(ks.keyword_matches, 0)
            FROM categorys c
            JOIN words w ON w.id = c.word_id
            LEFT JOIN word_stats ws ON ws.category_id = c.id
            LEFT JOIN keyword_stats ks ON ks.category_id = c.id
            LEFT JOIN file_stats fs ON fs.category_id = c.id
            {where}
        """, tuple(params) if params else None, fetch="all")  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters

        items = [
            {
                'id': r[0], 'name': r[1],
                'word_count': r[2], 'keyword_count': r[3], 'file_count': r[4],
                'word_matches': r[5], 'keyword_matches': r[6],
            }
            for r in (rows or []) if r[4] > 0  # keep hiding categories with no matched files yet
        ]
        items.sort(key=lambda x: (-x['file_count'], x['name'].lower()))
        page_items, pagination = _paginate_list(items, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_categories: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/categories/<int:category_id>', methods=['GET'])
def fa_category_detail(category_id):
    try:
        row = execute_query("""
            SELECT c.id, w.word FROM categorys c JOIN words w ON w.id = c.word_id WHERE c.id = %s
        """, (category_id,), fetch="one")
        if not row:
            return jsonify({'success': False, 'error': 'Category not found'}), 404
        return jsonify({'success': True, 'data': {'id': row[0], 'name': row[1]}})
    except Exception as e:
        logger.error(f"Error in fa_category_detail: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


def _keywords_query(where_sql, params, page, per_page):
    rows = execute_query(f"""
        SELECT k.id, k.category_id, k.keyword, w.word AS category_name,
               COUNT(DISTINCT p.id) AS file_count,
               COALESCE(SUM(kp.word_count), 0) AS occurrence_count
        FROM keywords k
        JOIN categorys c ON c.id = k.category_id
        JOIN words w ON w.id = c.word_id
        JOIN keywords_hashs kp ON kp.keyword_id = k.id
        JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
        JOIN paths p ON p.context_id = hc.id
        {where_sql}
        GROUP BY k.id, k.category_id, k.keyword, w.word
        ORDER BY file_count DESC, k.id ASC
    """, tuple(params) if params else None, fetch="all")  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
    rows = rows or []
    blobs = [r[2] for r in rows]
    word_dict = _batch_words_for_blobs(blobs)
    items = []
    for r in rows:
        text = _decode_word_blob(r[2], word_dict)
        if not text:
            continue
        items.append({
            'id': r[0], 'name': text, 'category_id': r[1], 'category_name': r[3],
            'file_count': r[4], 'occurrence_count': r[5],
        })
    return _paginate_list(items, page, per_page)


def _words_query(where_sql, params, page, per_page):
    """Category Words: exactly one lexical word each (``words_categorys``),
    as opposed to the 2+ word phrases in ``keywords``/``_keywords_query`` --
    see the module docstring's taxonomy note. A word's file/occurrence
    counts come from ``words_hashs`` (word_id -> hash_id, with a per-hash
    ``word_count`` occurrence tally), exactly mirroring how ``_keywords_query``
    reads ``keywords_hashs`` for phrases, so the two tiers are structurally
    parallel and never conflated.
    """
    rows = execute_query(f"""
        SELECT wc.word_id, wc.category_id, w.word, wcat.word AS category_name,
               COUNT(DISTINCT p.id) AS file_count,
               COALESCE(SUM(wh.word_count), 0) AS occurrence_count
        FROM words_categorys wc
        JOIN words w ON w.id = wc.word_id
        JOIN categorys c ON c.id = wc.category_id
        JOIN words wcat ON wcat.id = c.word_id
        JOIN words_hashs wh ON wh.word_id = wc.word_id
        JOIN hash_contexts hc ON hc.hash_id = wh.hash_id
        JOIN paths p ON p.context_id = hc.id
        {where_sql}
        GROUP BY wc.word_id, wc.category_id, w.word, wcat.word
        ORDER BY file_count DESC, w.word ASC
    """, tuple(params) if params else None, fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
    items = [{
        'id': r[0], 'name': r[2], 'category_id': r[1], 'category_name': r[3],
        'file_count': r[4], 'occurrence_count': r[5],
    } for r in rows]
    return _paginate_list(items, page, per_page)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/categories/<int:category_id>/keywords', methods=['GET'])
def fa_category_keywords(category_id):
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        page_items, pagination = _keywords_query("WHERE k.category_id = %s", [category_id], page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_category_keywords: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/categories/<int:category_id>/words', methods=['GET'])
def fa_category_words(category_id):
    """CATEGORY -> CATEGORY WORDS -> FILES: the single-lexical-word sibling
    of fa_category_keywords, never conflated with it (req #1-3)."""
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        page_items, pagination = _words_query("WHERE wc.category_id = %s", [category_id], page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_category_words: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Keywords (flat)
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/keywords', methods=['GET'])
def fa_keywords():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        page_items, pagination = _keywords_query("", [], page, per_page)
        search = _str_arg('search')
        if search:
            page_items = [k for k in page_items if search.lower() in k['name'].lower()]
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_keywords: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Words (flat) -- the single-lexical-word sibling of Keywords (req #20:
# "Words -> Word -> Files -> Viewer" as its own top-level IA branch, not a
# renamed Keywords tab).
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/words', methods=['GET'])
def fa_words():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        search = _str_arg('search')
        where, params = _word_search_where(execute_query, search)
        page_items, pagination = _words_query(where, params, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_words: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Titles (grouped by REAL decoded text -> real file count sharing that text)
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/titles', methods=['GET'])
def fa_titles():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        search = _str_arg('search')

        rows = execute_query("""
            SELECT tc.id, tc.hash_id, tc.title_data
            FROM titles_content tc
            WHERE tc.title_status = 'Main'
        """, fetch="all") or []

        word_dict = _batch_words_for_blobs([r[2] for r in rows])

        # Group by decoded text: several distinct hash_ids can share the
        # exact same document title (e.g. a recurring subject line/report
        # name), and the real "files sharing this title" count spans all of
        # them.
        groups = {}
        for title_id, hash_id, blob in rows:
            text = _decode_word_blob(blob, word_dict)
            if not text:
                continue
            g = groups.setdefault(text, {'title_ids': [], 'hash_ids': set()})
            g['title_ids'].append(title_id)
            g['hash_ids'].add(hash_id)

        if not groups:
            page_items, pagination = _paginate_list([], page, per_page)
            return jsonify({'success': True, 'data': page_items, 'pagination': pagination})

        all_hash_ids = set()
        for g in groups.values():
            all_hash_ids.update(g['hash_ids'])
        placeholders = ','.join(['%s'] * len(all_hash_ids))
        file_count_rows = execute_query(f"""
            SELECT hc.hash_id, COUNT(DISTINCT p.id)
            FROM hash_contexts hc JOIN paths p ON p.context_id = hc.id
            WHERE hc.hash_id IN ({placeholders})
            GROUP BY hc.hash_id
        """, list(all_hash_ids), fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
        file_count_by_hash = {r[0]: r[1] for r in file_count_rows}

        items = []
        for text, g in groups.items():
            if search and search.lower() not in text.lower():
                continue
            file_count = sum(file_count_by_hash.get(h, 0) for h in g['hash_ids'])
            if file_count <= 0:
                continue
            items.append({
                'id': min(g['title_ids']),
                'name': text,
                'file_count': file_count,
                'variant_count': len(g['hash_ids']),
            })
        items.sort(key=lambda x: (-x['file_count'], x['name'].lower()))
        page_items, pagination = _paginate_list(items, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_titles: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Sources / Sides (list) + scoped categories/keywords
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sources', methods=['GET'])
def fa_sources():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        search = _str_arg('search')
        where = "WHERE s.name ILIKE %s" if search else ""
        params = [f"%{search}%"] if search else []
        rows = execute_query(f"""
            SELECT s.id, s.name, s.job, s.country, s.city, COUNT(DISTINCT p.id) AS file_count
            FROM sources s
            JOIN hash_contexts hc ON hc.source_id = s.id
            JOIN paths p ON p.context_id = hc.id
            {where}
            GROUP BY s.id, s.name, s.job, s.country, s.city
            HAVING COUNT(DISTINCT p.id) > 0
            ORDER BY file_count DESC, s.name ASC
        """, tuple(params) if params else None, fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
        items = [{'id': r[0], 'name': r[1], 'job': r[2], 'country': r[3], 'city': r[4], 'file_count': r[5]} for r in rows]
        page_items, pagination = _paginate_list(items, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_sources: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sides', methods=['GET'])
def fa_sides():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        search = _str_arg('search')
        where = "WHERE si.name ILIKE %s" if search else ""
        params = [f"%{search}%"] if search else []
        rows = execute_query(f"""
            SELECT si.id, si.name, si.importance, COUNT(DISTINCT p.id) AS file_count
            FROM sides si
            JOIN hash_contexts hc ON hc.side_id = si.id
            JOIN paths p ON p.context_id = hc.id
            {where}
            GROUP BY si.id, si.name, si.importance
            HAVING COUNT(DISTINCT p.id) > 0
            ORDER BY file_count DESC, si.name ASC
        """, tuple(params) if params else None, fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
        items = [{'id': r[0], 'name': r[1], 'importance': float(r[2]) if r[2] is not None else None, 'file_count': r[3]} for r in rows]
        page_items, pagination = _paginate_list(items, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_sides: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


def _scoped_categories(dim_column, dim_id, page, per_page):
    rows = execute_query(f"""
        SELECT c.id, w.word, COUNT(DISTINCT p.id) AS file_count
        FROM categorys c
        JOIN words w ON w.id = c.word_id
        JOIN words_categorys wc ON wc.category_id = c.id
        JOIN words_hashs wp ON wp.word_id = wc.word_id
        JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
        JOIN paths p ON p.context_id = hc.id
        WHERE {dim_column} = %s
        GROUP BY c.id, w.word
        ORDER BY file_count DESC, w.word ASC
    """, (dim_id,), fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
    items = [{'id': r[0], 'name': r[1], 'file_count': r[2]} for r in rows]
    return _paginate_list(items, page, per_page)


def _scoped_words(dim_column, dim_id, page, per_page, category_id=None):
    where = f"WHERE {dim_column} = %s"
    params = [dim_id]
    if category_id:
        where += " AND wc.category_id = %s"
        params.append(category_id)
    return _words_query(where, params, page, per_page)


def _scoped_keywords(dim_column, dim_id, page, per_page, category_id=None):
    where = f"WHERE {dim_column} = %s"
    params = [dim_id]
    if category_id:
        where += " AND k.category_id = %s"
        params.append(category_id)
    rows = execute_query(f"""
        SELECT k.id, k.category_id, k.keyword, w.word AS category_name, COUNT(DISTINCT p.id) AS file_count
        FROM keywords k
        JOIN categorys c ON c.id = k.category_id
        JOIN words w ON w.id = c.word_id
        JOIN keywords_hashs kp ON kp.keyword_id = k.id
        JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
        JOIN paths p ON p.context_id = hc.id
        {where}
        GROUP BY k.id, k.category_id, k.keyword, w.word
        ORDER BY file_count DESC, k.id ASC
    """, tuple(params), fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
    blobs = [r[2] for r in rows]
    word_dict = _batch_words_for_blobs(blobs)
    items = []
    for r in rows:
        text = _decode_word_blob(r[2], word_dict)
        if not text:
            continue
        items.append({'id': r[0], 'name': text, 'category_id': r[1], 'category_name': r[3], 'file_count': r[4]})
    return _paginate_list(items, page, per_page)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sources/<int:source_id>/categories', methods=['GET'])
def fa_source_categories(source_id):
    try:
        page_items, pagination = _scoped_categories('hc.source_id', source_id, _int_arg('page', 1), _int_arg('per_page', 50))
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_source_categories: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sources/<int:source_id>/words', methods=['GET'])
def fa_source_words(source_id):
    try:
        category_id = _int_arg('category_id')
        page_items, pagination = _scoped_words('hc.source_id', source_id, _int_arg('page', 1), _int_arg('per_page', 50), category_id)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_source_words: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sources/<int:source_id>/keywords', methods=['GET'])
def fa_source_keywords(source_id):
    try:
        category_id = _int_arg('category_id')
        page_items, pagination = _scoped_keywords('hc.source_id', source_id, _int_arg('page', 1), _int_arg('per_page', 50), category_id)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_source_keywords: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sides/<int:side_id>/categories', methods=['GET'])
def fa_side_categories(side_id):
    try:
        page_items, pagination = _scoped_categories('hc.side_id', side_id, _int_arg('page', 1), _int_arg('per_page', 50))
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_side_categories: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sides/<int:side_id>/words', methods=['GET'])
def fa_side_words(side_id):
    try:
        category_id = _int_arg('category_id')
        page_items, pagination = _scoped_words('hc.side_id', side_id, _int_arg('page', 1), _int_arg('per_page', 50), category_id)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_side_words: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/sides/<int:side_id>/keywords', methods=['GET'])
def fa_side_keywords(side_id):
    try:
        category_id = _int_arg('category_id')
        page_items, pagination = _scoped_keywords('hc.side_id', side_id, _int_arg('page', 1), _int_arg('per_page', 50), category_id)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_side_keywords: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Relations - strict cross-(source,side) content matches
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/relations', methods=['GET'])
def fa_relations():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)

        rows = execute_query(f"""
            SELECT h.id, h.hash, COUNT(DISTINCT hc.id) AS context_count, COUNT(DISTINCT p.id) AS file_count
            FROM hashs h
            JOIN hash_contexts hc ON hc.hash_id = h.id
            JOIN paths p ON p.context_id = hc.id
            GROUP BY h.id, h.hash
            HAVING {_RELATION_HAVING}
            ORDER BY file_count DESC, h.id ASC
        """, fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters

        hash_ids = [r[0] for r in rows]
        contexts_by_hash = {}
        if hash_ids:
            placeholders = ','.join(['%s'] * len(hash_ids))
            ctx_rows = execute_query(f"""
                SELECT hc.hash_id, hc.source_id, s.name, hc.side_id, si.name, COUNT(DISTINCT p.id)
                FROM hash_contexts hc
                LEFT JOIN sources s ON s.id = hc.source_id
                LEFT JOIN sides si ON si.id = hc.side_id
                JOIN paths p ON p.context_id = hc.id
                WHERE hc.hash_id IN ({placeholders})
                GROUP BY hc.hash_id, hc.source_id, s.name, hc.side_id, si.name
                ORDER BY hc.hash_id
            """, hash_ids, fetch="all") or []  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
            for hid, sid, sname, sideid, sidename, fc in ctx_rows:
                contexts_by_hash.setdefault(hid, []).append({
                    'source_id': sid, 'source_name': sname or 'Unknown',
                    'side_id': sideid, 'side_name': sidename or 'Unknown',
                    'file_count': fc,
                })

        items = [{
            'id': r[0],
            'hash': r[1],
            'hash_short': (r[1] or '')[:12],
            'context_count': r[2],
            'file_count': r[3],
            'contexts': contexts_by_hash.get(r[0], []),
        } for r in rows]

        page_items, pagination = _paginate_list(items, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_relations: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Geolocation - real content-derived place mentions
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/geolocation/places', methods=['GET'])
def fa_geolocation_places():
    try:
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        rows = execute_query("""
            SELECT pgm.place_name, MAX(pgm.country) AS country, MAX(pgm.latitude) AS lat, MAX(pgm.longitude) AS lon,
                   SUM(pgm.mention_count) AS mention_count, COUNT(DISTINCT p.id) AS file_count
            FROM path_geo_mentions pgm
            JOIN hash_contexts hc ON hc.hash_id = pgm.hash_id
            JOIN paths p ON p.context_id = hc.id
            GROUP BY pgm.place_name
            ORDER BY file_count DESC, pgm.place_name ASC
        """, fetch="all") or []
        items = [{
            'place_name': r[0], 'country': r[1], 'latitude': r[2], 'longitude': r[3],
            'mention_count': r[4], 'file_count': r[5],
        } for r in rows]
        page_items, pagination = _paginate_list(items, page, per_page)
        return jsonify({'success': True, 'data': page_items, 'pagination': pagination})
    except Exception as e:
        logger.error(f"Error in fa_geolocation_places: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


@file_analysis_bp.route('/api/file-analysis/geolocation/scan', methods=['POST'])
def fa_geolocation_scan():
    try:
        # Delegates to the shared place detector / re-detection job
        # (Api/services/geo_extraction_service.py); no longer writes
        # paths.coordinates.
        force = request.args.get('force', 'false').lower() in ('1', 'true', 'yes')
        from core.security.flask_ext import current_user
        user = current_user()
        outcome = scan_and_tag_geolocations(
            force=force, created_by=getattr(user, 'username', None) or 'system')
        return jsonify({'success': True, **outcome})
    except Exception as e:
        logger.error(f"Error in fa_geolocation_scan: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Unified file rows for any facet, in /api/search's exact result shape.
# ---------------------------------------------------------------------------

@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_analysis_bp.route('/api/file-analysis/rows', methods=['GET'])
def fa_rows():
    try:
        facet = _str_arg('facet')
        item_id = _int_arg('id')
        page = _int_arg('page', 1)
        per_page = _int_arg('per_page', 50)
        source_id = _int_arg('source_id')
        side_id = _int_arg('side_id')

        if not facet or item_id is None:
            return jsonify({'success': False, 'error': 'facet and id are required'}), 400

        extra_where = ""
        extra_params = []
        if source_id:
            extra_where += " AND hc.source_id = %s"
            extra_params.append(source_id)
        if side_id:
            extra_where += " AND hc.side_id = %s"
            extra_params.append(side_id)

        if facet == 'category':
            where = f"""
                WHERE EXISTS (
                    SELECT 1 FROM words_hashs wp
                    JOIN words_categorys wc ON wc.word_id = wp.word_id
                    WHERE wp.hash_id = hc.hash_id AND wc.category_id = %s
                ) {extra_where}
            """  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
            params = [item_id] + extra_params
        elif facet == 'keyword':
            where = f"""
                WHERE EXISTS (
                    SELECT 1 FROM keywords_hashs kp WHERE kp.hash_id = hc.hash_id AND kp.keyword_id = %s
                ) {extra_where}
            """  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
            params = [item_id] + extra_params
        elif facet == 'word':
            # A Category Word's presence in a file is category-agnostic
            # (words_hashs is keyed by word_id, not by which category the
            # word happens to be curated under) -- so this one facet serves
            # both the flat Words browser AND CATEGORY -> CATEGORY WORDS ->
            # FILES drill-down; `id` is always a word_id either way.
            where = f"""
                WHERE EXISTS (
                    SELECT 1 FROM words_hashs wp WHERE wp.hash_id = hc.hash_id AND wp.word_id = %s
                ) {extra_where}
            """  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
            params = [item_id] + extra_params
        elif facet == 'title':
            title_row = execute_query("SELECT hash_id FROM titles_content WHERE id = %s", (item_id,), fetch="one")
            if not title_row:
                return jsonify({'success': True, 'data': [], 'pagination': _paginate_list([], page, per_page)[1]})
            # Group by the SAME decoded text as /titles, so "sharing that
            # title" is honored even across a different hash_id.
            this_blob = execute_query("SELECT title_data FROM titles_content WHERE id = %s", (item_id,), fetch="one")
            word_dict = _batch_words_for_blobs([this_blob[0]] if this_blob else [])
            target_text = _decode_word_blob(this_blob[0], word_dict) if this_blob else ''
            all_rows = execute_query("""
                SELECT id, hash_id, title_data FROM titles_content WHERE title_status = 'Main'
            """, fetch="all") or []
            all_word_dict = _batch_words_for_blobs([r[2] for r in all_rows])
            matching_hash_ids = [r[1] for r in all_rows if _decode_word_blob(r[2], all_word_dict) == target_text and target_text]
            if not matching_hash_ids:
                matching_hash_ids = [title_row[0]]
            placeholders = ','.join(['%s'] * len(matching_hash_ids))
            where = f"WHERE hc.hash_id IN ({placeholders}) {extra_where}"
            params = list(matching_hash_ids) + extra_params
        elif facet in ('source',):
            where = f"WHERE hc.source_id = %s {extra_where}"
            params = [item_id] + extra_params
        elif facet in ('side',):
            where = f"WHERE hc.side_id = %s {extra_where}"
            params = [item_id] + extra_params
        elif facet == 'relation':
            where = f"WHERE hc.hash_id = %s {extra_where}"
            params = [item_id] + extra_params
        elif facet == 'geo_place':
            place_name = _str_arg('place')
            where = f"""
                WHERE EXISTS (
                    SELECT 1 FROM path_geo_mentions pgm WHERE pgm.hash_id = hc.hash_id AND pgm.place_name = %s
                ) {extra_where}
            """  # nosec B608 # constant fragments, %s placeholder lists and column literals chosen in code; request values are bound parameters
            params = [place_name] + extra_params
        else:
            return jsonify({'success': False, 'error': f'Unknown facet: {facet}'}), 400

        count_row = execute_query(f"SELECT COUNT(DISTINCT p.id) {_FILE_JOINS} {where}", tuple(params), fetch="one")
        total = count_row[0] if count_row else 0

        page = max(1, page)
        per_page = max(1, min(200, per_page))
        offset = (page - 1) * per_page

        data_rows = execute_query(f"""
            SELECT DISTINCT {_FILE_COLUMNS}
            {_FILE_JOINS}
            {where}
            ORDER BY p.file_date DESC NULLS LAST, p.id DESC
            LIMIT %s OFFSET %s
        """, tuple(params) + (per_page, offset), fetch="all") or []

        results = _rows_to_search_shape(data_rows)
        total_pages = (total + per_page - 1) // per_page if total else 1
        return jsonify({
            'success': True,
            'results': results,
            'pagination': {
                'page': page, 'per_page': per_page, 'total': total, 'total_pages': total_pages,
                'has_prev': page > 1, 'has_next': page < total_pages,
            },
        })
    except Exception as e:
        logger.error(f"Error in fa_rows: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.file_analysis', success_key='success', status=500)
