"""
Words routes
"""

from flask import render_template, request, redirect, url_for, flash, jsonify

from core.validation import PayloadError, text_field
from Api.utils import (
    select_info_categories, get_query_cache, get_words_with_usage,
    word_exists, get_word_id, create_word, get_word_detail, update_word_query,
    delete_word, get_words_usage_by_ids, bulk_delete_words
)
from datetime import datetime
from core.table_paging import fetch_page, read_page_request
import logging
from core.errors import client_error

logger = logging.getLogger(__name__)


def register_words_routes(app):
    """Register words routes with the Flask app"""
    
    @app.route('/words')
    def words_list():
        """Words Management with server-side pagination and sorting"""
        try:
            req = read_page_request(request.args, request.headers)
            search = request.args.get('search', '').strip()
            # Column sort comes from the table headers: `sort` + `order` in
            # the query string. `get_words_with_usage` validates the column
            # against its allowlist and falls back to usage count.
            sort_by = request.args.get('sort', 'usage_count')
            sort_order = request.args.get('order', 'desc')
            
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'
            
            if len(search) > 500:
                search = search[:500]
            
            result = fetch_page(
                lambda page, size: get_words_with_usage(
                    search_term=search if search else None,
                    page=page,
                    per_page=size,
                    sort_by=sort_by,
                    sort_order=sort_order
                ),
                req,
            )
            words, total_words = result.rows, result.total
            page, per_page, total_pages = result.page, result.size, result.total_pages
            
            return render_template('Word/Word_list.html',
                                   words=words,
                                   page=page,
                                   per_page=per_page,
                                   total_pages=total_pages,
                                   total_words=total_words,
                                   search=search,
                                   sort_by=sort_by,
                                   sort_order=sort_order)
        except Exception as e:
            logger.error(f"Error loading words: {e}")
            flash(f"Error loading words: {e}", "error")
            return render_template('Word/Word_list.html', words=[], page=1, per_page=10, total_pages=1, total_words=0, search='', sort_by='usage_count', sort_order='desc')
    
    @app.route('/words/add', methods=['GET', 'POST'])
    def words_add():
        """Add new words"""
        if request.method == 'POST':
            try:
                data = request.get_json(silent=True) if request.is_json else request.form
                
                if not data:
                    return jsonify({'success': False, 'error': 'Data is required'}), 400
                
                try:
                    word_text = text_field(data, 'word', required=True,
                                           max_length=255, trim=True)
                except PayloadError as exc:
                    return jsonify({'success': False, **exc.to_dict()}), 400
                
                existing = word_exists(word_text)
                if existing:
                    return jsonify({'success': False, 'error': 'Word already exists'}), 400
                
                # Create new word
                word_id = create_word(word_text)
                
                if word_id:
                    # Invalidate cache
                    try:
                        cache = get_query_cache()
                        cache.clear()
                    except Exception:
                        pass
                    
                    logger.info(f"Successfully added word: {word_text} (id: {word_id})")
                    return jsonify({'success': True, 'id': word_id, 'message': 'Word added successfully'}), 201
                else:
                    return jsonify({'success': False, 'error': 'Failed to add word'}), 500
            except Exception as e:
                logger.error(f"Error adding word: {e}")
                return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)
        
        # AUDIT (UI-01): 'Word/Word_add.html' does not exist on disk, so this
        # page rendered a 500 error page for every visitor (and is linked from
        # the Words page). The Words list already ships a complete
        # "Add Word" modal, so hand off to it instead of inventing a new page.
        return redirect(url_for('words_list', add=1))
    
    @app.route('/words/<int:word_id>')
    def word_detail(word_id):
        """View word details"""
        try:
            word_data = get_word_detail(word_id)
            
            if not word_data:
                flash('Word not found', 'error')
                return redirect(url_for('words_list'))
            
            return render_template('Word/Word_detail.html', word=word_data)
        except Exception as e:
            logger.error(f"Error loading word {word_id}: {e}")
            flash('Error loading word', 'error')
            return redirect(url_for('words_list'))
    
    # ==================== WORD API ENDPOINTS ====================
    
    @app.route('/api/words', methods=['GET'])
    def api_words():
        """API endpoint for words with pagination, search, and sorting"""
        try:
            page = request.args.get('page', 1, type=int)
            per_page = min(request.args.get('per_page', 10, type=int), 100)
            query = request.args.get('q', '').strip()
            sort_by = request.args.get('sort', 'usage_count')
            sort_order = request.args.get('order', 'desc')
            status_filter = request.args.get('status', '')
            
            # Validate sort parameters ('status' is a derived column the
            # shared loader maps to its usage expression)
            valid_sorts = ['usage_count', 'word', 'id', 'status']
            if sort_by not in valid_sorts:
                sort_by = 'usage_count'
            if sort_order not in ['asc', 'desc']:
                sort_order = 'desc'
            
            words, total_words = get_words_with_usage(
                search_term=query if query else None,
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order,
                status_filter=status_filter
            )
            
            total_pages = (total_words + per_page - 1) // per_page if total_words > 0 else 1
            
            return jsonify({
                'success': True,
                'words': words,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages,
                'total': total_words,
                'sort_by': sort_by,
                'sort_order': sort_order,
                'filters': {
                    'status': status_filter
                }
            })
        except Exception as e:
            logger.error(f"API words error: {e}")
            return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)
    
    @app.route('/api/words', methods=['POST'])
    def api_words_create():
        """Create a new word or return existing word ID if it already exists"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'JSON data is required'}), 400
            
            try:
                word_text = text_field(data, 'word', required=True,
                                       max_length=255, trim=True)
            except PayloadError as exc:
                return jsonify({'success': False, **exc.to_dict()}), 400
            
            # Check if word already exists
            if word_exists(word_text):
                # "Create or get": answer with the id of the row that is
                # already there. ``word_exists`` answers True/False, and this
                # used to return that boolean as ``id`` - clients doing
                # ``data.id`` got ``true``.
                existing_id = get_word_id(word_text)
                logger.info(f"Word already exists: {word_text} (id: {existing_id})")
                return jsonify({
                    'success': True,
                    'id': existing_id,
                    'message': 'Word already exists',
                    'already_exists': True
                }), 200
            
            # Create new word
            word_id = create_word(word_text)
            
            if word_id:
                # Invalidate cache
                try:
                    cache = get_query_cache()
                    cache.clear()
                except Exception:
                    pass
                
                logger.info(f"Successfully created word: {word_text} (id: {word_id})")
                return jsonify({'success': True, 'id': word_id, 'message': 'Word created successfully'}), 201
            else:
                return jsonify({'success': False, 'error': 'Failed to create word'}), 500
        except Exception as e:
            logger.error(f"Error creating word: {e}")
            return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)
    
    @app.route('/api/words/<int:word_id>', methods=['GET'])
    def get_word(word_id):
        """Get a specific word by ID"""
        try:
            word_data = get_word_detail(word_id)
            
            if not word_data:
                return jsonify({'success': False, 'error': 'Word not found'}), 404
            
            return jsonify({
                'success': True,
                'word': word_data
            })
        except Exception as e:
            logger.error(f"Error getting word {word_id}: {e}")
            return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)
    
    @app.route('/api/words/<int:word_id>', methods=['PUT'])
    def update_word_api(word_id):
        """Update a word"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'JSON data is required'}), 400
            
            try:
                word_text = text_field(data, 'word', required=True,
                                       max_length=255, trim=True)
            except PayloadError as exc:
                return jsonify({'success': False, **exc.to_dict()}), 400
            
            existing = word_exists(word_text)
            if existing and existing != word_id:
                return jsonify({'success': False, 'error': 'Word already exists'}), 400
            
            # Check if word exists
            word_data = get_word_detail(word_id)
            if not word_data:
                return jsonify({'success': False, 'error': 'Word not found'}), 404
            
            # Update word - use imported function from queries module
            update_word_query(word_id, word_text)
            
            # Invalidate cache
            try:
                cache = get_query_cache()
                cache.clear()
            except Exception:
                pass
            
            logger.info(f"Successfully updated word {word_id}: {word_text}")
            return jsonify({'success': True, 'message': 'Word updated successfully'})
        except Exception as e:
            logger.error(f"Error updating word {word_id}: {e}")
            return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)
    
    @app.route('/api/words/<int:word_id>', methods=['DELETE'])
    def delete_word_api(word_id):
        """Delete a word"""
        try:
            word_data = get_word_detail(word_id)
            if not word_data:
                return jsonify({'success': False, 'error': 'Word not found'}), 404
            
            # Check if word is being used
            usage_count = word_data.get('usage_count', 0)
            if usage_count > 0:
                return jsonify({
                    'success': False,
                    'error': f'Cannot delete word: it is being used by {usage_count} file(s). Please remove associations first.'
                }), 400
            
            # Delete the word
            delete_word(word_id)
            
            # Invalidate cache
            try:
                cache = get_query_cache()
                cache.clear()
            except Exception:
                pass
            
            logger.info(f"Successfully deleted word: {word_id}")
            return jsonify({'success': True, 'message': 'Word deleted successfully'})
        except Exception as e:
            logger.error(f"Error deleting word {word_id}: {e}")
            return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)
    
    @app.route('/api/words/bulk-delete', methods=['POST'])
    def api_words_bulk_delete():
        """Bulk delete words"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'JSON data is required'}), 400
            
            word_ids = data.get('word_ids', [])
            if not word_ids or not isinstance(word_ids, list):
                return jsonify({'success': False, 'error': 'word_ids array is required'}), 400
            
            used_words = get_words_usage_by_ids(word_ids)
            # ``get_words_usage_by_ids`` returns id -> usage_count (0 = unused);
            # a word is only "in use" when its count is > 0.
            deletable_ids = [wid for wid in word_ids if not used_words.get(wid)]
            
            if not deletable_ids:
                return jsonify({
                    'success': False,
                    'error': 'All selected words are in use and cannot be deleted'
                }), 400
            
            # Delete words
            bulk_delete_words(deletable_ids)
            
            # Invalidate cache
            try:
                cache = get_query_cache()
                cache.clear()
            except Exception:
                pass
            
            deleted_count = len(deletable_ids)
            skipped_count = len(word_ids) - deleted_count
            
            logger.info(f"Bulk deleted {deleted_count} words, skipped {skipped_count} in-use words")
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'skipped_count': skipped_count,
                'message': f'Successfully deleted {deleted_count} word(s)'
            })
        except Exception as e:
            logger.error(f"Error bulk deleting words: {e}")
            return client_error(e, subsystem='Api.routes.words', success_key='success', status=500)

