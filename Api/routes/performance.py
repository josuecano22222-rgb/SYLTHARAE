"""
Database Performance Monitoring API Routes
Provides endpoints for query performance analysis, indexing recommendations, and cache statistics
"""

from flask import Blueprint, jsonify, request
import logging

from core.errors import client_error
from core.validation import query_int
from typing import Dict, Any

from database import (
    DatabaseHub,
    DatabasePerformanceAnalyzer,
    IndexAnalyzer,
    QueryProfiler
)
from Api.utils import get_query_cache

logger = logging.getLogger(__name__)

performance_bp = Blueprint('performance', __name__, url_prefix='/api/performance')


@performance_bp.route('/analyze', methods=['GET'])
def analyze_performance():
    """
    Perform comprehensive database performance analysis
    
    Query params:
        include_index_analysis: Include index analysis (default: true)
        slow_query_threshold: Threshold for slow queries in seconds (default: 1.0)
    """
    try:
        db_hub = DatabaseHub()
        # Use context manager for connection
        with db_hub._get_connection() as conn:
            # Get parameters
            include_index_analysis = request.args.get('include_index_analysis', 'true').lower() == 'true'
            slow_query_threshold = float(request.args.get('slow_query_threshold', 1.0))
            
            # Create analyzer
            analyzer = DatabasePerformanceAnalyzer(conn, slow_query_threshold)
            
            # Perform analysis
            analysis = analyzer.analyze_performance(include_index_analysis=include_index_analysis)
            
            return jsonify({
                'success': True,
                'data': analysis
            })
    
    except Exception as e:
        logger.error(f"Error performing performance analysis: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


@performance_bp.route('/report', methods=['GET'])
def get_performance_report():
    """Get human-readable performance report"""
    try:
        db_hub = DatabaseHub()
        # Use context manager for connection
        with db_hub._get_connection() as conn:
            slow_query_threshold = float(request.args.get('slow_query_threshold', 1.0))
            analyzer = DatabasePerformanceAnalyzer(conn, slow_query_threshold)
            
            report = analyzer.get_performance_report()
            
            return jsonify({
                'success': True,
                'report': report
            })
    
    except Exception as e:
        logger.error(f"Error generating performance report: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


@performance_bp.route('/indexes', methods=['GET'])
def get_index_analysis():
    """Get index analysis and recommendations"""
    try:
        db_hub = DatabaseHub()
        # Use context manager for connection
        with db_hub._get_connection() as conn:
            index_analyzer = IndexAnalyzer(conn)
            
            # Get all indexes
            indexes = index_analyzer.get_all_indexes()
            
            # Get recommendations
            recommendations = index_analyzer.analyze_missing_indexes()
            
            # Get statistics
            stats = index_analyzer.get_index_statistics()
            
            return jsonify({
                'success': True,
                'data': {
                    'indexes': [
                        {
                            'table_name': idx.table_name,
                            'index_name': idx.index_name,
                            'index_type': idx.index_type,
                            'columns': idx.columns,
                            'is_unique': idx.is_unique,
                            'is_primary': idx.is_primary,
                            'size_bytes': idx.size_bytes,
                            'usage_count': idx.usage_count,
                            'last_used': idx.last_used.isoformat() if idx.last_used else None
                        }
                        for idx in indexes
                    ],
                    'recommendations': [
                        {
                            'type': rec.type,
                            'table_name': rec.table_name,
                            'columns': rec.columns,
                            'reason': rec.reason,
                            'priority': rec.priority,
                            'estimated_impact': rec.estimated_impact,
                            'sql': rec.sql
                        }
                        for rec in recommendations
                    ],
                    'statistics': stats
                }
            })
    
    except Exception as e:
        logger.error(f"Error getting index analysis: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


@performance_bp.route('/cache/stats', methods=['GET'])
def get_cache_stats():
    """Get query cache statistics"""
    try:
        cache = get_query_cache()
        stats = cache.get_stats()
        
        # Get cache entries if requested
        entries = None
        if request.args.get('include_entries', 'false').lower() == 'true':
            limit = query_int("limit", 10, minimum=1, maximum=1000)
            entries = cache.get_entries(limit=limit)
        
        result = {
            'success': True,
            'data': {
                'statistics': stats,
                'entries': entries
            }
        }
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


@performance_bp.route('/cache/clear', methods=['POST'])
def clear_cache():
    """Clear query cache"""
    try:
        cache = get_query_cache()
        
        # Optional pattern to clear specific entries
        pattern = request.json.get('pattern') if request.is_json else None
        
        if pattern is None:
            cache.clear()
        else:
            try:
                cache.clear(pattern=pattern)
            except TypeError:
                # SimpleQueryCache/EnhancedQueryCache clear everything;
                # a selective pattern is unsupported there.
                cache.clear()
        
        return jsonify({
            'success': True,
            'message': 'Cache cleared successfully'
        })
    
    except Exception as e:
        logger.error(f"Error clearing cache: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


@performance_bp.route('/cache/cleanup', methods=['POST'])
def cleanup_cache():
    """Clean up expired cache entries"""
    try:
        cache = get_query_cache()
        
        if hasattr(cache, 'cleanup_expired'):
            expired_count = cache.cleanup_expired()
            return jsonify({
                'success': True,
                'expired_count': expired_count,
                'message': f'Removed {expired_count} expired entries'
            })
        else:
            return jsonify({
                'success': True,
                'message': 'Cache does not support cleanup'
            })
    
    except Exception as e:
        logger.error(f"Error cleaning up cache: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


@performance_bp.route('/queries/slow', methods=['GET'])
def get_slow_queries():
    """Get slow query information"""
    try:
        db_hub = DatabaseHub()
        # Use context manager for connection
        with db_hub._get_connection() as conn:
            slow_query_threshold = float(request.args.get('threshold', 1.0))
            limit = query_int("limit", 10, minimum=1, maximum=1000)
            
            profiler = QueryProfiler(conn, slow_query_threshold)
            
            # Note: This will only show queries profiled in this session
            # For production, you'd want to use pg_stat_statements
            slow_queries = profiler.get_slow_queries(limit=limit)
            
            return jsonify({
                'success': True,
                'data': {
                    'slow_queries': [
                        {
                            'query': q.query[:500] + '...' if len(q.query) > 500 else q.query,
                            'execution_time': q.execution_time,
                            'rows_returned': q.rows_returned,
                            'rows_examined': q.rows_examined,
                            'timestamp': q.timestamp.isoformat(),
                            'error': q.error
                        }
                        for q in slow_queries
                    ],
                    'summary': profiler.get_performance_summary()
                }
            })
    
    except Exception as e:
        logger.error(f"Error getting slow queries: {e}", exc_info=True)
        return client_error(e, subsystem="performance",
                            public_message="The performance data could not be read",
                            success_key="success")


def register_performance_routes(app):
    """Register performance monitoring routes"""
    app.register_blueprint(performance_bp)

    # ---
    # UI-facing aliases: the settings page calls /api/cache/* (without the
    # blueprint prefix) and reads a flat ``stats`` object.
    # ---
    @app.route('/api/cache/stats', methods=['GET'])
    def get_cache_stats_ui_alias():
        """Cache statistics in the shape the settings page reads."""
        try:
            cache = get_query_cache()
            return jsonify({'success': True, 'stats': cache.get_stats()})
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}", exc_info=True)
            return client_error(e, subsystem="performance",
                                public_message="The performance data could not be read",
                                success_key="success")

    @app.route('/api/cache/clear', methods=['POST'])
    def clear_cache_ui_alias():
        """Clear the query cache (settings page entry point)."""
        try:
            cache = get_query_cache()
            pattern = request.json.get('pattern') if request.is_json else None
            if pattern is None:
                cache.clear()
            else:
                try:
                    cache.clear(pattern=pattern)
                except TypeError:
                    cache.clear()
            return jsonify({'success': True, 'message': 'Cache cleared successfully'})
        except Exception as e:
            logger.error(f"Error clearing cache: {e}", exc_info=True)
            return client_error(e, subsystem="performance",
                                public_message="The performance data could not be read",
                                success_key="success")

    logger.info("Performance monitoring routes registered")
