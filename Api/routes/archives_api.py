"""
API Routes for Archives sections with cursor-based pagination
"""

from flask import Blueprint, request, jsonify
from services.i18n.term_search import expand_for_search
from Api.cursor_pagination import get_cursor_paginator, SortDirection
from Api.utils import (
    execute_query,
    load_text_title,
    count_relation_duplicates,
    relation_duplicates_sql,
)
from Api.routes.archives import TC_COUNT, TC_EXISTS, TC_NAME_SUBQ, TC_PATH_SUBQ
from Api.utils.title_similarity import group_similar_titles, find_similar_titles
from core.sql_safety import validate_identifier, IdentifierError
import json
import logging
import re
from urllib.parse import quote, unquote

from Api.utils.title import display_titles_sorted, filter_titles_by_search
from core.serialization import pack_int_list, unpack_int_list
from core.errors import client_error
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter


logger = logging.getLogger(__name__)

archives_api_bp = Blueprint('archives_api', __name__)


def get_file_filter_conditions():
    """Get file filter conditions from request parameters - SECURE VERSION"""
    from datetime import datetime
    
    category_id = request.args.get('category_id', type=int)
    source_id = request.args.get('source_id', type=int)
    side_id = request.args.get('side_id', type=int)
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    
    conditions = []
    params = []
    
    # SECURITY FIX: Validate date formats to prevent SQL injection
    if date_from:
        try:
            # Validate date format (YYYY-MM-DD)
            datetime.strptime(date_from, '%Y-%m-%d')
            conditions.append("p.file_date >= %s")
            params.append(date_from)
        except ValueError:
            logger.warning(f"Invalid date_from format: {date_from}")
            raise ValueError(f"Invalid date_from format: {date_from}. Expected YYYY-MM-DD.")
    
    if date_to:
        try:
            # Validate date format (YYYY-MM-DD)
            datetime.strptime(date_to, '%Y-%m-%d')
            conditions.append("p.file_date <= %s")
            params.append(date_to)
        except ValueError:
            logger.warning(f"Invalid date_to format: {date_to}")
            raise ValueError(f"Invalid date_to format: {date_to}. Expected YYYY-MM-DD.")
    
    # Validate that date_from is before date_to
    if date_from and date_to:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            to_date = datetime.strptime(date_to, '%Y-%m-%d')
            if from_date > to_date:
                raise ValueError("date_from must be before or equal to date_to")
        except ValueError as e:
            if "date_from must be before" in str(e):
                raise
            # Otherwise it's a format error, already handled above
    
    # SECURITY: type=int ensures category_id, source_id, side_id are integers (SQL injection safe)
    if category_id:
        conditions.append("""
            EXISTS (
                SELECT 1 FROM words_hashs wp2
                JOIN words_categorys wc ON wp2.word_id = wc.word_id
                WHERE wp2.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = p.id)
                AND wc.category_id = %s
            )
        """)
        params.append(category_id)
    
    if source_id:
        conditions.append("hc.source_id = %s")
        params.append(source_id)
    
    if side_id:
        conditions.append("hc.side_id = %s")
        params.append(side_id)
    
    return conditions, params


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/categories', methods=['GET'])
def api_archives_categories():
    """Get categories with cursor-based pagination"""
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'id')
        sort_dir = request.args.get('sort_dir', 'asc')
        
        # Get filter parameters
        source_id = request.args.get('source_id', type=int)
        side_id = request.args.get('side_id', type=int)
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        
        # Get file filter conditions
        file_filter_conditions, file_filter_params = get_file_filter_conditions()
        
        # Build file count subquery with filters
        if file_filter_conditions:
            # Build WHERE clause for file count subquery with parameterized conditions
            file_count_where_parts = []
            file_count_params = []
            
            if source_id:
                file_count_where_parts.append("hc.source_id = %s")
                file_count_params.append(source_id)
            if side_id:
                file_count_where_parts.append("hc.side_id = %s")
                file_count_params.append(side_id)
            if date_from:
                file_count_where_parts.append("p.file_date >= %s")
                file_count_params.append(date_from)
            if date_to:
                file_count_where_parts.append("p.file_date <= %s")
                file_count_params.append(date_to)
            
            file_count_where = "WHERE " + " AND ".join(file_count_where_parts) if file_count_where_parts else ""
            
            file_count_subquery = f"""
                SELECT wc.category_id, COUNT(DISTINCT p.id) as file_count 
                FROM words_categorys wc 
                LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id LEFT JOIN paths p ON p.context_id = hc.id
                
                {file_count_where}
                GROUP BY wc.category_id
            """
        else:
            file_count_subquery = """
                SELECT wc.category_id, COUNT(DISTINCT p.id) as file_count 
                FROM words_categorys wc 
                LEFT JOIN words_hashs wp ON wc.word_id = wp.word_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id LEFT JOIN paths p ON p.context_id = hc.id
                GROUP BY wc.category_id
            """
            file_count_params = []
        
        # Build query with joins for file counts
        joins = [
            "JOIN words w ON c.word_id = w.id",
            f"LEFT JOIN ({file_count_subquery}) file_counts ON c.id = file_counts.category_id",
            "LEFT JOIN (SELECT category_id, COUNT(DISTINCT word_id) as word_count FROM words_categorys GROUP BY category_id) word_counts ON c.id = word_counts.category_id"
        ]
        

        select_columns = [
            "c.id",
            "w.word as name",
            "COALESCE(file_counts.file_count, 0) as file_count",
            "COALESCE(word_counts.word_count, 0) as word_count"
        ]
        
        filters = {}
        if search:
            # Cross-language recall: the text matches, or the word's id is one
            # whose translation normalises to the query.
            expansion = expand_for_search(search)
            if expansion.word_ids:
                filters['OR'] = [
                    {'w.word': {'op': 'ILIKE', 'value': f'%{search}%'}},
                    {'c.word_id': {'op': 'IN', 'value': list(expansion.word_ids)}},
                ]
            else:
                filters['w.word'] = {'ILIKE': f'%{search}%'}
        
        # Determine sort column and direction
        sort_column_map = {
            'id': 'c.id',
            'name': 'w.word',
            'file_count': 'c.id'  # Will sort client-side
        }
        sql_sort_column = sort_column_map.get(sort_by, 'c.id')
        sort_direction = SortDirection.DESC if sort_dir.lower() == 'desc' else SortDirection.ASC
        
        paginator = get_cursor_paginator('categorys')
        # Fetch more items to account for filtering out categories with file_count = 0
        # We need to fetch enough to get the requested number after filtering
        # Use a multiplier that accounts for potential filtering, but not so large it breaks pagination
        fetch_limit = max(limit * 3, 50)  # Fetch 3x or minimum 50, whichever is larger
        
        result = paginator.get_page(
            cursor=cursor,
            limit=fetch_limit,
            sort_column=sql_sort_column,
            sort_direction=sort_direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias='c'
        )
        
        # PERFORMANCE FIX: Batch calculate file counts to avoid N+1 query problem
        categories = []
        last_valid_cursor = cursor  # Track the cursor for the last item we'll return
        
        # Collect all category IDs first
        category_ids = []
        category_rows = []
        for idx, row in enumerate(result['data']):
            if len(category_ids) >= limit * 2:  # Get enough to filter
                break
            category_id = row.get('id') or row.get('c.id')
            if category_id:
                category_ids.append(category_id)
                category_rows.append((idx, row, category_id))
        
        # Batch calculate file counts for all categories at once
        file_count_map = {}
        if file_filter_conditions and category_ids:
            placeholders = ','.join(['%s'] * len(category_ids))
            batch_count_query = f"""
                SELECT wc.category_id, COUNT(DISTINCT p.id) as file_count
                FROM words_categorys wc
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE wc.category_id IN ({placeholders})
            """
            count_params = list(category_ids)
            
            if source_id:
                batch_count_query += " AND hc.source_id = %s"
                count_params.append(source_id)
            if side_id:
                batch_count_query += " AND hc.side_id = %s"
                count_params.append(side_id)
            if date_from:
                batch_count_query += " AND p.file_date >= %s"
                count_params.append(date_from)
            if date_to:
                batch_count_query += " AND p.file_date <= %s"
                count_params.append(date_to)
            
            batch_count_query += " GROUP BY wc.category_id"
            
            try:
                batch_counts = execute_query(batch_count_query, tuple(count_params), fetch="all")
                if batch_counts:
                    file_count_map = {row[0]: row[1] for row in batch_counts}
            except Exception as e:
                logger.error(f"Error batch calculating file counts: {e}", exc_info=True)
                # Fallback to individual queries if batch fails
                file_count_map = {}
        
        # Process categories using pre-calculated counts
        for idx, row, category_id in category_rows:
            # Stop if we have enough categories
            if len(categories) >= limit:
                if idx > 0 and result['data']:
                    prev_row = result['data'][idx - 1]
                    last_valid_cursor = prev_row.get('id') or prev_row.get('c.id')
                break
            
            # Use pre-calculated count or fallback to row value
            if file_filter_conditions:
                file_count = file_count_map.get(category_id, 0)
                # If not in map and we need to calculate individually (fallback)
                if category_id not in file_count_map and not file_count_map:
                    count_query = """
                        SELECT COUNT(DISTINCT p.id)
                        FROM words_categorys wc
                        JOIN words_hashs wp ON wc.word_id = wp.word_id
                        JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                        
                        WHERE wc.category_id = %s
                    """
                    count_params = [category_id]
                    
                    if source_id:
                        count_query += " AND hc.source_id = %s"
                        count_params.append(source_id)
                    if side_id:
                        count_query += " AND hc.side_id = %s"
                        count_params.append(side_id)
                    if date_from:
                        count_query += " AND p.file_date >= %s"
                        count_params.append(date_from)
                    if date_to:
                        count_query += " AND p.file_date <= %s"
                        count_params.append(date_to)
                    
                    try:
                        count_result = execute_query(count_query, tuple(count_params), fetch="one")
                        file_count = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
                    except Exception as e:
                        logger.error(f"Error calculating file count for category {category_id}: {e}")
                        file_count = 0
            else:
                file_count = row.get('file_count') or 0
            
            # Only include categories that have files (or matching files if filters active)
            if file_count > 0:
                categories.append({
                    'id': category_id,
                    'name': row.get('name') or row.get('w.word') or 'Unnamed Category',
                    'file_count': file_count,
                    'word_count': row.get('word_count') or 0
                })
        
        # Sort categories if needed (client-side for file_count and name)
        if sort_by == 'file_count':
            categories.sort(key=lambda x: x.get('file_count', 0), reverse=(sort_dir.lower() == 'desc'))
        elif sort_by == 'name':
            categories.sort(key=lambda x: (x.get('name') or '').lower(), reverse=(sort_dir.lower() == 'desc'))
        
        # Limit to exactly the requested number after filtering and sorting
        categories = categories[:limit]
        
        # Determine if there are more pages
        # If we got exactly the limit and there are more items in the fetched data, there might be more
        # Also check if the original result has a next cursor
        has_more_items = (len(categories) == limit and len(result['data']) > len(categories)) or result.get('has_next', False)
        
        # Update next_cursor to point to the last item we're returning (for proper pagination)
        if has_more_items and categories:
            # Use the ID of the last category we're returning as the next cursor
            next_cursor = categories[-1].get('id')
        else:
            next_cursor = None
        
        # Always calculate accurate total count of categories with files
        # This ensures pagination shows the correct number of pages
        count_query = """
            SELECT COUNT(DISTINCT c.id)
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            WHERE EXISTS (
                SELECT 1 FROM words_categorys wc
                JOIN words_hashs wp ON wc.word_id = wp.word_id
                JOIN hash_contexts hc ON hc.hash_id = wp.hash_id JOIN paths p ON p.context_id = hc.id
                
                WHERE wc.category_id = c.id
        """
        count_params = []
        
        if source_id:
            count_query += " AND hc.source_id = %s"
            count_params.append(source_id)
        if side_id:
            count_query += " AND hc.side_id = %s"
            count_params.append(side_id)
        if date_from:
            count_query += " AND p.file_date >= %s"
            count_params.append(date_from)
        if date_to:
            count_query += " AND p.file_date <= %s"
            count_params.append(date_to)
        
        count_query += ")"
        
        if search:
            expansion = expand_for_search(search)
            if expansion.word_ids:
                count_query += (" AND (w.word ILIKE %s OR c.word_id = ANY(%s))")
                count_params.extend([f'%{search}%', list(expansion.word_ids)])
            else:
                count_query += " AND w.word ILIKE %s"
                count_params.append(f'%{search}%')
        
        count_result = execute_query(count_query, tuple(count_params) if count_params else None, fetch="one")
        total_estimated = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
        
        return jsonify({
            'success': True,
            'data': categories,
            'next_cursor': next_cursor if has_more_items else None,
            'prev_cursor': result['prev_cursor'],
            'has_next': has_more_items,
            'has_prev': result['has_prev'],
            'total_estimated': total_estimated
        })
    except ValueError as e:
        # ERROR HANDLING FIX: Handle validation errors separately
        logger.warning(f"Validation error in api_archives_categories: {e}")
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=400)
    except Exception as e:
        logger.error(f"Error in api_archives_categories: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'An error occurred while loading categories. Please try again.'}), 500


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/keywords', methods=['GET'])
def api_archives_keywords():
    """Get keywords with cursor-based pagination"""
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'id')
        sort_dir = request.args.get('sort_dir', 'asc')
        
        # Build query with joins for file counts - use INNER JOIN to filter only keywords with files
        # INNER JOIN naturally filters out keywords without any file associations
        joins = [
            "INNER JOIN keywords_hashs kp ON k.id = kp.keyword_id",
            "LEFT JOIN hash_contexts hc ON hc.hash_id = kp.hash_id",
            "LEFT JOIN paths p ON p.context_id = hc.id",
        ]
        
        select_columns = [
            "k.id",
            "k.category_id",
            "k.keyword",
            "COUNT(DISTINCT p.id) as file_count"
        ]
        
        filters = {}
        
        # Note: Search filtering will be done after loading keyword text
        # because we need to unpickle the keyword blob to get the text
        filters = {}
        
        # Determine sort column
        # Note: file_count is calculated, so we sort client-side for it
        # For SQL sorting, use id as default when sorting by file_count
        sort_column_map = {
            'id': 'k.id',
            'file_count': 'k.id',  # Use id for SQL, will sort by file_count client-side
            'name': 'k.id'  # Will sort by text after loading
        }
        sort_column = sort_column_map.get(sort_by, 'k.id')
        sort_direction = SortDirection.DESC if sort_dir.lower() == 'desc' else SortDirection.ASC
        
        paginator = get_cursor_paginator('keywords')
        # Fetch more items to account for filtering/searching
        fetch_limit = max(limit * 3 if search else limit * 2, 50)  # Fetch more if searching, minimum 50
        
        result = paginator.get_page(
            cursor=cursor,
            limit=fetch_limit,
            sort_column=sort_column,
            sort_direction=sort_direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias='k'
        )
        
        keywords = []
        all_word_ids = set()
        keyword_word_map = {}
        
        # First pass: extract word IDs from all keywords (but limit to what we need)
        for row in result['data']:
            # Stop early if we have enough keywords (rough estimate)
            if len(keyword_word_map) >= limit * 2:
                break
            keyword_id = row.get('id') or row.get('k.id')
            keyword_bytes = row.get('keyword') or row.get('k.keyword')
            try:
                if keyword_bytes:
                    word_ids = unpack_int_list(keyword_bytes)
                    if word_ids and isinstance(word_ids, list):
                        keyword_word_map[keyword_id] = word_ids
                        all_word_ids.update(word_ids)
            except Exception as e:
                logger.warning(f"Error unpickling keyword {keyword_id}: {e}")
        
        # Batch load all words
        word_dict = {}
        if all_word_ids:
            placeholders = ','.join(['%s'] * len(all_word_ids))
            words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
            words_result = execute_query(words_query, list(all_word_ids))
            if words_result:
                word_dict = {row[0]: row[1] for row in words_result}
        
        # Second pass: build keyword list with text
        # Note: All keywords from result already have files (filtered by INNER JOIN)
        for row in result['data']:
            # Stop if we have enough keywords
            if len(keywords) >= limit:
                break
                
            keyword_id = row.get('id') or row.get('k.id')
            file_count = row.get('file_count') or 0
            category_id = row.get('category_id') or row.get('k.category_id')
            
            # All keywords from query already have files (INNER JOIN ensures this), but verify
            if file_count > 0 and keyword_id in keyword_word_map:
                word_ids = keyword_word_map[keyword_id]
                words = [word_dict.get(wid, '') for wid in word_ids if wid in word_dict]
                if words:
                    keyword_text = ' '.join(words)
                    
                    # Apply search filter if provided
                    if search and search.lower() not in keyword_text.lower():
                        continue
                    
                    keywords.append({
                        'id': keyword_id,
                        'name': keyword_text,
                        'category_id': category_id,
                        'file_count': file_count
                    })
        
        # Sort by name if requested (after loading text)
        if sort_by == 'name':
            keywords.sort(key=lambda x: (x.get('name') or '').lower(), reverse=(sort_dir.lower() == 'desc'))
        elif sort_by == 'file_count':
            keywords.sort(key=lambda x: x.get('file_count', 0), reverse=(sort_dir.lower() == 'desc'))
        
        # Limit to exactly the requested number after filtering/searching and sorting
        keywords = keywords[:limit]
        
        # Always calculate accurate total count of keywords with files
        # Keywords already have files (filtered by INNER JOIN), but we need accurate count
        count_query = """
            SELECT COUNT(DISTINCT k.id)
            FROM keywords k
            INNER JOIN keywords_hashs kp ON k.id = kp.keyword_id
        """
        count_params = []
        
        if search:
            # For search, we need to unpickle and check text - this is complex
            # For now, use an approximation based on the paginator's estimate
            # A more accurate count would require loading all keywords and checking text
            # which is expensive, so we'll use the filtered result as an estimate
            total_estimated = len(keywords) if len(keywords) < limit else result.get('total_estimated', len(keywords))
        else:
            # Without search, count all keywords with files
            count_result = execute_query(count_query, tuple(count_params) if count_params else None, fetch="one")
            total_estimated = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
        
        # Determine if there are more pages
        has_more_items = (len(keywords) == limit and len(result['data']) > len(keywords)) or result.get('has_next', False)
        has_next = has_more_items
        
        # Update next_cursor to point to the last item we're returning
        if has_next and keywords:
            next_cursor = keywords[-1].get('id')
        else:
            next_cursor = None
        
        has_prev = cursor is not None and cursor > 0
        
        return jsonify({
            'success': True,
            'data': keywords,
            'next_cursor': next_cursor if has_next else None,
            'prev_cursor': result['prev_cursor'] if has_prev else None,
            'has_next': has_next,
            'has_prev': has_prev,
            'total_estimated': total_estimated
        })
    except Exception as e:
        logger.error(f"Error in api_archives_keywords: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'An error occurred while loading keywords. Please try again.'}), 500



# ============================================================================
# FLASK API ROUTES
# ============================================================================

@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/titles', methods=['GET'])
def api_archives_titles():
    """
    Get titles with cursor-based pagination - WITHOUT similarity grouping
    
    Query Parameters:
        cursor: Pagination cursor (int)
        limit: Number of results per page (default: 50)
        search: Search term to filter titles
        sort_by: Field to sort by (default: 'id')
        sort_order: 'asc' or 'desc' (default: 'desc')
    """
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'id')
        sort_order = request.args.get('sort_order', 'desc').lower()
        
        # Build query - include file_name as fallback for title. Titles belong
        # to canonical content (hash_id); the representative occurrence (first
        # live path, id order) is exposed for link-compatible DTOs.
        # NOTE: the subqueries deliberately avoid COUNT()/MIN() - the cursor
        # paginator's GROUP BY detection scans select_columns for aggregate
        # tokens and would mangle the grouping otherwise.
        joins = []
        
        select_columns = [
            "tc.id",
            "tc.title_status",
            f"{TC_PATH_SUBQ} AS path_id",
            "tc.title_data",
            "tc.hash_id",
            f"COALESCE({TC_NAME_SUBQ}, '') AS file_name",
            f"CASE WHEN {TC_EXISTS} THEN 1 ELSE 0 END AS file_count"
        ]
        
        filters = {'tc.title_status': 'Main'}
        
        # Determine sort direction
        direction = SortDirection.ASC if sort_order == 'asc' else SortDirection.DESC
        
        # SEC-03: explicit client value -> approved SQL column mapping.
        # Anything outside the allowlist is rejected with HTTP 400.
        _TITLE_SORT_COLUMNS = {
            'id': 'tc.id',
            'title_status': 'tc.title_status',
            'hash_id': 'tc.hash_id',
            'title_data': 'tc.title_data',
            'file_count': 'tc.id',  # calculated field; SQL sorts by id, client re-sorts
        }
        try:
            sql_sort_column = validate_identifier(sort_by, _TITLE_SORT_COLUMNS, field="sort_by")
        except IdentifierError:
            return jsonify({'success': False, 'error': 'Invalid sort_by parameter'}), 400
        
        paginator = get_cursor_paginator('titles_content')
        result = paginator.get_page(
            cursor=cursor,
            limit=limit,
            sort_column=sql_sort_column,
            sort_direction=direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias='tc'
        )

        titles = []
        all_title_word_ids = set()
        title_word_map = {}

        for row in result['data']:
            title_id = row.get('id') or row.get('tc.id')
            title_bytes = row.get('title_data') or row.get('tc.title_data')
            try:
                if not title_bytes:
                    continue

                if isinstance(title_bytes, memoryview):
                    title_bytes = bytes(title_bytes)
                elif not isinstance(title_bytes, bytes):
                    title_bytes = bytes(title_bytes)

                if len(title_bytes) < 2:
                    continue

                word_ids = unpack_int_list(title_bytes)
                if word_ids and isinstance(word_ids, list):
                    title_word_map[title_id] = word_ids
                    all_title_word_ids.update(word_ids)
            except Exception:
                continue

        title_word_dict = {}
        if all_title_word_ids:
            placeholders = ','.join(['%s'] * len(all_title_word_ids))
            words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
            words_result = execute_query(words_query, list(all_title_word_ids))
            if words_result:
                title_word_dict = {row[0]: row[1] for row in words_result}

        for row in result['data']:
            title_id = row.get('id') or row.get('tc.id')
            path_id = row.get('path_id') or row.get('tc.path_id')
            # Try multiple possible keys for file_name
            file_name = (
                row.get('file_name') or 
                row.get('p.file_name') or 
                row.get('p_file_name') or 
                ""
            )

            # Prioritize title constructed from title_data (word IDs) - this is the original file name
            title_text = ""
            if title_id in title_word_map:
                word_ids = title_word_map[title_id]
                # Preserve order: get words in the exact order of word_ids
                # Handle missing words gracefully (skip missing words)
                words = []
                for wid in word_ids:
                    word = title_word_dict.get(wid, '')
                    if word:  # Only add non-empty words
                        words.append(word)
                if words:
                    title_text = ' '.join(words).strip()

            if not title_text:
                try:
                    title_text = load_text_title(title_id) or ""
                except Exception as e:
                    logger.debug(f"Error loading title {title_id} via load_text_title: {e}")
                    title_text = ""

            # Only use file_name as a fallback if title_data couldn't be decoded
            if not title_text and file_name:
                title_text = file_name.strip()

            if not title_text:
                logger.debug(f"Title {title_id} has no decodable text and no file_name, using placeholder")
                title_text = f"[Title #{title_id}]"

            if search and search.lower() not in title_text.lower():
                continue

            titles.append({
                'id': title_id,
                'name': title_text,
                'name_display': title_text[:100],
                'status': row.get('title_status') or row.get('tc.title_status'),
                'file_count': row.get('file_count') or 0,
                'hash_id': row.get('hash_id') or row.get('tc.hash_id'),
                'path_id': row.get('path_id') or row.get('tc.path_id')
            })
        
        # Sort by file_count client-side if requested (since it's a calculated field)
        if sort_by == 'file_count':
            titles.sort(key=lambda x: x.get('file_count', 0), reverse=(sort_order == 'desc'))
        elif sort_by == 'name':
            titles.sort(key=lambda x: (x.get('name') or '').lower(), reverse=(sort_order == 'desc'))
        
        # Limit to exactly the requested number after filtering and sorting
        titles = titles[:limit]
        
        # Always calculate accurate total count of titles with Main status
        count_query = """
            SELECT COUNT(DISTINCT tc.id)
            FROM titles_content tc
            WHERE tc.title_status = 'Main'
        """
        count_params = []
        
        if search:
            # For search, we need to check title text which requires unpickling
            # This is expensive, so we'll use an approximation
            # If we got fewer than limit results, that's the total
            # Otherwise, estimate based on paginator
            if len(titles) < limit:
                total_estimated = len(titles)
            else:
                # Estimate: use paginator's count as base, but it might be higher
                total_estimated = result.get('total_estimated', len(titles))
        else:
            # Without search, count all Main titles
            count_result = execute_query(count_query, tuple(count_params) if count_params else None, fetch="one")
            total_estimated = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
        
        # Determine if there are more pages
        has_next = (len(titles) == limit and total_estimated > len(titles)) or result.get('has_next', False)
        
        # Return all titles without grouping
        return jsonify({
            'success': True,
            'data': titles,
            'grouped': False,
            'total_count': len(titles),
            'next_cursor': result['next_cursor'] if has_next else None,
            'prev_cursor': result['prev_cursor'],
            'has_next': has_next,
            'has_prev': result['has_prev'],
            'total_estimated': total_estimated
        })
    except Exception as e:
        logger.error(f"Error in api_archives_titles: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/titles/all', methods=['GET'])
def api_archives_titles_all():
    """
    Get ALL titles without pagination (use with caution for large datasets)
    
    Query Parameters:
        search: Search term to filter titles
        sort_by: Field to sort by (default: 'name')
        sort_order: 'asc' or 'desc' (default: 'asc')
        limit: Maximum number of results (default: 1000)
    """
    try:
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'name')
        sort_order = request.args.get('sort_order', 'asc').lower()
        limit = request.args.get('limit', 1000, type=int)
        
        # Prevent excessive queries
        if limit > 5000:
            return jsonify({
                'success': False, 
                'error': 'Limit exceeds maximum of 5000'
            }), 400
        
        # Build simple query - include file_name. Titles belong to canonical
        # content (hash_id); the representative occurrence (first live path,
        # id order) is exposed as path_id for link-compatible DTOs.
        query = f"""
            SELECT 
                tc.id,
                tc.title_status,
                {TC_PATH_SUBQ} AS path_id,
                tc.title_data,
                COALESCE({TC_NAME_SUBQ}, '') AS file_name,
                {TC_COUNT} AS file_count
            FROM titles_content tc
            WHERE tc.title_status = 'Main'
            ORDER BY tc.id DESC
            LIMIT %s
        """
        
        result = execute_query(query, [limit])
        
        if not result:
            return jsonify({
                'success': True,
                'data': [],
                'total_count': 0
            })
        
        titles = []
        all_title_word_ids = set()
        title_word_map = {}

        for row in result:
            title_id = row[0]
            title_bytes = row[3]
            try:
                if not title_bytes:
                    continue
                if isinstance(title_bytes, memoryview):
                    title_bytes = bytes(title_bytes)
                elif not isinstance(title_bytes, bytes):
                    title_bytes = bytes(title_bytes)
                if len(title_bytes) < 2:
                    continue
                word_ids = unpack_int_list(title_bytes)
                if word_ids and isinstance(word_ids, list):
                    title_word_map[title_id] = word_ids
                    all_title_word_ids.update(word_ids)
            except Exception:
                continue

        title_word_dict = {}
        if all_title_word_ids:
            placeholders = ','.join(['%s'] * len(all_title_word_ids))
            words_query = f"SELECT id, word FROM words WHERE id IN ({placeholders})"
            words_result = execute_query(words_query, list(all_title_word_ids))
            if words_result:
                title_word_dict = {row[0]: row[1] for row in words_result}

        for row in result:
            title_id = row[0]
            file_name = row[4] if len(row) > 4 else None  # file_name is at index 4

            # Prioritize title constructed from title_data (word IDs) - this is the original file name
            title_text = ""
            if title_id in title_word_map:
                word_ids = title_word_map[title_id]
                words = [title_word_dict.get(wid, '') for wid in word_ids if wid in title_word_dict]
                if words:
                    title_text = ' '.join(words)

            if not title_text:
                try:
                    title_text = load_text_title(title_id) or ""
                except Exception:
                    title_text = ""
            
            # Only use file_name as a fallback if title_data couldn't be decoded
            if not title_text and file_name:
                title_text = file_name.strip()
            
            if not title_text:
                title_text = f"[Title #{title_id}]"

            titles.append({
                'id': title_id,
                'name': title_text,
                'name_display': title_text[:100],
                'status': row[1],
                'file_count': row[4] or 0,
                'path_id': row[2]
            })
        
        # Apply search filter
        if search:
            titles = filter_titles_by_search(titles, search)
        
        # Apply sorting
        if sort_by in ['name', 'name_display', 'file_count', 'id']:
            titles = display_titles_sorted(
                titles, 
                sort_by=sort_by, 
                reverse=(sort_order == 'desc')
            )
        
        return jsonify({
            'success': True,
            'data': titles,
            'total_count': len(titles)
        })
    except Exception as e:
        logger.error(f"Error in api_archives_titles_all: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/titles/count', methods=['GET'])
def api_archives_titles_count():
    """Get total count of titles"""
    try:
        query = "SELECT COUNT(*) FROM titles_content WHERE title_status = 'Main'"
        result = execute_query(query)
        count = result[0][0] if result else 0
        
        return jsonify({
            'success': True,
            'count': count
        })
    except Exception as e:
        logger.error(f"Error in api_archives_titles_count: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)

@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/sources', methods=['GET'])
def api_archives_sources():
    """Get sources with cursor-based pagination"""
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'id')
        sort_dir = request.args.get('sort_dir', 'asc')
        
        # Get filter parameters
        category_id = request.args.get('category_id', type=int)
        side_id = request.args.get('side_id', type=int)
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        
        file_filter_conditions, _ = get_file_filter_conditions()
        
        # Build query
        joins = [
            "LEFT JOIN hash_contexts hc ON s.id = hc.source_id",
            "LEFT JOIN paths p ON p.context_id = hc.id"
        ]
        
        select_columns = [
            "s.id",
            "s.name",
            "s.job",
            "s.country",
            "s.city",
            "COUNT(DISTINCT p.id) as file_count"
        ]
        
        filters = {}
        if search:
            filters['s.name'] = {'ILIKE': f'%{search}%'}
        
        # Determine sort column and direction
        sort_column_map = {
            'id': 's.id',
            'name': 's.name',
            'file_count': 's.id'  # Will sort client-side
        }
        sql_sort_column = sort_column_map.get(sort_by, 's.id')
        sort_direction = SortDirection.DESC if sort_dir.lower() == 'desc' else SortDirection.ASC
        
        paginator = get_cursor_paginator('sources')
        # Fetch more items to account for filtering out sources with file_count = 0
        fetch_limit = max(limit * 3, 50)  # Fetch 3x or minimum 50, whichever is larger
        
        result = paginator.get_page(
            cursor=cursor,
            limit=fetch_limit,
            sort_column=sql_sort_column,
            sort_direction=sort_direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias='s'
        )
        
        # Post-process: recalculate file counts with actual filters
        sources = []
        for idx, row in enumerate(result['data']):
            # Stop if we have enough sources
            if len(sources) >= limit:
                break
                
            source_id = row.get('id') or row.get('s.id')
            
            # Recalculate file_count with actual filters
            if file_filter_conditions:
                count_query = """
                    SELECT COUNT(DISTINCT p.id)
                    FROM sources s2
                    JOIN hash_contexts hc ON s2.id = hc.source_id
                    JOIN paths p ON p.context_id = hc.id
                    WHERE s2.id = %s
                """
                count_params = [source_id]
                
                if category_id:
                    count_query += """
                        AND EXISTS (
                            SELECT 1 FROM words_hashs wp
                            JOIN words_categorys wc ON wp.word_id = wc.word_id
                            WHERE wp.hash_id = (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id) AND wc.category_id = %s
                        )
                    """
                    count_params.append(category_id)
                if side_id:
                    count_query += " AND hc.side_id = %s"
                    count_params.append(side_id)
                if date_from:
                    count_query += " AND p.file_date >= %s"
                    count_params.append(date_from)
                if date_to:
                    count_query += " AND p.file_date <= %s"
                    count_params.append(date_to)
                
                count_result = execute_query(count_query, tuple(count_params), fetch="one")
                file_count = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
            else:
                file_count = row.get('file_count') or 0
            
            # Only include sources that have files (or matching files if filters active)
            if file_count > 0:
                sources.append({
                    'id': source_id,
                    'name': row.get('name') or row.get('s.name') or 'Unnamed Source',
                    'job': row.get('job') or row.get('s.job') or 'N/A',
                    'country': row.get('country') or row.get('s.country') or 'N/A',
                    'city': row.get('city') or row.get('s.city') or 'N/A',
                    'file_count': file_count
                })
        
        # Sort sources if needed (client-side for file_count)
        if sort_by == 'file_count':
            sources.sort(key=lambda x: x.get('file_count', 0), reverse=(sort_dir.lower() == 'desc'))
        elif sort_by == 'name':
            sources.sort(key=lambda x: (x.get('name') or '').lower(), reverse=(sort_dir.lower() == 'desc'))
        
        # Limit to exactly the requested number after filtering and sorting
        sources = sources[:limit]
        
        # Determine if there are more pages
        has_more_items = (len(sources) == limit and len(result['data']) > len(sources)) or result.get('has_next', False)
        
        # Update next_cursor to point to the last item we're returning
        if has_more_items and sources:
            next_cursor = sources[-1].get('id')
        else:
            next_cursor = None
        
        # Always calculate accurate total count of sources with files
        count_query = """
            SELECT COUNT(DISTINCT s.id)
            FROM sources s
            WHERE EXISTS (
                SELECT 1 FROM hashs h
                JOIN hash_contexts hc ON hc.hash_id = h.id
                JOIN paths p ON p.context_id = hc.id
                WHERE hc.source_id = s.id
        """
        count_params = []
        
        if category_id:
            count_query += """
                AND EXISTS (
                    SELECT 1 FROM words_hashs wp
                    JOIN words_categorys wc ON wp.word_id = wc.word_id
                    WHERE wp.hash_id = (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id) AND wc.category_id = %s
                )
            """
            count_params.append(category_id)
        if side_id:
            count_query += " AND hc.side_id = %s"
            count_params.append(side_id)
        if date_from:
            count_query += " AND p.file_date >= %s"
            count_params.append(date_from)
        if date_to:
            count_query += " AND p.file_date <= %s"
            count_params.append(date_to)
        
        count_query += ")"
        
        if search:
            count_query += " AND s.name ILIKE %s"
            count_params.append(f'%{search}%')
        
        count_result = execute_query(count_query, tuple(count_params) if count_params else None, fetch="one")
        total_estimated = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
        
        return jsonify({
            'success': True,
            'data': sources,
            'next_cursor': next_cursor if has_more_items else None,
            'prev_cursor': result['prev_cursor'],
            'has_next': has_more_items,
            'has_prev': result['has_prev'],
            'total_estimated': total_estimated
        })
    except Exception as e:
        logger.error(f"Error in api_archives_sources: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/sides', methods=['GET'])
def api_archives_sides():
    """Get sides with cursor-based pagination"""
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'id')
        sort_dir = request.args.get('sort_dir', 'asc')
        
        # Get filter parameters
        category_id = request.args.get('category_id', type=int)
        source_id = request.args.get('source_id', type=int)
        date_from = request.args.get('date_from', '').strip()
        date_to = request.args.get('date_to', '').strip()
        
        file_filter_conditions, _ = get_file_filter_conditions()
        
        # Build query
        joins = [
            "LEFT JOIN hash_contexts hc ON si.id = hc.side_id",
            "LEFT JOIN paths p ON p.context_id = hc.id"
        ]
        
        select_columns = [
            "si.id",
            "si.name",
            "si.importance",
            "COUNT(DISTINCT p.id) as file_count"
        ]
        
        filters = {}
        if search:
            filters['si.name'] = {'ILIKE': f'%{search}%'}
        
        # Determine sort column and direction
        sort_column_map = {
            'id': 'si.id',
            'name': 'si.name',
            'file_count': 'si.id'  # Will sort client-side
        }
        sql_sort_column = sort_column_map.get(sort_by, 'si.id')
        sort_direction = SortDirection.DESC if sort_dir.lower() == 'desc' else SortDirection.ASC
        
        paginator = get_cursor_paginator('sides')
        # Fetch more items to account for filtering out sides with file_count = 0
        fetch_limit = max(limit * 3, 50)  # Fetch 3x or minimum 50, whichever is larger
        
        result = paginator.get_page(
            cursor=cursor,
            limit=fetch_limit,
            sort_column=sql_sort_column,
            sort_direction=sort_direction,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias='si'
        )
        
        # Post-process: recalculate file counts with actual filters
        sides = []
        for idx, row in enumerate(result['data']):
            # Stop if we have enough sides
            if len(sides) >= limit:
                break
                
            side_id = row.get('id') or row.get('si.id')
            
            # Recalculate file_count with actual filters
            if file_filter_conditions:
                count_query = """
                    SELECT COUNT(DISTINCT p.id)
                    FROM sides si2
                    JOIN hash_contexts hc ON si2.id = hc.side_id
                    JOIN paths p ON p.context_id = hc.id
                    WHERE si2.id = %s
                """
                count_params = [side_id]
                
                if category_id:
                    count_query += """
                        AND EXISTS (
                            SELECT 1 FROM words_hashs wp
                            JOIN words_categorys wc ON wp.word_id = wc.word_id
                            WHERE wp.hash_id = (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id) AND wc.category_id = %s
                        )
                    """
                    count_params.append(category_id)
                if source_id:
                    count_query += " AND hc.source_id = %s"
                    count_params.append(source_id)
                if date_from:
                    count_query += " AND p.file_date >= %s"
                    count_params.append(date_from)
                if date_to:
                    count_query += " AND p.file_date <= %s"
                    count_params.append(date_to)
                
                count_result = execute_query(count_query, tuple(count_params), fetch="one")
                file_count = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
            else:
                file_count = row.get('file_count') or 0
            
            # Only include sides that have files (or matching files if filters active)
            if file_count > 0:
                sides.append({
                    'id': side_id,
                    'name': row.get('name') or row.get('si.name') or 'Unnamed Side',
                    'importance': float(row.get('importance') or row.get('si.importance') or 0),
                    'file_count': file_count
                })
        
        # Sort sides if needed (client-side for file_count)
        if sort_by == 'file_count':
            sides.sort(key=lambda x: x.get('file_count', 0), reverse=(sort_dir.lower() == 'desc'))
        elif sort_by == 'name':
            sides.sort(key=lambda x: (x.get('name') or '').lower(), reverse=(sort_dir.lower() == 'desc'))
        
        # Limit to exactly the requested number after filtering and sorting
        sides = sides[:limit]
        
        # Determine if there are more pages
        has_more_items = (len(sides) == limit and len(result['data']) > len(sides)) or result.get('has_next', False)
        
        # Update next_cursor to point to the last item we're returning
        if has_more_items and sides:
            next_cursor = sides[-1].get('id')
        else:
            next_cursor = None
        
        # Always calculate accurate total count of sides with files
        count_query = """
            SELECT COUNT(DISTINCT si.id)
            FROM sides si
            WHERE EXISTS (
                SELECT 1 FROM hashs h
                JOIN hash_contexts hc ON hc.hash_id = h.id
                JOIN paths p ON p.context_id = hc.id
                WHERE hc.side_id = si.id
        """
        count_params = []
        
        if category_id:
            count_query += """
                AND EXISTS (
                    SELECT 1 FROM words_hashs wp
                    JOIN words_categorys wc ON wp.word_id = wc.word_id
                    WHERE wp.hash_id = (SELECT hc0.hash_id FROM hash_contexts hc0 WHERE hc0.id = p.context_id) AND wc.category_id = %s
                )
            """
            count_params.append(category_id)
        if source_id:
            count_query += " AND hc.source_id = %s"
            count_params.append(source_id)
        if date_from:
            count_query += " AND p.file_date >= %s"
            count_params.append(date_from)
        if date_to:
            count_query += " AND p.file_date <= %s"
            count_params.append(date_to)
        
        count_query += ")"
        
        if search:
            count_query += " AND si.name ILIKE %s"
            count_params.append(f'%{search}%')
        
        count_result = execute_query(count_query, tuple(count_params) if count_params else None, fetch="one")
        total_estimated = count_result[0] if count_result and isinstance(count_result, (tuple, list)) else (count_result if count_result else 0)
        
        return jsonify({
            'success': True,
            'data': sides,
            'next_cursor': next_cursor if has_more_items else None,
            'prev_cursor': result['prev_cursor'],
            'has_next': has_more_items,
            'has_prev': result['has_prev'],
            'total_estimated': total_estimated
        })
    except Exception as e:
        logger.error(f"Error in api_archives_sides: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


# ---------------------------------------------------------------------------
# Relations (duplicate content hashes)
# ---------------------------------------------------------------------------
#
# A relation is a content hash that ties more than one artifact together; see
# ``relation_duplicates_sql`` for the exact definition. The duplicate filter is
# part of the SQL that pages AND counts, so the list can no longer come back
# empty while the count says there are relations - which is exactly what the
# old shape did: it walked ``hashs`` in id order, dropped every non-duplicate
# after the fetch, and reported a total from a separate COUNT over the
# duplicate set. A page of ordinary (non-duplicate) hashes therefore answered
# ``data: []`` with ``total_estimated`` > 0.
#
# Paging is keyset ("start after the sort key of the last row shown"), never
# OFFSET. A cursor carries the sort key it was built from, so a request that
# presents one is ordered by that key: a page is always a coherent
# continuation of the page the client came from, whatever sort it names.
_RE_ORDERED_INT = re.compile(r'^-?\d+$')

_RELATION_SORT_COLUMNS = {
    'id': 'r.id',
    'name': 'r.name',
    'file_count': 'r.file_count',
}
_RELATION_SORT_PREFIXES = {'id': 'i', 'name': 'n', 'file_count': 'f'}
_RELATION_PREFIX_SORTS = {v: k for k, v in _RELATION_SORT_PREFIXES.items()}


def _relation_sort_value(sort_by, row):
    """The sort key value of a result row (columns: id, name, ..., file_count)."""
    if sort_by == 'id':
        return row[0]
    if sort_by == 'name':
        return row[1]
    return row[3]


def _encode_relation_cursor(sort_by, value, row_id):
    """Opaque cursor for the next page: sort key value + tie-breaking id.

    Id-ordered pages keep the historical shape (a bare integer) so cursors
    from an older build - and any link holding one - still work.
    """
    if sort_by == 'id':
        return str(row_id)
    return f"{_RELATION_SORT_PREFIXES[sort_by]}:{quote(str(value), safe='')}:{row_id}"


def _decode_relation_cursor(raw):
    """Parse a cursor into ``(sort_by, value, row_id)``, or None.

    ``'12'`` (legacy) and ``'i:12'`` both mean "continue after id 12";
    ``'f:3:17'`` means "continue after file_count 3, id 17";
    ``'n:<hash>:17'`` is the same for name ordering. Anything unparsable is
    treated as no cursor rather than raising, so a stale link shows page 1
    instead of an error page.
    """
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    parts = raw.split(':')
    if len(parts) == 1:
        if not _RE_ORDERED_INT.match(parts[0]):
            return None
        return ('id', None, int(parts[0]))
    sort_by = _RELATION_PREFIX_SORTS.get(parts[0])
    if sort_by is None:
        return None
    if sort_by == 'id':
        if len(parts) != 2 or not _RE_ORDERED_INT.match(parts[1]):
            return None
        return ('id', None, int(parts[1]))
    if len(parts) != 3:
        return None
    value, row_id = unquote(parts[1]), parts[2]
    if not _RE_ORDERED_INT.match(row_id):
        return None
    if sort_by == 'file_count':
        if not _RE_ORDERED_INT.match(value):
            return None
        value = int(value)
    return (sort_by, value, int(row_id))


def _relation_keyset(cursor, direction):
    """WHERE fragment + params resuming after ``cursor`` (``('', [])`` if none)."""
    if cursor is None:
        return '', []
    sort_by, value, row_id = cursor
    column = _RELATION_SORT_COLUMNS[sort_by]
    if sort_by == 'id':
        # Plain id continuation (also what the legacy integer cursor meant).
        operator = '<' if direction == 'DESC' else '>'
        return f"WHERE {column} {operator} %s", [row_id]
    # Ties on the sort key are broken by id ascending, so the resumed page
    # continues with a larger id at the same key value.
    operator = '>' if direction == 'ASC' else '<'
    return (
        f"WHERE ({column} {operator} %s OR ({column} = %s AND r.id > %s))",
        [value, value, row_id],
    )


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/hashs', methods=['GET'])
def api_archives_hashs():
    """Content relations: hashes shared by more than one artifact."""
    try:
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'file_count')
        sort_dir = str(request.args.get('sort_dir', 'desc')).lower()
        direction = 'ASC' if sort_dir == 'asc' else 'DESC'

        cursor = _decode_relation_cursor(request.args.get('cursor'))
        if cursor is not None:
            # The cursor dictates its own ordering (it carries one sort key).
            sort_by = cursor[0]
        if sort_by not in _RELATION_SORT_COLUMNS:
            sort_by = 'file_count'

        sort_column = _RELATION_SORT_COLUMNS[sort_by]
        tie_breaker = '' if sort_by == 'id' else ', r.id ASC'
        order_clause = f"ORDER BY {sort_column} {direction}{tie_breaker}"

        where_clause, keyset_params = _relation_keyset(cursor, direction)

        inner, filter_params = relation_duplicates_sql(search or None)
        # ``totals`` counts the whole filtered relation set, not just the page
        # that follows the cursor: the sidebar/nav count must describe the
        # section, not the remainder of it.
        query = f"""
            WITH relations AS (
                {inner}
            ),
            totals AS (
                SELECT COUNT(*) AS total_count FROM relations
            )
            SELECT r.id, r.name, r.context_count, r.file_count,
                   t.total_count
            FROM relations r
            CROSS JOIN totals t
            {where_clause}
            {order_clause}
            LIMIT %s
        """
        params = list(filter_params) + list(keyset_params) + [limit + 1]
        rows = execute_query(query, tuple(params), fetch="all") or []

        has_next = len(rows) > limit
        page_rows = rows[:limit]
        if page_rows:
            total_estimated = page_rows[0][4] or 0
        else:
            # Empty page (cursor walked off the end): the count still has to
            # describe the section rather than an empty remainder.
            total_estimated = count_relation_duplicates(search or None)

        relations = []
        for row in page_rows:
            relations.append({
                'id': row[0],
                'name': row[1] or 'Unknown Hash',
                'context_count': row[2] or 0,
                'file_count': row[3] or 0,
            })

        next_cursor = None
        if has_next and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_relation_cursor(
                sort_by, _relation_sort_value(sort_by, last), last[0]
            )

        return jsonify({
            'success': True,
            'data': relations,
            # ``prev_cursor`` stays None on purpose: a forward keyset cursor
            # cannot name the row *before* the page, and the section view
            # already remembers the cursor it used for each page. Returning a
            # value here would overwrite that map with a cursor that re-shows
            # the current page.
            'prev_cursor': None,
            'next_cursor': next_cursor,
            'has_next': has_next,
            'has_prev': cursor is not None,
            'total_estimated': total_estimated
        })
    except Exception as e:
        logger.error(f"Error in api_archives_hashs: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/addresses', methods=['GET'])
def api_archives_addresses():
    """Get addresses (words) with cursor-based pagination - WITHOUT similarity comparison"""
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        
        # Build query - addresses are stored in words table
        # Use subquery for file_count to avoid GROUP BY issues with cursor pagination
        joins = [
            "LEFT JOIN (SELECT wp.word_id, COUNT(DISTINCT p.id) as file_count"
            " FROM words_hashs wp"
            " LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id"
            " LEFT JOIN paths p ON p.context_id = hc.id"
            " GROUP BY wp.word_id) file_counts ON w.id = file_counts.word_id"
        ]
        
        select_columns = [
            "w.id",
            "w.word as name",
            "COALESCE(file_counts.file_count, 0) as file_count"
        ]
        
        filters = {}
        if search:
            # Same union as the category facet above: the word's text, or the
            # word whose name in another language normalises to the query.
            expansion = expand_for_search(search)
            if expansion.word_ids:
                filters['OR'] = [
                    {'w.word': {'op': 'ILIKE', 'value': f'%{search}%'}},
                    {'w.id': {'op': 'IN', 'value': list(expansion.word_ids)}},
                ]
            else:
                filters['w.word'] = {'ILIKE': f'%{search}%'}
        
        paginator = get_cursor_paginator('words')
        result = paginator.get_page(
            cursor=cursor,
            limit=limit,
            sort_column='w.id',
            sort_direction=SortDirection.ASC,
            filters=filters,
            joins=joins,
            select_columns=select_columns,
            table_alias='w'
        )
        
        addresses = []
        for row in result['data']:
            file_count = row.get('file_count') or 0
            # Get the exact word value from database - try multiple possible keys
            # The cursor paginator returns columns as-is from the SELECT clause
            # Since we use "w.word as name", the key will be 'name'
            word_value = (
                row.get('name') or  # 'w.word as name' alias (most likely)
                row.get('w.word') or  # Full column name (fallback)
                row.get('word') or  # Just column name (fallback)
                'Unknown Address'
            )
            # CRITICAL: Use exact database value - no string manipulation, no encoding changes
            # The value should be exactly as stored in the database words.word column (TEXT type)
            # Flask's jsonify will handle the encoding correctly, preserving the exact bytes
            addresses.append({
                'id': row.get('id') or row.get('w.id'),
                'name': word_value,  # Exact database value, no transformation
                'file_count': file_count
            })
        
        return jsonify({
            'success': True,
            'data': addresses,
            'grouped': False,  # No similarity grouping
            'next_cursor': result['next_cursor'],
            'prev_cursor': result['prev_cursor'],
            'has_next': result['has_next'],
            'has_prev': result['has_prev'],
            'total_estimated': result['total_estimated']
        })
    except Exception as e:
        logger.error(f"Error in api_archives_addresses: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/geolocation', methods=['GET'])
def api_archives_geolocation():
    """Get files with latitude and longitude coordinates with cursor-based pagination"""
    try:
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        # SECURITY FIX: Cap limit to prevent DoS attacks
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip()
        sort_by = request.args.get('sort_by', 'id')
        # AUDIT-SQLI-01: the direction is interpolated into ORDER BY, so it
        # must be one of two literals - never the raw request value (it was
        # only lower-cased, which let any authenticated user inject SQL).
        sort_dir = 'asc' if str(request.args.get('sort_dir', 'desc')).strip().lower() == 'asc' else 'desc'
        
        # Build query - files with coordinates
        joins = [
            "LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id",
            "LEFT JOIN sources s ON hc.source_id = s.id",
            "LEFT JOIN sides si ON hc.side_id = si.id"
        ]
        
        select_columns = [
            "p.id",
            "p.file_name as name",
            "p.file_path",
            "p.file_size",
            "p.file_type",
            "p.file_date",
            "p.coordinates",
            "COALESCE(s.name, 'Unknown') as source_name",
            "COALESCE(si.name, 'Unknown') as side_name",
            "1 as file_count"  # Each file is one item
        ]
        
        filters = {}
        
        if search:
            filters['p.file_name'] = {'ILIKE': f'%{search}%'}
        
        # Determine sort column and direction
        sort_column_map = {
            'id': 'p.id',
            'name': 'p.file_name',
            'file_count': 'p.id',  # All have count of 1
            'file_size': 'p.file_size',
            'file_date': 'p.file_date'
        }
        sort_column = sort_column_map.get(sort_by, 'p.id')
        sort_direction = SortDirection.DESC if sort_dir.lower() == 'desc' else SortDirection.ASC
        
        # Build query manually to handle IS NOT NULL and non-empty condition
        # Filter out NULL, empty strings, and whitespace-only coordinates
        where_conditions = [
            "p.coordinates IS NOT NULL",
            "TRIM(p.coordinates) != ''",
            "LENGTH(TRIM(p.coordinates)) > 0"
        ]
        params = []
        
        if search:
            where_conditions.append("p.file_name ILIKE %s")
            params.append(f'%{search}%')
        
        if cursor:
            if sort_direction == SortDirection.DESC:
                where_conditions.append(f"{sort_column} < %s")
            else:
                where_conditions.append(f"{sort_column} > %s")
            params.append(cursor)
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        order_clause = f"ORDER BY {sort_column} {sort_dir.upper()}"
        limit_clause = f"LIMIT {limit + 1}"  # Fetch one extra to check if there's a next page
        
        query = f"""
            SELECT {', '.join(select_columns)}
            FROM paths p
            {' '.join(joins)}
            {where_clause}
            {order_clause}
            {limit_clause}
        """
        
        results = execute_query(query, tuple(params) if params else None)
        
        # Helper function to validate GPS coordinates
        def is_valid_gps_coordinate(lat, lon):
            """Check if latitude and longitude are valid GPS coordinates"""
            try:
                lat_float = float(lat)
                lon_float = float(lon)
                # Valid GPS ranges: latitude -90 to 90, longitude -180 to 180
                return -90 <= lat_float <= 90 and -180 <= lon_float <= 180
            except (ValueError, TypeError):
                return False
        
        # Process and filter results - only include files with valid GPS coordinates
        geolocation_files = []
        valid_results = []
        
        for row in (results or []):
            # Convert row tuple/dict to dict format
            if isinstance(row, (list, tuple)):
                row_dict = {}
                for i, col in enumerate(select_columns):
                    col_name = col.split(' as ')[-1].strip() if ' as ' in col else col.split('.')[-1]
                    row_dict[col_name] = row[i] if i < len(row) else None
                row = row_dict
            
            file_id = row.get('id') or row.get('p.id')
            file_name = row.get('name') or row.get('p.file_name') or row.get('file_name') or 'Unnamed File'
            coordinates = row.get('coordinates') or row.get('p.coordinates') or ''
            
            # Parse and validate coordinates
            lat = None
            lon = None
            if coordinates:
                try:
                    parts = coordinates.split(',')
                    if len(parts) == 2:
                        lat_str = parts[0].strip()
                        lon_str = parts[1].strip()
                        lat = float(lat_str)
                        lon = float(lon_str)
                        
                        # Only include if coordinates are valid GPS coordinates
                        if not is_valid_gps_coordinate(lat, lon):
                            continue  # Skip this file - invalid GPS coordinates
                except (ValueError, AttributeError):
                    continue  # Skip this file - can't parse coordinates
            
            # Only add files with valid GPS coordinates
            if lat is not None and lon is not None:
                geolocation_files.append({
                    'id': file_id,
                    'name': file_name,
                    'file_name': file_name,
                    'file_path': row.get('file_path') or row.get('p.file_path') or '',
                    'file_size': row.get('file_size') or row.get('p.file_size') or 0,
                    'file_type': row.get('file_type') or row.get('p.file_type') or 'Unknown',
                    'file_date': str(row.get('file_date') or row.get('p.file_date') or '') if row.get('file_date') or row.get('p.file_date') else None,
                    'coordinates': coordinates,
                    'latitude': lat,
                    'longitude': lon,
                    'source_name': row.get('source_name') or 'Unknown',
                    'side_name': row.get('side_name') or 'Unknown',
                    'file_count': 1
                })
                valid_results.append(row)
        
        # Check if we have more results (need to fetch more if we filtered some out)
        has_next = False
        if results and len(results) > limit:
            has_next = True
            # If we filtered results, we might need to fetch more
            if len(geolocation_files) < limit and len(results) == limit + 1:
                # We got exactly limit+1 but filtered some out, so there might be more
                pass
        
        # Adjust pagination based on valid results
        if len(geolocation_files) > limit:
            has_next = True
            geolocation_files = geolocation_files[:limit]
            valid_results = valid_results[:limit]
        
        next_cursor = None
        prev_cursor = None
        if valid_results:
            last_row = valid_results[-1]
            # Get the ID from the first column (should be p.id)
            next_cursor = last_row[0] if isinstance(last_row, (list, tuple)) else last_row.get('id') or last_row.get('p.id')
        
        if cursor:
            prev_cursor = cursor
        
        # Get total count - we need to count files with valid GPS coordinates
        # Since we can't easily validate in SQL, we'll do a more accurate count
        # by checking a sample or using a subquery that validates coordinates
        # For now, we'll use an estimate based on the filtered results
        # A more accurate approach would be to validate all coordinates, but that's expensive
        # So we'll use a reasonable estimate
        count_query = f"""
            SELECT COUNT(*) FROM paths p
            {' '.join(joins)}
            WHERE p.coordinates IS NOT NULL
            AND TRIM(p.coordinates) != ''
            AND LENGTH(TRIM(p.coordinates)) > 0
        """
        if search:
            count_query += " AND p.file_name ILIKE %s"
            count_params = (f'%{search}%',)
        else:
            count_params = None
        total_result = execute_query(count_query, count_params, fetch="one")
        total_estimated = total_result[0] if total_result and total_result[0] is not None else 0
        
        # Note: The actual count might be slightly less due to invalid coordinate formats
        # but this gives us a reasonable estimate without validating every coordinate in the database
        
        return jsonify({
            'success': True,
            'data': geolocation_files,
            'next_cursor': next_cursor if has_next else None,
            'prev_cursor': prev_cursor,
            'has_next': has_next,
            'has_prev': cursor is not None,
            'total_estimated': total_estimated
        })
    except Exception as e:
        logger.error(f"Error in api_archives_geolocation: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/filetypes', methods=['GET'])
def api_archives_filetypes():
    """Browse-by-type: distinct file types with counts (offset cursor pagination)."""
    try:
        cursor = request.args.get('cursor', type=int) or 0
        if cursor < 0:
            cursor = 0
        limit = request.args.get('limit', 50, type=int)
        if limit > 500:
            limit = 200
        if limit < 1:
            limit = 1
        search = request.args.get('search', '').strip().lstrip('.')
        sort_by = (request.args.get('sort_by', 'file_count') or 'file_count').strip().lower()
        sort_dir = 'asc' if str(request.args.get('sort_dir', 'desc')).strip().lower() == 'asc' else 'desc'

        type_expr = """
            LOWER(REPLACE(
                CASE
                    WHEN p.file_type IS NULL
                         OR TRIM(p.file_type) = ''
                         OR UPPER(p.file_type) = 'FILE'
                    THEN COALESCE(NULLIF(SUBSTRING(p.file_name FROM '\\.([^.]+)$'), ''), 'unknown')
                    ELSE p.file_type
                END, '.', ''))
        """

        where_extra = ""
        params = []
        if search:
            where_extra = f" AND {type_expr} ILIKE %s"
            params.append(f"%{search.lower()}%")

        order_map = {
            'name': f'type_key {sort_dir.upper()}',
            'id': f'type_key {sort_dir.upper()}',
            'file_count': f'file_count {sort_dir.upper()}, type_key ASC',
            'size': f'total_size {sort_dir.upper()}, type_key ASC',
        }
        order_sql = order_map.get(sort_by, order_map['file_count'])

        count_row = execute_query(
            f"""
            SELECT COUNT(*) FROM (
                SELECT {type_expr} AS type_key
                FROM paths p
                WHERE 1=1 {where_extra}
                GROUP BY 1
            ) t
            WHERE type_key IS NOT NULL AND type_key <> ''
            """,
            tuple(params) if params else None,
            fetch="one",
        )
        total_estimated = (count_row[0] if count_row else 0) or 0

        rows = execute_query(
            f"""
            SELECT type_key, file_count, total_size FROM (
                SELECT
                    {type_expr} AS type_key,
                    COUNT(*) AS file_count,
                    COALESCE(SUM(p.file_size), 0) AS total_size
                FROM paths p
                WHERE 1=1 {where_extra}
                GROUP BY 1
            ) t
            WHERE type_key IS NOT NULL AND type_key <> ''
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit + 1, cursor]),
            fetch="all",
        ) or []

        has_next = len(rows) > limit
        rows = rows[:limit]
        items = []
        for row in rows:
            type_key = (row[0] or 'unknown').lower()
            file_count = row[1] or 0
            total_size = row[2] or 0
            display = f'.{type_key}'
            items.append({
                'id': type_key,
                'name': display,
                'name_display': display,
                'file_type': display,
                'file_count': file_count,
                'total_size': total_size,
                'details': f'{file_count} files',
            })

        next_cursor = (cursor + limit) if has_next else None
        return jsonify({
            'success': True,
            'data': items,
            'next_cursor': next_cursor,
            'prev_cursor': cursor if cursor > 0 else None,
            'has_next': has_next,
            'has_prev': cursor > 0,
            'total_estimated': total_estimated,
        })
    except Exception as e:
        logger.error(f"Error in api_archives_filetypes: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.archives_api', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@archives_api_bp.route('/api/archives/geolocation/export', methods=['GET'])
def api_archives_geolocation_export():
    """Export geolocation rows (same contract as ``GET /api/export/geolocation``)."""
    from Api.routes.exports import list_export_for_interface

    return list_export_for_interface('geolocation')
