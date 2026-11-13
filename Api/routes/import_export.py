"""
Import/Export Routes
Handles batch file import, database backup export/import, and settings export/import
"""

from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from Api.services.import_service import ImportService
from Api.services.export_service import ExportService
from settings import get_settings
import logging
from datetime import datetime
from io import BytesIO
from core.errors import client_error
from core.security.flask_ext import admin_required
from core.security.rate_limit import limiter

logger = logging.getLogger(__name__)

import_export_bp = Blueprint('import_export', __name__, url_prefix='/api/import-export')


# ==================== BATCH FILE IMPORT ====================

@import_export_bp.route('/batch-import', methods=['POST'])
@limiter.limit("20 per minute")
def batch_import_files():

    def _as_int(value):
        """Parse an optional numeric field; ``None`` for anything unusable."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    try:
        if request.is_json:
            data = request.get_json(silent=True) or {}
            file_paths = data.get('file_paths') or []
            source_id = _as_int(data.get('source_id'))
            side_id = _as_int(data.get('side_id'))
        else:
            file_paths_json = request.form.get('file_paths')
            if file_paths_json:
                import json
                try:
                    file_paths = json.loads(file_paths_json)
                except (TypeError, ValueError):
                    return jsonify({'error': 'file_paths must be a JSON array'}), 400
            else:
                file_paths = []
            source_id = _as_int(request.form.get('source_id'))
            side_id = _as_int(request.form.get('side_id'))

        if not file_paths:
            return jsonify({'error': 'No file paths provided'}), 400

        if not source_id or not side_id:
            return jsonify({'error': 'source_id and side_id are required'}), 400

        # Import files (path validation + queueing happen in the service)
        results = ImportService.import_batch_files(
            file_paths=file_paths,
            source_id=source_id,
            side_id=side_id
        )

        if not results.get('valid', False):
            return jsonify({'error': results.get('error', 'Import failed')}), 400

        status = 200 if results.get('accepted', 0) > 0 else 422
        return jsonify({
            'success': results.get('accepted', 0) > 0,
            **results
        }), status
        
    except Exception as e:
        logger.error(f"Batch import error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.import_export', status=500)


@import_export_bp.route('/batch-import/csv', methods=['POST'])
@limiter.limit("20 per minute")
def batch_import_from_csv():
    """
    Import file list from CSV and process files.
    
    Form Data:
    - file: CSV file with 'file_path' or 'path' column
    - source_id: Source ID (required)
    - side_id: Side ID (required)
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        csv_file = request.files['file']
        source_id = int(request.form.get('source_id'))
        side_id = int(request.form.get('side_id'))
        
        if not source_id or not side_id:
            return jsonify({'error': 'source_id and side_id are required'}), 400
        
        # Read CSV file
        csv_bytes = BytesIO(csv_file.read())
        
        # Import from CSV
        results = ImportService.import_file_list_from_csv(
            csv_file=csv_bytes,
            source_id=source_id,
            side_id=side_id
        )
        
        if not results.get('valid', True):
            return jsonify({'error': results.get('error', 'Import failed')}), 400
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"CSV batch import error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.import_export', status=500)


# ==================== DATABASE BACKUP ====================

@import_export_bp.route('/backup/export', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
@admin_required
def export_database_backup():
    """
    Export database backup.

    ADMIN-ONLY: a backup contains the full evidence tables. Backup export
    and restore are administrator capabilities (SEC-02; docs/SECURITY.md).

    Query Parameters (GET) or JSON Body (POST):
    - tables: Comma-separated list of table names (optional, evidence tables if not provided)
    - include_data: Include data in backup (default: true)
    """
    try:
        if request.method == 'POST':
            data = request.get_json() or {}
            tables_str = data.get('tables', '')
            include_data = data.get('include_data', True)
        else:
            tables_str = request.args.get('tables', '')
            include_data = request.args.get('include_data', 'true').lower() == 'true'
        
        tables = [t.strip() for t in tables_str.split(',')] if tables_str else None
        
        # Export backup
        backup_data = ExportService.export_database_backup(
            tables=tables,
            include_data=include_data
        )
        
        from core.security.disclosure import note_disclosure
        note_disclosure(kind='database_backup', scope='tables:' + (','.join(tables) if tables else 'evidence'),
                        unit='table_rows', row_count=None,
                        row_count_reason='the backup service does not report row counts',
                        include_data=include_data, format='zip')
        filename = f'database_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
        
        return send_file(
            backup_data,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Database backup export error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.import_export', status=500)


@import_export_bp.route('/backup/import', methods=['POST'])
@limiter.limit("20 per minute")
@admin_required
def import_database_backup():
    """
    Import/validate database backup.

    ADMIN-ONLY: restoring replaces rows in the evidence tables (SEC-02;
    docs/SECURITY.md).

    Form Data:
    - file: Backup ZIP file

    Query Parameters:
    - restore_data: Whether to restore data (default: false = validate only)
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No backup file provided'}), 400
        
        backup_file = request.files['file']
        restore_data = request.args.get('restore_data', 'false').lower() == 'true'
        
        # Read backup file
        backup_bytes = BytesIO(backup_file.read())
        
        # Import/validate backup
        results = ImportService.import_database_backup(
            backup_file=backup_bytes,
            restore_data=restore_data
        )
        
        return jsonify({
            'success': results.get('valid', False),
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Database backup import error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.import_export', status=500)


# ==================== SETTINGS EXPORT/IMPORT ====================

@import_export_bp.route('/settings/export', methods=['GET'])
@limiter.limit("20 per minute")
def export_settings():
    """
    Export current settings to JSON file.
    """
    try:
        settings = get_settings()
        
        # Get all settings
        settings_data = {
            'search_config': settings.get_search_config(),
            'display_config': settings.get_display_config(),
            'system_config': settings.get_system_config()
        }
        
        # Export settings
        settings_file = ExportService.export_settings(settings_data)
        
        from core.security.disclosure import note_disclosure
        note_disclosure(kind='settings_export', scope='settings:search,display,system',
                        unit='settings_document', row_count=1, format='json')
        filename = f'settings_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        return send_file(
            settings_file,
            mimetype='application/json',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Settings export error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.import_export', status=500)


@import_export_bp.route('/settings/import', methods=['POST'])
@limiter.limit("20 per minute")
def import_settings():
    """
    Import settings from JSON file.
    
    Form Data:
    - file: Settings JSON file
    
    Note: This validates the settings but does not automatically apply them.
    Use the settings API to actually update settings.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No settings file provided'}), 400
        
        settings_file = request.files['file']
        
        # Read settings file
        settings_bytes = BytesIO(settings_file.read())
        
        # Import settings
        results = ImportService.import_settings(settings_file=settings_bytes)
        
        return jsonify({
            'success': results.get('valid', False),
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Settings import error: {e}", exc_info=True)
        return client_error(e, subsystem='Api.routes.import_export', status=500)


def register_import_export_routes(app):
    """Register import/export routes with the Flask app"""
    from flask import render_template
    
    # Register API blueprint
    app.register_blueprint(import_export_bp)
    
    # Register page route
    @app.route('/import-export')
    def import_export_page():
        """Import/Export management page"""
        return render_template('ImportExport/import_export.html')

