"""
Categories routes
"""

from flask import redirect, url_for, request, jsonify, render_template, flash
from werkzeug.datastructures import MultiDict
from Api.blueprints.files import _documents_panel_response
from Api.utils import (
    execute_query, get_categories_with_stats, get_categories_paged, get_category,
    get_words_by_category, get_words_by_category_paged, get_word_id, insert_word, insert_category,
    list_categories, invalidate_query_cache
)
import logging
from core.errors import client_error, client_safe_message
from core.table_paging import DEFAULT_PAGE_SIZE, fetch_page, read_page_request

logger = logging.getLogger(__name__)

def _parent_names_for(categories):
    """{parent_id: name} for the parents of ``categories`` that are missing.

    Only the ids that are *not* in the page are looked up: a parent that is
    already rendered does not need a second query, and the template falls back
    to the row it has.
    """
    try:
        have = {row['id'] for row in categories if row.get('id') is not None}
        wanted = {row['parent_id'] for row in categories
                  if row.get('parent_id') is not None} - have
        if not wanted:
            return {}
        rows = execute_query(
            "SELECT c.id, w.word FROM categorys c JOIN words w ON c.word_id = w.id "
            "WHERE c.id = ANY(%s)", (list(wanted),), fetch="all")
        return {row[0]: row[1] for row in rows or []}
    except Exception:  # the page must render even if this nicety fails
        logger.warning("could not resolve parent names for the category list",
                       exc_info=True)
        return {}

def register_categories_routes(app):
    """Register category routes with the Flask app"""

    @app.route('/categories')
    def categories_list():
        """Category Management Page — server-side search, sort and pagination"""
        try:
            req = read_page_request(request.args, request.headers)
            search = request.args.get('search', '').strip()
            sort_by = request.args.get('sort', 'files')
            sort_order = request.args.get('order', 'desc')

            if sort_by not in ('name', 'files', 'words', 'id'):
                sort_by = 'files'
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'
            if len(search) > 500:
                search = search[:500]

            result = fetch_page(
                lambda page, size: get_categories_paged(
                    search=search if search else None,
                    page=page,
                    per_page=size,
                    sort_by=sort_by,
                    sort_order=sort_order
                ),
                req,
            )
            categories, total_categories = result.rows, result.total
            page, per_page, total_pages = result.page, result.size, result.total_pages

            # The Parent cell links to a search for the parent's *name*, and a
            # parent is often on another page (a subcategory shown by a search,
            # or simply page 2). Looking the names up for the rows on this page
            # is one query and stops the cell from rendering ``?search=`` with
            # nothing after it.
            parent_names = _parent_names_for(categories)

            # Names in the reader's language, in one query (services/i18n).
            # With no term_names rows for these words every label comes back
            # as ``canonical`` and the page renders exactly as before.
            try:
                from Api.utils import execute_query as _run
                from services.i18n.term_labels import labels_for_categories
                from Api.routes.i18n_terms import resolve_label_locale
                term_labels = labels_for_categories(
                    _run, [row['id'] for row in categories], resolve_label_locale())
            except Exception as _exc:  # noqa: BLE001 - labels are not critical
                logger.warning("category labels unavailable: %s: %s",
                               type(_exc).__name__, _exc)
                term_labels = {}

            return render_template('Category/categories_list.html',
                                 categories=categories,
                                 parent_names=parent_names,
                                 term_labels=term_labels,
                                 page=page,
                                 per_page=per_page,
                                 total_pages=total_pages,
                                 total_categories=total_categories,
                                 search=search,
                                 sort_by=sort_by,
                                 sort_order=sort_order)
        except Exception as e:
            logger.error(f"Error loading categories list: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return render_template('Category/categories_list.html',
                                 categories=[],
                                 parent_names={},
                                 page=1,
                                 per_page=DEFAULT_PAGE_SIZE,
                                 total_pages=1,
                                 total_categories=0,
                                 search='',
                                 sort_by='files',
                                 sort_order='desc')





    @app.route('/categories/<int:category_id>/names')
    def category_names(category_id):
        """Names of one category's word in every supported language.

        Read-only page plus a modal that writes through /api/term-names; the
        page itself exists so a curator can see coverage at a glance and import
        a glossary CSV without a command line.
        """
        from services.i18n.term_names import TermNameError, list_names
        from settings.languages import SUPPORTED_LANGUAGES

        category = get_category(category_id)
        if not category:
            return render_template('404.html'), 404
        try:
            names = list_names(execute_query, category_id=category_id)
        except TermNameError:
            names = []
        from Api.routes.i18n_terms import resolve_label_locale
        return render_template(
            'Category/category_names.html',
            category=category,
            names=names,
            # ``languages`` is the base template's dict; a list here
            # shadowed it and every page rendering base.html raised
            # 'list object' has no attribute 'items'.
            name_languages=SUPPORTED_LANGUAGES,
            interface_language=resolve_label_locale() or 'en')

    @app.route('/categories/<int:category_id>/documents')
    def category_documents(category_id):
        """One category's documents, as the unified-table fragment (panel).

        The Categories page embeds this in its side panel: the File
        Library's own list, pinned to the documents whose content carries a
        word assigned to this category.
        """
        return _documents_panel_response(
            'categoryFilesTable',
            "EXISTS (SELECT 1 FROM words_hashs wp "
            "JOIN words_categorys wc ON wp.word_id = wc.word_id "
            "WHERE wp.hash_id = hc.hash_id AND wc.category_id = %s)",
            (category_id,),
            request.args,
            {'category_id': category_id},
        )


    @app.route('/categories/<int:category_id>/words')
    def category_words(category_id):
        """View words in a specific category — server-side search, sort, pagination"""
        try:
            category = get_category(category_id)

            if not category:
                flash('Category not found', 'error')
                return redirect(url_for('categories_list'))

            req = read_page_request(request.args, request.headers)
            search = request.args.get('search', '').strip()
            sort_by = request.args.get('sort', 'word')
            sort_order = request.args.get('order', 'asc')

            if sort_by not in ('word', 'usage_count', 'id'):
                sort_by = 'word'
            if sort_order not in ('asc', 'desc'):
                sort_order = 'asc'
            if len(search) > 500:
                search = search[:500]

            result = fetch_page(
                lambda page, size: get_words_by_category_paged(
                    category_id,
                    search=search if search else None,
                    page=page,
                    per_page=size,
                    sort_by=sort_by,
                    sort_order=sort_order
                ),
                req,
            )
            words, total_words = result.rows, result.total
            page, per_page, total_pages = result.page, result.size, result.total_pages

            return render_template('Category/category_words.html',
                                 category=category,
                                 words=words,
                                 page=page,
                                 per_page=per_page,
                                 total_pages=total_pages,
                                 total_words=total_words,
                                 search=search,
                                 sort_by=sort_by,
                                 sort_order=sort_order)
        except Exception as e:
            logger.error(f"Error loading category words: {e}")
            import traceback
            logger.error(traceback.format_exc())
            flash('Error loading category words', 'error')
            return redirect(url_for('categories_list'))
    
    @app.route('/api/category/check', methods=['POST'])
    def category_check():
        """Check if a category name already exists"""
        try:
            data = request.get_json(silent=True) or request.form.to_dict()
            category_name = data.get('category_name', '').strip()
            
            if not category_name:
                return jsonify({'exists': False, 'error': 'Category name is required'}), 400
            
            # Check for duplicate by name (case-insensitive)
            existing_category = execute_query("""
                SELECT c.id, w.word
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                WHERE LOWER(w.word) = LOWER(%s)
                LIMIT 1
            """, (category_name,), fetch="one")
            
            if existing_category:
                return jsonify({
                    'exists': True,
                    'category_id': existing_category[0],
                    'category_name': existing_category[1],
                    'message': f'Category "{existing_category[1]}" already exists'
                })
            
            return jsonify({'exists': False})
            
        except Exception as e:
            logger.error(f"Error checking category: {e}")
            return jsonify({'exists': False, 'error': client_safe_message(e, subsystem='Api.routes.categories')}), 500
    
    @app.route('/category/add', methods=['POST'])
    def category_add():
        """Add a new category to the categories table"""
        try:
            data = request.get_json(silent=True) or request.form.to_dict()
            category_name = data.get('category_name', '').strip()
            
            if not category_name:
                return jsonify({'success': False, 'error': 'Category name is required'}), 400
            
            # Check for duplicate by name (case-insensitive)
            existing_category = execute_query("""
                SELECT c.id, w.word
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                WHERE LOWER(w.word) = LOWER(%s)
                LIMIT 1
            """, (category_name,), fetch="one")
            
            if existing_category:
                return jsonify({
                    'success': False, 
                    'error': f'Category "{existing_category[1]}" already exists (ID: {existing_category[0]})',
                    'existing_id': existing_category[0],
                    'existing_name': existing_category[1]
                }), 400
            
            # Get or create word_id
            word_id = get_word_id(category_name)
            if not word_id:
                word_id = insert_word(category_name)
                if not word_id:
                    return jsonify({'success': False, 'error': 'Failed to create word'}), 500
            
            # Insert category (word_id must be unique)
            category_id = insert_category(word_id)
            
            if category_id:
                invalidate_query_cache()
                logger.info(f"Created category: {category_name} (ID: {category_id})")
                return jsonify({'success': True, 'category_id': category_id, 'message': 'Category added successfully'})
            else:
                return jsonify({'success': False, 'error': 'Category already exists or failed to create'}), 400
                
        except Exception as e:
            logger.error(f"Error adding category: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Check if it's a unique constraint violation
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                return jsonify({'success': False, 'error': 'A category with this name already exists'}), 400
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/<int:category_id>', methods=['GET'])
    def api_get_category(category_id):
        """Get a single category by ID"""
        try:
            category = get_category(category_id)
            if category:
                return jsonify({'success': True, 'id': category['id'], 'name': category['name']})
            else:
                return jsonify({'success': False, 'error': 'Category not found'}), 404
        except Exception as e:
            logger.error(f"Error getting category: {e}")
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/all-words')
    def api_all_categories_words():
        """Get all categories with their words in a single request"""
        try:
            categories = list_categories(limit=1000)
            
            result = []
            for category in categories:
                words = get_words_by_category(category['id'], limit=1000)
                result.append({
                    'category': category,
                    'words': words
                })
            
            return jsonify({'success': True, 'data': result})
        except Exception as e:
            logger.error(f"Error getting all categories words: {e}")
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/<int:category_id>/words')
    def api_category_words(category_id):
        """Get words in a category"""
        try:
            words = get_words_by_category(category_id, limit=1000)
            return jsonify({'success': True, 'words': words})
        except Exception as e:
            logger.error(f"Error getting category words: {e}")
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/<int:category_id>/words/<int:word_id>', methods=['DELETE'])
    def api_remove_word_from_category(category_id, word_id):
        """Remove a word from a category"""
        try:
            result = execute_query("""
                DELETE FROM words_categorys 
                WHERE word_id = %s AND category_id = %s
                RETURNING word_id, category_id
            """, (word_id, category_id), fetch="one")
            
            if result:
                invalidate_query_cache()
                logger.info(f"Removed word {word_id} from category {category_id}")
                return jsonify({'success': True, 'message': 'Word removed from category successfully'})
            else:
                return jsonify({'success': False, 'error': 'Word-category relationship not found'}), 404
        except Exception as e:
            logger.error(f"Error removing word from category: {e}")
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/words-categorys/add', methods=['POST'])
    def words_categorys_add():
        """Add a new entry to the words_categorys junction table"""
        try:
            data = request.get_json(silent=True) or request.form.to_dict()
            # Get values and convert to int (dict.get() doesn't support type= parameter)
            word_id = data.get('word_id')
            category_id = data.get('category_id')
            
            # Convert to int if they exist
            try:
                word_id = int(word_id) if word_id is not None else None
                category_id = int(category_id) if category_id is not None else None
            except (ValueError, TypeError):
                return jsonify({'success': False, 'error': 'word_id and category_id must be valid integers'}), 400
            
            if not word_id or not category_id:
                return jsonify({'success': False, 'error': 'Both word_id and category_id are required'}), 400
            
            # Check if the relationship already exists
            existing = execute_query("""
                SELECT word_id, category_id 
                FROM words_categorys 
                WHERE word_id = %s AND category_id = %s
            """, (word_id, category_id), fetch="one")
            
            if existing:
                return jsonify({'success': False, 'error': 'This word-category relationship already exists'}), 400
            
            # Insert into words_categorys
            result = execute_query("""
                INSERT INTO words_categorys (word_id, category_id) 
                VALUES (%s, %s) 
                RETURNING word_id, category_id
            """, (word_id, category_id), fetch="one")
            
            if result:
                invalidate_query_cache()
                logger.info(f"Created words_categorys entry: word_id={word_id}, category_id={category_id}")
                return jsonify({
                    'success': True, 
                    'word_id': word_id, 
                    'category_id': category_id,
                    'message': 'Word-category relationship added successfully'
                })
            else:
                return jsonify({'success': False, 'error': 'Failed to create word-category relationship'}), 500
                
        except Exception as e:
            logger.error(f"Error adding words_categorys entry: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Check if it's a unique constraint violation
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                return jsonify({'success': False, 'error': 'This word-category relationship already exists'}), 400
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/<int:category_id>', methods=['DELETE'])
    def api_delete_category(category_id):
        """Delete a category"""
        try:
            # Check if category exists
            category = execute_query("""
                SELECT id FROM categorys WHERE id = %s
            """, (category_id,), fetch="one")
            
            if not category:
                return jsonify({'success': False, 'error': 'Category not found'}), 404
            
            # Delete category (cascade will handle words_categorys)
            execute_query("""
                DELETE FROM categorys WHERE id = %s
            """, (category_id,), fetch=False)
            
            invalidate_query_cache()
            logger.info(f"Deleted category {category_id}")
            return jsonify({'success': True, 'message': 'Category deleted successfully'})
        except Exception as e:
            logger.error(f"Error deleting category: {e}")
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/find-duplicates', methods=['GET'])
    def api_find_duplicate_categories():
        """Find duplicate categories by name (case-insensitive)"""
        try:
            # Find categories with the same name (case-insensitive)
            duplicates = execute_query("""
                SELECT 
                    LOWER(w.word) as name_lower,
                    COUNT(*) as count,
                    ARRAY_AGG(c.id ORDER BY c.id) as category_ids,
                    ARRAY_AGG(w.word ORDER BY c.id) as names
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                GROUP BY LOWER(w.word)
                HAVING COUNT(*) > 1
                ORDER BY COUNT(*) DESC, LOWER(w.word)
            """, fetch="all")
            
            if not duplicates:
                return jsonify({
                    'success': True,
                    'duplicates': [],
                    'total_duplicates': 0,
                    'message': 'No duplicate categories found'
                })
            
            # Format duplicates
            duplicate_list = []
            total_to_remove = 0
            
            for dup in duplicates:
                name_lower = dup[0]
                count = dup[1]
                category_ids = dup[2] if isinstance(dup[2], list) else [dup[2]]
                names = dup[3] if isinstance(dup[3], list) else [dup[3]]
                
                # Keep the first (lowest ID), mark others for removal
                keep_id = category_ids[0]
                remove_ids = category_ids[1:]
                
                duplicate_list.append({
                    'name': names[0] if names else name_lower,
                    'name_lower': name_lower,
                    'count': count,
                    'keep_id': keep_id,
                    'remove_ids': remove_ids,
                    'total_to_remove': len(remove_ids)
                })
                
                total_to_remove += len(remove_ids)
            
            return jsonify({
                'success': True,
                'duplicates': duplicate_list,
                'total_duplicates': len(duplicate_list),
                'total_to_remove': total_to_remove,
                'message': f'Found {len(duplicate_list)} duplicate category groups'
            })
        except Exception as e:
            logger.error(f"Error finding duplicate categories: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)
    
    @app.route('/api/categories/remove-duplicates', methods=['POST'])
    def api_remove_duplicate_categories():
        """Remove duplicate categories, keeping the one with the lowest ID and merging word relationships"""
        try:
            # Find all duplicates
            duplicates = execute_query("""
                SELECT 
                    LOWER(w.word) as name_lower,
                    ARRAY_AGG(c.id ORDER BY c.id) as category_ids
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                GROUP BY LOWER(w.word)
                HAVING COUNT(*) > 1
            """, fetch="all")
            
            if not duplicates:
                return jsonify({
                    'success': True,
                    'removed': 0,
                    'message': 'No duplicate categories found'
                })
            
            total_removed = 0
            total_words_merged = 0
            removed_ids = []
            
            for dup in duplicates:
                # Handle array results from PostgreSQL
                category_ids_raw = dup[1]
                if isinstance(category_ids_raw, list):
                    category_ids = category_ids_raw
                elif isinstance(category_ids_raw, (tuple, set)):
                    category_ids = list(category_ids_raw)
                else:
                    category_ids = [category_ids_raw]
                
                # Keep the first (lowest ID), remove the rest
                keep_id = category_ids[0]
                remove_ids = category_ids[1:]
                
                # Merge word relationships from duplicate categories into the kept category
                for cat_id in remove_ids:
                    try:
                        # Get words from the duplicate category
                        words_to_merge = execute_query("""
                            SELECT DISTINCT word_id 
                            FROM words_categorys 
                            WHERE category_id = %s
                        """, (cat_id,), fetch="all")
                        
                        # Merge words into the kept category (ignore duplicates)
                        if words_to_merge:
                            for word_row in words_to_merge:
                                word_id = word_row[0] if isinstance(word_row, (list, tuple)) else word_row
                                try:
                                    # Insert word into kept category (ON CONFLICT will ignore duplicates)
                                    execute_query("""
                                        INSERT INTO words_categorys (word_id, category_id)
                                        VALUES (%s, %s)
                                        ON CONFLICT (word_id, category_id) DO NOTHING
                                    """, (word_id, keep_id), fetch=False)
                                    total_words_merged += 1
                                except Exception as e:
                                    logger.warning(f"Error merging word {word_id} from category {cat_id} to {keep_id}: {e}")
                        
                        # Delete duplicate category (cascade will handle remaining words_categorys)
                        execute_query("""
                            DELETE FROM categorys WHERE id = %s
                        """, (cat_id,), fetch=False)
                        removed_ids.append(cat_id)
                        total_removed += 1
                    except Exception as e:
                        logger.warning(f"Error removing category {cat_id}: {e}")
                        continue
            
            logger.info(f"Removed {total_removed} duplicate categories, merged {total_words_merged} word relationships")
            return jsonify({
                'success': True,
                'removed': total_removed,
                'words_merged': total_words_merged,
                'removed_ids': removed_ids,
                'message': f'Successfully removed {total_removed} duplicate categories and merged {total_words_merged} word relationships'
            })
        except Exception as e:
            logger.error(f"Error removing duplicate categories: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return client_error(e, subsystem='Api.routes.categories', success_key='success', status=500)

