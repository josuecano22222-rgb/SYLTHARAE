"""
Files Blueprint - File Management Routes and Helpers
Handles file upload, browsing, viewing, and processing
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app, make_response
from werkzeug.datastructures import MultiDict
from werkzeug.utils import secure_filename
import os
import sys
from pathlib import Path
import re
import json
import logging

project_root = str(Path(__file__).parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


logger = logging.getLogger(__name__)

# Create blueprint
files_bp = Blueprint("files", __name__)


# The words a prepared action carries (its question, its terminal messages) are
# the page's, and they are translated here rather than assembled in JavaScript.
from flask_babel import gettext as _

from core.table_paging import clamp_page
from core.serialization import pack_int_list, unpack_int_list
from core.errors import client_error, client_safe_message
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from core.security.disclosure import note_disclosure
from Api.utils import (
    execute_query, select_info_sources, select_info_sides, select_info_file_types,
    load_text_content, select_classification, compute_percentage, get_content_stats,
    get_word_frequencies, get_file, get_query
)
from Api.services.file_navigation import (
    LIBRARY_JOINS, ORDER_BY, build_library_filters, context_params,
    navigation_for,
)


# ---------------------------------------------------------------------------
# The actions a record page offers
# ---------------------------------------------------------------------------
def record_actions_for(file_id, original=None):
    """Prepare this record's actions from the registry plus this page's bindings.

    Three layers meet here, and each keeps its own job:

    * the **Action Registry** says what each action is - its scope, the
      permission domain it belongs to, whether it is destructive, the
      confirmation key it asks with, the operation reference it will one day
      run, and whether the product offers it at all;
    * the **screen** (this function) says what each action does *here*: the
      endpoint, the group it is drawn in, the question it asks in the reader's
      language, and what the record currently allows;
    * the **runtime** asks the question, marks the control busy, and reports
      through the toast.

    Nothing here performs anything, and nothing here decides access: the
    endpoints authorise every request on their own, and ``permitted`` below is
    this page reading the same answer the route guard would give - a hidden
    control is not a secured one.
    """
    from core.experience import presentation
    from core.security import current_user

    user = current_user()
    can_write = bool(user and user.has_role("admin", "analyst"))

    if original is None:
        from Api.services.original_file import OriginalFileService

        original = OriginalFileService.describe(file_id)

    availability = None if original.get('available') else 'original_missing'
    message = None
    if availability:
        message = original.get('message') or _ORIGINAL_UNAVAILABLE

    bindings = {
        # The two halves of one idea, deliberately separate: showing a document
        # and taking a copy of it are different operations with different
        # consequences, and the labels say which is which.
        "files.view_original": {
            "href": url_for('files.original_file_content', file_id=file_id),
            "target": "_blank", "group": "primary",
            "disabled_reason": availability, "disabled_label": message,
        },
        "files.download_original": {
            "href": url_for('files.original_file_content', file_id=file_id,
                            download=1),
            "group": "secondary",
            "disabled_reason": availability, "disabled_label": message,
        },
        "files.export_content": {
            "endpoint": url_for('files.file_export', file_id=file_id),
            "method": "GET", "group": "secondary",
            "success_message": _("The extracted text was exported."),
            "failure_message": _("The extracted text could not be exported."),
        },
        "files.delete": {
            "endpoint": url_for('files.delete_file', file_id=file_id),
            "method": "POST", "group": "overflow",
            "permitted": can_write,
            "confirmation": {
                "title": _("Delete this file?"),
                "message": _(
                    "This permanently removes the record and everything "
                    "extracted from it. The original file on disk is not "
                    "touched."),
                "confirm_label": _("Delete file"),
                "cancel_label": _("Keep it"),
            },
            "success_message": _("The record was deleted."),
            "failure_message": _("The record could not be deleted."),
        },
        # Registered and not built. It is bound here so the page records what it
        # knows - the action exists, no operation performs it - and the surface
        # leaves it out with the reason `not_built` rather than offering a
        # control that cannot work.
        "files.reprocess": {"permitted": True},
    }
    return presentation.record_actions("file_library", bindings, scope="record")


#: Said when a source file is no longer where the record says it was. The
#: service's own wording is preferred whenever it has one.
_ORIGINAL_UNAVAILABLE = ("The original file is not available at its recorded "
                         "location.")


def get_keyword_frequencies(file_id, limit=50):
    """Get keyword frequencies for a file with BYTEA decoding"""
    try:
        # Import the database-level function with a different name
        from Api.utils import get_keyword_frequencies_db
        results = get_keyword_frequencies_db(file_id, limit)
        # Decode keyword BYTEA to text
        decoded_results = []
        for keyword_bytes, count in results:
            try:
                # Keywords are stored as pickled word IDs
                word_ids = unpack_int_list(keyword_bytes) if keyword_bytes else []
                # Convert word IDs to text
                if word_ids:
                    # Use IN clause with placeholders (same approach as load_text_keyword)
                    placeholders = ','.join(['%s'] * len(word_ids))
                    words_query = f"""
                        SELECT word 
                        FROM words 
                        WHERE id IN ({placeholders})
                        ORDER BY ARRAY_POSITION(ARRAY[{placeholders}]::INTEGER[], id)
                    """
                    words_result = execute_query(words_query, word_ids + word_ids)
                    keyword_text = ' '.join([w[0] for w in words_result]) if words_result else ''
                    decoded_results.append((keyword_text, count))
            except Exception as e:
                logger.warning(f"Error decoding keyword: {e}")
                continue
        return decoded_results
    except Exception as e:
        logger.error(f"Error getting keyword frequencies: {e}")
        return []


def get_repeated_elements(file_id, limit=50):
    """Get words that appear in multiple categories (duplicate/repeated words)"""
    try:
        query = """
            SELECT 
                w.word,
                wp.word_count,
                COUNT(DISTINCT wc.category_id) as category_count,
                STRING_AGG(DISTINCT w_cat.word, ', ' ORDER BY w_cat.word) as categories
            FROM words_hashs wp
            JOIN words w ON wp.word_id = w.id
            JOIN words_categorys wc ON w.id = wc.word_id
            JOIN categorys c ON wc.category_id = c.id
            JOIN words w_cat ON c.word_id = w_cat.id
            WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
            GROUP BY w.id, w.word, wp.word_count
            HAVING COUNT(DISTINCT wc.category_id) > 1
            ORDER BY category_count DESC, wp.word_count DESC
            LIMIT %s
        """
        results = execute_query(query, (file_id, limit))
        repeated_elements = []
        for word, word_count, category_count, categories in results:
            repeated_elements.append({
                'word': word,
                'count': word_count,
                'category_count': category_count,
                'categories': categories.split(', ') if categories else []
            })
        return repeated_elements
    except Exception as e:
        logger.error(f"Error getting repeated elements: {e}")
        return []


def get_enhanced_content_stats(file_id):
    """Get enhanced content statistics for charts"""
    try:
        # Get basic content stats
        basic_stats = get_content_stats(file_id)
        
        # Get word count from the hash-keyed word occurrences table
        # (words_paths was renamed words_hashs by the content-identity migration)
        word_count_result = execute_query("""
            SELECT COUNT(DISTINCT wp.word_id) as unique_words,
                   SUM(wp.word_count) as total_word_occurrences
            FROM words_hashs wp
            WHERE wp.hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)
        """, (file_id,), fetch="one")
        
        # Handle tuple result from multi-column query
        if isinstance(word_count_result, tuple) and len(word_count_result) >= 2:
            unique_words = word_count_result[0] or 0
            total_word_occurrences = word_count_result[1] or 0
        else:
            unique_words = 0
            total_word_occurrences = 0
        
        # Get sentence and paragraph count from content.
        # STAT-01: ``contents.content_data`` stores *zlib-compressed, pickled*
        # symbol pairs, not plain text. Reading it directly produced garbage
        # (psycopg2 returns a ``memoryview``; the old code fell back to
        # ``str(content_data)`` which is ``"<memory at 0x...>"`` — exactly the
        # bogus 26 characters Quick Stats reported). Use the same reconstruction
        # path as the full-content reader so both views agree by construction.
        words = 0
        sentences = 0
        paragraphs = 0
        characters = 0
        
        try:
            content_text = load_text_content(file_id)
            if content_text and content_text.strip():
                characters = len(content_text)
                words = len(content_text.split())
                sentences = content_text.count('.') + content_text.count('!') + content_text.count('?')
                paragraphs = content_text.count('\n\n') + 1
        except Exception as e:
            logger.error(f"Error processing content data: {e}")
            # Continue with default values
        
        # Get classification percentages
        percentages = {}
        top_categories = []
        
        try:
            classification_data = select_classification(file_id)
            percentages = compute_percentage(classification_data)
            
            # Get top categories for chart
            if percentages:
                sorted_categories = sorted(percentages.items(), key=lambda x: x[1].get('percentage', 0), reverse=True)
                top_categories = sorted_categories[:5]  # Top 5 categories
        except Exception as e:
            logger.error(f"Error getting classification data: {e}")
            # Continue with empty percentages
        
        return {
            'words': words if words else unique_words,
            'unique_words': unique_words,
            'sentences': sentences,
            'paragraphs': paragraphs,
            'characters': characters,
            'total_word_occurrences': total_word_occurrences,
            'chunks': basic_stats.get('chunks', 0),
            'total_size': basic_stats.get('total_size', 0),
            'estimated_words': basic_stats.get('estimated_words', 0),
            'top_categories': top_categories,
            'classification_percentages': percentages
        }
        
    except Exception as e:
        logger.error(f"Error getting enhanced content stats: {e}")
        return {
            'words': 0,
            'sentences': 0,
            'paragraphs': 0,
            'characters': 0,
            'total_word_occurrences': 0,
            'chunks': 0,
            'total_size': 0,
            'estimated_words': 0,
            'top_categories': [],
            'classification_percentages': {}
        }


def get_monitor():
    """Get performance monitor from app config"""
    return current_app.config.get('PERFORMANCE_MONITOR', None)


# ==================== ROUTES ====================

@files_bp.route('/upload')
def upload_page():
    """Compatibility alias for the single ingestion interface.

    There used to be two upload interfaces - this CLI-styled page and
    ``/operations/input`` - with different capabilities, different pipelines
    and different words for the same thing. The interface is now one page; this
    URL is kept so bookmarks, shortcuts and documentation that predate the
    merge keep working and land on it.
    """
    return redirect(url_for('operations_input_page'), code=302)


# ---------------------------------------------------------------------------
# Processing-task API (Api/task_manager.py)
#
# These four routes are the HTTP surface of the background processing-task
# manager. It is a live subsystem: the import flows create tasks in it
# (Api/services/import_service.py) and the dashboard's progress tracker polls
# /upload/active-tasks every 1.5 s. The ingestion page does NOT use them - it
# creates ingestion *jobs* (/api/input/jobs) whose progress, pause, resume and
# cancel live in the Jobs Center (/operations/jobs). The old
# POST /upload/process-path entry point, which started a path-ingestion task
# from the deleted upload page, was removed with that page: it was a second,
# differently-shaped way to start the same ingestion the Jobs API owns, and
# nothing called it any more. Path containment (SEC-06) is unchanged and still
# enforced in IngestionService.validate() for every ingestion path.
# ---------------------------------------------------------------------------
@files_bp.route('/upload/progress/<task_id>', methods=['GET'])
def upload_progress(task_id):
    """
    Get progress for a processing task.
    Returns current progress, status, and logs.
    """
    try:
        from Api.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        progress = task_manager.get_task_progress(task_id)
        
        if not progress:
            return jsonify({'error': 'Task not found'}), 404
        
        return jsonify(progress.to_dict()), 200
        
    except Exception as e:
        logger.error(f"Get progress error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/upload/active-tasks', methods=['GET'])
@limiter.exempt
def get_active_tasks():
    """
    Get all active (running/pending/paused) processing tasks.
    Returns list of tasks with their progress.
    """
    try:
        from Api.task_manager import get_task_manager, TaskStatus
        
        task_manager = get_task_manager()
        all_tasks = task_manager.get_all_tasks()
        
        # Filter for active tasks (running, pending, or paused)
        active_tasks = [
            task.to_dict() for task in all_tasks
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED)
        ]
        
        return jsonify({
            'success': True,
            'tasks': active_tasks,
            'count': len(active_tasks)
        }), 200
        
    except Exception as e:
        logger.error(f"Get active tasks error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/upload/pause/<task_id>', methods=['POST'])
def pause_task(task_id):
    """
    Pause a running processing task.
    """
    try:
        from Api.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        success = task_manager.pause_task(task_id)
        
        if not success:
            return jsonify({'error': 'Task not found or cannot be paused'}), 404
        
        return jsonify({
            'success': True,
            'message': 'Task paused successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Pause task error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/upload/resume/<task_id>', methods=['POST'])
def resume_task(task_id):
    """
    Resume a paused processing task.
    """
    try:
        from Api.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        success = task_manager.resume_task(task_id)
        
        if not success:
            return jsonify({'error': 'Task not found or cannot be resumed'}), 404
        
        return jsonify({
            'success': True,
            'message': 'Task resumed successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Resume task error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/upload/cancel/<task_id>', methods=['POST'])
def api_cancel_task(task_id):
    """
    API endpoint to cancel a running or paused processing task.
    
    This endpoint wraps FileProcessingTaskManager.cancel_task() for API access.
    
    Args:
        task_id: ID of the task to cancel
        
    Returns:
        JSON response with success status (200) or error (404/500)
    """
    try:
        from Api.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        success = task_manager.cancel_task(task_id)
        
        if not success:
            return jsonify({'error': 'Task not found or cannot be cancelled'}), 404
        
        return jsonify({
            'success': True,
            'message': 'Task cancelled successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Cancel task error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


# ==================== FILE BROWSER ====================

#: The columns a list table may sort the File Library by: the header's
#: `sort` key -> the SQL expression it means. The export endpoint reads the
#: same allowlist, so a sorted export is ordered exactly like the view.
LIST_SORT_COLUMNS = {
    'name': 'p.file_name',
    'size': 'p.file_size',
    'date': 'p.date_creation',
    'type': 'p.file_type',
    'status': 'p.file_status',
}


def _library_page(args, page=1, limit=10, scope_where=None, scope_params=()):
    """One page of the File Library's view: the rows, the count, the order.

    The single query surface every rendering of the library shares - the
    File Library page, a file type's panel, a keyword's or category's
    panel - so a filter, a sort column or the search highlighting can never
    disagree between them. `args` is the view's query string (search,
    source, side, status, file_type, date_from, date_to, size_min,
    size_max, sort, order); `page`/`limit` pick the slice.

    `scope_where`/`scope_params` pin the panel's identity - the one owner a
    panel is about (a keyword, a category). It is not a filter the reader
    toggles; it is what the panel IS, and it composes with every filter.
    """
    # ``page`` arrives from the query string; past ~10**19 the OFFSET is no
    # longer a bigint and PostgreSQL refuses the statement, so a page that
    # cannot exist answered 500. clamp_page keeps the arithmetic inside the
    # type (and any page past the end of the list is handled below by the
    # clamp to ``total_pages``).
    offset = (clamp_page(page) - 1) * limit

    filters = build_library_filters(args)
    where_parts = list(filters.where_parts)
    where_params = list(filters.params)
    if scope_where:
        where_parts.append(scope_where)
        where_params.extend(scope_params)
    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # Column sort comes from the table headers (`sort` + `order`), over
    # LIST_SORT_COLUMNS - the list's order, so a sorted view stays sorted
    # across pages. The default stays ORDER_BY, which is also the sequence
    # the record pages walk in.
    sort_key = args.get('sort', '')
    sort_order = args.get('order', '')
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'
    if sort_key in LIST_SORT_COLUMNS:
        list_order_by = (
            f"{LIST_SORT_COLUMNS[sort_key]} {sort_order.upper()} NULLS LAST, "
            "p.id DESC"
        )
    else:
        sort_key = 'date'
        list_order_by = ORDER_BY

    joins = list(LIBRARY_JOINS)
    count_query = f"""
        SELECT COUNT(*)
        FROM paths p
        {' '.join(joins) if joins else ''}
        {where_clause}
    """
    total_result = execute_query(
        count_query, tuple(where_params) if where_params else None, fetch="one")
    total_count = total_result[0] if isinstance(total_result, tuple) else (
        total_result if isinstance(total_result, int) else 0)

    rows_query = f"""
        SELECT
            p.id, p.file_name, p.file_path, p.file_size, p.file_type,
            p.file_status, p.file_date, p.date_creation,
            COALESCE(s.name, 'Unknown') as source_name,
            COALESCE(si.name, 'Unknown') as side_name
        FROM paths p
        {' '.join(joins) if joins else ''}
        {where_clause}
        ORDER BY {list_order_by}
        LIMIT %s OFFSET %s
    """
    rows = execute_query(rows_query, tuple(where_params) + (limit, offset))

    # When the reader is searching, the matched part of the name is
    # highlighted here - the same view, with the hit visible where it hit -
    # and the row the template marks as a hit.
    highlight_term = (args.get('search', '') or '').strip()
    highlight_lower = highlight_term.lower()
    files = []
    for row in rows or []:
        name = row[1]
        if highlight_lower and highlight_lower in (name or '').lower():
            from markupsafe import Markup, escape
            escaped = escape(name)
            # Safe by construction: `name` and the highlight term are both
            # escaped BEFORE the <mark> wrapper is added, so no untrusted
            # markup can result (step 25 security audit triage).
            name = Markup(str(escaped).replace(  # nosec B704
                str(escape(highlight_term)),
                f'<mark>{escape(highlight_term)}</mark>'))
        files.append((
            row[0],  # id
            name,    # file_name (highlighted when it matched the search)
            row[2],  # file_path
            row[3],  # file_size
            row[4],  # file_type
            row[5],  # file_status
            row[6],  # file_date
            row[7],  # date_creation
            row[8],  # source_name
            row[9]   # side_name
        ))

    total_pages = ((total_count - 1) // limit) + 1 if total_count > 0 else 1
    return {
        'files': files,
        'total_count': total_count,
        'total_pages': total_pages,
        'page': max(1, page),
        'sort_key': sort_key,
        'sort_order': sort_order,
    }



def _documents_panel_response(table_id, scope_where, scope_params, args,
                              identity_params):
    """The documents-panel fragment for one owner of library documents.

    Shared by every panel that pins the library to one owner - a file type,
    a keyword, a category. The view is `_library_page` (the File Library's
    own query and rendering), so the panel's rows, search highlighting,
    sorting and Load More are the library's, scoped.
    """
    page = args.get('page', 1, type=int)
    limit = args.get('limit', 25, type=int)
    limit = max(1, min(200, limit))

    view = _library_page(args, page=page, limit=limit,
                         scope_where=scope_where, scope_params=scope_params)
    nav_params = context_params(build_library_filters(args))

    panel_params = MultiDict([
        (key, value) for (key, value) in args.items(multi=True)
        if key not in ('page', 'limit', 'cursor')
    ])
    from urllib.parse import urlencode
    view_string = urlencode(panel_params)
    identity_string = urlencode(identity_params)

    return render_template(
        'file/_file_documents_fragment.html',
        files=view['files'],
        total_count=view['total_count'],
        total_pages=view['total_pages'],
        page=view['page'],
        limit=limit,
        sort_by=view['sort_key'],
        sort_order=view['sort_order'],
        search=(args.get('search', '') or '').strip(),
        nav_params=nav_params,
        start_position=(view['page'] - 1) * limit + 1,
        table_id=table_id,
        view_string=view_string,
        identity_string=identity_string,
        file_type='',
    )


@files_bp.route('/files')
def files_list():
    """Enhanced File Library - one continuous list through the shared view."""
    from settings import get_settings
    settings = get_settings()
    display_config = settings.get_display_config()

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)  # Page number
    default_limit = display_config.get('results_per_page', 10)
    limit = request.args.get('limit', default_limit, type=int)
    limit = max(1, min(1000, limit))

    # Filters are validated and rendered by Api.services.file_navigation so
    # that this list, its statistics counters and the Previous/Next controls
    # on the detail pages can never disagree about what the current view is.
    search = request.args.get('search', '')
    source_filter = request.args.get('source', '')
    side_filter = request.args.get('side', '')
    status_filter = request.args.get('status', '')
    file_type_filter = request.args.get('file_type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    size_min = request.args.get('size_min', '')
    size_max = request.args.get('size_max', '')

    view = _library_page(request.args, page=page, limit=limit)
    files = view['files']
    total_files_count = view['total_count']
    total_pages = view['total_pages']
    sort_key = view['sort_key']
    sort_order = view['sort_order']

    # The same view, as query parameters the detail pages hand back to us.
    # Every file link in this list carries them, which is what makes
    # Previous/Next on a detail page walk *this* list and not the whole
    # library.
    nav_params = context_params(build_library_filters(request.args))
    start_position = (max(1, page) - 1) * limit + 1

    try:
        # Calculate total analyzed and pending files from database with same filters
        total_analyzed = 0
        total_pending = 0

        try:
            # Same filters as the main query, minus status: each counter
            # appends its own status condition below.
            stats_filters = build_library_filters(request.args, include_status=False)
            stats_where = list(stats_filters.where_parts)
            stats_params = list(stats_filters.params)

            stats_query_base = """
                SELECT COUNT(*)
                FROM paths p
                LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
                LEFT JOIN sources s ON hc.source_id = s.id
                LEFT JOIN sides si ON hc.side_id = si.id
            """

            def _status_count(status):
                query = stats_query_base + " WHERE " + " AND ".join(
                    stats_where + ["p.file_status = %s"])
                result = execute_query(query, tuple(stats_params) + (status,), fetch="one")
                if isinstance(result, tuple):
                    return int(result[0] or 0)
                if isinstance(result, int):
                    return result
                return 0

            total_analyzed = _status_count('Read')
            total_pending = _status_count('Unread')
        except Exception as stats_error:
            logger.error(f"Error calculating statistics: {stats_error}", exc_info=True)
            # Fallback: count from current page
            total_analyzed = sum(1 for f in files if f[5] == 'Read')
            total_pending = sum(1 for f in files if f[5] != 'Read')

        # Cache sources, sides, and file types (they rarely change)
        # Use try-except for each to prevent one failure from breaking the page
        try:
            sources = select_info_sources()
        except Exception as e:
            logger.error(f"Error loading sources: {e}")
            sources = {}

        try:
            sides = select_info_sides()
        except Exception as e:
            logger.error(f"Error loading sides: {e}")
            sides = {}

        try:
            file_types = select_info_file_types()
        except Exception as e:
            logger.error(f"Error loading file types: {e}")
            file_types = {}

        return render_template('file/files_list.html',
                             files=files,
                             page=page,
                             total_pages=total_pages,
                             total_files=total_files_count,
                             total_analyzed=total_analyzed,
                             total_pending=total_pending,
                             sources=sources or {},
                             sides=sides or {},
                             file_types=file_types or {},
                             search=search or '',
                             source_filter=source_filter or '',
                             side_filter=side_filter or '',
                             status_filter=status_filter or '',
                             file_type_filter=file_type_filter or '',
                             date_from=date_from or '',
                             date_to=date_to or '',
                             size_min=size_min or '',
                             size_max=size_max or '',
                             limit=limit,
                             start_position=start_position,
                             sort_by=sort_key,
                             sort_order=sort_order,
                             nav_params=nav_params)

    except Exception as e:
        logger.error(f"Error in files_list: {e}", exc_info=True)
        # Fallback to empty results with safe defaults
        try:
            sources = select_info_sources()
        except Exception:
            sources = {}
        
        try:
            sides = select_info_sides()
        except Exception:
            sides = {}
        
        try:
            file_types = select_info_file_types()
        except Exception:
            file_types = {}
        
        return render_template('file/files_list.html',
                             files=[],
                             page=1,
                             total_pages=1,
                             total_files=0,
                             sources=sources or {},
                             sides=sides or {},
                             file_types=file_types or {},
                             search=search or '',
                             source_filter=source_filter or '',
                             side_filter=side_filter or '',
                             status_filter=status_filter or '',
                             file_type_filter=request.args.get('file_type', '') or '',
                             sort_by='date',
                             sort_order='desc',
                             cursor_pagination=True,
                             nav_params={},
                             error=str(e))


def _file_type_statistics():
    """Aggregate the type recorded by ingestion for the indexed file library."""
    rows = execute_query(
        """
        SELECT COALESCE(NULLIF(BTRIM(file_type), ''), 'Unknown') AS file_type,
               COUNT(*) AS file_count,
               COALESCE(SUM(file_size), 0) AS total_size
        FROM paths
        GROUP BY COALESCE(NULLIF(BTRIM(file_type), ''), 'Unknown')
        ORDER BY file_count DESC, file_type ASC
        """,
        fetch='all',
    ) or []
    return [
        {
            'file_type': str(row[0] or 'Unknown'),
            'file_count': int(row[1] or 0),
            'total_size': int(row[2] or 0),
        }
        for row in rows
    ]


def _file_type_label(value):
    """Readable format label without changing the exact stored filter value."""
    normalized = str(value or 'Unknown').strip().lstrip('.').lower()
    names = {
        'pdf': 'PDF', 'doc': 'Word (.doc)', 'docx': 'Word (.docx)',
        'xls': 'Excel (.xls)', 'xlsx': 'Excel (.xlsx)',
        'ppt': 'PowerPoint (.ppt)', 'pptx': 'PowerPoint (.pptx)',
        'jpg': 'Image (JPG)', 'jpeg': 'Image (JPEG)', 'png': 'Image (PNG)',
        'gif': 'Image (GIF)', 'bmp': 'Image (BMP)', 'tif': 'Image (TIF)',
        'tiff': 'Image (TIFF)', 'webp': 'Image (WebP)', 'txt': 'Text (.txt)',
        'csv': 'CSV', 'html': 'HTML', 'htm': 'HTML', 'eml': 'Email (.eml)',
        'msg': 'Email (.msg)', 'zip': 'ZIP archive', 'rar': 'RAR archive',
        '7z': '7-Zip archive', 'unknown': 'Unknown format',
    }
    return names.get(normalized, normalized.upper() if normalized else 'Unknown format')


@files_bp.route('/files/types/<file_type>/documents')
def file_type_documents(file_type):
    """One detected format's documents, as the unified-table fragment.

    The File Types dashboard embeds this in its side panel. It is the same
    view the File Library serves - the shared `_library_page` query, the
    same rows, the same search highlighting - pinned to one `file_type`,
    so the panel and the library can never disagree about what a row is.
    """
    args = request.args.copy()
    # The panel's identity is the type: it overrides whatever the query
    # string carried, and it is the one parameter the "entire dataset"
    # export scope keeps.
    view_params = MultiDict([
        (key, value) for (key, value) in request.args.items(multi=True)
        if key != 'file_type'
    ])
    view_params.add('file_type', file_type)
    args = view_params

    page = args.get('page', 1, type=int)
    limit = args.get('limit', 25, type=int)
    limit = max(1, min(200, limit))

    view = _library_page(args, page=page, limit=limit)
    nav_params = context_params(build_library_filters(args))

    # The panel's own view string (paging coordinates excluded): what its
    # Export menu speaks about, and what a sort or search re-ask carries.
    panel_params = MultiDict([
        (key, value) for (key, value) in args.items(multi=True)
        if key not in ('page', 'limit', 'cursor')
    ])
    from urllib.parse import urlencode
    view_string = urlencode(panel_params)
    identity_string = urlencode({'file_type': file_type})

    return render_template(
        'file/_file_documents_fragment.html',
        files=view['files'],
        total_count=view['total_count'],
        total_pages=view['total_pages'],
        page=view['page'],
        limit=limit,
        sort_by=view['sort_key'],
        sort_order=view['sort_order'],
        search=(args.get('search', '') or '').strip(),
        nav_params=nav_params,
        start_position=(view['page'] - 1) * limit + 1,
        table_id='panelFilesTable',
        view_string=view_string,
        identity_string=identity_string,
        file_type=file_type,
    )


@files_bp.route('/files/types')
def file_types_page():
    """Browse the automatically recorded format classification and counts."""
    try:
        type_rows = _file_type_statistics()
    except Exception as error:
        logger.error('Could not load file-type statistics: %s', error, exc_info=True)
        type_rows = []
    return render_template(
        'file/file_types.html',
        file_types=[
            {**row, 'label': _file_type_label(row['file_type'])}
            for row in type_rows
        ],
        total_files=sum(row['file_count'] for row in type_rows),
    )


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/files/types')
def api_file_type_statistics():
    """Return counts and total sizes grouped by ingestion-detected file type."""
    try:
        rows = _file_type_statistics()
        for row in rows:
            row['label'] = _file_type_label(row['file_type'])
        return jsonify({
            'success': True,
            'types': rows,
            # Every indexed document, unfiltered: the page's totals are dataset-wide.
            'total_files': sum(row['file_count'] for row in rows),
            'total_files_scope': 'dataset',
        })
    except Exception as error:
        logger.error('Could not load file-type statistics: %s', error, exc_info=True)
        return client_error(error, subsystem='Api.blueprints.files',
                            success_key='success', status=500)


# ==================== FILE DETAIL ====================

@files_bp.route('/file/<int:file_id>')
def file_detail(file_id):
    """Document Detail View with Pagination Support - OPTIMIZED with lazy loading"""
    # 🚀 OPTIMIZED: Single query for file info with proper NULL handling
    file_info = execute_query("""
        SELECT p.id, p.file_name, p.file_path, p.file_size, p.file_type,
               p.file_status, p.file_date, p.date_creation, hc.hash_id,
               COALESCE(s.name, 'Unknown') as source_name, 
               COALESCE(si.name, 'Unknown') as side_name, 
               COALESCE(h.hash, '') as hash
        FROM paths p
        LEFT JOIN hash_contexts hc ON p.context_id = hc.id LEFT JOIN hashs h ON hc.hash_id = h.id
        LEFT JOIN sources s ON hc.source_id = s.id
        LEFT JOIN sides si ON hc.side_id = si.id
        WHERE p.id = %s
    """, (file_id,), fetch="one")
    
    if not file_info:
        flash("File not found", "error")
        return redirect(url_for('files.files_list'))
    
    # Convert file_info tuple to list for easier manipulation and ensure dates are datetime objects
    from datetime import datetime, date
    file_info_list = list(file_info) if isinstance(file_info, tuple) else file_info
    
    # Ensure date fields are datetime/date objects, not strings
    # file_info structure: (id, file_name, file_path, file_size, file_type, file_status, file_date, date_creation, hash_id, source_name, side_name, hash)
    if len(file_info_list) > 6 and file_info_list[6] and isinstance(file_info_list[6], str):
        try:
            file_info_list[6] = datetime.strptime(file_info_list[6], '%Y-%m-%d').date() if len(file_info_list[6]) == 10 else datetime.fromisoformat(file_info_list[6].replace('Z', '+00:00')).date()
        except (ValueError, AttributeError):
            pass  # Keep as string if parsing fails
    
    if len(file_info_list) > 7 and file_info_list[7] and isinstance(file_info_list[7], str):
        try:
            file_info_list[7] = datetime.fromisoformat(file_info_list[7].replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass  # Keep as string if parsing fails
    
    file_info = tuple(file_info_list) if isinstance(file_info, tuple) else file_info_list
    
    # 🚀 OPTIMIZED: Get basic stats only (defer heavy computation)
    content_stats = get_content_stats(file_id)

    # Analyst (manual) categories for this file - read exclusively from the
    # analyst tables; the smart taxonomy is untouched (FR-1.4). Displayed in
    # its own card on the file detail page.
    analyst_categories = None
    try:
        from Api.services.analyst_categories import AnalystCategoryService
        analyst_categories = AnalystCategoryService.categories_for_files([file_id]).get(file_id, [])
    except Exception as analyst_err:  # never break the detail page
        logger.warning(f"Could not load analyst categories for file {file_id}: {analyst_err}")

    # Get pagination parameters - allow larger pages for better readability
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50000, type=int)  # Increased default for better readability
    per_page = min(per_page, 100000)  # Increased max for better content display
    
    # Get search parameters. "?q=" is accepted as an alias for "?search="
    # so links from the search results can carry the originating query and
    # the term is located precisely the moment the detail page opens.
    search_query = request.args.get('search', '') or request.args.get('q', '')
    case_sensitive = request.args.get('case_sensitive', 'false').lower() == 'true'
    whole_word = request.args.get('whole_word', 'false').lower() == 'true'
    
    # Initialize content variables (IMPORTANT: always initialize)
    content = ''
    total_chars = 0
    total_pages = 0
    
    # 🚀 OPTIMIZED: Load content with better error handling and diagnostics
    try:
        # Check if content exists first
        content_check = execute_query(
            "SELECT COUNT(*) FROM contents WHERE hash_id = (SELECT c2.hash_id FROM paths p2 JOIN hash_contexts c2 ON c2.id = p2.context_id WHERE p2.id = %s)",
            (file_id,),
            fetch="one"
        )
        
        # execute_query with fetch="one" returns the value directly for COUNT queries (int)
        # or a tuple for multi-column queries
        content_count = content_check[0] if isinstance(content_check, (tuple, list)) else content_check
        
        if content_count and content_count > 0:
            logger.info(f"Found {content_count} content chunks for file_id={file_id}")
            full_content = load_text_content(file_id)
            
            logger.info(f"Content loaded for file_id={file_id}: length={len(full_content) if full_content else 0}, type={type(full_content)}")
            
            # Check if content is empty or only whitespace
            if not full_content:
                logger.warning(f"Content loaded but is None/empty for file_id={file_id}. Content check returned {content_count} chunks.")
                flash("Content found but could not be decoded. File may need reprocessing.", "warning")
                content = ''
                total_pages = 0
                total_chars = 0
            elif not full_content.strip():
                logger.warning(f"Content loaded but is whitespace-only for file_id={file_id}. Content check returned {content_count} chunks.")
                flash("Content found but appears to be empty or whitespace-only. File may need reprocessing.", "warning")
                content = ''
                total_pages = 0
                total_chars = 0
            else:
                # Ensure content is a string (not bytes)
                if isinstance(full_content, bytes):
                    try:
                        full_content = full_content.decode('utf-8', errors='replace')
                    except Exception as decode_error:
                        logger.error(f"Failed to decode content bytes for file_id={file_id}: {decode_error}")
                        flash("Content found but could not be decoded properly. File may need reprocessing.", "warning")
                        content = ''
                        total_pages = 0
                        total_chars = 0
                    else:
                        total_chars = len(full_content)
                        total_pages = (total_chars + per_page - 1) // per_page if total_chars > 0 else 1
                        if page == 1:
                            content = full_content[:per_page]
                            logger.info(f"✅ Loaded {total_chars} characters for file_id={file_id}, showing page 1 of {total_pages}")
                        else:
                            start_idx = (page - 1) * per_page
                            end_idx = min(start_idx + per_page, total_chars)
                            content = full_content[start_idx:end_idx]
                            logger.info(f"✅ Loaded page {page} of {total_pages} for file_id={file_id}, showing chars {start_idx}-{end_idx}")
                else:
                    total_chars = len(full_content)
                    total_pages = (total_chars + per_page - 1) // per_page if total_chars > 0 else 1
                    if page == 1:
                        content = full_content[:per_page]
                        logger.info(f"✅ Loaded {total_chars} characters for file_id={file_id}, showing page 1 of {total_pages}")
                    else:
                        start_idx = (page - 1) * per_page
                        end_idx = min(start_idx + per_page, total_chars)
                        content = full_content[start_idx:end_idx]
                        logger.info(f"✅ Loaded page {page} of {total_pages} for file_id={file_id}, showing chars {start_idx}-{end_idx}")
        else:
            logger.warning(f"No content chunks found in database for file_id={file_id}")
            flash("No content found. File may need reprocessing.", "warning")
            content = ''
            total_pages = 0
            total_chars = 0
    except Exception as e:
        logger.error(f"Error loading content for file_id={file_id}: {e}", exc_info=True)
        flash(f"Error loading content: {str(e)}", "error")
        content = ''
        total_pages = 0
        total_chars = 0
    
    # 🚀 OPTIMIZED: Load essential chart data for Analysis tab
    enhanced_stats = get_enhanced_content_stats(file_id) if page == 1 else {}
    
    # Load classification percentages for pie chart
    percentages = {}
    try:
        classification_data = select_classification(file_id)
        # Structure only: how many categories came back. The values
        # themselves are document-derived content and stay out of the log
        # (step 25 security audit: document content must not reach logs).
        logger.info("Classification data for file %s: %d category entries",
                    file_id, len(classification_data or {}))
        if classification_data:
            percentages = compute_percentage(classification_data)
            logger.info("Computed percentages for file %s: %d categories",
                        file_id, len(percentages))
        else:
            logger.warning(f"No classification data returned for file {file_id}")
    except Exception as e:
        logger.error(f"Error loading classification data for file {file_id}: {e}", exc_info=True)
        percentages = {}
    
    # Load word frequencies for bar chart (top 15)
    word_frequencies = []
    try:
        word_frequencies = get_word_frequencies(file_id, limit=15)
    except Exception as e:
        logger.error(f"Error loading word frequencies for file {file_id}: {e}")
        word_frequencies = []
    
    # Ensure all variables are properly defined (never None) - final safety check
    if content is None:
        content = ''
    if total_chars is None:
        total_chars = 0
    if total_pages is None:
        total_pages = 0
    
    # Debug: Log content status; lengths and types only - never a preview
    # of the document (step 25 security audit: document content must not
    # reach logs).
    logger.info(f"Rendering template for file_id={file_id}: content_length={len(content)}, total_chars={total_chars}, total_pages={total_pages}, content_type={type(content)}")
    
    # Previous/Next for the file being displayed, resolved inside the
    # browsing context the operator arrived from (the filtered library list,
    # or the whole library when the page is opened directly).
    nav = navigation_for(file_id, request.args, endpoint='files.file_detail')

    # What can be done with this record, prepared from the Action Registry and
    # this page's bindings; the surface draws it and the runtime runs it.
    record_actions = record_actions_for(file_id)

    return render_template('file/file_detail.html',
                         nav=nav,
                         record_actions=record_actions,
                         file=file_info,
                         content=content,  # Already ensured to be string
                         content_stats=content_stats,
                         enhanced_stats=enhanced_stats,
                         percentages=percentages,
                         word_frequencies=word_frequencies,
                         current_page=page,
                         total_pages=total_pages,
                         per_page=per_page,
                         total_chars=total_chars,
                         search_query=search_query,
                         case_sensitive=case_sensitive,
                         whole_word=whole_word,
                         # Analyst (manual) categories - separate namespace from
                         # the smart classification data above (FR-1.4)
                         analyst_categories=analyst_categories or [])


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/file/<int:file_id>/content')
def file_content_lazy(file_id):
    """Lazy load content in chunks"""
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 50000, type=int)  # Increased default limit
    
    try:
        full_content = load_text_content(file_id)
        chunk = full_content[offset:offset + limit]
        
        return jsonify({
            'content': chunk,
            'offset': offset,
            'has_more': len(full_content) > offset + limit,
            'total_length': len(full_content)
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/api/files/<int:file_id>/export')
def file_export(file_id):
    """Export file content as downloadable text file"""
    try:
        # Get file info
        file_info = execute_query("""
            SELECT file_name, file_path, file_type FROM paths WHERE id = %s
        """, (file_id,), fetch="one")
        
        if not file_info:
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        file_name = file_info[0] or f'file_{file_id}'
        file_type = file_info[2] or 'txt'
        output_name = _export_basename(
            request.args.get('filename'),
            f"{Path(file_name).stem or f'file_{file_id}'}_extracted_text",
            'txt',
        )
        
        # Get file content
        content = load_text_content(file_id)
        if content is None:
            content = ''
        
        note_disclosure(kind='file_text_export', scope=f'file:{file_id}', unit='file',
                        row_count=1, format='txt', file_ids=[file_id])
        # Create response with file download
        from flask import Response
        response = Response(
            content,
            mimetype='text/plain',
            headers={
                'Content-Disposition': f'attachment; filename="{output_name}"'
            }
        )
        return response
        
    except Exception as e:
        logger.error(f"Error exporting file {file_id}: {e}")
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/files/export', methods=['POST'])
def bulk_export_files():
    """Batch export of the selected files as a single ZIP download.

    Two modes (form field ``mode``):

    * ``originals`` - the source files exactly as they sit on disk, the
      same bytes the per-file download hands out. Files whose original
      has disappeared are skipped and listed in a ``_not_included.txt``
      inside the archive.
    * ``text`` (default) - the extracted text of each file, the same
      content ``/api/files/<id>/export`` produces, batched into one
      download instead of one per file.

    ``file_ids`` comes from a form POST (the toolbar builds it) or a JSON
    body. The endpoint the toolbar has always POSTed to never existed -
    this implements it for both modes.
    """
    import io
    import zipfile as _zipfile
    from datetime import datetime as _datetime
    from flask import send_file

    try:
        body = request.get_json(silent=True) or {}
        file_ids = _selected_file_ids_from_request(max_files=500)
        mode_value = request.values.get('mode') or body.get('mode')
        mode = 'originals' if mode_value == 'originals' else 'text'
        requested_name = request.values.get('filename') or body.get('filename')
        naming_template = (request.values.get('naming_template')
                           or body.get('naming_template') or '').strip()
        rename_map_raw = body.get('rename_map') or {}
        rename_map = {}
        if isinstance(rename_map_raw, dict):
            for key, value in rename_map_raw.items():
                try:
                    rename_map[int(key)] = str(value or '').strip()
                except (TypeError, ValueError):
                    continue

        placeholders = ','.join(['%s'] * len(file_ids))
        rows = execute_query(
            f"""
            SELECT p.id, p.file_name, p.file_date,
                   COALESCE(s.name, 'Unknown') AS source_name
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id
            LEFT JOIN sources s ON hc.source_id = s.id
            WHERE p.id IN ({placeholders})
            """,
            tuple(file_ids),
            fetch="all",
        )
        records_meta = {
            int(row[0]): {'file_name': row[1], 'file_date': row[2], 'source_name': row[3]}
            for row in rows or []
        }
        names = {fid: meta['file_name'] for fid, meta in records_meta.items()}
        missing_ids = sorted(set(file_ids) - set(names))
        if missing_ids:
            return jsonify({
                'success': False,
                'error': 'Some selected file records no longer exist.',
                'missing_file_ids': missing_ids,
            }), 404

        zip_buffer = io.BytesIO()
        included = 0
        skipped = []
        seq_width = max(len(str(len(file_ids))), 3)

        def _named(fid, fname, *, sequence, force_extension=None):
            """The archive basename for one file: explicit rename map, then
            naming template, then the original file name."""
            meta = records_meta.get(fid) or {}
            real_ext = (os.path.splitext(fname or '')[1] or '').lstrip('.')
            ext = (force_extension or real_ext or 'bin')
            mapped = rename_map.get(int(fid))
            if mapped:
                result = mapped
                if force_extension and not result.lower().endswith(f'.{ext.lower()}'):
                    result = f'{result}.{ext}'
                return result
            if not naming_template:
                return fname or f'file_{fid}.{ext}'
            stem = os.path.splitext(fname or '')[0] or f'file_{fid}'
            date_value = meta.get('file_date')
            date_str = date_value.strftime('%Y-%m-%d') if hasattr(date_value, 'strftime') \
                else (str(date_value)[:10] if date_value else 'unknown-date')
            result = (naming_template
                      .replace('{name}', stem)
                      .replace('{extension}', ext)
                      .replace('{source}', str(meta.get('source_name') or 'Unknown'))
                      .replace('{date}', date_str)
                      .replace('{id}', str(fid))
                      .replace('{sequence}', str(sequence).zfill(seq_width)))
            if not result.lower().endswith(f'.{ext.lower()}'):
                result = f'{result}.{ext}'
            return result

        with _zipfile.ZipFile(zip_buffer, 'w', _zipfile.ZIP_DEFLATED) as zf:
            used_names = set()

            def _arc_for(file_name, fallback):
                base = secure_filename(file_name or '') or fallback
                candidate, counter = base, 2
                while candidate in used_names:
                    stem, dot, ext = base.partition('.')
                    candidate = f"{stem}_{counter}{dot}{ext}" if dot else f"{base}_{counter}"
                    counter += 1
                used_names.add(candidate)
                return candidate

            manifest_rows = []
            for sequence, fid in enumerate(file_ids, start=1):
                fname = names.get(fid)
                if fname is None:
                    skipped.append((fid, '', 'no such file record'))
                    continue
                if mode == 'originals':
                    from Api.services.original_file import OriginalFileService
                    info = OriginalFileService.describe(fid, include_path=True)
                    source_path = info.get('path') or ''
                    if info.get('available') and source_path and Path(source_path).is_file():
                        arc = _named(fid, fname, sequence=sequence)
                        archive_name = _arc_for(arc, f'file_{fid}')
                        zf.write(source_path, arcname=archive_name)
                        manifest_rows.append((fid, archive_name))
                        included += 1
                    else:
                        skipped.append((fid, fname, info.get('reason') or 'original unavailable'))
                else:
                    text = load_text_content(fid)
                    if text:
                        if isinstance(text, bytes):
                            text = text.decode('utf-8', errors='replace')
                        arc = _named(fid, fname, sequence=sequence, force_extension='txt')
                        archive_name = _arc_for(arc, f'file_{fid}.txt')
                        zf.writestr(archive_name, text)
                        manifest_rows.append((fid, archive_name))
                        included += 1
                    else:
                        skipped.append((fid, fname, 'no extracted text'))

            # The archive says what it is. A manifest is what makes it usable
            # for analysis after the download - the reader does not have to
            # remember which filters produced it, and a script does not have to
            # re-query the database to get the metadata back.
            manifest_lines = [
                "# SYLTHARAE export manifest",
                f"# generated\t{_datetime.now().isoformat(timespec='seconds')}",
                f"# mode\t{mode}",
                f"# requested\t{len(file_ids)}",
                f"# included\t{included}",
                f"# not_included\t{len(skipped)}",
                f"# naming_template\t{naming_template or ''}",
                "file_id\tfile_name\tsource\tdate\tarchive_name\tstatus\treason",
            ]
            for fid in file_ids:
                meta = records_meta.get(fid) or {}
                date_value = meta.get('file_date')
                date_text = date_value.strftime('%Y-%m-%d') if hasattr(date_value, 'strftime') \
                    else (str(date_value)[:10] if date_value else '')
                included_names = [arc for (included_id, arc) in manifest_rows if included_id == fid]
                reason = next((r for (skipped_id, _n, r) in skipped if skipped_id == fid), '')
                manifest_lines.append('\t'.join([
                    str(fid), str(meta.get('file_name') or ''), str(meta.get('source_name') or ''),
                    date_text, included_names[0] if included_names else '',
                    'included' if included_names else 'not included',
                    str(reason).replace('\t', ' ').replace('\n', ' '),
                ]))
            zf.writestr('_manifest.tsv', '\n'.join(manifest_lines) + '\n')

            if skipped:
                lines = [f"# Not included in this {mode} export",
                         "# file_id\tfile_name\treason"]
                lines += [f"{fid}\t{name}\t{reason}" for fid, name, reason in skipped]
                zf.writestr('_not_included.txt', '\n'.join(lines))

        if included == 0:
            return jsonify({
                'success': False,
                'error': 'None of the selected files could be exported',
                'skipped': [{'file_id': fid, 'file_name': name, 'reason': reason}
                            for fid, name, reason in skipped],
            }), 422

        note_disclosure(kind=f'bulk_{mode}_export', scope='selection', unit='file',
                        row_count=included, total=len(file_ids), skipped=len(skipped),
                        format='zip', file_ids=sorted(file_ids))
        stamp = _datetime.now().strftime('%Y%m%d_%H%M%S')
        fallback_name = (f"selected_originals_{stamp}"
                         if mode == 'originals'
                         else f"selected_extracted_text_{stamp}")
        zip_name = _export_basename(requested_name, fallback_name, 'zip')
        zip_buffer.seek(0)
        response = send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=zip_name,
        )
        response.headers['X-Export-Mode'] = mode
        response.headers['X-Export-Requested'] = str(len(file_ids))
        response.headers['X-Export-Included'] = str(included)
        response.headers['X-Export-Skipped'] = str(len(skipped))
        response.headers['X-Export-Named-Template'] = 'true' if naming_template else 'false'
        return response

    except ValueError as validation_error:
        return jsonify({'success': False, 'error': str(validation_error)}), 400
    except Exception as e:
        logger.error(f"Error in bulk export: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


def _selected_file_ids_from_request(max_files=200):
    """Read a bounded selection without ever accepting client-supplied paths."""
    from Api.services.document_intelligence import normalize_file_ids

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get('file_ids', [])
    else:
        raw_ids = request.form.getlist('file_ids')
    return normalize_file_ids(raw_ids, max_files=max_files)


def _export_basename(value, fallback, extension):
    """Return a safe, caller-renamable download filename."""
    base = secure_filename(str(value or '').strip()) or fallback
    suffix = f'.{extension}'
    if not base.lower().endswith(suffix):
        base = f'{base}{suffix}'
    if len(base) > 120:
        base = f'{base[:120 - len(suffix)]}{suffix}'
    return base


@limiter.limit('6 per minute')
@files_bp.route('/api/files/names/export', methods=['POST'])
def export_file_names():
    """Export names from an explicit selected/type/all indexed-file scope."""
    import csv
    from datetime import datetime
    from io import BytesIO, StringIO
    from flask import Response, send_file
    from Api.services.document_intelligence import normalize_file_ids, spreadsheet_safe_text

    try:
        payload = request.get_json(silent=True) or {}
        export_format = str(payload.get('format') or 'csv').lower()
        extension = 'xlsx' if export_format in {'excel', 'xlsx'} else 'csv'
        if export_format not in {'csv', 'excel', 'xlsx'}:
            return jsonify({'success': False, 'error': 'format must be csv or excel'}), 400

        scope = str(payload.get('scope') or '').lower()
        params = []
        where = ''
        requested_file_ids = None
        if scope == 'selected':
            file_ids = normalize_file_ids(payload.get('file_ids', []), max_files=5000)
            requested_file_ids = set(file_ids)
            placeholders = ','.join(['%s'] * len(file_ids))
            where = f'WHERE p.id IN ({placeholders})'
            params.extend(file_ids)
        elif scope == 'type':
            file_type = str(payload.get('file_type') or '').strip()
            if not file_type or len(file_type) > 128:
                return jsonify({'success': False, 'error': 'A valid file_type is required'}), 400
            where = 'WHERE COALESCE(NULLIF(BTRIM(p.file_type), \'\'), \'Unknown\') = %s'
            params.append(file_type)
        elif scope != 'all':
            return jsonify({'success': False, 'error': 'scope must be selected, type, or all'}), 400

        rows = execute_query(
            f"""
            SELECT p.id, p.file_name,
                   COALESCE(NULLIF(BTRIM(p.file_type), ''), 'Unknown') AS file_type
            FROM paths p
            {where}
            ORDER BY LOWER(COALESCE(p.file_name, '')), p.id
            LIMIT 50001
            """,
            tuple(params), fetch='all') or []
        if requested_file_ids is not None:
            found_ids = {int(row[0]) for row in rows}
            missing_ids = sorted(requested_file_ids - found_ids)
            if missing_ids:
                return jsonify({
                    'success': False,
                    'error': 'Some selected file records no longer exist.',
                    'missing_file_ids': missing_ids,
                }), 404
        if len(rows) > 50000:
            return jsonify({
                'success': False,
                'error': 'This filename export exceeds 50,000 rows. Narrow the list to export it synchronously.',
            }), 413
        if not rows:
            return jsonify({'success': False, 'error': 'No indexed filenames were found'}), 404

        columns = ('file_name', 'file_type')
        safe_rows = [
            {
                'file_name': spreadsheet_safe_text(row[1] or ''),
                'file_type': spreadsheet_safe_text(row[2] or 'Unknown'),
            }
            for row in rows
        ]
        note_disclosure(kind='file_names_export',
                        scope=scope if scope != 'type' else f'type:{file_type}',
                        unit='file', row_count=len(safe_rows), format=extension)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        scope_name = 'all_indexed' if scope == 'all' else (
            'selected' if scope == 'selected' else _file_type_label(file_type).lower().replace(' ', '_'))
        fallback_name = f'{scope_name}_filenames_{stamp}'
        filename = _export_basename(payload.get('filename'), fallback_name, extension)

        if extension == 'csv':
            output = StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
            writer.writerow({'file_name': 'File name', 'file_type': 'File type'})
            writer.writerows(safe_rows)
            response = Response(
                output.getvalue().encode('utf-8-sig'),
                mimetype='text/csv; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        elif extension == 'json':
            document = {
                'generated_at': datetime.now().isoformat(timespec='seconds'),
                'scope': scope,
                'row_count': len(safe_rows),
                'columns': [{'key': key, 'label': label}
                            for key, label in zip(column_keys, labels)],
                'rows': safe_rows,
            }
            response = Response(
                json.dumps(document, ensure_ascii=False, indent=2, default=str).encode('utf-8'),
                mimetype='application/json; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        else:
            try:
                from openpyxl import Workbook
            except ImportError:
                return jsonify({'success': False, 'error': 'XLSX export is unavailable on this server'}), 503
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet('Filenames')
            sheet.append(['File name', 'File type'])
            for item in safe_rows:
                sheet.append([item[column] for column in columns])
            buffer = BytesIO()
            workbook.save(buffer)
            buffer.seek(0)
            response = send_file(
                buffer,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=filename,
            )
        response.headers['X-Export-Scope'] = scope
        response.headers['X-Export-Rows'] = str(len(safe_rows))
        return response
    except ValueError as validation_error:
        return jsonify({'success': False, 'error': str(validation_error)}), 400
    except Exception as error:
        logger.error('Filename export failed: %s', error, exc_info=True)
        return client_error(error, subsystem='Api.blueprints.files',
                            success_key='success', status=500)


#: Columns a selected-files details export may include (key → SQL expr + label).
#: Analysis columns (category / category_terms / keywords) are filled after the
#: SQL fetch via document_analysis.analysis_column_values — expr is a placeholder.
_ANALYSIS_EXPORT_KEYS = frozenset({'category', 'category_terms', 'keywords'})
_DETAILS_EXPORT_COLUMNS = (
    ('id', 'Id', 'p.id'),
    ('name', 'File Name', 'p.file_name'),
    ('type', 'Type', 'p.file_type'),
    ('size', 'Size', 'p.file_size'),
    ('source', 'Source', "COALESCE(s.name, 'Unknown')"),
    ('side', 'Side', "COALESCE(si.name, 'Unknown')"),
    ('status', 'Status', 'p.file_status'),
    ('date', 'Date', 'p.file_date'),
    ('path', 'Path', 'p.file_path'),
    ('created', 'Created', 'p.date_creation'),
    ('category', 'Category', 'NULL'),
    ('category_terms', 'Category Terms', 'NULL'),
    ('keywords', 'Keywords', 'NULL'),
)


@limiter.limit('6 per minute')
@files_bp.route('/api/files/details/export', methods=['POST'])
def export_file_details():
    """Export selected (or all indexed) file rows with chosen columns.

    Body: ``{ scope: 'selected'|'all', file_ids?, columns: [...], format,
    filename? }``. Columns default to name/type/size/source/side/date.
    """
    import csv
    from datetime import datetime
    from io import BytesIO, StringIO
    from flask import Response, send_file
    from Api.services.document_intelligence import normalize_file_ids, spreadsheet_safe_text

    try:
        payload = request.get_json(silent=True) or {}
        export_format = str(payload.get('format') or 'csv').lower()
        if export_format in {'excel', 'xlsx'}:
            extension = 'xlsx'
        elif export_format == 'json':
            # A script wants keys, not a header row; the same allowlisted
            # columns, the same scope rules, one JSON document.
            extension = 'json'
        else:
            extension = 'csv'
        if export_format not in {'csv', 'excel', 'xlsx', 'json'}:
            return jsonify({'success': False,
                            'error': 'format must be csv, excel or json'}), 400

        known = {key: (label, expr) for key, label, expr in _DETAILS_EXPORT_COLUMNS}
        requested = payload.get('columns')
        if requested is None:
            column_keys = ['name', 'type', 'size', 'source', 'side', 'date']
        elif isinstance(requested, str):
            column_keys = [k.strip() for k in requested.split(',') if k.strip()]
        elif isinstance(requested, (list, tuple)):
            column_keys = [str(k).strip() for k in requested if str(k).strip()]
        else:
            return jsonify({'success': False, 'error': 'columns must be a list'}), 400
        if not column_keys:
            return jsonify({'success': False, 'error': 'choose at least one column'}), 400
        unknown = [k for k in column_keys if k not in known]
        if unknown:
            return jsonify({'success': False,
                            'error': f"unknown column(s): {', '.join(unknown)}"}), 400
        # Preserve request order, de-dupe.
        seen = set()
        column_keys = [k for k in column_keys if not (k in seen or seen.add(k))]

        need_analysis = any(k in _ANALYSIS_EXPORT_KEYS for k in column_keys)
        # Always pull p.id when enriching analysis columns (may not be exported).
        select_keys = list(column_keys)
        if need_analysis and 'id' not in select_keys:
            select_keys = ['id'] + select_keys

        scope = str(payload.get('scope') or 'selected').lower()
        params = []
        where = ''
        if scope == 'selected':
            file_ids = normalize_file_ids(payload.get('file_ids', []), max_files=5000)
            placeholders = ','.join(['%s'] * len(file_ids))
            where = f'WHERE p.id IN ({placeholders})'
            params.extend(file_ids)
        elif scope != 'all':
            return jsonify({'success': False, 'error': 'scope must be selected or all'}), 400

        select_exprs = ', '.join(known[k][1] for k in select_keys)
        rows = execute_query(
            f"""
            SELECT {select_exprs}
            FROM paths p
            LEFT JOIN hash_contexts hc ON hc.id = p.context_id
            LEFT JOIN sources s ON s.id = hc.source_id
            LEFT JOIN sides si ON si.id = hc.side_id
            {where}
            ORDER BY LOWER(COALESCE(p.file_name, '')), p.id
            LIMIT 50001
            """,
            tuple(params) if params else None, fetch='all') or []
        if len(rows) > 50000:
            return jsonify({
                'success': False,
                'error': 'This details export exceeds 50,000 rows. Narrow the selection.',
            }), 413
        if not rows:
            return jsonify({'success': False, 'error': 'No file records matched'}), 404

        labels = [known[k][0] for k in column_keys]
        analysis_cache = {}
        if need_analysis:
            from Api.services.document_analysis import analysis_column_values

        safe_rows = []
        for row in rows:
            item = {}
            for index, key in enumerate(select_keys):
                value = row[index]
                if value is None:
                    item[key] = ''
                elif hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
                elif isinstance(value, str):
                    item[key] = spreadsheet_safe_text(value)
                else:
                    item[key] = value
            if need_analysis:
                try:
                    fid = int(item.get('id') or 0)
                except (TypeError, ValueError):
                    fid = 0
                if fid and fid not in analysis_cache:
                    analysis_cache[fid] = analysis_column_values(fid)
                enriched = analysis_cache.get(fid) or {}
                for ak in _ANALYSIS_EXPORT_KEYS:
                    if ak in column_keys:
                        item[ak] = spreadsheet_safe_text(enriched.get(ak) or '')
            # Drop helper id when the caller did not ask for it.
            export_item = {k: item.get(k, '') for k in column_keys}
            safe_rows.append(export_item)

        note_disclosure(kind='file_details_export', scope=scope, unit='file',
                        row_count=len(safe_rows), format=extension)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        fallback_name = f"{'selected' if scope == 'selected' else 'all_indexed'}_file_details_{stamp}"
        filename = _export_basename(payload.get('filename'), fallback_name, extension)

        if extension == 'csv':
            output = StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=column_keys, extrasaction='ignore')
            writer.writerow(dict(zip(column_keys, labels)))
            writer.writerows(safe_rows)
            response = Response(
                output.getvalue().encode('utf-8-sig'),
                mimetype='text/csv; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        elif extension == 'json':
            document = {
                'generated_at': datetime.now().isoformat(timespec='seconds'),
                'scope': scope,
                'row_count': len(safe_rows),
                'columns': [{'key': key, 'label': label}
                            for key, label in zip(column_keys, labels)],
                'rows': safe_rows,
            }
            response = Response(
                json.dumps(document, ensure_ascii=False, indent=2, default=str).encode('utf-8'),
                mimetype='application/json; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        else:
            try:
                from openpyxl import Workbook
            except ImportError:
                return jsonify({'success': False, 'error': 'XLSX export is unavailable on this server'}), 503
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet('Files')
            sheet.append(labels)
            for item in safe_rows:
                sheet.append([item[key] for key in column_keys])
            buffer = BytesIO()
            workbook.save(buffer)
            buffer.seek(0)
            response = send_file(
                buffer,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=filename,
            )
        response.headers['X-Export-Scope'] = scope
        response.headers['X-Export-Rows'] = str(len(safe_rows))
        return response
    except ValueError as validation_error:
        return jsonify({'success': False, 'error': str(validation_error)}), 400
    except Exception as error:
        logger.error('File details export failed: %s', error, exc_info=True)
        return client_error(error, subsystem='Api.blueprints.files',
                            success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/files/first-pages/export', methods=['POST'])
def export_selected_first_pages():
    """Export the first-page/preview text for selected indexed documents.

    ``format`` is ``txt`` or ``docx``. The browser chooses the download
    destination; no server-side client folder is exposed or inferred.
    """
    from datetime import datetime
    from io import BytesIO
    from flask import Response, send_file
    from Api.services.document_intelligence import first_page_text

    try:
        file_ids = _selected_file_ids_from_request(max_files=200)
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            export_format = str(payload.get('format') or 'txt').lower()
            requested_name = payload.get('filename')
        else:
            export_format = str(request.form.get('format') or 'txt').lower()
            requested_name = request.form.get('filename')
        if export_format not in {'txt', 'docx'}:
            return jsonify({'success': False, 'error': 'format must be txt or docx'}), 400

        placeholders = ','.join(['%s'] * len(file_ids))
        rows = execute_query(
            f"SELECT id, file_name, file_path FROM paths WHERE id IN ({placeholders})",
            tuple(file_ids), fetch='all') or []
        records = {int(row[0]): (row[1] or f'file_{row[0]}', row[2] or '') for row in rows}
        missing_ids = sorted(set(file_ids) - set(records))
        if missing_ids:
            return jsonify({
                'success': False,
                'error': 'Some selected file records no longer exist.',
                'missing_file_ids': missing_ids,
            }), 404
        if not records:
            return jsonify({'success': False, 'error': 'No selected files were found'}), 404

        sections = []
        for file_id in file_ids:
            file_name, file_path = records[file_id]
            try:
                extracted = load_text_content(file_id)
                preview = first_page_text(
                    file_name=file_name, file_path=file_path,
                    extracted_text=extracted)
                text = preview.get('text') or '[No extracted text available]'
                method = preview.get('method') or 'unavailable'
            except Exception as extraction_error:
                logger.warning('First-page extraction failed for file %s: %s',
                               file_id, extraction_error)
                text, method = '[First-page extraction failed]', 'unavailable'
            sections.append({
                'file_id': file_id, 'file_name': file_name,
                'text': text, 'method': method,
            })

        note_disclosure(kind='first_pages_export', scope='selection', unit='file',
                        row_count=len(sections), format=export_format,
                        unavailable=sum(s.get('method') == 'unavailable' for s in sections),
                        file_ids=sorted(file_ids))
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        fallback = f'first_pages_{stamp}'
        if export_format == 'txt':
            from flask import Response
            body = '\n\n'.join(
                f"===== {item['file_name']} (ID {item['file_id']}) =====\n"
                f"Preview method: {item['method']}\n\n{item['text']}"
                for item in sections
            )
            filename = _export_basename(requested_name, fallback, 'txt')
            response = Response(body.encode('utf-8'), mimetype='text/plain; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            response.headers['X-Export-Documents'] = str(len(sections))
            response.headers['X-Export-Unavailable'] = str(
                sum(section.get('method') == 'unavailable' for section in sections))
            return response

        try:
            from docx import Document
        except ImportError:
            return jsonify({'success': False, 'error': 'DOCX export is unavailable on this server'}), 503
        document = Document()
        document.add_heading('First-page text export', level=1)
        document.add_paragraph(f'Exported {len(sections)} selected document(s) at {datetime.now().isoformat(timespec="seconds")}')
        for index, item in enumerate(sections):
            document.add_heading(f"{item['file_name']} (ID {item['file_id']})", level=2)
            document.add_paragraph(f"Preview method: {item['method']}")
            document.add_paragraph(item['text'])
            if index < len(sections) - 1:
                document.add_page_break()
        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)
        filename = _export_basename(requested_name, fallback, 'docx')
        response = send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename,
        )
        response.headers['X-Export-Documents'] = str(len(sections))
        response.headers['X-Export-Unavailable'] = str(
            sum(section.get('method') == 'unavailable' for section in sections))
        return response
    except ValueError as validation_error:
        return jsonify({'success': False, 'error': str(validation_error)}), 400
    except Exception as e:
        logger.error('First-page export failed: %s', e, exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/files/excerpt/export', methods=['POST'])
def export_excerpt():
    """Export a small provenance record for one piece of text the Content
    Viewer already has on screen - either a manual text selection, or the
    context a search already matched on.

    Document identity (name, source, side) is never taken from the client -
    it is looked up here from the same ``paths``/``hash_contexts`` row every
    other file view reads from, so an export can never claim to be "from"
    a document it did not actually come from. The excerpt text itself *is*
    accepted from the client because it is a substring the client is
    already displaying (server-provided content the operator selected, or a
    match line the server's own search already returned) - this endpoint
    does not run a second search or re-extract anything.

    No page or section number is invented: this application does not track
    page/section boundaries for extracted text, so none is included.
    """
    from datetime import datetime
    from io import BytesIO
    from flask import Response, send_file

    try:
        payload = request.get_json(silent=True) or {}

        try:
            file_id = int(payload.get('file_id'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'file_id is required and must be numeric'}), 400

        kind = str(payload.get('kind') or 'selection').strip().lower()
        if kind not in {'selection', 'search_match'}:
            return jsonify({'success': False, 'error': "kind must be 'selection' or 'search_match'"}), 400

        export_format = str(payload.get('format') or 'txt').lower()
        if export_format not in {'txt', 'docx'}:
            return jsonify({'success': False, 'error': 'format must be txt or docx'}), 400

        text = str(payload.get('text') or '').strip()
        if not text:
            return jsonify({'success': False, 'error': 'text is required and cannot be empty'}), 400
        if len(text) > 20000:
            return jsonify({'success': False, 'error': 'text exceeds the 20,000 character limit for one excerpt export'}), 400

        query = str(payload.get('query') or '').strip()
        if kind == 'search_match' and not query:
            return jsonify({'success': False, 'error': "query is required when kind is 'search_match'"}), 400

        row = execute_query(
            """
            SELECT p.id, p.file_name, COALESCE(s.name, 'Unknown') AS source_name,
                   COALESCE(si.name, 'Unknown') AS side_name
            FROM paths p
            LEFT JOIN hash_contexts hc ON p.context_id = hc.id
            LEFT JOIN sources s ON hc.source_id = s.id
            LEFT JOIN sides si ON hc.side_id = si.id
            WHERE p.id = %s
            """,
            (file_id,), fetch='one')
        if not row:
            return jsonify({'success': False, 'error': 'No stored document with that id.'}), 404
        _, file_name, source_name, side_name = row
        file_name = file_name or f'file_{file_id}'

        exported_at = datetime.now().isoformat(timespec='seconds')
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        label = 'Search Match' if kind == 'search_match' else 'Selected Text'
        fallback = f"{'search_match' if kind == 'search_match' else 'selected_text'}_{file_id}_{stamp}"
        requested_name = payload.get('filename')

        fields = [
            ('Source Document', file_name),
            ('File ID', str(file_id)),
            ('Source', source_name or 'Unknown'),
            ('Side', side_name or 'Unknown'),
            ('Exported', exported_at),
        ]
        if kind == 'search_match':
            fields.append(('Search Query', query))
        # Page/section are deliberately never added: this application does
        # not record page or section boundaries for extracted text.

        note_disclosure(kind=f'excerpt_{kind}', scope=f'file:{file_id}', unit='excerpt',
                        row_count=1, format=export_format, file_ids=[file_id])
        if export_format == 'txt':
            lines = [f'{label} Export', '=' * (len(label) + 7), '']
            lines += [f'{k}: {v}' for k, v in fields]
            lines += ['', f"{'Matched Text' if kind == 'search_match' else 'Selected Text'}:", '-' * 40, text]
            body = '\n'.join(lines)
            filename = _export_basename(requested_name, fallback, 'txt')
            response = Response(body.encode('utf-8'), mimetype='text/plain; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            response.headers['X-Export-Kind'] = kind
            return response

        try:
            from docx import Document
        except ImportError:
            return jsonify({'success': False, 'error': 'DOCX export is unavailable on this server'}), 503
        document = Document()
        document.add_heading(f'{label} Export', level=1)
        for k, v in fields:
            paragraph = document.add_paragraph()
            run = paragraph.add_run(f'{k}: ')
            run.bold = True
            paragraph.add_run(str(v))
        document.add_heading('Matched Text' if kind == 'search_match' else 'Selected Text', level=2)
        document.add_paragraph(text)
        buffer = BytesIO()
        document.save(buffer)
        buffer.seek(0)
        filename = _export_basename(requested_name, fallback, 'docx')
        response = send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename,
        )
        response.headers['X-Export-Kind'] = kind
        return response
    except Exception as e:
        logger.error('Excerpt export failed: %s', e, exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/files/extract-contacts/export', methods=['POST'])
def export_selected_contacts():
    """Extract unique email addresses and URLs from selected indexed files."""
    import csv
    from datetime import datetime
    from io import BytesIO, StringIO
    from flask import Response, send_file
    from Api.services.document_intelligence import (
        extract_contact_occurrences, spreadsheet_safe_text,
    )

    try:
        file_ids = _selected_file_ids_from_request(max_files=200)
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            export_format = str(payload.get('format') or 'csv').lower()
            requested_name = payload.get('filename')
            dedupe_raw = payload.get('dedupe')
        else:
            export_format = str(request.form.get('format') or 'csv').lower()
            requested_name = request.form.get('filename')
            dedupe_raw = request.form.get('dedupe')
        if export_format not in {'csv', 'xlsx'}:
            return jsonify({'success': False, 'error': 'format must be csv or xlsx'}), 400
        dedupe = str(dedupe_raw).strip().lower() in {'1', 'true', 'yes', 'on'} if dedupe_raw is not None else False

        placeholders = ','.join(['%s'] * len(file_ids))
        rows = execute_query(
            f"SELECT id, file_name FROM paths WHERE id IN ({placeholders})",
            tuple(file_ids), fetch='all') or []
        names = {int(row[0]): (row[1] or f'file_{row[0]}') for row in rows}
        missing_ids = sorted(set(file_ids) - set(names))
        if missing_ids:
            return jsonify({
                'success': False,
                'error': 'Some selected file records no longer exist.',
                'missing_file_ids': missing_ids,
            }), 404
        if not names:
            return jsonify({'success': False, 'error': 'No selected files were found'}), 404

        occurrences = []
        unavailable_ids = []
        for file_id in file_ids:
            file_name = names[file_id]
            try:
                content = load_text_content(file_id)
                if content is None:
                    unavailable_ids.append(file_id)
                    continue
                occurrences.extend(extract_contact_occurrences(
                    content, file_id=file_id, file_name=file_name))
            except Exception as extraction_error:
                logger.warning('Email/link extraction failed for file %s: %s',
                               file_id, extraction_error)
                unavailable_ids.append(file_id)
        if unavailable_ids:
            return jsonify({
                'success': False,
                'error': 'Some selected documents could not be read; no partial export was created.',
                'unavailable_file_ids': unavailable_ids,
            }), 422

        if dedupe:
            # Collapse to one row per unique (kind, value) across every
            # selected file, instead of one row per file the value appears
            # in. Nothing here re-derives the extraction itself - it only
            # groups the same rows the non-deduped export would produce, so
            # the numbers still trace back to the same per-file occurrences.
            columns = ('kind', 'value', 'total_occurrences', 'file_count', 'file_names', 'context')
            grouped = {}
            order = []
            for item in occurrences:
                key = (item['kind'], item['value'].casefold())
                entry = grouped.get(key)
                if entry is None:
                    entry = {
                        'kind': item['kind'], 'value': item['value'],
                        'total_occurrences': 0, 'file_names': [], 'context': item.get('context', ''),
                    }
                    grouped[key] = entry
                    order.append(key)
                entry['total_occurrences'] += item.get('occurrences', 0) or 0
                if item['file_name'] not in entry['file_names']:
                    entry['file_names'].append(item['file_name'])
            merged = []
            for key in order:
                entry = grouped[key]
                merged.append({
                    'kind': entry['kind'], 'value': entry['value'],
                    'total_occurrences': entry['total_occurrences'],
                    'file_count': len(entry['file_names']),
                    'file_names': '; '.join(entry['file_names']),
                    'context': entry['context'],
                })
            safe_occurrences = [
                {
                    column: spreadsheet_safe_text(item.get(column, ''))
                    if isinstance(item.get(column, ''), str) else item.get(column, '')
                    for column in columns
                }
                for item in merged
            ]
        else:
            columns = ('file_id', 'file_name', 'kind', 'value', 'occurrences', 'location', 'first_line', 'context')
            safe_occurrences = [
                {
                    column: spreadsheet_safe_text(item.get(column, ''))
                    if isinstance(item.get(column, ''), str) else item.get(column, '')
                    for column in columns
                }
                for item in occurrences
            ]
        note_disclosure(kind='contacts_export', scope='selection', unit='occurrence',
                        row_count=len(safe_occurrences), documents=len(file_ids),
                        format=export_format, deduplicated=bool(dedupe),
                        file_ids=sorted(file_ids))
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        fallback = ('unique_emails_and_links_' if dedupe else 'extracted_emails_and_links_') + stamp
        if export_format == 'csv':
            output = StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(safe_occurrences)
            filename = _export_basename(requested_name, fallback, 'csv')
            response = Response(output.getvalue().encode('utf-8-sig'), mimetype='text/csv; charset=utf-8')
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            response.headers['X-Export-Entities'] = str(len(safe_occurrences))
            response.headers['X-Export-Documents'] = str(len(file_ids))
            response.headers['X-Export-Empty'] = 'true' if not occurrences else 'false'
            response.headers['X-Export-Dedupe'] = 'true' if dedupe else 'false'
            return response

        try:
            from openpyxl import Workbook
        except ImportError:
            return jsonify({'success': False, 'error': 'XLSX export is unavailable on this server'}), 503
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet('Emails and links')
        sheet.append(list(columns))
        for item in safe_occurrences:
            sheet.append([item.get(column, '') for column in columns])
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        filename = _export_basename(requested_name, fallback, 'xlsx')
        response = send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
        response.headers['X-Export-Entities'] = str(len(safe_occurrences))
        response.headers['X-Export-Documents'] = str(len(file_ids))
        response.headers['X-Export-Empty'] = 'true' if not occurrences else 'false'
        response.headers['X-Export-Dedupe'] = 'true' if dedupe else 'false'
        return response
    except ValueError as validation_error:
        return jsonify({'success': False, 'error': str(validation_error)}), 400
    except Exception as e:
        logger.error('Email/link export failed: %s', e, exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@limiter.limit('10 per minute')
@files_bp.route('/api/search/group-similar', methods=['POST'])
def group_similar_search_results():
    """Group the explicitly submitted current-page IDs by extracted-text similarity."""
    from Api.services.document_intelligence import group_similar_documents, normalize_file_ids

    try:
        payload = request.get_json(silent=True) or {}
        file_ids = normalize_file_ids(payload.get('file_ids', []), max_files=100)
        try:
            threshold = float(payload.get('threshold', 0.32))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'threshold must be numeric'}), 400
        placeholders = ','.join(['%s'] * len(file_ids))
        rows = execute_query(
            f"SELECT id, file_name FROM paths WHERE id IN ({placeholders})",
            tuple(file_ids), fetch='all') or []
        names = {int(row[0]): (row[1] or f'file_{row[0]}') for row in rows}
        documents = []
        for file_id in file_ids:
            if file_id not in names:
                continue
            documents.append({
                'id': file_id,
                'file_name': names[file_id],
                'content': load_text_content(file_id) or '',
            })
        groups = group_similar_documents(documents, threshold=threshold)
        return jsonify({
            'success': True,
            'scope': 'submitted_page',
            'document_count': len(documents),
            'groups': groups,
        })
    except ValueError as validation_error:
        return jsonify({'success': False, 'error': str(validation_error)}), 400
    except Exception as e:
        logger.error('Similarity grouping failed: %s', e, exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/file/<int:file_id>/content/page')
def file_content_page(file_id):
    """Get specific content page with pagination info"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 5000, type=int)
    per_page = min(per_page, 10000)  # Max 10000 characters per page
    
    try:
        full_content = load_text_content(file_id)
        if not full_content:
            return jsonify({'error': 'No content found'}), 404
        
        # Calculate pagination
        total_chars = len(full_content)
        total_pages = (total_chars + per_page - 1) // per_page
        
        # Get content for requested page
        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total_chars)
        content = full_content[start_idx:end_idx]
        
        return jsonify({
            'content': content,
            'page': page,
            'total_pages': total_pages,
            'per_page': per_page,
            'total_chars': total_chars,
            'start_char': start_idx + 1,
            'end_char': end_idx,
            'has_previous': page > 1,
            'has_next': page < total_pages
        })
    except Exception as e:
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/file/<int:file_id>/analysis', methods=['GET'])
def file_document_analysis(file_id):
    """Structured categories / category terms / keywords for one document."""
    try:
        from Api.services.document_analysis import build_document_analysis
        analysis = build_document_analysis(file_id)
        if not analysis:
            return jsonify({'success': False, 'error': 'File not found'}), 404
        return jsonify({'success': True, 'analysis': analysis})
    except Exception as e:
        logger.error('Document analysis failed for file %s: %s', file_id, e, exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@limiter.limit('12 per minute')
@files_bp.route('/api/files/analysis', methods=['POST'])
def files_document_analysis_batch():
    """Aggregate document analysis for a selection of files."""
    try:
        from Api.services.document_analysis import BATCH_FILE_CAP, build_selection_analysis
        from Api.services.document_intelligence import normalize_file_ids
        payload = request.get_json(silent=True) or {}
        file_ids = normalize_file_ids(payload.get('file_ids', []), max_files=BATCH_FILE_CAP)
        if not file_ids:
            return jsonify({'success': False, 'error': 'Select one or more files'}), 400
        result = build_selection_analysis(file_ids)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error('Batch document analysis failed: %s', e, exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/file/<int:file_id>/chart-data')
def file_chart_data(file_id):
    """Get chart data for a specific file"""
    try:
        # Get enhanced statistics
        enhanced_stats = get_enhanced_content_stats(file_id)
        
        # Get word frequencies
        word_frequencies = get_word_frequencies(file_id, limit=50)
        
        # Get keyword frequencies
        keyword_frequencies = get_keyword_frequencies(file_id, limit=50)
        
        # Get repeated elements (words in multiple categories)
        repeated_elements = get_repeated_elements(file_id, limit=50)
        
        # Get classification data (categories)
        classification_data = select_classification(file_id)
        percentages = compute_percentage(classification_data)
        
        # Format categories data for charts
        categories_data = []
        if percentages:
            for category_name, data in percentages.items():
                categories_data.append({
                    'name': category_name,
                    'count': data.get('count', 0),
                    'percentage': data.get('percentage', 0)
                })
            # Sort by count descending
            categories_data.sort(key=lambda x: x['count'], reverse=True)
        
        # Format words data for charts
        words_data = []
        for word, count in word_frequencies:
            words_data.append({
                'name': word,
                'count': count
            })
        
        # Format keywords data for charts
        keywords_data = []
        for keyword, count in keyword_frequencies:
            keywords_data.append({
                'name': keyword,
                'count': count
            })
        
        # Get file name (optimized - single query)
        file_name_result = execute_query("SELECT file_name FROM paths WHERE id = %s", (file_id,), fetch="one")
        file_name = file_name_result[0] if file_name_result and file_name_result[0] else 'Unknown'
        
        # Format data for charts
        from Api.services.document_analysis import build_document_analysis
        analysis = build_document_analysis(file_id)

        chart_data = {
            'content_stats': {
                'words': enhanced_stats.get('words', 0),
                'sentences': enhanced_stats.get('sentences', 0),
                'paragraphs': enhanced_stats.get('paragraphs', 0),
                'characters': enhanced_stats.get('characters', 0)
            },
            'categories': categories_data,
            'words': words_data,
            'keywords': keywords_data,
            'repeated_elements': repeated_elements,
            'classification_percentages': percentages,
            'top_categories': enhanced_stats.get('top_categories', []),
            'file_info': {
                'id': file_id,
                'name': file_name
            },
            'analysis': analysis,
        }
        
        return jsonify({
            'success': True,
            'data': chart_data
        })
        
    except Exception as e:
        logger.error(f"Error getting chart data: {e}")
        return jsonify({
            'success': False,
            'error': client_safe_message(e, subsystem='Api.routes.files')
        }), 500


@files_bp.route('/file/<int:file_id>/search')
def file_search_all_pages(file_id):
    """Locate every positive literal term/phrase in a file's full text.

    The shared query parser supplies literal, escaped alternatives, so regex
    metacharacters never become executable patterns and exclusions are not
    treated as hits. This endpoint reports absolute character offsets plus a
    1-based line number for precise in-document navigation.
    """
    from Api.services.search_service import SearchService

    query = request.args.get('q', '').strip()
    case_sensitive = request.args.get('case_sensitive', 'false').lower() == 'true'
    whole_word = request.args.get('whole_word', 'false').lower() == 'true'

    if not query:
        return jsonify({
            'query': '',
            'total_matches': 0,
            'total_pages': 0,
            'matches_by_page': {},
            'matches': [],
            'search_options': {
                'case_sensitive': case_sensitive,
                'whole_word': whole_word
            }
        }), 200

    try:
        full_content = load_text_content(file_id)
        if not full_content:
            return jsonify({'error': 'No content found'}), 404

        # Only positive terms/phrases are navigable hits. The same matcher
        # configuration used by search-result annotations preserves quotes,
        # case-sensitivity and whole-word semantics here.
        patterns = SearchService._query_match_patterns(
            query, case_sensitive=case_sensitive, whole_word=whole_word)
        if not patterns:
            return jsonify({
                'query': query,
                'total_matches': 0,
                'total_pages': 0,
                'matches_by_page': {},
                'matches': [],
                'search_options': {
                    'case_sensitive': case_sensitive,
                    'whole_word': whole_word,
                },
            })
        alternatives = '|'.join(
            pattern.pattern for _value, pattern in
            sorted(patterns, key=lambda item: len(item[0]), reverse=True)
        )
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(alternatives, flags)

        # Precompute line-start offsets so each match can report its line.
        line_starts = [0]
        for i, ch in enumerate(full_content):
            if ch == '\n':
                line_starts.append(i + 1)

        import bisect

        def line_of(pos):
            return bisect.bisect_right(line_starts, pos)

        # Find all matches
        matches = []
        for match in regex.finditer(full_content):
            matches.append({
                'text': match.group(),
                'start': match.start(),
                'end': match.end(),
                'length': match.end() - match.start(),
                'line': line_of(match.start())
            })

        # Group matches by page. The page size mirrors the viewer's slicing
        # so a match's page number maps 1:1 onto the detail view's pages
        # (the client passes its per_page; default matches the server default).
        try:
            per_page = min(max(int(request.args.get('per_page', 5000)), 1000), 100000)
        except (TypeError, ValueError):
            per_page = 5000
        total_chars = len(full_content)
        total_pages = (total_chars + per_page - 1) // per_page

        page_matches = {}
        for match in matches:
            page_num = (match['start'] // per_page) + 1
            if page_num not in page_matches:
                page_matches[page_num] = []

            # Adjust match position relative to page start
            page_start = (page_num - 1) * per_page
            page_matches[page_num].append({
                'text': match['text'],
                'start': match['start'] - page_start,
                'end': match['end'] - page_start,
                'length': match['length'],
                'global_start': match['start'],
                'global_end': match['end'],
                'line': match['line']
            })

        return jsonify({
            'query': query,
            'total_matches': len(matches),
            'total_pages': total_pages,
            'matches_by_page': page_matches,
            # Absolute offsets for in-pane navigation. Keep both ``start`` and
            # ``global_start`` so older clients and the review pane agree.
            'matches': [
                {
                    'text': match['text'],
                    'start': match['start'],
                    'end': match['end'],
                    'length': match['length'],
                    'line': match['line'],
                    'global_start': match['start'],
                    'global_end': match['end'],
                }
                for match in matches[:1000]
            ],
            'total_chars': total_chars,
            'search_options': {
                'case_sensitive': case_sensitive,
                'whole_word': whole_word
            }
        })

    except Exception as e:
        logger.error(f"File search error: {e}")
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/file/<int:file_id>/full-content')
def file_full_content(file_id):
    """Display full content in a dedicated page with pagination"""
    try:
        # Get file information
        file_info = get_file(file_id)
        if not file_info:
            flash("File not found", "error")
            return redirect(url_for('files.files_list'))
        
        # Get pagination parameters - allow larger pages for better readability
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50000, type=int)  # Increased default for better readability
        per_page = min(per_page, 100000)  # Increased max for better content display
        
        # Get search parameters ("?q=" alias, same as the detail page)
        search_query = request.args.get('search', '') or request.args.get('q', '')
        case_sensitive = request.args.get('case_sensitive', 'false').lower() == 'true'
        whole_word = request.args.get('whole_word', 'false').lower() == 'true'
        
        # Load full content
        full_content = load_text_content(file_id)
        if not full_content:
            flash("No content found", "error")
            return redirect(url_for('files.files_list'))
        
        # Ensure content is a string
        if isinstance(full_content, bytes):
            try:
                full_content = full_content.decode('utf-8', errors='replace')
            except Exception as decode_error:
                logger.error(f"Failed to decode content bytes for file_id={file_id}: {decode_error}")
                flash("Content found but could not be decoded properly. File may need reprocessing.", "warning")
                full_content = ''
        
        # Calculate pagination
        total_chars = len(full_content)
        total_pages = (total_chars + per_page - 1) // per_page if total_chars > 0 else 1
        
        # Get content for current page (or full content if page size is large enough)
        if total_chars <= per_page:
            # Display full content if it fits in one page
            content = full_content
            start_idx = 0
            end_idx = total_chars
        else:
            start_idx = (page - 1) * per_page
            end_idx = min(start_idx + per_page, total_chars)
            content = full_content[start_idx:end_idx]
        
        content_stats = get_content_stats(file_id)
        
        # Ensure all variables are properly defined (never None) for JSON serialization
        if content is None:
            content = ''
        if total_chars is None:
            total_chars = 0
        if total_pages is None:
            total_pages = 0
        if search_query is None:
            search_query = ''
        if start_idx is None:
            start_idx = 0
        if end_idx is None:
            end_idx = 0
        
        # Convert file_info dict to tuple-like structure for template compatibility
        # get_file() returns a dict, but template expects tuple-like access (file[0], file[1], etc.)
        from datetime import datetime, date
        if isinstance(file_info, dict):
            # Get date values and convert strings to datetime/date objects if needed
            file_date = file_info.get('file_date', None)
            date_creation = file_info.get('date_creation', None)
            
            # Convert string dates to datetime/date objects
            if file_date and isinstance(file_date, str):
                try:
                    if len(file_date) == 10:  # YYYY-MM-DD format
                        file_date = datetime.strptime(file_date, '%Y-%m-%d').date()
                    else:
                        file_date = datetime.fromisoformat(file_date.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass  # Keep as string if parsing fails
            
            if date_creation and isinstance(date_creation, str):
                try:
                    date_creation = datetime.fromisoformat(date_creation.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass  # Keep as string if parsing fails
            
            # Convert dict to tuple in the same order as file_detail route
            file_info = (
                file_info.get('id', 0),
                file_info.get('file_name', ''),
                file_info.get('file_path', ''),
                file_info.get('file_size', 0),
                file_info.get('file_type', ''),
                file_info.get('file_status', ''),
                file_date,
                date_creation,
                file_info.get('hash_id', None),
                file_info.get('source_id', None),
                file_info.get('side_id', None),
                file_info.get('source_name', 'Unknown'),
                file_info.get('side_name', 'Unknown')
            )
        elif not isinstance(file_info, (tuple, list)):
            logger.error(f"file_info is not a tuple/list/dict: {type(file_info)}")
            flash("Invalid file data", "error")
            return redirect(url_for('files.files_list'))
        
        # Analyst (manual) categories for this file - same read source as the
        # detail page so both content surfaces show identical state
        # (FR-1.4: analyst namespace only, smart taxonomy untouched).
        analyst_categories = []
        try:
            from Api.services.analyst_categories import AnalystCategoryService
            analyst_categories = AnalystCategoryService.categories_for_files([file_id]).get(file_id, [])
        except Exception as analyst_err:  # never break the reader page
            logger.warning(f"Could not load analyst categories for file {file_id}: {analyst_err}")

        # Same Previous/Next control, same browsed set - the reader is a
        # second view of the file, not a different browsing session.
        nav = navigation_for(file_id, request.args, endpoint='files.file_full_content')

        response = make_response(render_template('file/full_content.html',
                             nav=nav,
                             file=file_info, 
                             content=content,
                             content_stats=content_stats,
                             current_page=page,
                             total_pages=total_pages,
                             per_page=per_page,
                             total_chars=total_chars,
                             start_char=start_idx + 1,
                             end_char=end_idx,
                             search_query=search_query,
                             case_sensitive=case_sensitive,
                             whole_word=whole_word,
                             analyst_categories=analyst_categories))
        # `?embed=1`: this render is meant to sit inside the React app's
        # FullDocumentViewer iframe (same origin via the Vite dev proxy /
        # reverse proxy in production). The app-wide default is
        # X-Frame-Options: DENY / frame-ancestors 'none' (see
        # apps/web/app.py add_security_headers) so framing must be
        # deliberately opted into here, same-origin only - exactly the
        # precedent Api/services/original_file.py already sets for the
        # original-file viewer.
        if request.args.get('embed') == '1':
            response.headers['X-Frame-Options'] = 'SAMEORIGIN'
            response.headers['Content-Security-Policy'] = "frame-ancestors 'self'"
        return response
    except Exception as e:
        logger.error(f"Error loading full content: {e}", exc_info=True)
        flash(f"Error loading content: {str(e)}", "error")
        return redirect(url_for('files.files_list'))


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/file/serve', methods=['GET'])
def serve_file():
    """
    Serve file content (especially images) by file path
    Security: Only serves files that exist in the database
    """
    from flask import send_file, abort
    import mimetypes
    
    file_path = request.args.get('path')
    if not file_path:
        return jsonify({'error': 'File path not provided'}), 400
    
    try:
        # Security check: Verify file exists in database
        file_check = execute_query("""
            SELECT id, file_path, file_type FROM paths WHERE file_path = %s
        """, (file_path,), fetch="one")
        
        if not file_check:
            logger.warning(f"File not found in database: {file_path}")
            return jsonify({'error': 'File not found'}), 404
        
        # Check if file exists on filesystem
        path_obj = Path(file_path)
        if not path_obj.exists():
            logger.warning(f"File does not exist on filesystem: {file_path}")
            return jsonify({'error': 'File not found on server'}), 404
        
        # Security: Basic validation - ensure it's an absolute path
        # Note: We trust the database to have valid paths
        # Additional security could be added here if needed
        if not path_obj.is_absolute():
            logger.warning(f"Relative path provided: {file_path}")
            return jsonify({'error': 'Invalid file path'}), 403
        
        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(str(path_obj))
        if not mime_type:
            # Default to image if extension suggests it
            if path_obj.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg']:
                mime_type = 'image/jpeg'
            else:
                mime_type = 'application/octet-stream'
        
        # Send file with appropriate headers
        return send_file(
            str(path_obj),
            mimetype=mime_type,
            as_attachment=False,
            download_name=path_obj.name
        )
        
    except Exception as e:
        logger.error(f"Error serving file {file_path}: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/file/<int:file_id>/serve', methods=['GET'])
def serve_file_by_id(file_id):
    """
    Serve file content by file ID (especially images)
    """
    from flask import send_file
    import mimetypes
    
    try:
        # Get file info from database
        file_info = execute_query("""
            SELECT file_path, file_type FROM paths WHERE id = %s
        """, (file_id,), fetch="one")
        
        if not file_info:
            return jsonify({'error': 'File not found'}), 404
        
        file_path, file_type = file_info
        
        # Check if file exists on filesystem
        path_obj = Path(file_path)
        if not path_obj.exists():
            logger.warning(f"File does not exist on filesystem: {file_path}")
            return jsonify({'error': 'File not found on server'}), 404
        
        # Security: Basic validation - ensure it's an absolute path
        # Note: We trust the database to have valid paths
        if not path_obj.is_absolute():
            logger.warning(f"Relative path provided: {file_path}")
            return jsonify({'error': 'Invalid file path'}), 403
        
        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(str(path_obj))
        if not mime_type:
            if path_obj.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg']:
                mime_type = 'image/jpeg'
            else:
                mime_type = 'application/octet-stream'
        
        # Send file
        return send_file(
            str(path_obj),
            mimetype=mime_type,
            as_attachment=False,
            download_name=path_obj.name
        )
        
    except Exception as e:
        logger.error(f"Error serving file {file_id}: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/file/<int:file_id>/original', methods=['GET'])
def original_file_info(file_id):
    """Describe the ORIGINAL file a stored object was extracted from.

    Everything the comparison viewer needs: is the source still on disk, what
    kind of viewer fits it, and the URLs to show or download it. Always 200 for
    an existing row - a source file that has disappeared is a state to report,
    not an error, because the extracted text it produced is still there.
    """
    from Api.services.original_file import OriginalFileService

    try:
        info = OriginalFileService.describe(file_id)
    except Exception as e:
        logger.error(f"Error describing original file {file_id}: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)

    if not info.get('available') and info.get('reason') == 'not-found':
        return jsonify({'success': False, 'error': 'File not found'}), 404
    return jsonify({'success': True, 'original': info})


@limiter.limit(INTERACTIVE_READ_LIMIT)
@files_bp.route('/api/file/<int:file_id>/original/content', methods=['GET'])
def original_file_content(file_id):
    """Serve the original file's bytes for display or download.

    The path comes from the database row, never from the request. Disposition
    and content type are decided by the service: only formats the browser
    renders without script go inline (see Api/services/original_file.py).
    """
    from Api.services.original_file import OriginalFileService

    download = request.args.get('download') in ('1', 'true', 'yes', 'on')
    save_as_name = (request.args.get('filename') or '').strip() or None
    try:
        # Recorded only when the response is an attachment (download=1) -
        note_disclosure(kind='original_file', scope=f'file:{file_id}', unit='file',
                        row_count=1, file_ids=[file_id])
        return OriginalFileService.content_response(
            file_id, download=download, filename=save_as_name)
    except FileNotFoundError as missing:
        reason = str(missing)
        status = 404
        message = {
            'not-found': 'No stored object with that id.',
            'missing-on-disk': 'The source file is no longer at the recorded location.',
            'no-path': 'This object has no source path recorded.',
            'relative-path': 'The recorded source path is not usable.',
            'not-a-file': 'The recorded path is not a file.',
            'unreadable': 'The source file exists but cannot be read by the application.',
        }.get(reason, 'The original file is not available.')
        return jsonify({'success': False, 'error': message, 'reason': reason}), status
    except Exception as e:
        logger.error(f"Error serving original file {file_id}: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', status=500)


@files_bp.route('/file/<int:file_id>/delete', methods=['POST', 'DELETE'])
def delete_file(file_id):
    """Delete a single file and all its related data"""
    try:
        
        # First check if file exists
        file_check = execute_query("""
            SELECT id, file_name FROM paths WHERE id = %s
        """, (file_id,), fetch="one")
        
        if not file_check:
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        file_id_db, file_name = file_check[0], file_check[1]
        
        # Deletion is a content-lifecycle operation (One Content, Many
        # Contexts): extracted content is SHARED between every occurrence of
        # the same bytes, so it may only disappear when the last occurrence
        # goes. One authoritative implementation performs the whole cascade
        # (occurrence -> context -> content, guarded by real reference
        # counts) in a single transaction.
        from database.services.contents_db_service import ContentDBService
        with ContentDBService()._dedup_session() as dedup:
            lifecycle = dedup.delete_path(file_id)
        
        try:
            cache = get_query()
            cache.clear()
            logger.debug("Cleared query cache after deleting file")
        except Exception as e:
            logger.warning(f"Could not clear cache after deleting file: {e}")
        
        logger.info(f"Successfully deleted file: {file_id} ({file_name})")
        return jsonify({'success': True, 'message': f'File deleted successfully'})
        
    except Exception as e:
        logger.error(f"Error deleting file {file_id}: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


def _same_context_service():
    from database.services.contents_db_service import ContentDBService
    return ContentDBService()._dedup_session()


@files_bp.route('/api/files/same-context-duplicates', methods=['GET'])
def same_context_duplicates_preview():
    """Preview the repetitions inside each (source, side): what would go, what stays.

    Read-only. A repetition is the same content recorded more than once in one
    source and one side; the earliest occurrence is kept.

    The preview names files and ids across the whole corpus, so it is limited
    to the roles that may remove them (admin, analyst). Viewers get 403.
    """
    from core.security import current_user
    user = current_user()
    if not (user and user.has_role('admin', 'analyst')):
        return jsonify({'success': False, 'error': 'Not permitted'}), 403
    try:
        with _same_context_service() as dedup:
            groups = dedup.same_context_duplicates()
        sample = [
            {
                'source': g['source'], 'side': g['side'],
                'file_name': g['file_names'][0],
                'kept_id': g['keep_id'],
                'removed_ids': g['remove_ids'],
                'copies': len(g['remove_ids']) + 1,
            }
            for g in groups[:50]
        ]
        return jsonify({
            'success': True,
            'groups': len(groups),
            'to_remove': sum(len(g['remove_ids']) for g in groups),
            'sample': sample,
        })
    except Exception as e:
        logger.error(f"Error previewing same-context duplicates: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@files_bp.route('/api/files/same-context-duplicates/remove', methods=['POST'])
def same_context_duplicates_remove():
    """Remove the repetitions inside each (source, side), keeping one occurrence.

    Analysts and admins only. Original files on disk are never touched, and
    the extracted text is kept because one occurrence always remains.
    """
    from core.security import current_user
    user = current_user()
    if not (user and user.has_role('admin', 'analyst')):
        return jsonify({'success': False, 'error': 'Not permitted'}), 403
    try:
        with _same_context_service() as dedup:
            result = dedup.remove_same_context_duplicates()
        try:
            get_query().clear()
        except Exception as e:
            logger.warning(f"Could not clear cache after same-context cleanup: {e}")
        logger.info(f"Same-context duplicate cleanup: {result}")
        return jsonify({
            'success': True,
            'groups': result['groups'],
            'removed': result['removed'],
            'message': f"Removed {result['removed']} repeated file record(s) in {result['groups']} group(s).",
        })
    except Exception as e:
        logger.error(f"Error removing same-context duplicates: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)


@files_bp.route('/files/bulk-delete', methods=['POST'])
def bulk_delete_files():
    """Delete multiple files and all their related data"""
    try:
        
        data = request.get_json(silent=True)
        if not data or 'file_ids' not in data:
            return jsonify({'success': False, 'error': 'No file IDs provided'}), 400
        
        file_ids = data['file_ids']
        
        # Validate file_ids is a list
        if not isinstance(file_ids, list):
            return jsonify({'success': False, 'error': 'file_ids must be a list'}), 400
        
        # Validate file_ids are integers
        try:
            file_ids = [int(fid) for fid in file_ids]
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid file ID format'}), 400
        
        if not file_ids:
            return jsonify({'success': False, 'error': 'No file IDs provided'}), 400
        
        # Limit batch size for safety
        if len(file_ids) > 1000:
            return jsonify({'success': False, 'error': 'Maximum 1000 files can be deleted at once'}), 400
        
        deleted_count = 0
        errors = []
        
        for file_id in file_ids:
            try:
                # Check if file exists
                file_check = execute_query("""
                    SELECT id, file_name FROM paths WHERE id = %s
                """, (file_id,), fetch="one")
                
                if not file_check:
                    errors.append(f"File {file_id} not found")
                    continue
                
                file_id_db, file_name = file_check[0], file_check[1]
                
                # Same lifecycle as the single delete (see delete_file).
                from database.services.contents_db_service import ContentDBService
                with ContentDBService()._dedup_session() as dedup:
                    dedup.delete_path(file_id)
                
                deleted_count += 1
                logger.info(f"Deleted file: {file_id} ({file_name})")
                
            except Exception as e:
                error_msg = f"Error deleting file {file_id}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        try:
            cache = get_query()
            cache.clear()
            logger.debug("Cleared query cache after bulk delete")
        except Exception as e:
            logger.warning(f"Could not clear cache after bulk delete: {e}")
        
        response_message = f'Successfully deleted {deleted_count} file(s)'
        if errors:
            response_message += f'. {len(errors)} error(s) occurred.'
        
        logger.info(f"Bulk delete completed: {deleted_count} files deleted, {len(errors)} errors")
        
        return jsonify({
            'success': True,
            'message': response_message,
            'deleted_count': deleted_count,
            'errors': errors if errors else []
        })
        
    except Exception as e:
        logger.error(f"Error in bulk delete: {e}", exc_info=True)
        return client_error(e, subsystem='Api.blueprints.files', success_key='success', status=500)

