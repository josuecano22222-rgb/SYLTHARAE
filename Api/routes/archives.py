"""
Archives routes
"""

from flask import render_template, request, jsonify, flash, make_response
from Api.utils import (
    execute_query, load_text_keyword, load_text_title,
    select_info_sources, select_info_sides, get_categories_with_stats,
    get_archive_statistics, relation_duplicates_sql
)
import logging
from core.serialization import pack_int_list, unpack_int_list
from core.errors import client_error
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

# Safe ORDER BY allow-list for /api/archives/files (paths alias ``p``).
_ARCHIVES_FILE_SORT_COLUMNS = {
    'file_date': 'p.file_date',
    'date': 'p.file_date',
    'name': 'p.file_name',
    'size': 'p.file_size',
    'type': 'p.file_type',
    'file_type': 'p.file_type',
    'id': 'p.id',
}


def _archives_files_order_clause(sort_by, sort_dir):
    """Return a safe ORDER BY clause for archive file listings."""
    column = _ARCHIVES_FILE_SORT_COLUMNS.get((sort_by or '').strip().lower(), 'p.file_date')
    direction = 'ASC' if str(sort_dir or '').strip().lower() == 'asc' else 'DESC'
    return f"ORDER BY {column} {direction} NULLS LAST, p.id DESC"


def _archives_normalized_type_expr(alias='p'):
    """SQL expression that yields a dotted-free lowercase file-type key."""
    return f"""LOWER(REPLACE(
        CASE
            WHEN {alias}.file_type IS NULL
                 OR TRIM({alias}.file_type) = ''
                 OR UPPER({alias}.file_type) = 'FILE'
            THEN COALESCE(NULLIF(SUBSTRING({alias}.file_name FROM '\\.([^.]+)$'), ''), 'unknown')
            ELSE {alias}.file_type
        END, '.', ''))"""


def _archives_files_path_filters(file_type=None, search=None, exact_type=False):
    """Build optional path filters (file type / name search) and bind params.

    ``file_type`` may be one value or a comma-separated set (and accepted as
    a list/tuple). Multiple types match with OR (ANY / IN); a single type
    keeps the historical exact or LIKE behaviour.
    """
    clauses = []
    params = []
    if file_type:
        if isinstance(file_type, (list, tuple)):
            raw_parts = list(file_type)
        else:
            raw_parts = str(file_type).split(',')
        normalized = [
            str(part).strip().lstrip('.').lower()
            for part in raw_parts
            if str(part).strip()
        ]
        # De-dupe while preserving order.
        seen = set()
        normalized = [t for t in normalized if not (t in seen or seen.add(t))]
        if normalized:
            type_expr = _archives_normalized_type_expr('p')
            if len(normalized) == 1:
                if exact_type:
                    clauses.append(f"{type_expr} = %s")
                    params.append(normalized[0])
                else:
                    clauses.append(f"{type_expr} LIKE %s")
                    params.append(f"%{normalized[0]}%")
            elif exact_type:
                placeholders = ','.join(['%s'] * len(normalized))
                clauses.append(f"{type_expr} IN ({placeholders})")
                params.extend(normalized)
            else:
                likes = ' OR '.join([f"{type_expr} LIKE %s"] * len(normalized))
                clauses.append(f"({likes})")
                params.extend(f"%{t}%" for t in normalized)
    if search:
        term = str(search).strip()
        if term:
            clauses.append(
                "(p.file_name ILIKE %s OR COALESCE(p.file_path, '') ILIKE %s)"
            )
            params.extend([f"%{term}%", f"%{term}%"])
    if not clauses:
        return '', []
    return ' AND ' + ' AND '.join(clauses), params


# ---------------------------------------------------------------------------
# Title archive SQL fragments (One Content, Many Contexts).
#
# ``titles_content`` belongs to canonical content (``hash_id``) since
# migration m0011, but the archive UI is occurrence-oriented: file_count
# counts the LIVE occurrences (paths) of the title's content across its
# contexts, and every title is surfaced through a representative occurrence
# (its first live path, in id order) so pre-existing links keep working.
#
# The subqueries deliberately avoid COUNT()/MIN() where they are used as
# cursor-paginator select columns: the paginator's GROUP BY detection scans
# select_columns for aggregate tokens and would mangle the grouping otherwise
# (hence ORDER BY ... LIMIT 1 and EXISTS instead). TC_COUNT - the only
# fragment containing COUNT(*) - is for queries executed directly.
_TC_TITLE_PATHS = (
    " FROM hash_contexts tchc"
    " JOIN paths tchp ON tchp.context_id = tchc.id"
    " WHERE tchc.hash_id = tc.hash_id"
)
#: Number of live occurrences (stored paths) of the title's content.
TC_COUNT = f"(SELECT COUNT(*){_TC_TITLE_PATHS})"
#: Representative occurrence of the title's content (first live path id).
TC_PATH_SUBQ = f"(SELECT tchp.id{_TC_TITLE_PATHS} ORDER BY tchp.id LIMIT 1)"
#: File name of the representative occurrence.
TC_NAME_SUBQ = (
    f"(SELECT tchp.file_name{_TC_TITLE_PATHS} ORDER BY tchp.id LIMIT 1)"
)
#: Whether the title's content has any live occurrence at all.
TC_EXISTS = f"EXISTS (SELECT 1{_TC_TITLE_PATHS})"


# The declared row cap for the archives page's usage-ranked navigation
# lists (categories, keywords, titles, sources, sides, hashs). The
# interactive section views page through the full sets server-side; this
# cap bounds only the initial navigation render.
ARCHIVE_LIST_CAP = 100


def register_archives_routes(app):
    """Register archives routes with the Flask app"""
    
    @app.route('/archives')
    def archives_page():
        """Archives page displaying categories, keywords, titles, sources, sides, and hashs"""
        try:
            categories_data = get_categories_with_stats(limit=100)
            
            categories = []
            for cat in categories_data:
                file_count = cat.get('file_count') or 0
                # Only include categories that have files
                if file_count > 0:
                    categories.append({
                        'id': cat.get('id'),
                        'name': cat.get('name') or 'Unnamed Category',
                        'file_count': file_count,
                        'word_count': cat.get('word_count') or 0
                    })

            keywords_data = execute_query("""
                SELECT k.id, k.category_id, k.keyword,
                       COUNT(DISTINCT p.id) as file_count
                FROM keywords k
                LEFT JOIN keywords_hashs kp ON k.id = kp.keyword_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
                LEFT JOIN paths p ON p.context_id = hc.id
                GROUP BY k.id, k.category_id, k.keyword
                ORDER BY file_count DESC, k.id ASC
                LIMIT 100
            """, params=None, fetch="all", use_cache=True)
            
            keywords = []
            if keywords_data:
                # Batch load all word IDs to avoid N+1 queries
                all_word_ids = set()
                keyword_word_map = {}
                
                for row in keywords_data:
                    keyword_id = row[0]
                    keyword_bytes = row[2]
                    try:
                        if keyword_bytes:
                            word_ids = unpack_int_list(keyword_bytes)
                            if word_ids and isinstance(word_ids, list):
                                keyword_word_map[keyword_id] = word_ids
                                all_word_ids.update(word_ids)
                    except Exception as e:
                        logger.warning(f"Error unpickling keyword {keyword_id}: {e}")
                
                # Batch load all words at once
                word_dict = {}
                if all_word_ids:
                    placeholders = ','.join(['%s'] * len(all_word_ids))
                    words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
                    words_result = execute_query(words_query, list(all_word_ids))
                    if words_result:
                        word_dict = {row[0]: row[1] for row in words_result}
                
                # Build keyword list with text
                for row in keywords_data:
                    keyword_id = row[0]
                    file_count = row[3] or 0
                    # Only include keywords that have files
                    if file_count > 0 and keyword_id in keyword_word_map:
                        word_ids = keyword_word_map[keyword_id]
                        words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
                        if words:
                            keyword_text = ' '.join(words)
                            keywords.append({
                                'id': keyword_id,
                                'name': keyword_text,
                                'category_id': row[1],
                                'file_count': file_count
                            })
            
            logger.info(f"✅ Loaded {len(keywords)} keywords from database (query returned {len(keywords_data or [])} rows)")
            

            # Titles belong to canonical content (hash_id); file_count counts
            # the live occurrences (paths) of that content across contexts.
            titles_data = execute_query(f"""
                SELECT tc.id, tc.title_status, tc.hash_id, tc.title_data,
                       {TC_COUNT} as file_count
                FROM titles_content tc
                WHERE tc.title_status = 'Main'
                ORDER BY tc.id DESC
                LIMIT 100
            """)
            
            titles = []
            if titles_data:
                # Batch load all word IDs
                all_title_word_ids = set()
                title_word_map = {}
                
                for row in titles_data:
                    title_id = row[0]
                    title_bytes = row[3]
                    try:
                        if title_bytes:
                            # Validate bytes before unpickling
                            if not isinstance(title_bytes, (bytes, bytearray, memoryview)):
                                continue
                            
                            # Convert to bytes if needed
                            if isinstance(title_bytes, memoryview):
                                title_bytes = bytes(title_bytes)
                            elif not isinstance(title_bytes, bytes):
                                title_bytes = bytes(title_bytes)
                            
                            # Validate minimum size
                            if len(title_bytes) < 2:
                                continue
                            
                            word_ids = unpack_int_list(title_bytes)
                            if word_ids and isinstance(word_ids, list):
                                title_word_map[title_id] = word_ids
                                all_title_word_ids.update(word_ids)
                    except (ValueError, TypeError, EOFError):
                        # Silently skip corrupted data
                        pass
                    except Exception as e:
                        logger.debug(f"Unexpected error unpickling title {title_id}: {type(e).__name__}")
                
                # Batch load all words
                title_word_dict = {}
                if all_title_word_ids:
                    placeholders = ','.join(['%s'] * len(all_title_word_ids))
                    words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
                    words_result = execute_query(words_query, list(all_title_word_ids))
                    if words_result:
                        title_word_dict = {row[0]: row[1] for row in words_result}
                
                # Build title list
                for row in titles_data:
                    title_id = row[0]
                    if title_id in title_word_map:
                        word_ids = title_word_map[title_id]
                        words = [title_word_dict.get(wid, '') for wid in word_ids if wid in title_word_dict]
                        if words:
                            title_text = ' '.join(words)
                            titles.append({
                                'id': title_id,
                                'name': title_text[:100],  # Limit display length
                                'status': row[1],
                                'file_count': row[4] or 0
                            })
            
            # Sources
            sources_data = execute_query("""
                SELECT s.id, s.name, s.job, s.country, s.city,
                       COUNT(DISTINCT p.id) as file_count
                FROM sources s
                LEFT JOIN hash_contexts hc ON s.id = hc.source_id
                LEFT JOIN paths p ON p.context_id = hc.id
                GROUP BY s.id, s.name, s.job, s.country, s.city
                ORDER BY file_count DESC, s.name ASC
                LIMIT 100
            """)
            
            sources = []
            for row in (sources_data or []):
                sources.append({
                    'id': row[0],
                    'name': row[1] or 'Unnamed Source',
                    'job': row[2] or 'N/A',
                    'country': row[3] or 'N/A',
                    'city': row[4] or 'N/A',
                    'file_count': row[5] or 0
                })
            
            # Sides
            sides_data = execute_query("""
                SELECT si.id, si.name, si.importance,
                       COUNT(DISTINCT p.id) as file_count
                FROM sides si
                LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                LEFT JOIN paths p ON p.context_id = hc.id
                GROUP BY si.id, si.name, si.importance
                ORDER BY si.importance DESC, si.name ASC
                LIMIT 100
            """)
            
            sides = []
            for row in (sides_data or []):
                sides.append({
                    'id': row[0],
                    'name': row[1] or 'Unnamed Side',
                    'importance': float(row[2]) if row[2] else 0.0,
                    'file_count': row[3] or 0
                })
            

            # Relations: the same duplicate-content definition the API and the
            # sidebar count use (``relation_duplicates_sql``).
            relations_sql, _ = relation_duplicates_sql()
            hashs_data = execute_query(f"""
                SELECT id, name, context_count, file_count
                FROM ({relations_sql}) AS relations
                ORDER BY file_count DESC, id ASC
                LIMIT 100
            """)
            
            hashs = []
            for row in (hashs_data or []):
                hashs.append({
                    'id': row[0],
                    'name': row[1] or 'Unknown Hash',
                    'context_count': row[2] or 0,
                    'file_count': row[3] or 0
                })

            # All six section lists are now populated. Logging their bounds
            # before these queries run referenced unbound locals and sent the
            # page through its empty error fallback.
            logger.info(
                "Archives navigation lists capped at %d rows per section "
                "(categories=%d, keywords=%d, titles=%d, sources=%d, "
                "sides=%d, hashs=%d); section views page the full sets",
                ARCHIVE_LIST_CAP, len(categories), len(keywords),
                len(titles), len(sources), len(sides), len(hashs))

            stats = get_archive_statistics()
            
            # Top categories by file count
            top_categories = categories[:10] if len(categories) > 10 else categories
            
            # Top sources by file count
            top_sources = sources[:10] if len(sources) > 10 else sources
            
            # Category distribution data
            category_distribution = []
            for cat in categories[:15]:
                category_distribution.append({
                    'name': cat['name'],
                    'file_count': cat['file_count']
                })
            
            response = make_response(render_template('file/File_Management_Analysis_System.html',
                                 categories=categories,
                                 keywords=keywords,
                                 titles=titles,
                                 sources=sources,
                                 sides=sides,
                                 hashs=hashs,
                                 stats=stats,
                                 top_categories=top_categories,
                                 top_sources=top_sources,
                                 category_distribution=category_distribution))
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response
            
        except Exception as e:
            logger.error(f"Error loading archives page: {e}")
            import traceback
            logger.error(traceback.format_exc())
            flash("Error loading archives page", "error")
            return render_template('file/File_Management_Analysis_System.html',
                                 categories=[],
                                 keywords=[],
                                 titles=[],
                                 sources=[],
                                 sides=[],
                                 hashs=[],
                                 stats={},
                                 top_categories=[],
                                 top_sources=[],
                                 category_distribution=[])
    
    @app.route('/api/archives/search')
    def api_archives_search():
        """API endpoint for searching archives"""
        try:
            section = request.args.get('section', '')
            search_query = request.args.get('q', '').strip()
            
            if not section:
                return jsonify({'success': False, 'error': 'Section parameter required'}), 400
            
            results = []
            
            if section == 'category':
                if search_query:
                    query = """
                        SELECT c.id, w.word as name, 
                               COUNT(DISTINCT p.id) as file_count
                        FROM categorys c
                        JOIN words w ON c.word_id = w.id
                        LEFT JOIN words_categorys wc ON c.id = wc.category_id
                        LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
                        LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id LEFT JOIN paths p ON p.context_id = hc.id
                        WHERE w.word ILIKE %s
                        GROUP BY c.id, w.word, c.parent_id
                        ORDER BY file_count DESC, w.word ASC
                        LIMIT 100
                    """
                    data = execute_query(query, (f'%{search_query}%',))
                else:
                    query = """
                        SELECT c.id, w.word as name, 
                               COUNT(DISTINCT p.id) as file_count
                        FROM categorys c
                        JOIN words w ON c.word_id = w.id
                        LEFT JOIN words_categorys wc ON c.id = wc.category_id
                        LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
                        LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id LEFT JOIN paths p ON p.context_id = hc.id
                        GROUP BY c.id, w.word, c.parent_id
                        ORDER BY file_count DESC, w.word ASC
                        LIMIT 100
                    """
                    data = execute_query(query)
                
                for row in (data or []):
                    results.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed Category',
                        'category': 'Category',
                        'details': f'{row[2] or 0} files',
                        'file_count': row[2] or 0
                    })
            
            elif section == 'keywords':
                # 🚀 PERFORMANCE: Load top 500 keywords max to avoid N+1 query problem
                # Even with search, we limit results for reasonable performance
                query = """
                    SELECT k.id, k.category_id,
                           COUNT(DISTINCT p.id) as file_count
                    FROM keywords k
                    LEFT JOIN keywords_hashs kp ON k.id = kp.keyword_id
                    LEFT JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
                    LEFT JOIN paths p ON p.context_id = hc.id
                    GROUP BY k.id, k.category_id
                    ORDER BY file_count DESC, k.id ASC
                    LIMIT 500
                """
                data = execute_query(query, params=None, fetch="all", use_cache=True)
                
                for row in (data or []):
                    keyword_text = load_text_keyword(row[0])
                    if keyword_text:
                        # Filter by search query if provided
                        if search_query and search_query.lower() not in keyword_text.lower():
                            continue
                        results.append({
                            'id': row[0],
                            'name': keyword_text,  # Full text, no truncation
                            'category': 'Keywords',
                            'details': f'{row[2] or 0} files',
                            'file_count': row[2] or 0
                        })
            
            elif section == 'titles':
                query = f"""
                    SELECT tc.id, tc.title_status, tc.hash_id,
                           {TC_COUNT} as file_count
                    FROM titles_content tc
                    WHERE tc.title_status = 'Main'
                    ORDER BY tc.id DESC
                    LIMIT 100
                """
                data = execute_query(query)
                
                for row in (data or []):
                    title_text = load_text_title(row[0])
                    if title_text and (not search_query or search_query.lower() in title_text.lower()):
                        results.append({
                            'id': row[0],
                            'name': title_text[:100],
                            'category': 'Titles',
                            'details': f'Status: {row[1]} ({row[3] or 0} files)',
                            'file_count': row[3] or 0
                        })
            
            elif section == 'sources':
                if search_query:
                    query = """
                        SELECT s.id, s.name, s.job, s.country, s.city,
                               COUNT(DISTINCT p.id) as file_count
                        FROM sources s
                        LEFT JOIN hash_contexts hc ON s.id = hc.source_id
                        LEFT JOIN paths p ON p.context_id = hc.id
                        WHERE s.name ILIKE %s OR s.job ILIKE %s OR s.country ILIKE %s
                        GROUP BY s.id, s.name, s.job, s.country, s.city
                        ORDER BY file_count DESC, s.name ASC
                        LIMIT 100
                    """
                    pattern = f'%{search_query}%'
                    data = execute_query(query, (pattern, pattern, pattern))
                else:
                    query = """
                        SELECT s.id, s.name, s.job, s.country, s.city,
                               COUNT(DISTINCT p.id) as file_count
                        FROM sources s
                        LEFT JOIN hash_contexts hc ON s.id = hc.source_id
                        LEFT JOIN paths p ON p.context_id = hc.id
                        GROUP BY s.id, s.name, s.job, s.country, s.city
                        ORDER BY file_count DESC, s.name ASC
                        LIMIT 100
                    """
                    data = execute_query(query)
                
                for row in (data or []):
                    results.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed Source',
                        'category': 'Sources',
                        'details': f'{row[2] or "N/A"} - {row[3] or "N/A"} ({row[5] or 0} files)',
                        'file_count': row[5] or 0
                    })
            
            elif section == 'sides':
                if search_query:
                    query = """
                        SELECT si.id, si.name, si.importance,
                               COUNT(DISTINCT p.id) as file_count
                        FROM sides si
                        LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                        LEFT JOIN paths p ON p.context_id = hc.id
                        WHERE si.name ILIKE %s
                        GROUP BY si.id, si.name, si.importance
                        ORDER BY si.importance DESC, si.name ASC
                        LIMIT 100
                    """
                    data = execute_query(query, (f'%{search_query}%',))
                else:
                    query = """
                        SELECT si.id, si.name, si.importance,
                               COUNT(DISTINCT p.id) as file_count
                        FROM sides si
                        LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                        LEFT JOIN paths p ON p.context_id = hc.id
                        GROUP BY si.id, si.name, si.importance
                        ORDER BY si.importance DESC, si.name ASC
                        LIMIT 100
                    """
                    data = execute_query(query)
                
                for row in (data or []):
                    results.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed Side',
                        'category': 'Sides',
                        'details': f'Importance: {row[2] or 0.0} ({row[3] or 0} files)',
                        'file_count': row[3] or 0
                    })
            
            elif section == 'hash':
                # Relations: same duplicate-content definition as the API and
                # the sidebar count (``relation_duplicates_sql``).
                relations_sql, relations_params = relation_duplicates_sql(
                    search_query or None)
                query = f"""
                    SELECT id, name, context_count, file_count
                    FROM ({relations_sql}) AS relations
                    ORDER BY file_count DESC, id ASC
                    LIMIT 100
                """
                data = execute_query(
                    query, tuple(relations_params) if relations_params else None)
                
                for row in (data or []):
                    results.append({
                        'id': row[0],
                        'name': row[1] or 'Unknown Hash',
                        'category': 'Hash',
                        'details': f'{row[3] or 0} files',
                        'file_count': row[3] or 0
                    })
            
            return jsonify({'success': True, 'results': results})
            
        except Exception as e:
            logger.error(f"Error searching archives: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return client_error(e, subsystem='Api.routes.archives', success_key='success', status=500)
    
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route('/api/archives/files')
    def api_archives_files():
        """API endpoint for getting files associated with an archive item"""
        try:
            section = request.args.get('section', '').strip()
            raw_id = request.args.get('id', '').strip()
            limit = request.args.get('limit', 50, type=int)
            offset = request.args.get('offset', 0, type=int)
            page = request.args.get('page', 1, type=int)
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            file_type = request.args.get('file_type', '').strip()
            sort_by = request.args.get('sort_by', 'file_date').strip() or 'file_date'
            sort_dir = request.args.get('sort_dir', 'desc').strip() or 'desc'
            search = request.args.get('search', '').strip()
            
            # Enforce reasonable limits for performance (max 1000 per page)
            limit = min(max(1, limit), 1000)
            order_sql = _archives_files_order_clause(sort_by, sort_dir)

            # Browse-by-type uses a string id (e.g. "pdf"); other sections use ints.
            item_id = None
            if section == 'filetypes':
                type_key = (file_type or raw_id).strip().lstrip('.').lower()
                if not type_key:
                    return jsonify({'success': False, 'error': 'File type id required'}), 400
                file_type = type_key
                item_id = type_key
            else:
                try:
                    item_id = int(raw_id) if raw_id else None
                except (TypeError, ValueError):
                    return jsonify({'success': False, 'error': 'Invalid item id'}), 400

            path_filter_sql, path_filter_params = _archives_files_path_filters(
                file_type, search, exact_type=(section == 'filetypes')
            )
            
            logger.info(
                f"API request: section={section}, item_id={item_id}, page={page}, "
                f"limit={limit}, offset={offset}, source_id={source_id}, side_id={side_id}, "
                f"file_type={file_type!r}, sort_by={sort_by}, sort_dir={sort_dir}, search={search!r}"
            )
            
            if not section or item_id is None or item_id == '':
                logger.warning(f"Missing parameters: section={section}, item_id={item_id}")
                return jsonify({'success': False, 'error': 'Section and ID required'}), 400
            
            # Calculate offset from page if provided
            if page > 1:
                offset = (page - 1) * limit
            elif offset < 0:
                offset = 0
            
            files = []
            total_count = 0
            total_size = 0
            
            if section == 'filetypes':
                where_clause = "1=1" + path_filter_sql
                query_params = list(path_filter_params)

                count_result = execute_query(
                    f"""
                    SELECT COUNT(DISTINCT p.id)
                    FROM paths p
                    LEFT JOIN hash_contexts hc ON hc.id = p.context_id
                    WHERE {where_clause}
                    """,
                    tuple(query_params),
                    fetch="one",
                )
                total_count = (count_result[0] if count_result else 0) or 0

                if total_count > 0:
                    size_result = execute_query(
                        f"""
                        SELECT COALESCE(SUM(file_size), 0)
                        FROM (
                            SELECT DISTINCT p.id, p.file_size
                            FROM paths p
                            LEFT JOIN hash_contexts hc ON hc.id = p.context_id
                            WHERE {where_clause}
                        ) distinct_files
                        """,
                        tuple(query_params),
                        fetch="one",
                    )
                    total_size = (size_result[0] if size_result else 0) or 0

                    files_data = execute_query(
                        f"""
                        SELECT DISTINCT
                            p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                            p.file_status, p.file_date, p.date_creation,
                            COALESCE(s.name, 'Unknown') as source_name,
                            COALESCE(si.name, 'Unknown') as side_name
                        FROM paths p
                        LEFT JOIN hash_contexts hc ON hc.id = p.context_id
                        LEFT JOIN sources s ON hc.source_id = s.id
                        LEFT JOIN sides si ON hc.side_id = si.id
                        WHERE {where_clause}
                        {order_sql}
                        LIMIT %s OFFSET %s
                        """,
                        tuple(query_params + [limit, offset]),
                        fetch="all",
                    )
                    for row in (files_data or []):
                        files.append({
                            'id': row[0],
                            'name': row[1] or 'Unknown',
                            'path': row[2] or '',
                            'size': row[3] or 0,
                            'type': row[4] or 'Unknown',
                            'status': row[5] or 'Unknown',
                            'file_date': row[6].isoformat() if row[6] else None,
                            'date_creation': row[7].isoformat() if row[7] else None,
                            'source': row[8] or 'Unknown',
                            'side': row[9] or 'Unknown'
                        })
            elif section == 'category':
                # Build WHERE clause with optional source/side filters
                # Check if this is a main category (no parent) - if so, include its subcategories
                cat_parent_check = execute_query(
                    "SELECT parent_id FROM categorys WHERE id = %s",
                    (item_id,), fetch="one"
                )
                is_main_category = cat_parent_check and cat_parent_check[0] is None
                
                if is_main_category:
                    # Include both the main category and its subcategories
                    where_clause = "wc.category_id = %s OR wc.category_id IN (SELECT id FROM categorys WHERE parent_id = %s)"
                    query_params = [item_id, item_id]
                else:
                    where_clause = "wc.category_id = %s"
                    query_params = [item_id]
                
                if source_id:
                    where_clause += " AND hc.source_id = %s"
                    query_params.append(source_id)
                
                if side_id:
                    where_clause += " AND hc.side_id = %s"
                    query_params.append(side_id)

                where_clause += path_filter_sql
                query_params.extend(path_filter_params)
                
                # Get total count - must match the files query structure exactly
                # Using DISTINCT to ensure accurate count when files have multiple word associations
                count_query = f"""
                    SELECT COUNT(DISTINCT p.id)
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                    JOIN words_categorys wc ON wc.word_id = wp.word_id
                    WHERE {where_clause}
                """
                count_result = execute_query(count_query, tuple(query_params), fetch="one")
                
                if count_result:
                    total_count = count_result[0] if isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
                else:
                    total_count = 0
                
                # Get total size separately using a subquery to sum distinct file sizes
                if total_count > 0:
                    size_query = f"""
                        SELECT COALESCE(SUM(file_size), 0)
                        FROM (
                            SELECT DISTINCT p.id, p.file_size
                            FROM paths p
                            JOIN hash_contexts hc ON hc.id = p.context_id
                            JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                            JOIN words_categorys wc ON wc.word_id = wp.word_id
                            WHERE {where_clause}
                        ) AS distinct_files
                    """
                    size_result = execute_query(size_query, tuple(query_params), fetch="one")
                    total_size = size_result[0] if size_result and isinstance(size_result, (tuple, list)) else (size_result if size_result else 0)
                else:
                    total_size = 0
                
                # Files query - using DISTINCT to ensure each file appears only once
                # ORDER BY ensures consistent pagination across pages
                files_query = f"""
                    SELECT DISTINCT
                        p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                        p.file_status, p.file_date, p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                    JOIN words_categorys wc ON wc.word_id = wp.word_id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    WHERE {where_clause}
                    {order_sql}
                    LIMIT %s OFFSET %s
                """
                files_params = query_params + [limit, offset]
                files_data = execute_query(files_query, tuple(files_params), fetch="all")
                
                for row in (files_data or []):
                    files.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed File',
                        'path': row[2] or '',
                        'size': row[3] or 0,
                        'type': row[4] or 'Unknown',
                        'status': row[5] or 'Unknown',
                        'file_date': row[6].isoformat() if row[6] else None,
                        'date_creation': row[7].isoformat() if row[7] else None,
                        'source': row[8] or 'Unknown',
                        'side': row[9] or 'Unknown'
                    })
            
            elif section == 'keywords':
                # Build WHERE clause with optional source/side filters
                where_clause = "kp.keyword_id = %s"
                query_params = [item_id]
                
                if source_id:
                    where_clause += " AND hc.source_id = %s"
                    query_params.append(source_id)
                
                if side_id:
                    where_clause += " AND hc.side_id = %s"
                    query_params.append(side_id)

                where_clause += path_filter_sql
                query_params.extend(path_filter_params)
                
                # Get total count - must match the files query structure exactly
                count_query = f"""
                    SELECT COUNT(DISTINCT p.id)
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id
                    WHERE {where_clause}
                """
                count_result = execute_query(count_query, tuple(query_params), fetch="one")
                
                if count_result:
                    total_count = count_result[0] if isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
                else:
                    total_count = 0
                
                # Get total size separately using a subquery to sum distinct file sizes
                if total_count > 0:
                    size_query = f"""
                        SELECT COALESCE(SUM(file_size), 0)
                        FROM (
                            SELECT DISTINCT p.id, p.file_size
                            FROM paths p
                            JOIN hash_contexts hc ON hc.id = p.context_id
                            JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id
                            WHERE {where_clause}
                        ) AS distinct_files
                    """
                    size_result = execute_query(size_query, tuple(query_params), fetch="one")
                    total_size = size_result[0] if size_result and isinstance(size_result, (tuple, list)) else (size_result if size_result else 0)
                else:
                    total_size = 0
                
                # Files query - using DISTINCT to ensure each file appears only once
                # ORDER BY ensures consistent pagination across pages
                files_query = f"""
                    SELECT DISTINCT
                        p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                        p.file_status, p.file_date, p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN keywords_hashs kp ON kp.hash_id = hc.hash_id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    WHERE {where_clause}
                    {order_sql}
                    LIMIT %s OFFSET %s
                """
                files_params = query_params + [limit, offset]
                files_data = execute_query(files_query, tuple(files_params), fetch="all")
                
                for row in (files_data or []):
                    files.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed File',
                        'path': row[2] or '',
                        'size': row[3] or 0,
                        'type': row[4] or 'Unknown',
                        'status': row[5] or 'Unknown',
                        'file_date': row[6].isoformat() if row[6] else None,
                        'date_creation': row[7].isoformat() if row[7] else None,
                        'source': row[8] or 'Unknown',
                        'side': row[9] or 'Unknown'
                    })
            
            elif section == 'titles':
                # Titles belong to canonical content (hash_id); the files of a
                # title are the live occurrences of that content, reached
                # through its contexts.
                title_where = "tc.id = %s" + path_filter_sql
                title_params = [item_id] + list(path_filter_params)
                count_result = execute_query(f"""
                    SELECT COUNT(*), COALESCE(SUM(p.file_size), 0)
                    FROM titles_content tc
                    JOIN hash_contexts hc ON hc.hash_id = tc.hash_id
                    JOIN paths p ON p.context_id = hc.id
                    WHERE {title_where}
                """, tuple(title_params), fetch="one")
                
                if count_result:
                    if isinstance(count_result, tuple) and len(count_result) >= 2:
                        total_count = count_result[0] or 0
                        total_size = count_result[1] or 0
                    else:
                        total_count = count_result or 0
                
                files_data = execute_query(f"""
                    SELECT DISTINCT
                        p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                        p.file_status, p.file_date, p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name
                    FROM titles_content tc
                    JOIN hash_contexts hc ON hc.hash_id = tc.hash_id
                    JOIN paths p ON p.context_id = hc.id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    WHERE {title_where}
                    {order_sql}
                    LIMIT %s OFFSET %s
                """, tuple(title_params + [limit, offset]), fetch="all")
                
                for row in (files_data or []):
                    files.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed File',
                        'path': row[2] or '',
                        'size': row[3] or 0,
                        'type': row[4] or 'Unknown',
                        'status': row[5] or 'Unknown',
                        'file_date': row[6].isoformat() if row[6] else None,
                        'date_creation': row[7].isoformat() if row[7] else None,
                        'source': row[8] or 'Unknown',
                        'side': row[9] or 'Unknown'
                    })
            
            elif section == 'sources':
                # Sources are linked through hashs
                logger.info(f"Fetching files for source_id={item_id}")
                source_where = "hc.source_id = %s" + path_filter_sql
                source_params = [item_id] + list(path_filter_params)
                count_result = execute_query(f"""
                    SELECT COUNT(DISTINCT p.id), COALESCE(SUM(p.file_size), 0)
                    FROM paths p
                    JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                    WHERE {source_where}
                """, tuple(source_params), fetch="one")
                
                logger.info(f"Count result for source_id={item_id}: {count_result}")
                
                if count_result:
                    if isinstance(count_result, tuple) and len(count_result) >= 2:
                        total_count = count_result[0] or 0
                        total_size = count_result[1] or 0
                    else:
                        total_count = count_result or 0
                else:
                    total_count = 0
                    total_size = 0
                
                logger.info(f"Total count for source_id={item_id}: {total_count}")
                
                files_data = execute_query(f"""
                    SELECT DISTINCT
                        p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                        p.file_status, p.file_date, p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name
                    FROM paths p
                    JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    WHERE {source_where}
                    {order_sql}
                    LIMIT %s OFFSET %s
                """, tuple(source_params + [limit, offset]), fetch="all")
                
                logger.info(f"Files data query returned {len(files_data) if files_data else 0} rows for source_id={item_id}")
                
                for row in (files_data or []):
                    files.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed File',
                        'path': row[2] or '',
                        'size': row[3] or 0,
                        'type': row[4] or 'Unknown',
                        'status': row[5] or 'Unknown',
                        'file_date': row[6].isoformat() if row[6] else None,
                        'date_creation': row[7].isoformat() if row[7] else None,
                        'source': row[8] or 'Unknown',
                        'side': row[9] or 'Unknown'
                    })
            
            elif section == 'sides':
                # Sides are linked through hashs
                side_where = "hc.side_id = %s" + path_filter_sql
                side_params = [item_id] + list(path_filter_params)
                count_result = execute_query(f"""
                    SELECT COUNT(DISTINCT p.id), COALESCE(SUM(p.file_size), 0)
                    FROM paths p
                    JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                    WHERE {side_where}
                """, tuple(side_params), fetch="one")
                
                if count_result:
                    if isinstance(count_result, tuple) and len(count_result) >= 2:
                        total_count = count_result[0] or 0
                        total_size = count_result[1] or 0
                    else:
                        total_count = count_result or 0
                
                files_data = execute_query(f"""
                    SELECT DISTINCT
                        p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                        p.file_status, p.file_date, p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name
                    FROM paths p
                    JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    WHERE {side_where}
                    {order_sql}
                    LIMIT %s OFFSET %s
                """, tuple(side_params + [limit, offset]), fetch="all")
                
                for row in (files_data or []):
                    files.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed File',
                        'path': row[2] or '',
                        'size': row[3] or 0,
                        'type': row[4] or 'Unknown',
                        'status': row[5] or 'Unknown',
                        'file_date': row[6].isoformat() if row[6] else None,
                        'date_creation': row[7].isoformat() if row[7] else None,
                        'source': row[8] or 'Unknown',
                        'side': row[9] or 'Unknown'
                    })
            
            elif section == 'hash':
                # For hash section, show all files with the same hash value
                # Get the hash value first
                hash_info = execute_query("""
                    SELECT hash FROM hashs WHERE id = %s
                """, (item_id,), fetch="one")
                
                if hash_info:
                    # execute_query with fetch="one" returns the value directly for single column
                    hash_value = hash_info if isinstance(hash_info, str) else (hash_info[0] if isinstance(hash_info, (tuple, list)) and len(hash_info) > 0 else None)
                    
                    if hash_value:
                        hash_where = "h.hash = %s" + path_filter_sql
                        hash_params = [hash_value] + list(path_filter_params)
                        # Count all files with this hash (across all sources/sides)
                        count_result = execute_query(f"""
                            SELECT COUNT(DISTINCT p.id), COALESCE(SUM(p.file_size), 0)
                            FROM paths p
                            JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                            WHERE {hash_where}
                        """, tuple(hash_params), fetch="one")
                        
                        if count_result:
                            if isinstance(count_result, tuple) and len(count_result) >= 2:
                                total_count = count_result[0] or 0
                                total_size = count_result[1] or 0
                            else:
                                total_count = count_result or 0
                        
                        # Get all files with this hash (showing all sources/sides)
                        files_data = execute_query(f"""
                            SELECT DISTINCT
                                p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                                p.file_status, p.file_date, p.date_creation,
                                COALESCE(s.name, 'Unknown') as source_name,
                                COALESCE(si.name, 'Unknown') as side_name
                            FROM paths p
                            JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                            LEFT JOIN sources s ON hc.source_id = s.id
                            LEFT JOIN sides si ON hc.side_id = si.id
                            WHERE {hash_where}
                            {order_sql}
                            LIMIT %s OFFSET %s
                        """, tuple(hash_params + [limit, offset]), fetch="all")
                        
                        for row in (files_data or []):
                            files.append({
                                'id': row[0],
                                'name': row[1] or 'Unnamed File',
                                'path': row[2] or '',
                                'size': row[3] or 0,
                                'type': row[4] or 'Unknown',
                                'status': row[5] or 'Unknown',
                                'file_date': row[6].isoformat() if row[6] else None,
                                'date_creation': row[7].isoformat() if row[7] else None,
                                'source': row[8] or 'Unknown',
                                'side': row[9] or 'Unknown'
                            })
            
            elif section == 'address':

                logger.info(f"Fetching files for address (word_id={item_id})")
                addr_where = "wp.word_id = %s" + path_filter_sql
                addr_params = [item_id] + list(path_filter_params)
                
                # Count files containing this word
                count_result = execute_query(f"""
                    SELECT COUNT(DISTINCT p.id), COALESCE(SUM(p.file_size), 0)
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                    WHERE {addr_where}
                """, tuple(addr_params), fetch="one")
                
                if count_result:
                    if isinstance(count_result, tuple) and len(count_result) >= 2:
                        total_count = count_result[0] or 0
                        total_size = count_result[1] or 0
                    else:
                        total_count = count_result or 0
                
                logger.info(f"Total count for address (word_id={item_id}): {total_count}")
                
                # Get files containing this word
                files_data = execute_query(f"""
                    SELECT DISTINCT
                        p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                        p.file_status, p.file_date, p.date_creation,
                        COALESCE(s.name, 'Unknown') as source_name,
                        COALESCE(si.name, 'Unknown') as side_name
                    FROM paths p
                    JOIN hash_contexts hc ON hc.id = p.context_id
                    JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                    LEFT JOIN sources s ON hc.source_id = s.id
                    LEFT JOIN sides si ON hc.side_id = si.id
                    WHERE {addr_where}
                    {order_sql}
                    LIMIT %s OFFSET %s
                """, tuple(addr_params + [limit, offset]), fetch="all")
                
                logger.info(f"Files data query returned {len(files_data) if files_data else 0} rows for address (word_id={item_id})")
                
                for row in (files_data or []):
                    files.append({
                        'id': row[0],
                        'name': row[1] or 'Unnamed File',
                        'path': row[2] or '',
                        'size': row[3] or 0,
                        'type': row[4] or 'Unknown',
                        'status': row[5] or 'Unknown',
                        'file_date': row[6].isoformat() if row[6] else None,
                        'date_creation': row[7].isoformat() if row[7] else None,
                        'source': row[8] or 'Unknown',
                        'side': row[9] or 'Unknown'
                    })
            else:
                # Unknown section
                logger.warning(f"Unknown section requested: {section}")
                return jsonify({
                    'success': False,
                    'error': f'Unknown section: {section}'
                }), 400
            
            # Calculate pagination info
            total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
            current_page = page if page > 0 else (offset // limit) + 1
            
            logger.info(f"Returning {len(files)} files for section={section}, item_id={item_id}, total={total_count}")
            
            return jsonify({
                'success': True,
                'files': files,
                'pagination': {
                    'total': total_count,
                    'total_size': total_size,
                    'page': current_page,
                    'per_page': limit,
                    'total_pages': total_pages,
                    'has_prev': current_page > 1,
                    'has_next': current_page < total_pages
                }
            })
            
        except Exception as e:
            logger.error(f"Error fetching archive files: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return client_error(e, subsystem='Api.routes.archives', success_key='success', status=500)
    
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route('/api/archives/source-categories-keywords')
    def api_source_categories_keywords():
        """API endpoint for getting categories and keywords for a specific source with file counts"""
        try:
            source_id = request.args.get('source_id', type=int)
            page = request.args.get('page', 1, type=int)
            limit = request.args.get('limit', 20, type=int)
            offset = (page - 1) * limit
            
            if not source_id:
                return jsonify({'success': False, 'error': 'source_id required'}), 400
            
            # Get categories for this source
            categories_query = """
                SELECT DISTINCT c.id, w.word as category_name, c.parent_id,
                       COUNT(DISTINCT p.id) as file_count
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                JOIN words_categorys wc ON c.id = wc.category_id
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.source_id = %s
                GROUP BY c.id, w.word, c.parent_id
                ORDER BY file_count DESC, w.word ASC
                LIMIT %s OFFSET %s
            """
            try:
                categories_data = execute_query(categories_query, (source_id, limit, offset), fetch="all")
            except Exception as query_error:
                logger.error(f"Error executing categories query: {query_error}")
                categories_data = []
            
            # Get total count of categories
            total_categories_query = """
                SELECT COUNT(DISTINCT c.id)
                FROM categorys c
                JOIN words_categorys wc ON c.id = wc.category_id
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.source_id = %s
            """
            try:
                total_categories_result = execute_query(total_categories_query, (source_id,), fetch="one")
                total_categories = total_categories_result[0] if total_categories_result else 0
            except Exception as query_error:
                logger.error(f"Error executing total categories query: {query_error}")
                total_categories = len(categories_data) if categories_data else 0
            
            categories = []
            for row in (categories_data or []):
                categories.append({
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'parent_id': row[2],
                    'file_count': row[3] or 0
                })
            
            # Get keywords for this source
            keywords_query = """
                SELECT DISTINCT k.id, k.category_id, k.keyword,
                       COUNT(DISTINCT p.id) as file_count
                FROM keywords k
                JOIN keywords_hashs kp ON k.id = kp.keyword_id
                JOIN hash_contexts hc ON hc.hash_id = kp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.source_id = %s
                GROUP BY k.id, k.category_id, k.keyword
                ORDER BY file_count DESC, k.id ASC
                LIMIT %s OFFSET %s
            """
            try:
                keywords_data = execute_query(keywords_query, (source_id, limit, offset), fetch="all")
            except Exception as query_error:
                logger.error(f"Error executing keywords query: {query_error}")
                keywords_data = []
            
            # Get total count of keywords
            total_keywords_query = """
                SELECT COUNT(DISTINCT k.id)
                FROM keywords k
                JOIN keywords_hashs kp ON k.id = kp.keyword_id
                JOIN hash_contexts hc ON hc.hash_id = kp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.source_id = %s
            """
            try:
                total_keywords_result = execute_query(total_keywords_query, (source_id,), fetch="one")
                total_keywords = total_keywords_result[0] if total_keywords_result else 0
            except Exception as query_error:
                logger.error(f"Error executing total keywords query: {query_error}")
                total_keywords = len(keywords_data) if keywords_data else 0
            
            keywords = []
            if keywords_data:
                for row in keywords_data:
                    keyword_id = row[0]
                    keyword_bytes = row[2]
                    try:
                        if keyword_bytes:
                            word_ids = unpack_int_list(keyword_bytes)
                            if word_ids and isinstance(word_ids, list):
                                # Batch load words
                                if word_ids:
                                    placeholders = ','.join(['%s'] * len(word_ids))
                                    words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
                                    words_result = execute_query(words_query, list(word_ids))
                                    if words_result:
                                        word_dict = {row[0]: row[1] for row in words_result}
                                        words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
                                        if words:
                                            keyword_text = ' '.join(words)
                                            keywords.append({
                                                'id': keyword_id,
                                                'name': keyword_text,
                                                'category_id': row[1],
                                                'file_count': row[3] or 0
                                            })
                    except Exception as e:
                        logger.warning(f"Error unpickling keyword {keyword_id}: {e}")
            
            # Calculate pagination
            total_categories_pages = (total_categories + limit - 1) // limit if total_categories > 0 else 1
            total_keywords_pages = (total_keywords + limit - 1) // limit if total_keywords > 0 else 1
            
            return jsonify({
                'success': True,
                'categories': categories,
                'keywords': keywords,
                'pagination': {
                    'categories': {
                        'total': total_categories,
                        'page': page,
                        'per_page': limit,
                        'total_pages': total_categories_pages,
                        'has_prev': page > 1,
                        'has_next': page < total_categories_pages
                    },
                    'keywords': {
                        'total': total_keywords,
                        'page': page,
                        'per_page': limit,
                        'total_pages': total_keywords_pages,
                        'has_prev': page > 1,
                        'has_next': page < total_keywords_pages
                    }
                }
            })
            
        except Exception as e:
            logger.error(f"Error fetching source categories/keywords: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return client_error(e, subsystem='Api.routes.archives', success_key='success', status=500)
    
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route('/api/archives/side-categories-keywords')
    def api_side_categories_keywords():
        """API endpoint for getting categories and keywords for a specific side with file counts"""
        try:
            side_id = request.args.get('side_id', type=int)
            page = request.args.get('page', 1, type=int)
            limit = request.args.get('limit', 20, type=int)
            offset = (page - 1) * limit
            
            if not side_id:
                return jsonify({'success': False, 'error': 'side_id required'}), 400
            
            # Get categories for this side
            categories_query = """
                SELECT DISTINCT c.id, w.word as category_name, c.parent_id,
                       COUNT(DISTINCT p.id) as file_count
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                JOIN words_categorys wc ON c.id = wc.category_id
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.side_id = %s
                GROUP BY c.id, w.word, c.parent_id
                ORDER BY file_count DESC, w.word ASC
                LIMIT %s OFFSET %s
            """
            try:
                categories_data = execute_query(categories_query, (side_id, limit, offset), fetch="all")
            except Exception as query_error:
                logger.error(f"Error executing categories query: {query_error}")
                categories_data = []
            
            # Get total count of categories
            total_categories_query = """
                SELECT COUNT(DISTINCT c.id)
                FROM categorys c
                JOIN words_categorys wc ON c.id = wc.category_id
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.side_id = %s
            """
            try:
                total_categories_result = execute_query(total_categories_query, (side_id,), fetch="one")
                total_categories = total_categories_result[0] if total_categories_result else 0
            except Exception as query_error:
                logger.error(f"Error executing total categories query: {query_error}")
                total_categories = len(categories_data) if categories_data else 0
            
            categories = []
            for row in (categories_data or []):
                categories.append({
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'parent_id': row[2],
                    'file_count': row[3] or 0
                })
            
            # Get keywords for this side
            keywords_query = """
                SELECT DISTINCT k.id, k.category_id, k.keyword,
                       COUNT(DISTINCT p.id) as file_count
                FROM keywords k
                JOIN keywords_hashs kp ON k.id = kp.keyword_id
                JOIN hash_contexts hc ON hc.hash_id = kp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.side_id = %s
                GROUP BY k.id, k.category_id, k.keyword
                ORDER BY file_count DESC, k.id ASC
                LIMIT %s OFFSET %s
            """
            try:
                keywords_data = execute_query(keywords_query, (side_id, limit, offset), fetch="all")
            except Exception as query_error:
                logger.error(f"Error executing keywords query: {query_error}")
                keywords_data = []
            
            # Get total count of keywords
            total_keywords_query = """
                SELECT COUNT(DISTINCT k.id)
                FROM keywords k
                JOIN keywords_hashs kp ON k.id = kp.keyword_id
                JOIN hash_contexts hc ON hc.hash_id = kp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE hc.side_id = %s
            """
            try:
                total_keywords_result = execute_query(total_keywords_query, (side_id,), fetch="one")
                total_keywords = total_keywords_result[0] if total_keywords_result else 0
            except Exception as query_error:
                logger.error(f"Error executing total keywords query: {query_error}")
                total_keywords = len(keywords_data) if keywords_data else 0
            
            keywords = []
            if keywords_data:
                for row in keywords_data:
                    keyword_id = row[0]
                    keyword_bytes = row[2]
                    try:
                        if keyword_bytes:
                            word_ids = unpack_int_list(keyword_bytes)
                            if word_ids and isinstance(word_ids, list):
                                # Batch load words
                                if word_ids:
                                    placeholders = ','.join(['%s'] * len(word_ids))
                                    words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
                                    words_result = execute_query(words_query, list(word_ids))
                                    if words_result:
                                        word_dict = {row[0]: row[1] for row in words_result}
                                        words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
                                        if words:
                                            keyword_text = ' '.join(words)
                                            keywords.append({
                                                'id': keyword_id,
                                                'name': keyword_text,
                                                'category_id': row[1],
                                                'file_count': row[3] or 0
                                            })
                    except Exception as e:
                        logger.warning(f"Error unpickling keyword {keyword_id}: {e}")
            
            # Calculate pagination
            total_categories_pages = (total_categories + limit - 1) // limit if total_categories > 0 else 1
            total_keywords_pages = (total_keywords + limit - 1) // limit if total_keywords > 0 else 1
            
            return jsonify({
                'success': True,
                'categories': categories,
                'keywords': keywords,
                'pagination': {
                    'categories': {
                        'total': total_categories,
                        'page': page,
                        'per_page': limit,
                        'total_pages': total_categories_pages,
                        'has_prev': page > 1,
                        'has_next': page < total_categories_pages
                    },
                    'keywords': {
                        'total': total_keywords,
                        'page': page,
                        'per_page': limit,
                        'total_pages': total_keywords_pages,
                        'has_prev': page > 1,
                        'has_next': page < total_keywords_pages
                    }
                }
            })
            
        except Exception as e:
            logger.error(f"Error fetching side categories/keywords: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return client_error(e, subsystem='Api.routes.archives', success_key='success', status=500)