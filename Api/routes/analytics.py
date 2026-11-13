"""
Analytics routes
"""

from flask import render_template

import logging

logger = logging.getLogger(__name__)

def register_analytics_routes(app):
    """Register analytics routes with the Flask app"""
    
    @app.route('/analytics/path-analysis')
    def path_analysis_page():
        """Path Analysis page"""
        return render_template('Analysis/path_analysis.html')

    @app.route('/analysis/classification')
    def file_classification_page():
        """FUNC-03: File Classification Analysis page.

        The template and its page script existed without a route; this serves
        them as the fourth view in the analysis navigation (File, Path,
        Batch, Classification), backed by /api/analytics/path/classifications.
        """
        # The template renders three header stat cards; compute the same
        # numbers the client-side API refresh will use so the page is correct
        # even before JS loads.
        try:
            from Api.utils import execute_query
            # Content identity (m0011): paths -> hash_contexts -> contents is
            # keyed by hash (contents.path_id no longer exists). COUNT(DISTINCT
            # p.id) keeps chunk-level contents rows from multiplying files.
            row = execute_query("""
                SELECT
                    COUNT(DISTINCT p.id) AS total_files,
                    COUNT(DISTINCT p.id) FILTER (WHERE c.id IS NOT NULL) AS analyzed_files,
                    (SELECT COUNT(DISTINCT w.id) FROM words w
                     JOIN categorys cat ON cat.word_id = w.id) AS total_categories
                FROM paths p
                LEFT JOIN hash_contexts hc ON hc.id = p.context_id
                LEFT JOIN contents c ON c.hash_id = hc.hash_id
            """, fetch="one")
            total_files = int(row[0] or 0) if row else 0
            analyzed_files = int(row[1] or 0) if row else 0
            total_categories = int(row[2] or 0) if row else 0
        except Exception:
            logger.exception("Failed to load classification page stats")
            total_files = analyzed_files = total_categories = 0

        stats = {
            'total_files': total_files,
            'analyzed_files': analyzed_files,
            'total_categories': total_categories,
        }
        return render_template('Analysis/file_classification.html', stats=stats)

