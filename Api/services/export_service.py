"""
Export Service
Handles exporting search results, database backups, and settings
"""

import csv
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from pathlib import Path
from io import StringIO, BytesIO
import zipfile
from Api.utils import get_connection, return_connection

try:
    import openpyxl
    from openpyxl import Workbook
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False
    logging.warning("openpyxl not available, Excel export will be disabled")


logger = logging.getLogger(__name__)


class ExportService:
    """
    Service for exporting data in various formats.
    
    Supports:
    - CSV export
    - Excel export
    - JSON export
    - Database backup export
    - Settings export
    """
    
    @staticmethod
    def export_search_results_csv(
        results: List[Dict[str, Any]],
        filename: Optional[str] = None,
        include_line_matches: bool = True
    ) -> BytesIO:
        """
        Export search results to CSV format.
        
        Args:
            results: List of search result dictionaries
            filename: Optional filename (not used, kept for compatibility)
            include_line_matches: If True, export each matching line as a separate row
        
        Returns:
            BytesIO object containing CSV data
        """
        try:
            output = StringIO()
            
            if not results:
                # Write header only
                writer = csv.DictWriter(output, fieldnames=[
                    'id', 'file_name', 'file_path', 'file_type', 'file_size',
                    'file_date', 'file_status', 'source_name', 'side_name',
                    'smart_categories', 'analyst_categories'
                ])
                writer.writeheader()
            else:
                # Check if we should export line matches as separate rows
                has_line_matches = any(result.get('line_matches') for result in results)
                
                if include_line_matches and has_line_matches:
                    # Export with line matches - each matching line is a separate row
                    fieldnames = [
                        'file_id', 'file_name', 'file_path', 'file_type', 'file_size',
                        'file_date', 'file_status', 'source_name', 'side_name',
                        'line_number', 'line_text', 'context_before', 'context_after'
                    ]
                    writer = csv.DictWriter(output, fieldnames=fieldnames)
                    writer.writeheader()
                    
                    for result in results:
                        file_id = result.get('id', '')
                        file_name = result.get('file_name', '')
                        file_path = result.get('file_path', '')
                        file_type = result.get('file_type', '')
                        file_size = result.get('file_size', '')
                        file_date = result.get('file_date', '')
                        if file_date and not isinstance(file_date, str):
                            file_date = file_date.isoformat()
                        file_status = result.get('file_status', '')
                        source_name = result.get('source_name', '')
                        side_name = result.get('side_name', '')
                        
                        line_matches = result.get('line_matches', [])
                        if line_matches:
                            # Write one row per matching line
                            for match in line_matches:
                                writer.writerow({
                                    'file_id': file_id,
                                    'file_name': file_name,
                                    'file_path': file_path,
                                    'file_type': file_type,
                                    'file_size': file_size,
                                    'file_date': file_date,
                                    'file_status': file_status,
                                    'source_name': source_name,
                                    'side_name': side_name,
                                    'line_number': match.get('line_number', ''),
                                    'line_text': match.get('line_text', ''),
                                    'context_before': match.get('context_before', ''),
                                    'context_after': match.get('context_after', '')
                                })
                        else:
                            # Write one row for file with no line matches
                            writer.writerow({
                                'file_id': file_id,
                                'file_name': file_name,
                                'file_path': file_path,
                                'file_type': file_type,
                                'file_size': file_size,
                                'file_date': file_date,
                                'file_status': file_status,
                                'source_name': source_name,
                                'side_name': side_name,
                                'line_number': '',
                                'line_text': '',
                                'context_before': '',
                                'context_after': ''
                            })
                else:
                    # Standard export - one row per file
                    # Get all possible fieldnames from results
                    fieldnames = set()
                    for result in results:
                        fieldnames.update(result.keys())
                    
                    # Remove line_matches from fieldnames (we'll handle it separately if needed)
                    fieldnames.discard('line_matches')
                    # Internal display detail (id/color objects) - not for export
                    fieldnames.discard('analyst_category_details')
                    
                    # FR-1.4: smart vs analyst categories must be exportable
                    # as separate, independently referenceable fields. Rename
                    # the smart-taxonomy 'categories' key and keep the analyst
                    # 'analyst_categories' key adjacent to it.
                    fieldnames.discard('categories')
                    fieldnames.discard('analyst_categories')
                    
                    # Order fieldnames
                    ordered_fieldnames = [
                        'id', 'file_name', 'file_path', 'file_type', 'file_size',
                        'file_date', 'file_status', 'date_creation',
                        'source_name', 'side_name', 'source_id', 'side_id',
                        'relevance_score', 'line_match_count',
                        'smart_categories', 'analyst_categories'
                    ]
                    # Add any additional fields
                    for field in ordered_fieldnames:
                        if field in fieldnames:
                            fieldnames.remove(field)
                    ordered_fieldnames.extend(sorted(fieldnames))
                    
                    writer = csv.DictWriter(output, fieldnames=ordered_fieldnames, extrasaction='ignore')
                    writer.writeheader()
                    
                    for result in results:
                        # Convert dates to strings and remove line_matches
                        row = {k: v for k, v in result.items() if k != 'line_matches'}
                        if 'file_date' in row and row['file_date']:
                            if isinstance(row['file_date'], str):
                                pass  # Already a string
                            else:
                                row['file_date'] = row['file_date'].isoformat()
                        if 'date_creation' in row and row['date_creation']:
                            if isinstance(row['date_creation'], str):
                                pass
                            else:
                                row['date_creation'] = row['date_creation'].isoformat()
                        # Emit the two category namespaces as separate,
                        # clearly labelled columns (FR-1.4)
                        smart_cats = result.get('categories')
                        row['smart_categories'] = '; '.join(smart_cats) if isinstance(smart_cats, (list, tuple)) else (smart_cats or '')
                        analyst_cats = result.get('analyst_categories')
                        row['analyst_categories'] = '; '.join(analyst_cats) if isinstance(analyst_cats, (list, tuple)) else (analyst_cats or '')
                        
                        writer.writerow(row)
            
            # Convert to BytesIO
            output.seek(0)
            csv_bytes = BytesIO(output.getvalue().encode('utf-8-sig'))  # BOM for Excel compatibility
            return csv_bytes
            
        except Exception as e:
            logger.error(f"Error exporting CSV: {e}", exc_info=True)
            raise
    
    @staticmethod
    def export_search_results_excel(
        results: List[Dict[str, Any]],
        filename: Optional[str] = None,
        include_line_matches: bool = True
    ) -> BytesIO:
        """
        Export search results to Excel format.
        
        Args:
            results: List of search result dictionaries
            filename: Optional filename (not used, kept for compatibility)
            include_line_matches: If True, export each matching line as a separate row
        
        Returns:
            BytesIO object containing Excel data
        
        Raises:
            ImportError: If openpyxl is not installed
        """
        if not EXCEL_AVAILABLE:
            raise ImportError("openpyxl is required for Excel export. Install it with: pip install openpyxl")
        
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Search Results"
            
            if not results:
                # Write header only
                headers = [
                    'ID', 'File Name', 'File Path', 'File Type', 'File Size',
                    'File Date', 'File Status', 'Source Name', 'Side Name'
                ]
                ws.append(headers)
            else:
                # Check if we should export line matches as separate rows
                has_line_matches = any(result.get('line_matches') for result in results)
                
                if include_line_matches and has_line_matches:
                    # Export with line matches - each matching line is a separate row
                    headers = [
                        'File ID', 'File Name', 'File Path', 'File Type', 'File Size',
                        'File Date', 'File Status', 'Source Name', 'Side Name',
                        'Line Number', 'Line Text', 'Context Before', 'Context After'
                    ]
                    ws.append(headers)
                    
                    for result in results:
                        file_id = result.get('id', '')
                        file_name = result.get('file_name', '')
                        file_path = result.get('file_path', '')
                        file_type = result.get('file_type', '')
                        file_size = result.get('file_size', '')
                        file_date = result.get('file_date', '')
                        if file_date and not isinstance(file_date, str):
                            file_date = file_date.isoformat()
                        file_status = result.get('file_status', '')
                        source_name = result.get('source_name', '')
                        side_name = result.get('side_name', '')
                        
                        line_matches = result.get('line_matches', [])
                        if line_matches:
                            # Write one row per matching line
                            for match in line_matches:
                                ws.append([
                                    file_id, file_name, file_path, file_type, file_size,
                                    file_date, file_status, source_name, side_name,
                                    match.get('line_number', ''),
                                    match.get('line_text', ''),
                                    match.get('context_before', ''),
                                    match.get('context_after', '')
                                ])
                        else:
                            # Write one row for file with no line matches
                            ws.append([
                                file_id, file_name, file_path, file_type, file_size,
                                file_date, file_status, source_name, side_name,
                                '', '', '', ''
                            ])
                else:
                    # Standard export - one row per file
                    # Get all fieldnames
                    fieldnames = set()
                    for result in results:
                        fieldnames.update(result.keys())
                    
                    # Remove line_matches from fieldnames
                    fieldnames.discard('line_matches')
                    
                    # Order fieldnames
                    ordered_fieldnames = [
                        'id', 'file_name', 'file_path', 'file_type', 'file_size',
                        'file_date', 'file_status', 'date_creation',
                        'source_name', 'side_name', 'source_id', 'side_id',
                        'relevance_score', 'line_match_count'
                    ]
                    for field in ordered_fieldnames:
                        if field in fieldnames:
                            fieldnames.remove(field)
                    ordered_fieldnames.extend(sorted(fieldnames))
                    
                    # Write headers
                    headers = [field.replace('_', ' ').title() for field in ordered_fieldnames]
                    ws.append(headers)
                    
                    # Write data
                    for result in results:
                        row = []
                        for field in ordered_fieldnames:
                            value = result.get(field, '')
                            # Convert dates to strings
                            if field in ('file_date', 'date_creation') and value:
                                if not isinstance(value, str):
                                    value = value.isoformat()
                            row.append(value)
                        ws.append(row)
                
                # Auto-adjust column widths (limit to prevent freezing on very wide sheets)
                try:
                    for column in ws.columns:
                        max_length = 0
                        column_letter = column[0].column_letter
                        # Limit iteration to first 1000 rows to prevent freezing
                        for idx, cell in enumerate(column):
                            if idx > 1000:
                                break
                            try:
                                if cell.value and len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except:
                                pass
                        adjusted_width = min(max_length + 2, 50)
                        ws.column_dimensions[column_letter].width = adjusted_width
                except Exception as width_error:
                    # Non-fatal: continue without auto-width adjustment
                    logger.debug(f"Could not auto-adjust column widths: {width_error}")
            
            # Save to BytesIO with error handling
            output = BytesIO()
            try:
                wb.save(output)
                output.seek(0)
                return output
            except Exception as save_error:
                logger.error(f"Error saving Excel workbook to BytesIO: {save_error}")
                # Try to close workbook and cleanup
                try:
                    wb.close()
                except:
                    pass
                raise
            
        except PermissionError as e:
            logger.error(f"Permission denied exporting Excel: {e}")
            raise
        except MemoryError as e:
            logger.error(f"Out of memory exporting Excel (file too large): {e}")
            raise
        except Exception as e:
            logger.error(f"Error exporting Excel: {e}", exc_info=True)
            raise
    
    @staticmethod
    def export_search_results_json(
        results: List[Dict[str, Any]],
        filename: Optional[str] = None
    ) -> BytesIO:
        """
        Export search results to JSON format.
        
        Args:
            results: List of search result dictionaries
            filename: Optional filename (not used, kept for compatibility)
        
        Returns:
            BytesIO object containing JSON data
        """
        try:
            export_data = {
                'export_date': datetime.now().isoformat(),
                'total_results': len(results),
                'results': results
            }
            
            json_str = json.dumps(export_data, indent=2, ensure_ascii=False, default=str)
            return BytesIO(json_str.encode('utf-8'))
            
        except Exception as e:
            logger.error(f"Error exporting JSON: {e}", exc_info=True)
            raise
    
    @staticmethod
    def export_database_backup(
        tables: Optional[List[str]] = None,
        include_data: bool = True
    ) -> BytesIO:
        """
        Export database backup (schema and optionally data).
        
        Args:
            tables: List of table names to export (None for all)
            include_data: Whether to include data or just schema
        
        Returns:
            BytesIO object containing backup data
        """
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            backup_data = {
                'export_date': datetime.now().isoformat(),
                'include_data': include_data,
                'tables': {}
            }
            
            # Get list of tables. Default (and hard cap): the evidence
            # tables shared with the backup importer (ALLOWED_TABLES).
            # System tables (users, sessions, audit_log, jobs,
            # schema_migrations, ...) are never exported: they must not be
            # restored over a live system, and credential/session material
            # must not end up in backup files.
            if tables is None:
                from Api.services.import_service import ImportService

                tables = sorted(ImportService.ALLOWED_TABLES)
            else:
                from Api.services.import_service import ImportService

                disallowed = [t for t in tables
                              if t not in ImportService.ALLOWED_TABLES]
                if disallowed:
                    raise ValueError(
                        "Tables not exportable: " + ", ".join(sorted(disallowed))
                    )
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            existing = {row[0] for row in cursor.fetchall()}
            tables = [t for t in tables if t in existing]
            
            for table_name in tables:
                try:
                    # Get table schema
                    cursor.execute("""
                        SELECT column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_name = %s
                        AND table_schema = 'public'
                        ORDER BY ordinal_position
                    """, (table_name,))
                    
                    columns = []
                    for col in cursor.fetchall():
                        columns.append({
                            'name': col[0],
                            'type': col[1],
                            'nullable': col[2] == 'YES',
                            'default': col[3]
                        })
                    
                    table_info = {
                        'schema': columns
                    }
                    
                    # Get data if requested
                    if include_data:
                        cursor.execute(f"SELECT * FROM {table_name} LIMIT 10000")
                        rows = cursor.fetchall()
                        table_info['row_count'] = len(rows)
                        table_info['data'] = []
                        
                        # Convert rows to dictionaries
                        column_names = [col['name'] for col in columns]
                        for row in rows:
                            row_dict = {}
                            for i, col_name in enumerate(column_names):
                                value = row[i]
                                # Convert dates and other types to strings
                                if value is None:
                                    row_dict[col_name] = None
                                elif isinstance(value, (datetime, date)):
                                    row_dict[col_name] = value.isoformat()
                                elif isinstance(value, bytes):
                                    row_dict[col_name] = f"<BYTEA: {len(value)} bytes>"
                                else:
                                    row_dict[col_name] = value
                            table_info['data'].append(row_dict)
                    
                    backup_data['tables'][table_name] = table_info
                    
                except Exception as e:
                    logger.warning(f"Error exporting table {table_name}: {e}")
                    continue
            
            cursor.close()
            return_connection(conn)
            
            # Create ZIP file with JSON backup
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                json_str = json.dumps(backup_data, indent=2, ensure_ascii=False, default=str)
                zip_file.writestr('backup.json', json_str.encode('utf-8'))
            
            zip_buffer.seek(0)
            return zip_buffer
            
        except Exception as e:
            logger.error(f"Error exporting database backup: {e}", exc_info=True)
            if conn:
                return_connection(conn)
            raise
    
    @staticmethod
    def export_settings(settings_data: Dict[str, Any]) -> BytesIO:
        """
        Export settings to JSON file.
        
        Args:
            settings_data: Dictionary containing settings
        
        Returns:
            BytesIO object containing settings JSON
        """
        try:
            export_data = {
                'export_date': datetime.now().isoformat(),
                'settings': settings_data
            }
            
            json_str = json.dumps(export_data, indent=2, ensure_ascii=False, default=str)
            return BytesIO(json_str.encode('utf-8'))
            
        except Exception as e:
            logger.error(f"Error exporting settings: {e}", exc_info=True)
            raise

