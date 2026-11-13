"""
Import Service
Handles importing files, database backups, and settings
"""

import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from io import BytesIO
import zipfile
import csv
from Api.utils import get_connection, return_connection



logger = logging.getLogger(__name__)

#: Maximum accepted size of a decompressed backup.json (SEC-04 size limits)
MAX_BACKUP_JSON_BYTES = 512 * 1024 * 1024


def order_paths_by_lineage(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order ``paths`` rows so a parent precedes any child referencing it.

    ``paths.parent_path_id`` is a self-referencing foreign key (migration 0007),
    so a bulk insert must create parents first. A JSON backup preserves the
    original ids, so the required order is recoverable from the data itself.

    Iterative rather than recursive: a crafted backup could otherwise exhaust
    the Python stack. A row whose parent is absent from the batch is emitted in
    place, so the foreign key still rejects it loudly instead of the restore
    silently reordering around corrupt data.
    """
    if not rows:
        return rows

    by_id = {
        row["id"]: row
        for row in rows
        if isinstance(row, dict) and row.get("id") is not None
    }
    if not by_id:
        return list(rows)

    ordered: List[Dict[str, Any]] = []
    emitted = set()

    for row in rows:
        if not isinstance(row, dict):
            ordered.append(row)
            continue

        # Walk up to the topmost ancestor, then emit downwards.
        pending = []
        current = row
        seen = set()
        while isinstance(current, dict):
            rid = current.get("id")
            if rid in emitted or rid in seen:
                break
            seen.add(rid)
            pending.append(current)
            parent_id = current.get("parent_path_id")
            if parent_id is None or parent_id not in by_id or parent_id in emitted:
                break
            current = by_id[parent_id]

        for item in reversed(pending):
            item_id = item.get("id")
            if item_id in emitted:
                continue
            ordered.append(item)
            emitted.add(item_id)

    return ordered


class ImportService:
    """
    Service for importing data in various formats.
    
    Supports:
    - Batch file import
    - Database backup import
    - Settings import
    """
    

    
    @staticmethod
    def import_database_backup(
        backup_file: BytesIO,
        restore_data: bool = False
    ) -> Dict[str, Any]:
        """
        Import database backup.

        Args:
            backup_file: BytesIO object containing backup ZIP file
                (ZIP with a single ``backup.json`` member, as produced by
                ``Api.services.export_service.ExportService``).
            restore_data: When True, performs the restore: rows in
                ``ALLOWED_TABLES`` are deleted and re-inserted from the
                backup (bounded by MAX_BACKUP_ROWS_PER_TABLE). Only tables
                in ``ALLOWED_TABLES`` are ever touched; schema is always
                taken from the live information_schema. When False the
                backup is only validated (dry run, no writes).

        Returns:
            Dictionary with import validation/restoration results
        """
        try:
            # Extract and validate backup
            backup_file.seek(0)
            
            with zipfile.ZipFile(backup_file, 'r') as zip_file:
                if 'backup.json' not in zip_file.namelist():
                    raise ValueError("Invalid backup file: backup.json not found")
                
                backup_data = json.loads(zip_file.read('backup.json').decode('utf-8'))
            
            # Validate backup structure
            validation_results = {
                'valid': True,
                'export_date': backup_data.get('export_date'),
                'include_data': backup_data.get('include_data', False),
                'tables': list(backup_data.get('tables', {}).keys()),
                'table_count': len(backup_data.get('tables', {})),
                'warnings': []
            }
            
            # Check if tables exist in current database
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE'
            """)
            existing_tables = {row[0] for row in cursor.fetchall()}
            
            backup_tables = set(validation_results['tables'])
            missing_tables = backup_tables - existing_tables
            extra_tables = existing_tables - backup_tables
            
            if missing_tables:
                validation_results['warnings'].append(
                    f"Backup contains tables not in current database: {', '.join(missing_tables)}"
                )
            
            if extra_tables:
                validation_results['warnings'].append(
                    f"Current database has tables not in backup: {', '.join(extra_tables)}"
                )
            
            # If restore_data is True, perform actual restoration
            if restore_data:
                if not backup_data.get('include_data', False):
                    validation_results['valid'] = False
                    validation_results['error'] = "Backup does not contain data. Cannot restore."
                    cursor.close()
                    return_connection(conn)
                    return validation_results
                
                # Validate schema compatibility before restoration
                schema_validation = ImportService._validate_schema_compatibility(
                    backup_data, cursor, existing_tables
                )
                
                if not schema_validation['compatible']:
                    validation_results['valid'] = False
                    validation_results['error'] = "Schema incompatibility detected"
                    validation_results['schema_errors'] = schema_validation['errors']
                    cursor.close()
                    return_connection(conn)
                    return validation_results
                
                # Perform restoration in a transaction
                try:
                    restoration_result = ImportService._restore_backup_data(
                        backup_data, conn, cursor
                    )
                    validation_results.update(restoration_result)
                    conn.commit()
                except Exception as restore_error:
                    conn.rollback()
                    logger.error(f"Error restoring backup data: {restore_error}", exc_info=True)
                    validation_results['valid'] = False
                    validation_results['error'] = f"Restoration failed: {str(restore_error)}"
                    raise
            else:
                validation_results['warnings'].append(
                    "Data restoration not requested. Use restore_data=True to restore."
                )
            
            cursor.close()
            return_connection(conn)
            
            return validation_results
            
        except Exception as e:
            logger.error(f"Error importing database backup: {e}", exc_info=True)
            return {
                'valid': False,
                'error': str(e)
            }
    
    # ------------------------------------------------------------------
    # SEC-04: backup import hardening constants
    # ------------------------------------------------------------------
    #: Only these application tables may ever be touched by backup import.
    #: Anything else in the backup is rejected (SQL identifiers from an
    #: uploaded file are untrusted input).
    ALLOWED_TABLES = frozenset(
        {
            "words", "punctuation", "categorys", "words_categorys", "sides",
            "sources", "hashs", "hash_contexts", "paths", "contents", "contents_raw",
            "titles_content", "keywords", "words_hashs", "keywords_hashs", "alerts",
        }
    )
    MAX_BACKUP_ROWS_PER_TABLE = 500_000
    MAX_BACKUP_VALUE_LENGTH = 5 * 1024 * 1024  # 5 MB per value
    _IDENTIFIER_RE = None  # compiled lazily

    @staticmethod
    def _validate_identifier(name: Any) -> str:
        """Strict SQL identifier validation for names arriving from backup data."""
        import re

        if not isinstance(name, str) or not re.match(r"^[a-z_][a-z0-9_]*$", name or ""):
            raise ValueError(f"Invalid identifier in backup: {name!r}")
        return name

    @staticmethod
    def _validate_schema_compatibility(
        backup_data: Dict[str, Any],
        cursor,
        existing_tables: set
    ) -> Dict[str, Any]:
        """
        Validate that backup schema is compatible with current database schema.

        SEC-04: table and column names are validated against an allowlist and
        the live information_schema; nothing from the backup file is ever
        interpolated into SQL.
        """
        errors = []
        backup_tables = backup_data.get('tables', {})

        for table_name, table_info in backup_tables.items():
            try:
                table_name = ImportService._validate_identifier(table_name)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if table_name not in ImportService.ALLOWED_TABLES:
                errors.append(f"Table '{table_name}' is not importable (not in allowlist)")
                continue
            if table_name not in existing_tables:
                errors.append(f"Table '{table_name}' does not exist in current database")
                continue

            # Get current table schema (parameterized)
            cursor.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = %s
                AND table_schema = 'public'
                ORDER BY ordinal_position
            """, (table_name,))

            current_columns = {
                row[0]: {'type': row[1], 'nullable': row[2] == 'YES'}
                for row in cursor.fetchall()
            }

            backup_schema = table_info.get('schema', [])
            if not isinstance(backup_schema, list):
                errors.append(f"Table '{table_name}': schema must be a list")
                continue
            backup_columns = {}
            for col in backup_schema:
                if not isinstance(col, dict) or not isinstance(col.get('name'), str):
                    errors.append(f"Table '{table_name}': invalid column entry in schema")
                    continue
                try:
                    col_name = ImportService._validate_identifier(col['name'])
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                backup_columns[col_name] = {
                    'type': col.get('type', 'unknown'),
                    'nullable': col.get('nullable', True)
                }

            # Check for missing columns in current schema
            missing_columns = set(backup_columns.keys()) - set(current_columns.keys())
            if missing_columns:
                errors.append(
                    f"Table '{table_name}' has columns missing in current schema: {', '.join(sorted(missing_columns))}"
                )

            # Check for type mismatches (basic check)
            for col_name, backup_col in backup_columns.items():
                if col_name in current_columns:
                    current_col = current_columns[col_name]
                    backup_type = str(backup_col['type']).upper()
                    current_type = current_col['type'].upper()

                    # Allow some type variations (e.g., VARCHAR vs TEXT)
                    type_compatible = (
                        backup_type == current_type or
                        (backup_type in ('VARCHAR', 'TEXT', 'CHARACTER VARYING') and
                         current_type in ('VARCHAR', 'TEXT', 'CHARACTER VARYING')) or
                        (backup_type in ('INTEGER', 'INT', 'BIGINT', 'SMALLINT') and
                         current_type in ('INTEGER', 'INT', 'BIGINT', 'SMALLINT'))
                    )

                    if not type_compatible:
                        errors.append(
                            f"Table '{table_name}', column '{col_name}': "
                            f"type mismatch (backup: {backup_type}, current: {current_type})"
                        )

            # Row-count limit
            data = table_info.get('data') or []
            if not isinstance(data, list):
                errors.append(f"Table '{table_name}': data must be a list")
            elif len(data) > ImportService.MAX_BACKUP_ROWS_PER_TABLE:
                errors.append(
                    f"Table '{table_name}': row count {len(data)} exceeds the import limit "
                    f"({ImportService.MAX_BACKUP_ROWS_PER_TABLE})"
                )

        return {
            'compatible': len(errors) == 0,
            'errors': errors
        }

    @staticmethod
    def _restore_backup_data(
        backup_data: Dict[str, Any],
        conn,
        cursor
    ) -> Dict[str, Any]:
        """
        Restore data from a validated backup.

        SEC-04 hardening:
        * identifiers validated + allowlisted, composed via psycopg2.sql
        * values always parameterized
        * single transaction; caller rolls back on any failure
        """
        from psycopg2 import sql as pg_sql
        from psycopg2.extras import Json

        restored_tables = []
        restored_rows = 0
        errors = []

        backup_tables = backup_data.get('tables', {})

        # FK-aware ordering: children are deleted first (so no parent row is
        # removed while still referenced) and parents are inserted first (so
        # every FK target exists before its dependent). The order is derived
        # from the LIVE pg_catalog constraints, never from the backup file.
        restore_set = set()
        for _tn, _ti in backup_tables.items():
            if _ti.get('data'):
                try:
                    restore_set.add(ImportService._validate_identifier(_tn))
                except ValueError:
                    continue

        cursor.execute(
            "SELECT conrelid::regclass::text AS child,"
            " confrelid::regclass::text AS parent"
            " FROM pg_constraint"
            " WHERE contype = 'f'"
            " AND connamespace = 'public'::regnamespace"
        )
        edges = set()
        for child, parent in cursor.fetchall():
            child = child.split('.')[-1].strip('"')
            parent = parent.split('.')[-1].strip('"')
            if child != parent and child in restore_set and parent in restore_set:
                edges.add((parent, child))  # parent must exist before child

        # Kahn topological sort (parents first); self-references ignored.
        indegree = {t: 0 for t in restore_set}
        children_of = {t: [] for t in restore_set}
        for parent, child in edges:
            indegree[child] += 1
            children_of[parent].append(child)
        queue = sorted(t for t, d in indegree.items() if d == 0)
        insert_order = []
        while queue:
            t = queue.pop(0)
            insert_order.append(t)
            for c in sorted(children_of[t]):
                indegree[c] -= 1
                if indegree[c] == 0:
                    queue.append(c)
        if len(insert_order) != len(restore_set):
            raise ValueError(
                "Backup tables contain a foreign-key cycle; restore refused"
            )
        delete_order = list(reversed(insert_order))

        for _tn in delete_order:
            cursor.execute(
                pg_sql.SQL("DELETE FROM {}").format(pg_sql.Identifier(_tn))
            )

        for table_name in insert_order:
            table_info = backup_tables[table_name]
            if not table_info.get('data'):
                continue

            try:
                table_name = ImportService._validate_identifier(table_name)
                if table_name not in ImportService.ALLOWED_TABLES:
                    raise ValueError(f"Table '{table_name}' is not importable")

                # Column names validated against identifier rules AND the
                # columns that actually exist for this table.
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = %s AND table_schema = 'public'",
                    (table_name,),
                )
                live_columns = {row[0] for row in cursor.fetchall()}

                validated_columns = []
                for col in table_info.get('schema', []):
                    col_name = ImportService._validate_identifier(
                        col.get('name') if isinstance(col, dict) else None
                    )
                    if col_name not in live_columns:
                        raise ValueError(
                            f"Column '{col_name}' does not exist in table '{table_name}'"
                        )
                    validated_columns.append(col_name)

                if not validated_columns:
                    errors.append(f"Table '{table_name}': No schema information available")
                    continue

                # GENERATED ALWAYS identity columns reject explicit values
                # unless the statement overrides them (backups always carry
                # the original ids, and referential integrity needs them).
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = %s AND table_schema = 'public'"
                    " AND is_identity = 'YES' AND identity_generation = 'ALWAYS'",
                    (table_name,),
                )
                identity_columns = {row[0] for row in cursor.fetchall()}
                overriding = ""
                if identity_columns & set(validated_columns):
                    overriding = " OVERRIDING SYSTEM VALUE"

                # Safe identifier composition + parameterized values
                query = pg_sql.SQL(
                    "INSERT INTO {} ({})" + overriding + " VALUES ({})"
                ).format(
                    pg_sql.Identifier(table_name),
                    pg_sql.SQL(", ").join(map(pg_sql.Identifier, validated_columns)),
                    pg_sql.SQL(", ").join(pg_sql.Placeholder() for _ in validated_columns),
                )

                batch_size = 1000
                data_rows = table_info['data']

                # paths.parent_path_id is a self-FK, so parents must be
                # inserted before the children that reference them.
                if table_name == 'paths':
                    data_rows = order_paths_by_lineage(data_rows)

                for i in range(0, len(data_rows), batch_size):
                    batch = data_rows[i:i + batch_size]
                    batch_values = []

                    for row in batch:
                        if not isinstance(row, dict):
                            raise ValueError("Backup row is not an object")
                        values = []
                        for col_name in validated_columns:
                            value = row.get(col_name)

                            if value is None:
                                values.append(None)
                            elif isinstance(value, str) and value.startswith('<BYTEA:'):
                                # Binary payloads are exported as markers; they
                                # cannot be reconstructed from a JSON backup.
                                values.append(None)
                            elif isinstance(value, str) and len(value) > ImportService.MAX_BACKUP_VALUE_LENGTH:
                                raise ValueError("Backup value exceeds size limit")
                            elif isinstance(value, (dict, list)):
                                # JSONB columns round-trip through the JSON
                                # backup as Python dict/list, which psycopg2
                                # cannot adapt directly. Not specific to
                                # paths.extraction_provenance: jobs.options,
                                # jobs.stats, jobs.errors, jobs.warnings and
                                # job_queue.payload are JSONB too.
                                values.append(Json(value))
                            else:
                                values.append(value)

                        batch_values.append(tuple(values))

                    if batch_values:
                        cursor.executemany(query, batch_values)

                # Resync identity sequences so post-restore inserts continue
                # after the highest restored id instead of colliding.
                for id_col in identity_columns:
                    stmt = pg_sql.SQL(
                        "SELECT setval(pg_get_serial_sequence(%s, %s),"
                        " COALESCE((SELECT MAX({col}) FROM {tbl}), 0) + 1,"
                        " false)"
                    ).format(col=pg_sql.Identifier(id_col),
                             tbl=pg_sql.Identifier(table_name))
                    cursor.execute(stmt, (table_name, id_col))

                restored_tables.append(table_name)
                restored_rows += len(data_rows)

            except ValueError as e:
                # Deterministic validation error: report without internals.
                logger.error("Backup restore validation error: %s", e)
                errors.append(str(e))
                raise  # abort the whole transactional restore
            except Exception as e:
                logger.error("Error restoring table during backup import", exc_info=True)
                errors.append(f"Error restoring table '{table_name}'")
                raise  # abort the whole transactional restore

        return {
            'restored_tables': restored_tables,
            'restored_rows': restored_rows,
            'restoration_errors': errors,
            'restoration_success': len(errors) == 0
        }
    
    @staticmethod
    def import_settings(
        settings_file: BytesIO
    ) -> Dict[str, Any]:
        """
        Import settings from JSON file.
        
        Args:
            settings_file: BytesIO object containing settings JSON
        
        Returns:
            Dictionary with imported settings and validation results
        """
        try:
            settings_file.seek(0)
            data = json.loads(settings_file.read().decode('utf-8'))
            
            # Extract settings
            if 'settings' in data:
                settings = data['settings']
            else:
                settings = data
            
            # Validate settings structure
            validation_results = {
                'valid': True,
                'imported_settings': settings,
                'export_date': data.get('export_date'),
                'warnings': []
            }
            
            # Basic validation
            if not isinstance(settings, dict):
                validation_results['valid'] = False
                validation_results['error'] = "Settings must be a dictionary"
                return validation_results
            
            return validation_results
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing settings JSON: {e}", exc_info=True)
            return {
                'valid': False,
                'error': f"Invalid JSON: {str(e)}"
            }
        except Exception as e:
            logger.error(f"Error importing settings: {e}", exc_info=True)
            return {
                'valid': False,
                'error': str(e)
            }
    
    @staticmethod
    def import_file_list_from_csv(
        csv_file: BytesIO,
        source_id: int,
        side_id: int
    ) -> Dict[str, Any]:
        """
        Import file list from CSV and process files.

        CSV should have a 'file_path' or 'path' column. Every path is
        validated against the configured ingestion roots (SEC-06) - paths
        outside the approved roots are rejected.
        """
        try:
            csv_file.seek(0)
            raw = csv_file.read()
            if len(raw) > ImportService.MAX_IMPORT_CSV_BYTES:
                return {'valid': False, 'error': 'CSV file exceeds the allowed size'}
            csv_content = raw.decode('utf-8-sig')  # Handle BOM
            csv_reader = csv.DictReader(csv_content.splitlines())

            file_paths = []
            for row in csv_reader:
                # Try different column names
                file_path = row.get('file_path') or row.get('path') or row.get('filepath')
                if file_path:
                    file_paths.append(file_path)
            if not file_paths:
                return {
                    'valid': False,
                    'error': 'No file paths found in CSV. Expected column: file_path, path, or filepath'
                }
            if len(file_paths) > ImportService.MAX_IMPORT_PATHS:
                return {'valid': False, 'error': 'Too many paths in CSV'}

            # Import files (path validation happens inside import_batch_files)
            return ImportService.import_batch_files(file_paths, source_id, side_id)

        except UnicodeDecodeError:
            return {'valid': False, 'error': 'CSV file must be UTF-8 encoded'}
        except Exception:
            logger.error("Error importing file list from CSV", exc_info=True)
            from core.errors import new_correlation_id
            return {
                'valid': False,
                'error': 'An internal error occurred while importing the CSV',
                'correlation_id': new_correlation_id()
            }

    # ------------------------------------------------------------------
    # Batch import (SEC-06: no arbitrary server-path access)
    # ------------------------------------------------------------------
    MAX_IMPORT_PATHS = 5000
    MAX_IMPORT_CSV_BYTES = 10 * 1024 * 1024

    @staticmethod
    def import_batch_files(
        file_paths: List[str],
        source_id: int,
        side_id: int,
    ) -> Dict[str, Any]:
        """
        Queue a batch of server-side files for ingestion.

        Security (SEC-06): every path is validated against the configured
        ``INGESTION_ROOTS``. An empty roots configuration disables direct
        server-path import entirely; clients must upload files instead.

        Returns a per-path result list; never raises to the caller.

        Accounting: every submitted path ends in exactly one outcome, and the
        response says which one.

        * ``rejected`` - path validation refused it (unsafe, missing, not a
          file). Nothing was queued.
        * ``queued`` - validation passed and a processing task was started.
        * ``deferred`` - validation passed but no task could be started (the
          concurrent-task cap, or the task subsystem being unavailable). The
          file was NOT queued and has to be resubmitted later; it is reported
          rather than dropped.

        ``accepted`` is the number that passed validation, so it always equals
        ``queued + deferred``, and ``accepted + rejected == total``: no file can
        disappear from the counters (a deferred file used to be counted as
        accepted *and* as an error, which is what this accounting removes).
        """
        from core.path_safety import validate_ingestion_path, PathSafetyError

        results: List[Dict[str, Any]] = []
        validated: List[Dict[str, Any]] = []
        rejected = 0

        if not isinstance(file_paths, list) or not file_paths:
            return {'valid': False, 'error': 'No file paths provided', 'results': []}
        if len(file_paths) > ImportService.MAX_IMPORT_PATHS:
            return {'valid': False, 'error': 'Too many file paths in one request',
                    'results': []}

        # Fail closed: path validation with no configured roots raises for
        # every path (server-path import disabled).
        for raw_path in file_paths[:ImportService.MAX_IMPORT_PATHS]:
            entry: Dict[str, Any] = {'path': str(raw_path)[:512]}
            try:
                resolved = validate_ingestion_path(raw_path)
                if not resolved.is_file():
                    entry.update({'status': 'rejected', 'error': 'File not found'})
                    rejected += 1
                else:
                    entry['resolved_path'] = str(resolved)
                    # validated: whether it is queued or deferred is decided
                    # below, when the task manager is asked for a slot
                    validated.append(entry)
            except PathSafetyError as exc:
                entry.update({'status': 'rejected', 'error': str(exc)})
                rejected += 1
            except Exception:
                logger.exception("Unexpected error validating import path")
                entry.update({'status': 'rejected', 'error': 'Path validation failed'})
                rejected += 1
            results.append(entry)

        # Queue validated files through the task manager (wired concurrency
        # subsystem); each task runs the full reader -> storage pipeline. A file
        # the task manager refuses is deferred and said so, never silently
        # dropped: create_task raises at the concurrent-task cap, and the
        # operator has to submit that file again once a slot frees up.
        queued = 0
        deferred = 0
        if validated:
            try:
                from Api.task_manager import get_task_manager

                tm = get_task_manager()
            except Exception:
                logger.exception("Task manager unavailable for batch import")
                tm = None
            for entry in validated:
                if tm is None:
                    entry.update({'status': 'deferred',
                                  'error': 'Processing subsystem unavailable '
                                           '(deferred); resubmit this file later'})
                    deferred += 1
                    continue
                try:
                    task_id = tm.create_task(
                        file_path=entry['resolved_path'],
                        source_id=int(source_id),
                        side_id=int(side_id),
                    )
                except Exception as task_exc:
                    # Not an error of the file: the batch is larger than the
                    # concurrent-task capacity. Reported, and counted.
                    logger.warning("Deferred %s: %s", entry['path'],
                                   task_exc.__class__.__name__)
                    entry.update({'status': 'deferred',
                                  'error': 'No processing slot was free (deferred); '
                                           'resubmit this file later'})
                    deferred += 1
                else:
                    entry['task_id'] = task_id
                    entry['status'] = 'queued'
                    queued += 1

        accepted = queued + deferred
        # Every submitted path has one outcome; this is the accounting the
        # response promises (and tests/unit/test_batch_import_accounting.py
        # pins it).
        if accepted + rejected != len(results):
            logger.error("Batch import accounting mismatch: accepted=%s rejected=%s "
                         "results=%s", accepted, rejected, len(results))

        return {
            'valid': True,
            'total': len(file_paths),
            'accepted': accepted,
            'rejected': rejected,
            'queued': queued,
            'deferred': deferred,
            'results': results,
        }
