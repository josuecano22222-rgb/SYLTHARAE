"""
Database file reader - Extract schema and data from database files
Aligned with database design principles - all functions within class
Supports: SQLite, DB files
"""

import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Set

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error, is_retryable_error,
    ErrorCategory, ErrorSeverity, format_validation_error,
    safe_execute
)

logger = logging.getLogger(__name__)


class DatabaseFileReader(BaseReader):
    """
    Reader for database files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported database extensions"""
        return {
            '.db', '.sqlite', '.sqlite3', '.db3', '.s3db', '.sdb'
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read database file and extract schema with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with database schema and data, or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        
        try:
            return self.read_sqlite_file(file_path)
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def read_sqlite_file(self, filepath: str, max_rows_per_table: int = 100) -> Dict[str, Any]:
        """
        Read SQLite database and extract schema + sample data with improved error handling
        
        Args:
            filepath: Path to SQLite database
            max_rows_per_table: Maximum rows to extract per table (default: 100)
        
        Returns:
            dict: Database schema and sample data, or error information
        """
        # Initialize result with basic info
        result: Dict[str, Any] = {
            "filepath": filepath,
            "format": "SQLite"
        }
        
        # Validate filepath
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_sqlite_file'}
            )
            result["error"] = error_msg
            return result
        
        # Check if file exists
        if not os.path.exists(filepath):
            error_msg = f"File not found: {filepath}"
            handle_error(
                FileNotFoundError(error_msg),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_sqlite_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        # Get file size safely
        try:
            result["file_size"] = os.path.getsize(filepath)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_sqlite_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        # Validate max_rows_per_table
        if not isinstance(max_rows_per_table, int) or max_rows_per_table < 0:
            logger.warning(f"Invalid max_rows_per_table: {max_rows_per_table}, using default 100")
            max_rows_per_table = 100
        
        try:
            import sqlite3
        except ImportError:
            error_msg = "sqlite3 module not available"
            handle_error(
                ImportError(error_msg),
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.HIGH,
                context={'operation': 'read_sqlite_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        conn = None
        try:
            # Connect to database with timeout
            conn = sqlite3.connect(filepath, timeout=30.0)
            cursor = conn.cursor()
            
            # Get SQLite version
            cursor.execute("SELECT sqlite_version();")
            result["sqlite_version"] = cursor.fetchone()[0]
            
            # Get list of tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = [row[0] for row in cursor.fetchall()]
            
            result["tables"] = []
            result["table_count"] = len(tables)
            
            for table_name in tables:
                table_info = {
                    "name": table_name,
                    "columns": [],
                    "row_count": 0,
                    "sample_data": [],
                    "indexes": [],
                    "foreign_keys": []
                }
                
                # Get column info
                cursor.execute(f"PRAGMA table_info({table_name});")
                columns = cursor.fetchall()
                table_info["columns"] = [
                    {
                        "id": col[0],
                        "name": col[1],
                        "type": col[2],
                        "not_null": bool(col[3]),
                        "default_value": col[4],
                        "primary_key": bool(col[5])
                    }
                    for col in columns
                ]
                table_info["column_count"] = len(table_info["columns"])
                
                # Get row count
                cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
                table_info["row_count"] = cursor.fetchone()[0]
                
                # Get indexes
                cursor.execute(f"PRAGMA index_list({table_name});")
                indexes = cursor.fetchall()
                table_info["indexes"] = [
                    {
                        "name": idx[1],
                        "unique": bool(idx[2]),
                        "origin": idx[3]
                    }
                    for idx in indexes
                ]
                
                # Get foreign keys
                cursor.execute(f"PRAGMA foreign_key_list({table_name});")
                fks = cursor.fetchall()
                table_info["foreign_keys"] = [
                    {
                        "id": fk[0],
                        "from_column": fk[3],
                        "to_table": fk[2],
                        "to_column": fk[4]
                    }
                    for fk in fks
                ]
                
                # Get sample data
                if table_info["row_count"] > 0:
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT {max_rows_per_table};")
                    rows = cursor.fetchall()
                    table_info["sample_data"] = [
                        dict(zip([col["name"] for col in table_info["columns"]], row))
                        for row in rows
                    ]
                    table_info["sample_count"] = len(table_info["sample_data"])
                
                result["tables"].append(table_info)
            
            # Get views
            cursor.execute("SELECT name FROM sqlite_master WHERE type='view';")
            views = [row[0] for row in cursor.fetchall()]
            result["views"] = views
            result["view_count"] = len(views)
            
            # Get triggers
            cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger';")
            triggers = [row[0] for row in cursor.fetchall()]
            result["triggers"] = triggers
            result["trigger_count"] = len(triggers)
            
            # Calculate statistics safely
            try:
                result["total_rows"] = sum(t.get("row_count", 0) for t in result.get("tables", []))
                result["total_columns"] = sum(t.get("column_count", 0) for t in result.get("tables", []))
            except Exception as stats_error:
                logger.warning(f"Error calculating statistics: {stats_error}")
                result["total_rows"] = 0
                result["total_columns"] = 0
            
            return result
            
        except sqlite3.DatabaseError as e:
            error_msg = f"Database error: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.DATABASE,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_sqlite_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        except sqlite3.OperationalError as e:
            error_msg = f"Database operational error: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.DATABASE,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_sqlite_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        except Exception as e:
            error_msg = f"Unexpected error reading database: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_sqlite_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        finally:
            # Ensure connection is closed
            if conn:
                try:
                    conn.close()
                except Exception as close_error:
                    logger.warning(f"Error closing database connection: {close_error}")



