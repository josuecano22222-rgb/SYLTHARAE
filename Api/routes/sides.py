"""
Sides routes
"""

from flask import render_template, redirect, url_for, request, flash
from Api.utils import (
    execute_query, select_info_sides
)
from core.table_paging import fetch_page, read_page_request
import logging

logger = logging.getLogger(__name__)


def register_sides_routes(app):
    """Register sides routes with the Flask app"""
    
    @app.route('/sides')
    def sides_list():
        """Side Management — server-side search, sort and pagination"""
        try:
            req = read_page_request(request.args, request.headers)
            search = (request.args.get('search') or '').strip()

            if len(search) > 500:
                search = search[:500]

            # Column sort comes from the table headers: `sort` + `order` in
            # the query string, resolved against an allowlist.
            SORT_COLUMNS = {
                'name': 'si.name',
                'importance': 'si.importance',
                'date': 'si.date_creation',
            }
            sort_by = request.args.get('sort', 'importance')
            sort_order = request.args.get('order', 'desc')
            if sort_by not in SORT_COLUMNS:
                sort_by = 'importance'
            if sort_order not in ('asc', 'desc'):
                sort_order = 'desc'
            order_by = f"{SORT_COLUMNS[sort_by]} {sort_order.upper()} NULLS LAST, si.id DESC"

            joins = (
                "LEFT JOIN hash_contexts hc ON si.id = hc.side_id "
                "LEFT JOIN paths p ON p.context_id = hc.id"
            )
            where = "WHERE si.name ILIKE %s" if search else ""
            base_params = (f"%{search}%",) if search else ()

            def _load(page, size):
                offset = (page - 1) * size
                rows = execute_query(
                    f"""
                    SELECT si.id, si.name, si.importance, si.date_creation,
                           COUNT(DISTINCT p.id) AS doc_count,
                           COUNT(DISTINCT hc.source_id) AS source_count
                    FROM sides si
                    {joins}
                    {where}
                    GROUP BY si.id
                    ORDER BY {order_by}
                    LIMIT %s OFFSET %s
                    """,
                    base_params + (size, offset),
                    fetch="all",
                )
                total_row = execute_query(
                    f"SELECT COUNT(DISTINCT si.id) FROM sides si {joins} {where}",
                    base_params,
                    fetch="one",
                )
                return rows, (total_row[0] if total_row else 0) or 0
            result = fetch_page(_load, req)
            rows, total_sides = result.rows, result.total
            page, per_page, total_pages = result.page, result.size, result.total_pages

            def _iso(value):
                if value is None:
                    return None
                if isinstance(value, str):
                    return value
                if hasattr(value, 'isoformat'):
                    return value.isoformat()
                return str(value)

            formatted_sides = []
            for row in rows or []:
                formatted_sides.append({
                    'id': row[0],
                    'name': row[1],
                    'importance': row[2],
                    'date_creation': _iso(row[3]),
                    'doc_count': row[4] or 0,
                    'source_count': row[5] or 0,
                })

            return render_template('Side/sides_list.html',
                                 sides=formatted_sides,
                                 search=search,
                                 total_sides=total_sides,
                                 page=page,
                                 per_page=per_page,
                                 total_pages=total_pages,
                                 sort_by=sort_by,
                                 sort_order=sort_order)

        except Exception as e:
            logger.error(f"Error in sides_list: {e}", exc_info=True)
            flash('Error loading sides. Please check database connection.', 'error')
            return render_template('Side/sides_list.html',
                                 sides=[],
                                 search=search or '',
                                 total_sides=0,
                                 page=1,
                                 per_page=50,
                                 total_pages=1,
                                 sort_by='importance',
                                 sort_order='desc')

    @app.route('/side/add', methods=['GET', 'POST'])
    def side_add():
        """Add new side - redirects to sides list with modal"""
        # Redirect to sides list page (modal will be opened via JavaScript)
        return redirect(url_for('sides_list'))
    
    @app.route('/sides/<int:side_id>')
    def side_detail(side_id):
        """View side details page - OPTIMIZED with aggregated stats"""
        try:
            # 🚀 OPTIMIZED: Get side data with document count in single query
            side_data = execute_query("""
                SELECT si.id, si.name, si.importance, si.date_creation,
                       COUNT(DISTINCT p.id) as doc_count,
                       COUNT(DISTINCT hc.source_id) as source_count,
                       COALESCE(SUM(p.file_size), 0) as total_size
                FROM sides si
                LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                LEFT JOIN paths p ON p.context_id = hc.id
                WHERE si.id = %s
                GROUP BY si.id, si.name, si.importance, si.date_creation
            """, (side_id,), fetch="one")
            
            if not side_data:
                flash('Side not found', 'error')
                return redirect(url_for('sides_list'))
            
            return render_template('Side/side_detail.html', 
                                 side={
                                     'id': side_data[0], 'name': side_data[1], 
                                     'importance': side_data[2], 'date_creation': side_data[3],
                                     'doc_count': side_data[4] or 0,
                                     'source_count': side_data[5] or 0,
                                     'total_size': side_data[6] or 0
                                 })
        except Exception as e:
            logger.error(f"Error loading side {side_id}: {e}")
            flash('Error loading side', 'error')
            return redirect(url_for('sides_list'))
    
    @app.route('/sides/<int:side_id>/categories-keywords')
    def side_categories_keywords(side_id):
        """View categories and keywords for a specific side"""
        try:
            side_data = execute_query("""
                SELECT si.id, si.name, si.importance, si.date_creation,
                       COUNT(DISTINCT p.id) as doc_count,
                       COUNT(DISTINCT hc.source_id) as source_count,
                       COALESCE(SUM(p.file_size), 0) as total_size
                FROM sides si
                LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                LEFT JOIN paths p ON p.context_id = hc.id
                WHERE si.id = %s
                GROUP BY si.id, si.name, si.importance, si.date_creation
            """, (side_id,), fetch="one")
            
            if not side_data:
                flash('Side not found', 'error')
                return redirect(url_for('sides_list'))
            
            return render_template('Side/side_categories_keywords.html', 
                                 side={
                                     'id': side_data[0], 'name': side_data[1], 
                                     'importance': side_data[2], 'date_creation': side_data[3],
                                     'doc_count': side_data[4] or 0,
                                     'source_count': side_data[5] or 0,
                                     'total_size': side_data[6] or 0
                                 })
        except Exception as e:
            logger.error(f"Error loading side categories/keywords {side_id}: {e}")
            flash('Error loading side categories/keywords', 'error')
            return redirect(url_for('sides_list'))
    
    @app.route('/sides/<int:side_id>/edit', methods=['GET', 'POST'])
    def side_edit(side_id):
        """Edit side - redirects to sides list with modal"""
        # Redirect to sides list page with edit parameter (modal will be opened via JavaScript)
        return redirect(url_for('sides_list', edit=side_id))
