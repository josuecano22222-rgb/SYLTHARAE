"""
Notifications Page Route
Displays notifications in a table view with pagination, search, and filtering

Accuracy/volume design
----------------------
All filtering, searching, sorting, pagination and counting happen in SQL.
The server never loads "everything then slices in Python": totals come from
``COUNT(*) FILTER`` over the full filtered set (correct for any table size),
each page fetches at most ``per_page`` rows (clamped to 1000), and the
source/side filter joins ``paths``/``hash_contexts`` once instead of running
one query per notification (the previous N+1 pattern).
"""

from flask import render_template, request, jsonify
from flask_babel import gettext as _
from core.monitoring.notification_service import get_notification_service, notification_from_row
from core.monitoring.notification_display import display_payload, path_names
from Api.utils import select_info_sources, select_info_sides, execute_query
import logging
from core.errors import client_error
from core.table_paging import MAX_PAGE
from core.validation import query_int

logger = logging.getLogger(__name__)

# Whitelisted ORDER BY expressions - request values never reach SQL raw.
_SORT_COLUMNS = {
    'created_at': "a.created_at",
    'priority': (
        "CASE a.priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
        "WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"
    ),
    'title': "LOWER(a.title)",
}

_READ_STATUSES = ('all', 'unread', 'read')


def register_notification_page_routes(app):
    """Register notification page routes"""

    @app.route('/notifications')
    def notifications_page():
        """Notifications page with table view"""
        try:
            # Get sources and sides for filtering
            sources = select_info_sources()
            sides = select_info_sides()

            # Exact SQL aggregates - no forced refresh of the in-memory cache.
            from Api.routes.notifications import _viewer_id
            stats = get_notification_service().get_stats(for_user_id=_viewer_id())

            return render_template(
                'Notifications/notifications.html',
                sources=sources,
                sides=sides,
                stats=stats
            )
        except Exception as e:
            logger.error(f"Error loading notifications page: {e}")
            return render_template(
                'Notifications/notifications.html',
                sources={},
                sides={},
                stats={'total': 0, 'unread': 0, 'by_type': {}, 'by_priority': {}}
            )

    @app.route('/api/notifications/paginated', methods=['GET'])
    def get_paginated_notifications():
        """Get paginated notifications with search and filtering"""
        try:
            # Get query parameters
            # The page is the offset multiplier and is bounded; per_page keeps
            # its long-standing clamp exactly as the volume guard describes it.
            page = query_int("page", 1, minimum=1, maximum=MAX_PAGE)
            per_page = max(1, min(request.args.get('per_page', 20, type=int), 1000))
            search = request.args.get('search', '').strip()
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            notification_type = request.args.get('type', '')
            show_read = request.args.get('show_read', 'false').lower() == 'true'
            raw_read_status = request.args.get('read_status')
            explicit_read_status = raw_read_status is not None
            read_status = (raw_read_status or 'all').strip().lower()
            if read_status not in _READ_STATUSES:
                read_status = 'all'
            sort_by = request.args.get('sort_by', 'created_at')  # created_at, priority, title
            sort_order = request.args.get('sort_order', 'desc')  # asc, desc

            from Api.routes.notifications import _viewer_id
            from core.monitoring.notification_service import ALERT_COLUMNS, visibility_clause

            # ``alerts`` holds the path id only; the name shown by the page
            # comes from ``paths``. Joined unconditionally so search and the
            # payload resolve it the same way.
            joins = " LEFT JOIN paths p ON p.id = a.file_id"
            # Only what this user may see: system-wide alerts and their own.
            where = ["a.dismissed = FALSE", visibility_clause("a")]
            params = [_viewer_id()]

            # Filter by type if specified
            if notification_type:
                type_list = [t.strip() for t in notification_type.split(',') if t.strip()]
                if type_list:
                    where.append(
                        f"a.type IN ({','.join(['%s'] * len(type_list))})"
                    )
                    params.extend(type_list)

            # Backward compatibility: legacy callers used show_read=false to
            # request unread-only records. Newer paginated workspaces send an
            # explicit read_status so the server can return full read/unread
            # summary counts while paginating only the active sub-view.
            if not explicit_read_status and not show_read:
                where.append("a.read = FALSE")

            # Case-insensitive substring search (escaped LIKE wildcards)
            if search:
                escaped = (
                    search.replace('\\', '\\\\')
                    .replace('%', '\\%')
                    .replace('_', '\\_')
                )
                pattern = f"%{escaped}%"
                where.append(
                    "(a.title ILIKE %s ESCAPE '\\' OR "
                    "a.message ILIKE %s ESCAPE '\\' OR "
                    "COALESCE(p.file_name, a.metadata->>'file_name', '')"
                    " ILIKE %s ESCAPE '\\')"
                )
                params.extend([pattern, pattern, pattern])

            # Source/side: metadata wins, the file's content context
            # (paths -> hash_contexts, m0011) is the fallback, and rows with no
            # source/side information are excluded (strict filtering) - all
            # expressed once in SQL instead of per-row Python queries.
            if source_id or side_id:
                joins = (
                    " LEFT JOIN paths p ON p.id = a.file_id"
                    " LEFT JOIN hash_contexts hc ON hc.id = p.context_id"
                )
                if source_id:
                    where.append(
                        "COALESCE(a.metadata->>'source_id', hc.source_id::text) = %s"
                    )
                    params.append(str(source_id))
                if side_id:
                    where.append(
                        "COALESCE(a.metadata->>'side_id', hc.side_id::text) = %s"
                    )
                    params.append(str(side_id))

            where_sql = " AND ".join(where)

            # Exact counts over the FULL filtered set (before the read-status
            # split) - the badges must not depend on the current page size.
            summary_row = execute_query(
                f"""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE NOT a.read),
                       COUNT(*) FILTER (WHERE a.read)
                FROM alerts a
                {joins}
                WHERE {where_sql}
                """,
                tuple(params),
                fetch="one"
            ) or (0, 0, 0)
            summary = {
                'total': int(summary_row[0] or 0),
                'unread': int(summary_row[1] or 0),
                'read': int(summary_row[2] or 0),
            }

            # Row query for the active sub-view
            row_where = list(where)
            row_params = list(params)
            if explicit_read_status and read_status == 'unread':
                row_where.append("a.read = FALSE")
            elif explicit_read_status and read_status == 'read':
                row_where.append("a.read = TRUE")

            order_col = _SORT_COLUMNS.get(sort_by, _SORT_COLUMNS['created_at'])
            direction = "ASC" if sort_order == 'asc' else "DESC"
            # a.id as final tie-break keeps LIMIT/OFFSET pages stable
            # (without it rows can repeat or vanish between pages).
            order_sql = f"{order_col} {direction}, a.id {direction}"

            rows = execute_query(
                f"""
                SELECT {', '.join('a.' + c.strip() for c in ALERT_COLUMNS.split(','))}
                FROM alerts a
                {joins}
                WHERE {' AND '.join(row_where)}
                ORDER BY {order_sql}
                LIMIT %s OFFSET %s
                """,
                tuple(row_params + [per_page, (page - 1) * per_page]),
                fetch="all"
            ) or []

            # Convert rows to Notification objects
            notifications = []
            for row in rows:
                try:
                    notifications.append(notification_from_row(row))
                except Exception as e:
                    logger.warning(f"Error converting notification row: {e}")
                    continue

            # Totals for the ACTIVE sub-view
            if explicit_read_status and read_status != 'all':
                total = summary.get(read_status, summary['total'])
            else:
                total = summary['total']

            # Format for JSON with translation (shared formatter). File names
            # are resolved from ``paths`` in one query for the whole page.
            names = path_names([n.file_id for n in notifications])
            notifications_data = [display_payload(n, _, names) for n in notifications]

            logger.debug(
                f"Returning {len(notifications_data)} notifications "
                f"(page {page}, per_page {per_page}, total {total})"
            )

            return jsonify({
                'success': True,
                'notifications': notifications_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page if total > 0 else 0
                },
                'summary': summary,
                'read_status': read_status if explicit_read_status else ('all' if show_read else 'unread')
            })

        except Exception as e:
            logger.error(f"Error getting paginated notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications_page', success_key='success', status=500)
