"""
Error Dashboard API Routes
Provides endpoints for error monitoring, metrics, and pattern detection.
"""

from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import logging

from Hdg_Err_Ex_Log import get_error_monitor, ErrorCategory, ErrorSeverity
from core.errors import client_safe_message

logger = logging.getLogger(__name__)

error_dashboard_bp = Blueprint("error_dashboard", __name__)


@error_dashboard_bp.route('/api/errors/metrics', methods=['GET'])
def get_error_metrics():
    """Get error metrics and statistics"""
    try:
        monitor = get_error_monitor()
        metrics = monitor.get_metrics()
        
        return jsonify({
            'success': True,
            'metrics': metrics
        }), 200
    except Exception as e:
        logger.error(f"Error getting metrics: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': client_safe_message(e, subsystem='Api.routes.error_dashboard')
        }), 500


@error_dashboard_bp.route('/api/errors/recent', methods=['GET'])
def get_recent_errors():
    """Get recent error events"""
    try:
        monitor = get_error_monitor()
        limit = request.args.get('limit', 100, type=int)
        limit = min(limit, 1000)  # Max 1000
        
        category = request.args.get('category')
        severity = request.args.get('severity')
        
        errors = monitor.get_recent_errors(limit=limit)
        
        # Filter by category/severity if provided
        if category:
            errors = [e for e in errors if e.get('category') == category]
        if severity:
            errors = [e for e in errors if e.get('severity') == severity]
        
        return jsonify({
            'success': True,
            'errors': errors,
            'count': len(errors)
        }), 200
    except Exception as e:
        logger.error(f"Error getting recent errors: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': client_safe_message(e, subsystem='Api.routes.error_dashboard')
        }), 500


@error_dashboard_bp.route('/api/errors/patterns', methods=['GET'])
def get_error_patterns():
    """Get detected error patterns"""
    try:
        monitor = get_error_monitor()
        patterns = monitor.get_patterns()
        
        # Filter by minimum frequency if provided
        min_frequency = request.args.get('min_frequency', type=float)
        if min_frequency:
            patterns = [p for p in patterns if p.get('frequency', 0) >= min_frequency]
        
        # Sort by frequency (descending)
        patterns.sort(key=lambda p: p.get('frequency', 0), reverse=True)
        
        return jsonify({
            'success': True,
            'patterns': patterns,
            'count': len(patterns)
        }), 200
    except Exception as e:
        logger.error(f"Error getting patterns: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': client_safe_message(e, subsystem='Api.routes.error_dashboard')
        }), 500


@error_dashboard_bp.route('/api/errors/stats', methods=['GET'])
def get_error_stats():
    """Get comprehensive error statistics"""
    try:
        monitor = get_error_monitor()
        metrics = monitor.get_metrics()
        patterns = monitor.get_patterns()
        recent_errors = monitor.get_recent_errors(limit=100)
        
        # Calculate time-based statistics
        now = datetime.now()
        time_ranges = {
            'last_hour': now - timedelta(hours=1),
            'last_24_hours': now - timedelta(hours=24),
            'last_7_days': now - timedelta(days=7)
        }
        
        stats_by_time = {}
        for range_name, cutoff in time_ranges.items():
            errors_in_range = [
                e for e in recent_errors
                if datetime.fromisoformat(e['timestamp']) >= cutoff
            ]
            stats_by_time[range_name] = {
                'count': len(errors_in_range),
                'by_category': {},
                'by_severity': {}
            }
            
            for error in errors_in_range:
                category = error.get('category', 'unknown')
                severity = error.get('severity', 'medium')
                
                stats_by_time[range_name]['by_category'][category] = \
                    stats_by_time[range_name]['by_category'].get(category, 0) + 1
                stats_by_time[range_name]['by_severity'][severity] = \
                    stats_by_time[range_name]['by_severity'].get(severity, 0) + 1
        
        return jsonify({
            'success': True,
            'overall_metrics': metrics,
            'patterns': patterns[:10],  # Top 10 patterns
            'stats_by_time': stats_by_time,
            'recent_errors_count': len(recent_errors)
        }), 200
    except Exception as e:
        logger.error(f"Error getting stats: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': client_safe_message(e, subsystem='Api.routes.error_dashboard')
        }), 500

