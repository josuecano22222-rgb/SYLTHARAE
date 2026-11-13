"""
Sources routes
"""

from flask import render_template, redirect, url_for, request, flash
from Api.utils import (
    execute_query, select_info_sources, select_info_sides,
    get_source_with_stats, insert_info_sources
)
from core.table_paging import fetch_page, read_page_request
import logging

logger = logging.getLogger(__name__)


def register_sources_routes(app):
    """Register sources routes with the Flask app"""
    
    @app.route('/sources')
    def sources_list():
        """Source Management — server-side search, sort and pagination"""
        try:
            req = read_page_request(request.args, request.headers)
            search = (request.args.get('search') or '').strip()

            if len(search) > 500:
                search = search[:500]

            # Column sort comes from the table headers: `sort` + `order` in
            # the query string, resolved against an allowlist.
            SORT_COLUMNS = {
                'name': 's.name',
                'importance': 's.importance',
                'date': 's.date_creation',
                'discovered': 's.entry_date',
            }
            sort_by = request.args.get('sort', 'importance')
            sort_order = request.args.get('order', 'desc')
            if sort_by not in SORT_COLUMNS:
                sort_by = 'importance'
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'
            order_by = f"{SORT_COLUMNS[sort_by]} {sort_order.upper()} NULLS LAST, s.id DESC"

            joins = (
                "LEFT JOIN hash_contexts hc ON s.id = hc.source_id "
                "LEFT JOIN paths p ON p.context_id = hc.id "
                "LEFT JOIN categorys c ON s.category_id = c.id "
                "LEFT JOIN words w ON c.word_id = w.id"
            )
            where = "WHERE s.name ILIKE %s" if search else ""
            base_params = (f"%{search}%",) if search else ()

            def _load(page, size):
                offset = (page - 1) * size
                rows = execute_query(
                    f"""
                    SELECT s.id, s.name, s.job, s.importance, s.country, s.city,
                           s.description, s.accounts, s.note, s.attachments,
                           s.date_creation, s.ownership, s.access_status, s.entry_date,
                           s.category_id, w.word AS category_name,
                           COUNT(DISTINCT p.id) AS doc_count
                    FROM sources s
                    {joins}
                    {where}
                    GROUP BY s.id, w.word
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s
                    """,
                    base_params + (size, offset),
                    fetch="all",
                )
                total_row = execute_query(
                    f"SELECT COUNT(DISTINCT s.id) FROM sources s {joins} {where}",
                    base_params,
                    fetch="one",
                )
                return rows, (total_row[0] if total_row else 0) or 0
            result = fetch_page(_load, req)
            rows, total_sources = result.rows, result.total
            page, per_page, total_pages = result.page, result.size, result.total_pages

            def _iso(value):
                if value is None:
                    return None
                if isinstance(value, str):
                    return value
                if hasattr(value, 'isoformat'):
                    return value.isoformat()
                return str(value)

            formatted_sources = []
            for row in rows or []:
                formatted_sources.append({
                    'id': row[0],
                    'name': row[1],
                    'job': row[2],
                    'importance': row[3],
                    'country': row[4],
                    'city': row[5],
                    'description': row[6],
                    'accounts': row[7],
                    'note': row[8],
                    'attachments': row[9],
                    'date_creation': _iso(row[10]),
                    'ownership': row[11],
                    'access_status': row[12],
                    'date_source_discovery': _iso(row[13]),
                    'category_id': row[14],
                    'category_name': row[15],
                    'doc_count': row[16] or 0,
                })

            return render_template('Sources/sources_list.html',
                                 sources=formatted_sources,
                                 search=search,
                                 total_sources=total_sources,
                                 page=page,
                                 per_page=per_page,
                                 total_pages=total_pages,
                                 sort_by=sort_by,
                                 sort_order=sort_order)

        except Exception as e:
            logger.error(f"Error in sources_list: {e}", exc_info=True)
            flash(f'Error loading sources: {str(e)}', 'error')
            return render_template('Sources/sources_list.html',
                                 sources=[],
                                 search=search or '',
                                 total_sources=0,
                                 page=1,
                                 per_page=50,
                                 total_pages=1,
                                 sort_by='importance',
                                 sort_order='desc')

    @app.route('/source/add', methods=['GET', 'POST'])
    def source_add():
        """Add new source - redirects to sources list with modal"""
        # Redirect to sources list page (modal will be opened via JavaScript)
        return redirect(url_for('sources_list'))
    
    @app.route('/sources/<int:source_id>')
    def source_detail(source_id):
        """View source details page - OPTIMIZED with aggregated stats"""
        try:

            source_data = get_source_with_stats(source_id)
            
            if not source_data:
                flash('Source not found', 'error')
                return redirect(url_for('sources_list'))
            
            return render_template('Sources/source_detail.html', 
                                 source=source_data)
        except Exception as e:
            logger.error(f"Error loading source {source_id}: {e}")
            flash('Error loading source', 'error')
            return redirect(url_for('sources_list'))
    
    @app.route('/sources/<int:source_id>/categories-keywords')
    def source_categories_keywords(source_id):
        """View categories and keywords for a specific source"""
        try:
            source_data = get_source_with_stats(source_id)
            
            if not source_data:
                flash('Source not found', 'error')
                return redirect(url_for('sources_list'))
            
            return render_template('Sources/source_categories_keywords.html', 
                                 source=source_data)
        except Exception as e:
            logger.error(f"Error loading source categories/keywords {source_id}: {e}")
            flash('Error loading source categories/keywords', 'error')
            return redirect(url_for('sources_list'))
    
    @app.route('/sources/<int:source_id>/edit', methods=['GET', 'POST'])
    def source_edit(source_id):
        """Edit source - redirects to sources list with modal"""
        # Redirect to sources list page with edit parameter (modal will be opened via JavaScript)
        return redirect(url_for('sources_list', edit=source_id))
