"""
Dashboard routes
"""

from flask import render_template
import logging

from Api.utils import (
    get_statistics, get_processing_statistics, get_category_statistics_detailed,
    get_period_comparison, get_recent_files, get_processing_chart_data
)

logger = logging.getLogger(__name__)


# ``get_recent_files()`` returns raw psycopg2 rows (tuples) laid out by
# ``FileQueries.get_recent_files()``. The dashboard template addresses the
# fields by name, so the rows are normalised here once, in one place.
_RECENT_FILE_COLUMNS = (
    "id",
    "file_name",
    "file_path",
    "file_size",
    "file_type",
    "file_status",
    "file_date",
    "date_creation",
    "hash_id",
    "source_id",
    "side_id",
    "source_name",
    "side_name",
)


def _normalise_recent_file(row):
    """Return a dict for a ``get_recent_files()`` row (tuple or dict).

    DB-AUDIT: the caller previously did ``f['id']`` on a tuple, which raised
    ``TypeError: tuple indices must be integers or slices, not str``. That
    exception was swallowed by the page-level ``except``, so *every* dashboard
    KPI silently fell back to 0 and the Recent Files table rendered empty even
    though the same queries succeed elsewhere (``/api/dashboard/stats``).
    """
    if isinstance(row, dict):
        return dict(row)
    if not isinstance(row, (list, tuple)):
        return None
    mapped = {
        column: row[index]
        for index, column in enumerate(_RECENT_FILE_COLUMNS)
        if index < len(row)
    }
    mapped.setdefault("source_name", "Unknown")
    mapped.setdefault("side_name", "Unknown")
    return mapped


def register_dashboard_routes(app):
    """Register dashboard routes with the Flask app"""
    
    @app.route('/')
    def index():
        """Enhanced Dashboard with Real Database Statistics - OPTIMIZED"""
        try:
            stats = get_statistics()
            processing_stats = get_processing_statistics()
            category_stats = get_category_statistics_detailed()
            period_stats = get_period_comparison()
            
            recent_files_list = get_recent_files(limit=10)
            # Convert to format expected by template
            recent_files = [
                normalised
                for normalised in (
                    _normalise_recent_file(f) for f in (recent_files_list or [])
                )
                if normalised
            ]
            
            processing_chart_data = get_processing_chart_data(days=7)
            
            processing_chart = {
                'labels': [str(date) for date, _ in processing_chart_data] if processing_chart_data else [],
                'data': [count for _, count in processing_chart_data] if processing_chart_data else []
            }
            

            percentages = {}
            
            return render_template('Analysis/dashboard.html',
                                 stats=stats,
                                 processing_stats=processing_stats,
                                 category_stats=category_stats,
                                 period_stats=period_stats,
                                 recent_files=recent_files or [],
                                 processing_chart=processing_chart,
                                 percentages=percentages)
            
        except Exception as e:
            # DB-AUDIT: keep the page usable, but log the traceback. The
            # previous single-line log hid the exact failure for months.
            logger.error(f"Dashboard error: {e}", exc_info=True)
            return render_template('Analysis/dashboard.html',
                                 stats={},
                                 processing_stats={},
                                 category_stats={},
                                 period_stats={},
                                 recent_files=[],
                                 processing_chart={'labels': [], 'data': []},
                                 percentages={})
    
    @app.route('/dashboard/comprehensive')
    def comprehensive_dashboard():
        """Comprehensive Dashboard with multiple layout views"""
        return render_template('Analysis/comprehensive_dashboard.html')
    
    @app.route('/dashboard/charts')
    def charts_dashboard():
        """Charts-only Dashboard with filters - all sections unified"""
        return render_template('Analysis/charts_dashboard.html')

