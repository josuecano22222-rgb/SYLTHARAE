"""
Keywords routes
"""

from flask import render_template, request, redirect, url_for, flash, jsonify
from werkzeug.datastructures import MultiDict

from Api.utils import (
    execute_query, load_text_keyword, batch_load_keywords_from_rows,
    select_info_sources, select_info_sides, select_info_categories,
    get_keywords_with_usage, get_word_id,
    get_query_cache, get_words_for_dropdown,
    get_categories_for_dropdown, get_keyword_stats, get_email_words,
    invalidate_query_cache,
    get_email_domains, delete_keyword, keyword_exists, db_delete_keyword,
    get_email_words_all
)
from database.operations import get_category_operations, get_keyword_operations
from Api.performance_utils import batch_load_keywords
from datetime import datetime
import logging
from core.serialization import pack_int_list
from services.i18n.term_search import expand_for_search
from Api.blueprints.files import _documents_panel_response, unpack_int_list
from core.errors import client_error, client_safe_message

from core.table_paging import fetch_page, read_page_request
from core.keyword_search import api_rank, category_names, keyword_page

logger = logging.getLogger(__name__)


def register_keywords_routes(app):
    """Register keywords routes with the Flask app"""
    
    @app.route('/keywords')
    def keywords_list():
        try:
            req = read_page_request(request.args, request.headers)
            search = request.args.get('search', '').strip()
            # Column sort comes from the table headers. Keyword text lives in
            # a pickled column the database cannot order by, so the page sorts
            # the formatted rows in Python over this allowlist - the same
            # keys, and the same fallbacks, the API endpoint accepts.
            sort_by = request.args.get('sort', 'usage_count')
            sort_order = request.args.get('order', 'desc')
            
            if sort_by not in ('usage_count', 'text', 'id', 'status'):
                sort_by = 'usage_count'
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'
            
            if len(search) > 500:
                search = search[:500]

            result = fetch_page(
                lambda page, size: get_keywords_with_usage(
                    search_term=search if search else None,
                    page=page,
                    per_page=size,
                    sort_by=sort_by,
                    sort_order=sort_order
                ),
                req,
            )
            keywords_rows, total_keywords = result.rows, result.total
            page, per_page, total_pages = result.page, result.size, result.total_pages
            
            keyword_text_map = batch_load_keywords_from_rows(keywords_rows) if keywords_rows else {}
            names = category_names(execute_query, [kw[1] for kw in keywords_rows or []])
            
            formatted_keywords = []
            keyword_texts = {}  # Track texts for duplicate detection
            
            for kw in keywords_rows or []:
                try:
                    keyword_id = kw[0]
                    usage_count = kw[3]
                    
                    keyword_text = keyword_text_map.get(keyword_id)
                    if not keyword_text:
                        continue
                        
                    # Check for duplicates
                    is_duplicate = keyword_text.lower().strip() in keyword_texts
                    if not is_duplicate:
                        keyword_texts[keyword_text.lower().strip()] = True
                    
                    formatted_keywords.append({
                        'id': keyword_id,
                        'text': keyword_text,
                        'importance': 1.0,
                        'usage_count': usage_count,
                        'is_duplicate': is_duplicate,
                        'category_id': kw[1],
                        'category_name': names.get(kw[1]),
                    })
                except Exception as e:
                    logger.error(f"Error loading keyword {kw[0]}: {e}")
                    continue
            
            # The loader filtered (search), ordered (requested column) and
            # paginated the full list; the page only formats the rows it got.

            return render_template('Keyword/keywords_list.html',
                                   keywords=formatted_keywords,
                                   page=page,
                                   per_page=per_page,
                                   total_pages=total_pages,
                                   total_keywords=total_keywords,
                                   search=search,
                                   sort_by=sort_by,
                                   sort_order=sort_order,
                                   delete_keyword_url=url_for('delete_keyword_api', keyword_id=0).replace('/0', ''))
        except Exception as e:
            flash(f"Error loading keywords: {e}", "error")
            return render_template('Keyword/keywords_list.html', keywords=[], page=1, per_page=20, total_pages=1, total_keywords=0, search='', sort_by='usage_count', sort_order='desc', delete_keyword_url='/keyword/')
    




    @app.route('/keywords/<int:keyword_id>/documents')
    def keyword_documents(keyword_id):
        """One keyword's documents, as the unified-table fragment (panel).

        The Keywords page embeds this in its side panel: the File Library's
        own list, pinned to the documents this keyword occurs in.
        """
        return _documents_panel_response(
            'keywordFilesTable',
            "EXISTS (SELECT 1 FROM keywords_hashs kh "
            "WHERE kh.hash_id = hc.hash_id AND kh.keyword_id = %s)",
            (keyword_id,),
            request.args,
            {'keyword_id': keyword_id},
        )


    @app.route('/api/keyword/check', methods=['POST'])
    def keyword_check():
        """Check if a keyword already exists in a category"""
        try:
            data = request.get_json(silent=True) or request.form.to_dict()
            keyword_text = data.get('keyword_text', '').strip()
            category_id = data.get('category_id')
            
            if not keyword_text:
                return jsonify({'exists': False, 'error': 'Keyword text is required'}), 400
            
            if not category_id:
                return jsonify({'exists': False, 'error': 'Category ID is required'}), 400
            
            try:
                category_id = int(category_id)
            except (ValueError, TypeError):
                return jsonify({'exists': False, 'error': 'Invalid category ID'}), 400
            
            # Import database operations
            
            # Check if it's a single word or multi-word phrase
            words = keyword_text.split()
            
            if len(words) < 2:
                # Single word - check words_categorys table
                word_id = get_word_id(keyword_text.lower())
                if word_id:
                    existing = execute_query("""
                        SELECT 1 FROM words_categorys 
                        WHERE word_id = %s AND category_id = %s
                        LIMIT 1
                    """, (word_id, category_id), fetch="one")
                    
                    if existing:
                        return jsonify({
                            'exists': True,
                            'type': 'word',
                            'message': f'Word "{keyword_text}" already exists in this category'
                        })
            else:
                # Multi-word phrase - check keywords table using the keyword operations
                # Get word IDs for the phrase
                word_ids = []
                for word in words:
                    word_id = get_word_id(word.lower())
                    if not word_id:
                        # Word doesn't exist, so keyword can't exist
                        return jsonify({'exists': False})
                    word_ids.append(word_id)
                
                # Create keyword blob
                keyword_blob = pack_int_list(word_ids)
                
                # Check if keyword exists
                keyword_ops = get_keyword_operations()
                existing_keyword_id = keyword_ops.get_keyword_id_by_blob(keyword_blob, category_id)
                if existing_keyword_id:
                    return jsonify({
                        'exists': True,
                        'type': 'keyword',
                        'message': f'Keyword phrase "{keyword_text}" already exists in this category'
                    })
            
            return jsonify({'exists': False})
            
        except Exception as e:
            logger.error(f"Error checking keyword: {e}")
            return jsonify({'exists': False, 'error': client_safe_message(e, subsystem='Api.routes.keywords')}), 500
    
    @app.route('/keywords/add', methods=['GET', 'POST'])
    def keywords_add():
        """Add new keywords"""
        if request.method == 'POST':
            try:
                keywords_text = request.form.get('keywords_text', [])
                custom_keywords = request.form.get('custom_keywords', '')
                category_id = request.form.get('category_id', '1')
                
                # ✅ FIXED: Input validation function
                def validate_keyword_text(text):
                    """Validate keyword text input"""
                    if not text or not isinstance(text, str):
                        return False, "Keyword text is required"
                    
                    text = text.strip()
                    
                    if len(text) < 2:
                        return False, "Keyword must be at least 2 characters"
                    
                    if len(text) > 500:
                        return False, "Keyword cannot exceed 500 characters"
                    
                    # Check for dangerous patterns
                    dangerous_patterns = ['<script', 'javascript:', 'onerror=', 'onload=']
                    if any(pattern in text.lower() for pattern in dangerous_patterns):
                        return False, "Invalid characters in keyword"
                    
                    return True, text
                
                # When sent as FormData with single value, it's a string
                # When sent as FormData with multiple values, it's a list
                if isinstance(keywords_text, str):
                    selected_keywords = [keywords_text] if keywords_text.strip() else []
                else:
                    selected_keywords = keywords_text if isinstance(keywords_text, list) else [keywords_text]
                
                # Process custom keywords
                custom_list = []
                if custom_keywords:
                    custom_list = [kw.strip() for kw in custom_keywords.replace('\n', ',').split(',') if kw.strip()]
                
                # Combine all keywords
                all_keywords = selected_keywords + custom_list
                
                if not all_keywords:
                    error_msg = "Please select or enter at least one keyword"
                    if request.headers.get('Content-Type', '').startswith('application/json') or \
                       request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, "error")
                    return redirect(url_for('keywords_add'))
                
                # ✅ FIXED: Validate all keywords before processing
                validated_keywords = []
                validation_errors = []
                for term in all_keywords:
                    is_valid, result = validate_keyword_text(term)
                    if is_valid:
                        validated_keywords.append(result)
                    else:
                        validation_errors.append(f"'{term}': {result}")
                
                if validation_errors:
                    error_msg = "Validation errors: " + "; ".join(validation_errors[:3])
                    if request.headers.get('Content-Type', '').startswith('application/json') or \
                       request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, "error")
                    return redirect(url_for('keywords_add'))
                
                # Use validated keywords
                all_keywords = validated_keywords
                
                # Add terms to database - automatically routes:
                # - Single words → words_categorys table
                # - Multi-word phrases (2+) → keywords table
                
                success_count = 0
                words_count = 0
                keywords_count = 0
                error_messages = []
                created_keyword_ids = []
                
                # Get category operations instance
                category_ops = get_category_operations()
                
                # KW-CAT-01: resolve the category up front. The form's dynamic
                # typeahead only submits existing ids, but a hand-crafted (or
                # defaulted) category_id used to fail per-term with a swallowed
                # FK error ("internal error"). Fail once, clearly, instead.
                try:
                    category_id_int = int(category_id)
                except (TypeError, ValueError):
                    category_id_int = None
                category_row = None
                if category_id_int is not None:
                    try:
                        category_row = category_ops.db_service.categorys_repo.select_category_by_id(category_id_int)
                    except Exception as cat_err:
                        logger.warning("Category lookup failed for id %s: %s", category_id_int, cat_err)
                if category_row is None:
                    error_msg = f"Selected category does not exist (id: {category_id}). Create it first."
                    if request.headers.get('Content-Type', '').startswith('application/json') or \
                       request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, "error")
                    return redirect(url_for('keywords_add'))
                
                for term in all_keywords:
                    # ✅ FIXED: Validate minimum 2 words for keywords (not single words)
                    words = term.split()
                    if len(words) < 2:
                        error_messages.append(f"'{term}': Keyword must contain at least 2 words")
                        continue
                    
                    if term.strip():
                        try:
                            result = category_ops.process_term(term.strip(), int(category_id))
                            
                            if result['success']:
                                success_count += 1
                                if result['type'] == 'word':
                                    words_count += 1
                                    logger.info(f"Added single word '{term.strip()}' to words_categorys (word_id: {result['id']})")
                                elif result['type'] == 'keyword':
                                    keywords_count += 1
                                    if result.get('id'):
                                        created_keyword_ids.append(result['id'])
                                    logger.info(f"Added multi-word phrase '{term.strip()}' to keywords (keyword_id: {result['id']})")
                            else:
                                error_messages.append(f"'{term.strip()}': {result['message']}")
                        except Exception as e:
                            logger.error(f"Error processing term '{term}': {e}")
                            error_messages.append(f"Error processing '{term}': internal error (see server logs)")
                            continue
                
                is_ajax = request.headers.get('Content-Type', '').startswith('application/json') or \
                          request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                
                if success_count > 0:
                    # A keyword added after ingestion must be matched against
                    # existing files now, not only on the next import.
                    try:
                        if created_keyword_ids:
                            category_ops.db_service.refresh_keyword_associations(created_keyword_ids)
                    except Exception as refresh_error:
                        logger.warning("Keyword created but existing-file association refresh failed: %s", refresh_error)
                    invalidate_query_cache()
                    try:
                        cache = get_query_cache()
                        # Clear all keyword-related cache entries
                        cache.clear()
                        logger.info("Cleared query cache after adding keywords")
                    except Exception as e:
                        logger.warning(f"Could not clear cache after adding keywords: {e}")
                    
                    # Build detailed success message
                    parts = []
                    if words_count > 0:
                        parts.append(f"{words_count} word{'s' if words_count != 1 else ''} (words_categorys)")
                    if keywords_count > 0:
                        parts.append(f"{keywords_count} keyword{'s' if keywords_count != 1 else ''} (keywords table)")
                    
                    message = f'Successfully added {success_count} item{"s" if success_count != 1 else ""}!'
                    if parts:
                        message += f" ({', '.join(parts)})"
                    if error_messages:
                        message += f" ({len(error_messages)} failed)"
                    
                    if is_ajax:
                        return jsonify({
                            'success': True,
                            'message': message,
                            'success_count': success_count,
                            'words_count': words_count,
                            'keywords_count': keywords_count,
                            'errors': error_messages
                        })
                    flash(message, 'success')
                    return redirect(url_for('keywords_list'))
                else:
                    error_msg = 'No keywords were added. ' + (' '.join(error_messages[:3]) if error_messages else 'Please check the validation requirements.')
                    if is_ajax:
                        return jsonify({
                            'success': False,
                            'error': error_msg,
                            'errors': error_messages
                        }), 400
                    flash(error_msg, 'warning')
                    return redirect(url_for('keywords_list'))
                
            except Exception as e:
                error_msg = f"Error adding keywords: {e}"
                logger.error(error_msg)
                is_ajax = request.headers.get('Content-Type', '').startswith('application/json') or \
                          request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                if is_ajax:
                    return jsonify({'success': False, 'error': error_msg}), 500
                flash(error_msg, "error")
        
        # AUDIT (UI-01): 'Keyword/keywords_add_edit.html' does not exist on
        # disk, so this page rendered a 500 for every visitor (it is linked
        # from the Keywords page and from the keyword detail "Edit" button).
        # The Keywords list already ships working add/edit modals, so hand
        # off to it rather than adding a second, parallel add form.
        keyword_id = request.args.get('keyword_id', type=int)
        if keyword_id:
            return redirect(url_for('keywords_list', add=1, keyword_id=keyword_id))
        return redirect(url_for('keywords_list', add=1))
    
    @app.route('/keywords/<int:keyword_id>')
    def keyword_detail(keyword_id):
        """View keyword details page - OPTIMIZED with extended stats"""
        try:
            keyword_text = load_text_keyword(keyword_id)
            if not keyword_text:
                flash('Keyword not found', 'error')
                return redirect(url_for('keywords_list'))
            
            stats = get_keyword_stats(keyword_id)
            
            usage_count = stats['usage_count'] if stats else 0
            file_types = stats['file_types'] if stats else 0
            last_used = stats['last_used'] if stats else None
            
            return render_template('Keyword/keyword_detail.html', 
                                 keyword={
                                     'id': keyword_id, 
                                     'text': keyword_text, 
                                     'usage_count': usage_count,
                                     'file_types': file_types,
                                     'last_used': last_used
                                 })
        except Exception as e:
            logger.error(f"Error loading keyword {keyword_id}: {e}")
            flash('Error loading keyword', 'error')
            return redirect(url_for('keywords_list'))
    
    @app.route('/email-words')
    def email_words():
        try:
            # Input validation and sanitization
            try:
                page = request.args.get('page', 1, type=int)
                if page < 1:
                    page = 1
            except (ValueError, TypeError):
                page = 1
            
            try:
                per_page = request.args.get('per_page', 50, type=int)
                per_page = min(max(1, per_page), 200)  # Clamp between 1 and 200
            except (ValueError, TypeError):
                per_page = 50
            
            # Filters with validation
            search_term = (request.args.get('q') or '').strip()
            if search_term and len(search_term) > 500:
                search_term = search_term[:500]
                flash("Search term was too long and has been truncated", "warning")
            
            domain = (request.args.get('domain') or '').strip()
            if domain and len(domain) > 255:
                domain = domain[:255]
                flash("Domain filter was too long and has been truncated", "warning")
            
            domain_mode = (request.args.get('domain_mode') or 'exact').strip().lower()
            if domain_mode not in ('exact', 'contains', 'endswith'):
                domain_mode = 'exact'

            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            
            # Sorting with validation
            sort_by = request.args.get('sort_by', 'usage_count', type=str)
            if sort_by not in ('word', 'usage_count'):
                sort_by = 'usage_count'
            
            sort_order = request.args.get('sort_order', 'desc', type=str)
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'
            
            email_words, total_words = get_email_words(
                page=page,
                per_page=per_page,
                search_term=search_term if search_term else None,
                domain=domain if domain else None,
                domain_mode=domain_mode,
                source_filter=source_id,
                side_filter=side_id,
                sort_by=sort_by,
                sort_order=sort_order
            )
            
            # Log for debugging
            logger.info(f"Email words page {page}: Found {len(email_words)} words, total: {total_words}")
            
            total_pages = (total_words + per_page - 1) // per_page if total_words > 0 else 1

            # Domain dropdown (based on current search/filter set, but not locked to selected domain)
            domains = get_email_domains(
                search_term=search_term if search_term else None,
                domain=None,  # Don't filter domains dropdown by selected domain
                domain_mode=domain_mode,
                limit=10000
            )

            source_rows = execute_query(
                "SELECT id, name FROM sources ORDER BY name ASC",
                fetch="all",
            ) or []
            side_rows = execute_query(
                "SELECT id, name FROM sides ORDER BY name ASC",
                fetch="all",
            ) or []
            sources = {
                row[0]: row[1]
                for row in source_rows
                if row and row[0] is not None and row[1]
            }
            sides = {
                row[0]: row[1]
                for row in side_rows
                if row and row[0] is not None and row[1]
            }
            
            return render_template(
                'email_words/email_words.html',
                email_words=email_words,
                total_words=total_words,
                page=page,
                total_pages=total_pages,
                per_page=per_page,
                search_term=search_term,
                domain=domain,
                domain_mode=domain_mode,
                domains=domains,
                sources=sources,
                sides=sides,
                source_id=source_id,
                side_id=side_id,
                sort_by=sort_by,
                sort_order=sort_order
            )
        except Exception as e:
            logger.error(f"Email words error: {e}", exc_info=True)
            flash("Error loading email words: internal error (see server logs)", "error")
            return render_template('email_words/email_words.html',
                                   email_words=[],
                                   total_words=0,
                                   page=1,
                                   total_pages=1,
                                   per_page=50,
                                   search_term='',
                                   domain='',
                                   domain_mode='exact',
                                   domains=[],
                                   sources={},
                                   sides={},
                                   source_id=None,
                                   side_id=None,
                                   sort_by='usage_count',
                                   sort_order='desc')
    
    # ==================== KEYWORD API ENDPOINTS ====================
    
    @app.route('/api/keywords', methods=['GET'])
    def api_keywords():
        """Enhanced API endpoint for keywords with advanced features"""
        try:
            req = read_page_request(request.args, request.headers)
            page, per_page = req.page, req.size
            query = request.args.get('q', '').strip()
            sort_by = request.args.get('sort', 'usage_count')
            sort_order = request.args.get('order', 'desc')
            status_filter = request.args.get('status', '')
            category_filter = request.args.get('category', '')
            
            # Validate sort parameters
            valid_sorts = ['usage_count', 'text', 'id', 'date_creation']
            if sort_by not in valid_sorts:
                sort_by = 'usage_count'
            if sort_order not in ['asc', 'desc']:
                sort_order = 'desc'
            
            # Build base query with joins for category info
            base_query = """
                SELECT k.id, k.keyword, k.category_id,
                       COALESCE(COUNT(DISTINCT p.id), 0) as usage_count,
                       c.word as category_name
                FROM keywords k
                LEFT JOIN keywords_hashs kp ON k.id = kp.keyword_id
                LEFT JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
                LEFT JOIN paths p ON p.context_id = hc.id
                LEFT JOIN categorys cat ON k.category_id = cat.id
                LEFT JOIN words c ON cat.word_id = c.id
            """
            
            # Build WHERE clause
            where_conditions = []
            params = []
            
            if status_filter == 'active':
                where_conditions.append("EXISTS (SELECT 1 FROM keywords_hashs kp2 WHERE kp2.keyword_id = k.id)")
            elif status_filter == 'unused':
                where_conditions.append("NOT EXISTS (SELECT 1 FROM keywords_hashs kp2 WHERE kp2.keyword_id = k.id)")
            
            if category_filter:
                where_conditions.append("k.category_id = %s")
                params.append(category_filter)
            
            where_clause = " WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            
            if query:
                # Bounded search (core/keyword_search.py): the keywords are
                # streamed and only the requested page is kept in memory. The
                # matches, their order and the total are unchanged.
                # Cross-language recall: the query also matches a keyword one
                # of whose words is the translation of what was typed.
                expansion = expand_for_search(query)
                needle_ids = list(expansion.word_ids) or None
                result = fetch_page(
                    lambda p, n: keyword_page(
                        execute_query, page=p, size=n, needle=query.lower(),
                        conditions=where_conditions, params=params,
                        rank=api_rank(sort_by, sort_order == 'desc'),
                        needle_ids=needle_ids),
                    req)
                page_rows = result.rows
                keywords_rows = page_rows  # read by the empty-result diagnostics below
                total_keywords = result.total
                page, per_page, total_pages = result.page, result.size, result.total_pages
                names = category_names(execute_query, [row[2] for row in page_rows])
                formatted_keywords = [
                    {
                        'id': kid,
                        'text': text,
                        'importance': 1.0,
                        'usage_count': usage,
                        'is_duplicate': False,
                        'category_id': cat,
                        'category_name': names.get(cat),
                        'date_creation': None,
                        'status': 'active' if usage > 0 else 'unused',
                    }
                    for kid, _blob, cat, text, usage in page_rows
                ]
            else:
                # No search - use efficient database pagination. The model decides
                # the page; this runs the count and one page of rows for it.
                sort_column_map = {
                    'usage_count': 'usage_count',
                    'text': 'k.id',
                    'id': 'k.id',
                    'date_creation': 'k.id'
                }
                sort_column = sort_column_map.get(sort_by, 'usage_count')
                order_clause = f"ORDER BY {sort_column} {sort_order.upper()}"
                if sort_column != 'k.id':
                    order_clause += ", k.id ASC"

                def _load_sql(p, n):
                    count_query = f"SELECT COUNT(DISTINCT k.id) FROM keywords k{where_clause}"
                    total_result = execute_query(count_query, tuple(params), fetch="one")
                    total = total_result[0] if total_result and isinstance(total_result, tuple) else (total_result if isinstance(total_result, int) else 0)
                    keywords_query = f"""
                        {base_query}
                        {where_clause}
                        GROUP BY k.id, k.keyword, k.category_id, c.word
                        {order_clause}
                        LIMIT %s OFFSET %s
                    """
                    rows = execute_query(keywords_query, tuple(params + [n, (p - 1) * n]))
                    return rows or [], total

                result = fetch_page(_load_sql, req)
                keywords_rows = result.rows
                total_keywords = result.total
                page, per_page, total_pages = result.page, result.size, result.total_pages
                
                if keywords_rows:
                    logger.info(f"Fetched {len(keywords_rows)} keyword rows")
                    if len(keywords_rows) > 0:
                        first_row = keywords_rows[0]
                        logger.info(f"First row structure: len={len(first_row)}, types={[type(x).__name__ for x in first_row]}, row[1] type={type(first_row[1]).__name__ if len(first_row) > 1 else 'N/A'}")
                        if len(first_row) > 1 and isinstance(first_row[1], (bytes, bytearray, memoryview)):
                            logger.info(f"Row[1] is bytes, length={len(first_row[1])}")
                        elif len(first_row) > 1:
                            logger.warning(f"Row[1] is NOT bytes, it's {type(first_row[1])}: {str(first_row[1])[:100]}")
                
                keyword_text_map = batch_load_keywords_from_rows(keywords_rows) if keywords_rows else {}
                formatted_keywords = []
                
                if keywords_rows and not keyword_text_map:
                    logger.warning(f"batch_load_keywords returned empty map for {len(keywords_rows)} rows. Falling back to individual loading.")
                    loaded_count = 0
                    for kw in keywords_rows:
                        try:
                            keyword_id = kw[0]
                            keyword_text = load_text_keyword(keyword_id)
                            if keyword_text:
                                keyword_text_map[keyword_id] = keyword_text
                                loaded_count += 1
                        except Exception as e:
                            logger.error(f"Error loading keyword {kw[0]} individually: {e}")
                    logger.info(f"Individual loading: {loaded_count}/{len(keywords_rows)} keywords loaded successfully")
                elif keyword_text_map:
                    logger.info(f"batch_load_keywords returned {len(keyword_text_map)} keyword texts")
                
                if keywords_rows and not keyword_text_map:
                    logger.error(f"⚠️ CRITICAL: All loading methods failed! Trying direct database query for first keyword...")
                    try:
                        test_id = keywords_rows[0][0]
                        result = execute_query("SELECT keyword FROM keywords WHERE id = %s", (test_id,), fetch="one", use_cache=False)
                        if result and result[0]:
                            keyword_bytes = result[0]
                            word_ids = unpack_int_list(keyword_bytes)
                            logger.info(f"Direct DB access works! Keyword {test_id} has {len(word_ids)} word IDs")
                        else:
                            logger.error(f"Direct DB query returned no data for keyword {test_id}")
                    except Exception as e:
                        logger.error(f"Direct DB access also failed: {e}", exc_info=True)
                
                for kw in keywords_rows or []:
                    try:
                        keyword_id = kw[0]
                        # kw[1] is now k.keyword (BYTEA), skip it - we get text from keyword_text_map
                        category_id = kw[2] if len(kw) > 2 else None
                        usage_count = kw[3] if len(kw) > 3 else 0
                        category_name = kw[4] if len(kw) > 4 else 'Uncategorized'
                        
                        keyword_text = keyword_text_map.get(keyword_id)
                        if not keyword_text:
                            logger.debug(f"Keyword {keyword_id} has no text in map. Trying individual load...")
                            try:
                                keyword_text = load_text_keyword(keyword_id)
                                if keyword_text:
                                    keyword_text_map[keyword_id] = keyword_text
                                else:
                                    logger.warning(f"Keyword {keyword_id} has no text even with individual load")
                                    continue
                            except Exception as e:
                                logger.error(f"Error loading keyword {keyword_id} individually: {e}")
                                continue
                        
                        formatted_keywords.append({
                            'id': keyword_id,
                            'text': keyword_text,
                            'importance': 1.0,
                            'usage_count': usage_count,
                            'is_duplicate': False,
                            'category_id': category_id,
                            'category_name': category_name,
                            'date_creation': None,
                            'status': 'active' if usage_count > 0 else 'unused'
                        })
                    except Exception as e:
                        logger.error(f"Error loading keyword {kw[0]}: {e}")
                        continue
                
                if sort_by == 'text' and formatted_keywords:
                    reverse_order = (sort_order == 'desc')
                    formatted_keywords.sort(key=lambda x: (x.get('text') or '').lower(), reverse=reverse_order)
                
                if sort_by == 'status' and formatted_keywords:
                    reverse_order = (sort_order == 'desc')
                    # Sort: active first if desc, unused first if asc
                    formatted_keywords.sort(key=lambda x: (x.get('status') == 'active', x.get('usage_count', 0)), reverse=reverse_order)
            
            logger.info(f"API Response: {len(formatted_keywords)} keywords, page {page}/{total_pages}, total {total_keywords}")
            if formatted_keywords:
                logger.info(f"First keyword sample: id={formatted_keywords[0].get('id')}, text={formatted_keywords[0].get('text', '')[:50]}")
            else:
                logger.warning(f"⚠️ No formatted keywords to return! keywords_rows had {len(keywords_rows) if keywords_rows else 0} rows")
                if keywords_rows and len(keywords_rows) > 0:
                    try:
                        test_id = keywords_rows[0][0]
                        test_text = load_text_keyword(test_id)
                        logger.info(f"Test load for keyword {test_id}: {'SUCCESS' if test_text else 'FAILED'}, text={test_text[:50] if test_text else 'None'}")
                    except Exception as e:
                        logger.error(f"Test load failed: {e}")
            
            return jsonify({
                'success': True,
                'keywords': formatted_keywords,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages,
                'total': total_keywords,
                'sort_by': sort_by,
                'sort_order': sort_order,
                'filters': {
                    'status': status_filter,
                    'category': category_filter
                }
            })
        except Exception as e:
            logger.error(f"API keywords error: {e}")
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/<int:keyword_id>', methods=['GET'])
    def get_keyword(keyword_id):
        """Get a specific keyword by ID with word IDs and category"""
        try:
            
            # Get keyword data including category_id and keyword bytes
            keyword_data = execute_query("""
                SELECT k.keyword, k.category_id
                FROM keywords k
                WHERE k.id = %s
            """, (keyword_id,), fetch="one")
            
            if not keyword_data:
                return jsonify({'success': False, 'error': 'Keyword not found'}), 404
            
            keyword_bytes = keyword_data[0] if isinstance(keyword_data, tuple) else keyword_data
            category_id = keyword_data[1] if isinstance(keyword_data, tuple) and len(keyword_data) > 1 else None
            
            # Unpickle to get word IDs
            try:
                word_ids = unpack_int_list(keyword_bytes) if keyword_bytes else []
            except Exception as e:
                logger.error(f"Error unpickling keyword {keyword_id}: {e}")
                word_ids = []
            
            # Get keyword text
            keyword_text = load_text_keyword(keyword_id)
            
            usage_count = execute_query("""
                SELECT COUNT(DISTINCT p.id)
                FROM keywords_hashs kp
                JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
                JOIN paths p ON p.context_id = hc.id
                WHERE kp.keyword_id = %s
            """, (keyword_id,), fetch="one") or 0
            
            return jsonify({
                'success': True,
                'keyword': {
                    'id': keyword_id,
                    'text': keyword_text,
                    'word_ids': word_ids,
                    'category_id': category_id,
                    'usage_count': usage_count
                }
            })
        except Exception as e:
            logger.error(f"Error getting keyword {keyword_id}: {e}")
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/<int:keyword_id>', methods=['PUT'])
    def update_keyword(keyword_id):
        """Update a keyword"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'JSON data is required'}), 400
            
            # Support both old format (text) and new format (word_ids)
            word_ids = data.get('word_ids')
            category_id = data.get('category_id')
            new_text = data.get('text', '').strip()
            
            # Remove stale matches only after the request has been validated
            # enough to represent a real update. The refresh below repopulates
            # only true matches.
            if not (word_ids and isinstance(word_ids, list) and len(word_ids) > 0) and not new_text and category_id is None:
                return jsonify({'success': False, 'error': 'Either word_ids or text is required'}), 400
            execute_query("DELETE FROM keywords_hashs WHERE keyword_id = %s", (keyword_id,), fetch=None)
            invalidate_query_cache()

            # If word_ids provided, use them directly
            if word_ids and isinstance(word_ids, list) and len(word_ids) > 0:
                keyword_blob = pack_int_list(word_ids)
                update_query = "UPDATE keywords SET keyword = %s"
                update_params = [keyword_blob]
                
                if category_id is not None:
                    update_query += ", category_id = %s"
                    update_params.append(category_id)
                
                update_query += " WHERE id = %s"
                update_params.append(keyword_id)
                
                execute_query(update_query, tuple(update_params), fetch=None)
            elif new_text:
                # Legacy format: convert text to word_ids
                words = new_text.strip().split()
                if not words:
                    return jsonify({'success': False, 'error': 'Keyword must contain at least one word'}), 400
                
                # Get or create word IDs using database facade
                from Api.utils import get_word_id, insert_word
                
                word_ids = []
                for word in words:
                    try:
                        word_id = get_word_id(word)
                        if not word_id:
                            word_id = insert_word(word)
                        if word_id:
                            word_ids.append(word_id)
                    except Exception as e:
                        logger.error(f"Error getting/creating word ID for '{word}': {e}")
                        continue
                
                if not word_ids:
                    return jsonify({'success': False, 'error': 'Failed to process words'}), 500
                
                keyword_blob = pack_int_list(word_ids)
                update_query = "UPDATE keywords SET keyword = %s"
                update_params = [keyword_blob]
                
                if category_id is not None:
                    update_query += ", category_id = %s"
                    update_params.append(category_id)
                
                update_query += " WHERE id = %s"
                update_params.append(keyword_id)
                
                execute_query(update_query, tuple(update_params), fetch=None)
            else:
                # Only category update
                if category_id is not None:
                    execute_query("""
                        UPDATE keywords SET category_id = %s WHERE id = %s
                    """, (category_id, keyword_id), fetch=None)
                else:
                    return jsonify({'success': False, 'error': 'Either word_ids or text is required'}), 400
            
            try:
                from database.services.contents_db_service import ContentDBService
                ContentDBService().refresh_keyword_associations([keyword_id])
            except Exception as refresh_error:
                logger.warning("Keyword updated but association refresh failed: %s", refresh_error)
            invalidate_query_cache()
            return jsonify({'success': True, 'message': 'Keyword updated successfully'})
        except Exception as e:
            logger.error(f"Error updating keyword {keyword_id}: {e}")
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/<int:keyword_id>', methods=['DELETE'])
    def delete_keyword_api(keyword_id):
        """Delete a keyword"""
        try:
            delete_keyword(keyword_id)
            return jsonify({'success': True, 'message': 'Keyword deleted successfully'})
        except Exception as e:
            logger.error(f"Error deleting keyword {keyword_id}: {e}")
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/<int:keyword_id>/delete', methods=['DELETE', 'POST'])
    def api_delete_keyword(keyword_id):
        """
        API endpoint to delete a keyword.
        
        This route handler wraps the database query function delete_keyword()
        from managers.database.queries.keywords for API access.
        
        Args:
            keyword_id: ID of the keyword to delete
            
        Returns:
            JSON response with success status (200) or error (404/500)
        """
        try:
            if not keyword_exists(keyword_id):
                return jsonify({'success': False, 'error': 'Keyword not found'}), 404
            
            db_delete_keyword(keyword_id)
            
            logger.info(f"✅ Deleted keyword {keyword_id}")
            return jsonify({'success': True, 'message': 'Keyword deleted successfully'})
        except Exception as e:
            logger.error(f"Error deleting keyword {keyword_id}: {e}")
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/update-associations', methods=['POST'])
    def update_keyword_associations():
        """Update keyword associations for all files in the database"""
        try:
            import json
            import gzip
            import zlib
            from database.processors.content_processor import ContentProcessor
            from database.operations import get_word_operations, get_keyword_operations
            from database.services.contents_db_service import ContentDBService
            
            # Initialize content processor for keyword extraction
            content_processor = ContentProcessor()
            word_ops = get_word_operations()
            keyword_ops = get_keyword_operations()
            
            # Get all keywords (keyword_id -> word_ids mapping)
            logger.info("Loading all keywords from managers.database...")
            keywords_query = "SELECT id, keyword FROM keywords"
            keywords_rows = execute_query(keywords_query)
            
            if not keywords_rows:
                return jsonify({
                    'success': False,
                    'error': 'No keywords found in database'
                }), 400
            
            # Build keywords dictionary (keyword_id -> list of word_ids)
            keywords_dict = {}
            for row in keywords_rows:
                keyword_id = row[0]
                keyword_bytes = row[1]
                if keyword_bytes:
                    try:
                        word_ids = unpack_int_list(keyword_bytes)
                        if word_ids and isinstance(word_ids, list) and len(word_ids) > 0:
                            keywords_dict[keyword_id] = word_ids
                    except Exception as e:
                        logger.warning(f"Failed to decode keyword {keyword_id}: {e}")
                        continue
            
            if not keywords_dict:
                return jsonify({
                    'success': False,
                    'error': 'No valid keywords found'
                }), 400
            
            logger.info(f"Loaded {len(keywords_dict)} keywords for matching")
            
            # Get all documents (canonical contents) that have extraction
            logger.info("Loading all extracted contents from managers.database...")
            contents_query = "SELECT DISTINCT hash_id FROM contents ORDER BY hash_id"
            contents_rows = execute_query(contents_query)
            
            if not contents_rows:
                return jsonify({
                    'success': False,
                    'error': 'No files with content found in database'
                }), 400
            
            total_files = len(contents_rows)
            logger.info(f"Found {total_files} files to process")
            
            # Process each path
            files_processed = 0
            new_associations = 0
            errors = 0
            
            def get_word_ids_from_content(hash_id):
                """Extract ordered word IDs from content table"""
                try:
                    # Get all content chunks for this document
                    content_query = "SELECT id, content_data FROM contents WHERE hash_id=%s ORDER BY id"
                    content_rows = execute_query(content_query, (hash_id,))
                    
                    if not content_rows:
                        return []
                    
                    word_ids_list = []
                    
                    for content_row in content_rows:
                        content_id, content_data = content_row
                        
                        if not content_data:
                            continue
                        
                        # Handle different data types
                        if isinstance(content_data, memoryview):
                            content_data = bytes(content_data)
                        elif not isinstance(content_data, bytes):
                            content_data = bytes(content_data)
                        
                        try:
                            # Content is stored as: JSON -> zlib compressed -> BYTEA
                            # Try to decompress
                            try:
                                packed = zlib.decompress(content_data)
                            except zlib.error:
                                # Try gzip as fallback
                                packed = gzip.decompress(content_data)
                            
                            # Deserialize JSON (not pickle!)
                            chunk_data = json.loads(packed.decode('utf-8'))
                            
                            # Handle different content formats
                            if isinstance(chunk_data, dict) and 'word_ids' in chunk_data:
                                # New format: dict with word_ids
                                word_ids_list.extend(chunk_data['word_ids'])
                            elif isinstance(chunk_data, list) and chunk_data:
                                if isinstance(chunk_data[0], (list, tuple)) and len(chunk_data[0]) >= 1:
                                    # Token format: [(word_id, punct_before_id, punct_after_id, spacing_id), ...]
                                    for token in chunk_data:
                                        if token and len(token) > 0 and token[0]:
                                            word_ids_list.append(token[0])
                                elif isinstance(chunk_data[0], int):
                                    # Simple list of word IDs
                                    word_ids_list.extend(chunk_data)
                        except Exception as e:
                            logger.debug(f"Could not process content chunk {content_id} for hash_id {hash_id}: {e}")
                            continue
                    
                    return word_ids_list
                    
                except Exception as e:
                    logger.error(f"Error extracting word IDs from content for hash_id {hash_id}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return []
            
            db_service = ContentDBService()
            for content_row in contents_rows:
                hash_id = content_row[0]
                try:
                    # Get ordered word IDs from content
                    word_ids_list = get_word_ids_from_content(hash_id)
                    
                    if not word_ids_list:
                        continue
                    
                    # Extract keywords using fast method
                    keyword_counts = content_processor.extract_keywords_fast(word_ids_list, keywords_dict)
                    
                    if keyword_counts:
                        # Update keywords_hashs using bulk insert.
                        # ``keyword_counts`` is already ``{keyword_id: count}``;
                        # the previous call handed it to
                        # process_keywords_for_content(), which expects the
                        # document's word-id SEQUENCE and re-matches it - it
                        # sliced the dict and failed every file with
                        # "unhashable type: 'slice'".
                        # The insert runs in its own try so one failing row
                        # logs the REAL cause (e.g. a DB error) and the loop
                        # keeps its per-file progress, instead of aborting
                        # into the generic outer handler with no detail.
                        try:
                            written = keyword_ops.insert_keyword_path_relationships(hash_id, keyword_counts)
                        except Exception as insert_err:
                            written = None
                            errors += 1
                            logger.warning(
                                f"Failed to insert keyword associations for hash_id {hash_id}: {insert_err}"
                            )

                        if written:
                            new_associations += written
                        elif written is not None:
                            errors += 1
                            logger.warning(f"Failed to insert keyword associations for hash_id {hash_id}")
                    
                    files_processed += 1
                    
                    # Log progress every 100 files
                    if files_processed % 100 == 0:
                        logger.info(f"Processed {files_processed}/{total_files} files... ({new_associations} associations found)")
                
                except Exception as e:
                    errors += 1
                    logger.error(f"Error processing hash_id {hash_id}: {e}", exc_info=True)
                    continue
            
            logger.info(f"✅ Completed keyword association update: {files_processed} files, {new_associations} new associations, {errors} errors")
            
            return jsonify({
                'success': True,
                'files_processed': files_processed,
                'total_files': total_files,
                'new_associations': new_associations,
                'keywords_checked': len(keywords_dict),
                'errors': errors
            })
            
        except Exception as e:
            logger.error(f"Error updating keyword associations: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': client_safe_message(e, subsystem='Api.routes.keywords')
            }), 500
    
    @app.route('/api/keywords/bulk-delete', methods=['POST'])
    def bulk_delete_keywords():
        """Bulk delete multiple keywords"""
        try:
            # ✅ FIXED: Validate CSRF token
            csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
            if not csrf_token:
                return jsonify({'success': False, 'error': 'CSRF token missing'}), 400
            
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'JSON data is required'}), 400
            
            keyword_ids = data.get('keyword_ids', [])
            if not keyword_ids or not isinstance(keyword_ids, list):
                return jsonify({'success': False, 'error': 'keyword_ids array is required'}), 400
            
            if len(keyword_ids) == 0:
                return jsonify({'success': False, 'error': 'At least one keyword ID is required'}), 400
            
            # Validate all IDs are integers
            try:
                keyword_ids = [int(kid) for kid in keyword_ids]
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'All keyword IDs must be integers'}), 400
            
            # Limit bulk operations to prevent DoS
            if len(keyword_ids) > 100:
                return jsonify({'success': False, 'error': 'Cannot delete more than 100 keywords at once'}), 400
            
            deleted_count = 0
            errors = []
            
            for keyword_id in keyword_ids:
                try:
                    if keyword_exists(keyword_id):
                        db_delete_keyword(keyword_id)
                        deleted_count += 1
                    else:
                        errors.append(f"Keyword {keyword_id} not found")
                except Exception as e:
                    logger.error(f"Error deleting keyword {keyword_id}: {e}")
                    errors.append(f"Keyword {keyword_id}: update failed (see server logs)")
            
            # Clear cache after bulk delete
            try:
                cache = get_query_cache()
                cache.clear()
            except Exception as e:
                logger.warning(f"Could not clear cache after bulk delete: {e}")
            
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'total_requested': len(keyword_ids),
                'errors': errors if errors else None
            })
        except Exception as e:
            logger.error(f"Error in bulk delete: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/merge-duplicates', methods=['POST'])
    def merge_duplicates():
        """Merge duplicate keywords with the same text"""
        try:
            # ✅ FIXED: Validate CSRF token
            csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
            if not csrf_token:
                return jsonify({'success': False, 'error': 'CSRF token missing'}), 400
            
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'JSON data is required'}), 400
            
            keyword_text = data.get('keyword_text', '').strip()
            if not keyword_text:
                return jsonify({'success': False, 'error': 'keyword_text is required'}), 400
            
            # ✅ FIXED: Input validation
            if len(keyword_text) > 500:
                return jsonify({'success': False, 'error': 'Keyword text cannot exceed 500 characters'}), 400
            
            # Check for dangerous patterns
            dangerous_patterns = ['<script', 'javascript:', 'onerror=', 'onload=']
            if any(pattern in keyword_text.lower() for pattern in dangerous_patterns):
                return jsonify({'success': False, 'error': 'Invalid characters in keyword text'}), 400
            
            from Api.utils import get_word_id
            
            # Convert keyword text to word IDs for matching
            words = keyword_text.split()
            if len(words) < 2:
                return jsonify({'success': False, 'error': 'Keyword must contain at least 2 words'}), 400
            
            word_ids = []
            for word in words:
                word_id = get_word_id(word.lower())
                if not word_id:
                    return jsonify({
                        'success': False,
                        'error': f'Word "{word}" not found in database'
                    }), 400
                word_ids.append(word_id)
            
            keyword_blob = pack_int_list(word_ids)
            
            # Find all keywords with the same text (case-insensitive)
            keywords_query = """
                SELECT id, category_id
                FROM keywords
                WHERE keyword = %s
                ORDER BY id ASC
            """
            duplicate_rows = execute_query(keywords_query, (keyword_blob,))
            
            if not duplicate_rows or len(duplicate_rows) < 2:
                return jsonify({
                    'success': True,
                    'merged_count': 0,
                    'message': 'No duplicates found'
                })
            
            # Keep the first keyword (lowest ID), merge others into it
            keep_keyword_id = duplicate_rows[0][0]
            keep_category_id = duplicate_rows[0][1]
            duplicate_ids = [row[0] for row in duplicate_rows[1:]]
            
            # Get total usage count before merging
            total_usage_query = """
                SELECT COUNT(*) FROM keywords_hashs
                WHERE keyword_id IN %s
            """
            all_ids = tuple([keep_keyword_id] + duplicate_ids)
            usage_result = execute_query(
                total_usage_query,
                (all_ids,),
                fetch="one"
            )
            total_usage = usage_result[0] if usage_result else 0
            
            # Transfer all keyword_paths associations to the kept keyword
            for dup_id in duplicate_ids:
                # Update associations
                execute_query("""
                    UPDATE keywords_hashs
                    SET keyword_id = %s
                    WHERE keyword_id = %s
                    AND NOT EXISTS (
                        SELECT 1 FROM keywords_hashs kp2
                        WHERE kp2.hash_id = keywords_hashs.hash_id
                        AND kp2.keyword_id = %s
                    )
                """, (keep_keyword_id, dup_id, keep_keyword_id), fetch=None)
                
                # Delete remaining associations (duplicates)
                execute_query(
                    "DELETE FROM keywords_hashs WHERE keyword_id = %s",
                    (dup_id,),
                    fetch=None
                )
                
                # Delete the duplicate keyword
                db_delete_keyword(dup_id)
            
            # Clear cache
            try:
                cache = get_query_cache()
                cache.clear()
            except Exception as e:
                logger.warning(f"Could not clear cache after merge: {e}")
            
            merged_count = len(duplicate_ids)
            logger.info(f"✅ Merged {merged_count} duplicate keywords into keyword {keep_keyword_id}")
            
            return jsonify({
                'success': True,
                'merged_count': merged_count,
                'total_usage': total_usage,
                'kept_keyword_id': keep_keyword_id
            })
        except Exception as e:
            logger.error(f"Error merging duplicates: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/merge-all-duplicates', methods=['POST'])
    def merge_all_duplicates():
        """Merge all duplicate keywords in the database"""
        try:
            # ✅ FIXED: Validate CSRF token
            csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
            if not csrf_token:
                return jsonify({'success': False, 'error': 'CSRF token missing'}), 400
            
            from collections import defaultdict
            
            # Load all keywords
            keywords_query = "SELECT id, keyword FROM keywords ORDER BY id ASC"
            all_keywords = execute_query(keywords_query)
            
            if not all_keywords:
                return jsonify({
                    'success': True,
                    'merged': 0,
                    'duplicate_sets': 0,
                    'message': 'No keywords found'
                })
            
            # Group keywords by their text (unpickled)
            keyword_groups = defaultdict(list)
            keyword_text_map = batch_load_keywords_from_rows(all_keywords)
            
            for row in all_keywords:
                keyword_id = row[0]
                keyword_text = keyword_text_map.get(keyword_id)
                if keyword_text:
                    # Normalize text for grouping (lowercase, trimmed)
                    normalized_text = keyword_text.lower().strip()
                    keyword_groups[normalized_text].append({
                        'id': keyword_id,
                        'text': keyword_text
                    })
            
            # Find duplicate groups (groups with more than 1 keyword)
            duplicate_groups = {
                text: keywords
                for text, keywords in keyword_groups.items()
                if len(keywords) > 1
            }
            
            if not duplicate_groups:
                return jsonify({
                    'success': True,
                    'merged': 0,
                    'duplicate_sets': 0,
                    'message': 'No duplicates found'
                })
            
            total_merged = 0
            duplicate_sets = len(duplicate_groups)
            
            # Merge each duplicate group
            for normalized_text, keywords in duplicate_groups.items():
                # Sort by ID to keep the first one
                keywords.sort(key=lambda k: k['id'])
                keep_keyword = keywords[0]
                duplicates = keywords[1:]
                
                keep_id = keep_keyword['id']
                duplicate_ids = [k['id'] for k in duplicates]
                
                # Transfer associations
                for dup_id in duplicate_ids:
                    # Update associations (avoid duplicates)
                    execute_query("""
                        UPDATE keywords_hashs
                        SET keyword_id = %s
                        WHERE keyword_id = %s
                        AND NOT EXISTS (
                            SELECT 1 FROM keywords_hashs kp2
                            WHERE kp2.hash_id = keywords_hashs.hash_id
                            AND kp2.keyword_id = %s
                        )
                    """, (keep_id, dup_id, keep_id), fetch=None)
                    
                    # Delete remaining duplicate associations
                    execute_query(
                        "DELETE FROM keywords_hashs WHERE keyword_id = %s",
                        (dup_id,),
                        fetch=None
                    )
                    
                    # Delete duplicate keyword
                    db_delete_keyword(dup_id)
                    total_merged += 1
                
                logger.info(f"✅ Merged {len(duplicates)} duplicates of '{keep_keyword['text']}' into keyword {keep_id}")
            
            # Clear cache
            try:
                cache = get_query_cache()
                cache.clear()
            except Exception as e:
                logger.warning(f"Could not clear cache after merge-all: {e}")
            
            logger.info(f"✅ Merged all duplicates: {total_merged} keywords merged, {duplicate_sets} duplicate sets")
            
            return jsonify({
                'success': True,
                'merged_count': total_merged,
                'duplicate_sets': duplicate_sets
            })
        except Exception as e:
            logger.error(f"Error merging all duplicates: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/keywords/export', methods=['GET'])
    def export_keywords():
        """Export keywords to CSV"""
        try:
            import csv
            from flask import Response
            from io import StringIO
            
            keyword_ids = request.args.getlist('ids', type=int)
            
            # Build query
            if keyword_ids and len(keyword_ids) > 0:
                placeholders = ','.join(['%s'] * len(keyword_ids))
                query = f"SELECT id, keyword FROM keywords WHERE id IN ({placeholders}) ORDER BY id"
                rows = execute_query(query, tuple(keyword_ids))
            else:
                query = "SELECT id, keyword FROM keywords ORDER BY id"
                rows = execute_query(query)
            
            if not rows:
                return jsonify({'success': False, 'error': 'No keywords found'}), 404
            
            # Load keyword texts
            keyword_text_map = batch_load_keywords_from_rows(rows)
            
            # Generate CSV
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(['ID', 'Keyword', 'Usage Count'])
            
            for row in rows:
                keyword_id = row[0]
                keyword_text = keyword_text_map.get(keyword_id, '')
                
                # Get usage count
                usage_result = execute_query(
                    "SELECT COUNT(*) FROM keywords_hashs WHERE keyword_id = %s",
                    (keyword_id,),
                    fetch="one"
                )
                usage_count = usage_result[0] if usage_result else 0
                
                writer.writerow([keyword_id, keyword_text, usage_count])
            
            from core.security.disclosure import note_disclosure
            note_disclosure(kind='keywords_export', scope='selection' if keyword_ids else 'all',
                            unit='keyword', row_count=len(rows), format='csv')
            csv_output = output.getvalue()
            output.close()
            
            return Response(
                csv_output,
                mimetype='text/csv',
                headers={
                    'Content-Disposition': 'attachment; filename=keywords_export.csv',
                    'Content-Type': 'text/csv; charset=utf-8'
                }
            )
        except Exception as e:
            logger.error(f"Error exporting keywords: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/email-words/all')
    def api_email_words_all():
        """API endpoint to get ALL email words for export/copy operations (supports filters)."""
        try:
            search_term = (request.args.get('q') or '').strip()
            domain = (request.args.get('domain') or '').strip()
            domain_mode = (request.args.get('domain_mode') or 'exact').strip().lower()
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)

            email_words = get_email_words_all(
                search_term=search_term if search_term else None,
                domain=domain if domain else None,
                domain_mode=domain_mode,
                source_filter=source_id,
                side_filter=side_id,
            )
            
            result = []
            if email_words:
                for row in email_words:
                    # get_email_words_all returns (email, usage_count)
                    result.append({'email': row[0], 'usage_count': row[1]})
            
            return jsonify({
                'success': True,
                'total': len(result),
                'emails': result,
                'filters': {
                    'q': search_term,
                    'domain': domain,
                    'domain_mode': domain_mode,
                    'source_id': source_id,
                    'side_id': side_id,
                },
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            logger.error(f"Error fetching all email words: {e}")
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
    
    @app.route('/api/email-words/files')
    def api_email_words_files():
        """API endpoint to get files containing a specific email address."""
        try:
            email = (request.args.get('email') or '').strip()
            limit = min(request.args.get('limit', 100, type=int), 500)
            
            if not email:
                return jsonify({'success': False, 'error': 'Email parameter required'}), 400
            
            # Get word ID for this email
            from Api.utils import execute_query
            word_result = execute_query(
                "SELECT id FROM words WHERE word = %s",
                (email,),
                fetch="one"
            )
            
            if not word_result:
                return jsonify({
                    'success': True,
                    'email': email,
                    'files': [],
                    'total': 0
                })
            
            word_id = word_result[0]
            
            # Get files containing this email
            files_query = """
                SELECT DISTINCT 
                    p.id,
                    p.file_name,
                    p.file_path,
                    p.file_type,
                    p.file_size,
                    p.file_date,
                    p.file_status,
                    p.date_creation,
                    wp.word_count,
                    s.name as source_name,
                    si.name as side_name
                FROM paths p
                JOIN hash_contexts hc ON hc.id = p.context_id
                JOIN words_hashs wp ON wp.hash_id = hc.hash_id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
                WHERE wp.word_id = %s
                ORDER BY wp.word_count DESC, p.file_name ASC
                LIMIT %s
            """
            
            files_result = execute_query(
                files_query,
                (word_id, limit),
                fetch="all"
            )
            
            # Get total count
            count_result = execute_query(
                "SELECT COUNT(DISTINCT p.id) FROM paths p JOIN hash_contexts hc ON hc.id = p.context_id JOIN words_hashs wp ON wp.hash_id = hc.hash_id WHERE wp.word_id = %s",
                (word_id,),
                fetch="one"
            )
            total = count_result[0] if count_result else 0
            
            files = []
            if files_result:
                for row in files_result:
                    files.append({
                        'id': row[0],
                        'name': row[1],
                        'path': row[2],
                        'type': row[3] or 'unknown',
                        'size': row[4] or 0,
                        'date': row[5].isoformat() if row[5] else None,
                        'status': row[6] or 'Unknown',
                        'created': row[7].isoformat() if row[7] else None,
                        'word_count': row[8] or 0,
                        'source': row[9],
                        'side': row[10]
                    })
            
            return jsonify({
                'success': True,
                'email': email,
                'files': files,
                'total': total,
                'showing': len(files)
            })
        except Exception as e:
            logger.error(f"Error fetching files for email '{email}': {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.keywords', success_key='success', status=500)
