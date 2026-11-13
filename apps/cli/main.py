
import os
import hashlib
from pathlib import Path
import sys
from typing import List, Optional

# Ensure UTF-8 encoding for stdout on Windows (emoji progress output)
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except (AttributeError, ValueError):
        # reconfigure not available
        pass

# Line-buffer stdout on every platform so print() lines flush atomically;
# merged stdout/stderr logs then interleave only at line boundaries, never
# mid-line ("Progress: 2/11INFO:...").
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except (AttributeError, ValueError, OSError):
        pass

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

def safe_print(message: str) -> None:
    """Safely print message, handling encoding issues"""
    try:
        print(message)
    except UnicodeEncodeError:
        # Fallback: replace problematic characters
        safe_message = message.encode('ascii', 'replace').decode('ascii')
        print(safe_message)

# Standard-library logging, imported normally.
#
# This file used to delete ``logging`` from ``sys.modules`` and re-import it
# ("protect the standard library logging module"), on the theory that a
# multiprocessing spawn or a stray ``managers.logging`` could poison the cached
# module. Re-importing does not protect anything: it executes the module again
# and creates a *second* logging universe (fresh root logger, manager, handler
# registry and level state). Every module that had already imported logging -
# the web app, the database layer, any library, the test harness - keeps
# logging into the first universe, while code imported afterwards logs into the
# second, so handlers and levels configured on one side silently do not apply
# to the other. Observed in the suite: a warning logged through the new root
# never reached pytest's capture handler (caplog.records was empty while the
# message appeared on stderr).
#
# What is worth guarding is the real failure the comment was reaching for: a
# local module shadowing the stdlib. Refuse loudly instead of re-importing.
import logging

if not (hasattr(logging, "getLogger") and hasattr(logging, "basicConfig")
        and hasattr(logging, "Formatter")):
    raise ImportError(
        "'logging' resolved to a module that is not the standard library "
        "(a local logging.py / logging/ package is shadowing it). Remove it "
        "from the import path; do not re-import logging through sys.modules, "
        "which splits the process into two logging configurations."
    )

logger = logging.getLogger(__name__)


# NEW: Import threaded reader
from pipeline.integrated_reader import IntegratedFileReader  

from core.time_utils import (
    print_execution_time,
    calculate_processing_statistics
)

from Hdg_Err_Ex_Log.logging_utils import (
    record_command_line_action,
    start_action_recording,
    stop_action_recording,
    is_recording_enabled
    
)

from database.queries import (
    list_sources,
    search_sources,
    insert_source,
    get_source_by_name,
    list_sides,
    search_sides,
    insert_side,
    get_side_by_name
)

# OPTIONAL: Import for backward compatibility (if threading fails)
try:
    from reader_file.main_specify_method import (
        main_specify_method_of_reading_the_file,
        specify_method_of_reading_the_file_list
    )

    from core.file_utils import (
        get_standardized_metadata,
        read_tree
    )
    FALLBACK_AVAILABLE = True
except ImportError:
    FALLBACK_AVAILABLE = False


# Import centralized configuration
from settings.config import get_config
from settings import get_processing_config, get_storage_config

# Get configuration (will be loaded from environment or use defaults)
_config = get_config()
_processing_config = get_processing_config()
_storage_config = get_storage_config()

# Backward compatibility: Export as module-level constants
USE_THREADING = _processing_config.use_threading
MAX_WORKERS = _processing_config.max_workers
ENABLE_MONITORING = _processing_config.enable_monitoring
MONITOR_INTERVAL = _processing_config.monitor_interval

# Storage configuration (will be set by user input)
ENABLE_STORAGE = _storage_config.enable_storage
STORAGE_SOURCE = None  # Will be set by user input
STORAGE_SIDE = None  # Will be set by user input


def main_read_file_threaded(file_path, storage_source=None, storage_side=None):
    """
    Process a single file using threading
    
    This is the NEW version that uses parallel processing capabilities
    """
    record_command_line_action(
        "FUNCTION_CALL", 
        f"Processing file (threaded): {os.path.basename(file_path)}", 
        {"file_path": file_path, "function": "main_read_file_threaded"}
    )
    
    # Source and side are MANDATORY - must be explicitly provided
    processing_cfg = get_processing_config()
    
    # Get source and side - no defaults allowed
    source = storage_source or STORAGE_SOURCE
    side = storage_side or STORAGE_SIDE
    
    if not source:
        print("ERROR: source is MANDATORY - must be explicitly provided. No defaults allowed.")
        print("Please provide source using --source parameter or set STORAGE_SOURCE environment variable.")
        return None
    
    if not side:
        print("ERROR: side is MANDATORY - must be explicitly provided. No defaults allowed.")
        print("Please provide side using --side parameter or set STORAGE_SIDE environment variable.")
        return None
    
    try:
        with IntegratedFileReader(
            max_workers=processing_cfg.max_workers,
            enable_monitoring=processing_cfg.enable_monitoring,
            enable_storage=True,  # Always enable when source/side provided
            storage_source=source,
            storage_side=side
        ) as reader:
            result = reader.process_single_file(file_path)
            
            # Get storage statistics to check if file was actually stored
            storage_stats = None
            if reader.enable_storage:
                storage_stats = reader.get_storage_statistics()
            
            if result:
                stored = storage_stats and storage_stats.get('completed', 0) > 0
                duplicate = storage_stats and storage_stats.get('duplicates', 0) > 0
                
                if stored:
                    record_command_line_action(
                        "FILE_OP", 
                        f"File processed and stored successfully (threaded): {os.path.basename(file_path)}",
                        {"file_path": file_path, "success": True, "stored": True}
                    )
                elif duplicate:
                    record_command_line_action(
                        "FILE_OP", 
                        f"File processed but skipped as duplicate (threaded): {os.path.basename(file_path)}",
                        {"file_path": file_path, "success": True, "stored": False, "duplicate": True}
                    )
                else:
                    record_command_line_action(
                        "FILE_OP", 
                        f"File processed successfully (threaded): {os.path.basename(file_path)}",
                        {"file_path": file_path, "success": True, "stored": False}
                    )
            else:
                record_command_line_action(
                    "FILE_OP", 
                    f"File processing failed (threaded): {os.path.basename(file_path)}",
                    {"file_path": file_path, "success": False}, 
                    level="ERROR"
                )
            
            # Return result with storage info
            if result and storage_stats:
                result['_storage_stats'] = storage_stats
                
            return result
            
    except Exception as e:
        error_msg = f"Error in threaded processing: {str(e)}"
        safe_print(f"[ERROR] {error_msg}")
        record_command_line_action(
            "ERROR", 
            error_msg, 
            {"file_path": file_path, "error": str(e)}, 
            level="ERROR"
        )
        
        # Fallback to sequential if available
        if FALLBACK_AVAILABLE:
            print("→ Falling back to sequential processing...")
            # Note: Sequential file processing doesn't support storage yet
            return main_read_file_sequential(file_path)
        return None


def main_read_folder_threaded(folder_path, storage_source=None, storage_side=None, checkpoint_file=None):
    """
    Process an entire folder using parallel threading with priority
    
    Args:
        folder_path: Path to folder to process
        storage_source: Storage source name
        storage_side: Storage side name
        checkpoint_file: Optional checkpoint file path for resume functionality
    """
    record_command_line_action(
        "FUNCTION_CALL", 
        f"Processing folder (threaded): {os.path.basename(folder_path)}",
        {"folder_path": folder_path, "function": "main_read_folder_threaded", "checkpoint_file": checkpoint_file}
    )
    
    # Source and side are MANDATORY - must be explicitly provided
    processing_cfg = get_processing_config()
    
    # Get source and side - no defaults allowed
    source = storage_source or STORAGE_SOURCE
    side = storage_side or STORAGE_SIDE
    
    if not source:
        print("ERROR: source is MANDATORY - must be explicitly provided. No defaults allowed.")
        print("Please provide source using --source parameter or set STORAGE_SOURCE environment variable.")
        return None
    
    if not side:
        print("ERROR: side is MANDATORY - must be explicitly provided. No defaults allowed.")
        print("Please provide side using --side parameter or set STORAGE_SIDE environment variable.")
        return None
    
    try:
        with IntegratedFileReader(
            max_workers=processing_cfg.max_workers,
            enable_monitoring=processing_cfg.enable_monitoring,
            use_priority=processing_cfg.use_priority,
            enable_storage=True,  # Always enable when source/side provided
            storage_source=source,
            storage_side=side,
            checkpoint_file=checkpoint_file
        ) as reader:
            results = reader.process_folder(folder_path)
            
            stats = reader.get_statistics()
            storage_stats = reader.get_storage_statistics()
            
            # Display comprehensive report
            print(f"\n{'='*70}")
            print("📊 PROCESSING STATISTICS")
            print(f"{'='*70}")
            
            # The report is built from the ledger, not from the (deliberately
            # bounded) list of retained results. Reporting ``len(results)``
            # under the label "Total Files" made a 19 566-attachment container
            # print "Total Files: 2" - the number of top-level files whose
            # results were still in the window - while the work actually done
            # was 1 400x larger and invisible.
            snapshot = reader.get_live_progress()
            total_files = snapshot.get('files_discovered', stats.get('total', 0))
            nested_files = snapshot.get('files_nested', 0)
            containers = snapshot.get('containers_opened', 0)
            completed_files = snapshot.get('files_completed', 0)
            pending_files = snapshot.get('files_pending', 0)
            files_stored = storage_stats.get('completed', 0)
            duplicate_files = storage_stats.get('duplicates', 0)
            failed_files = snapshot.get('files_failed', 0)
            retryable_files = snapshot.get('files_retryable', 0)
            skipped_files = snapshot.get('files_skipped', 0)
            unsupported_files = snapshot.get('files_unsupported', 0)
            locked_files = snapshot.get('files_locked', 0)
            cancelled_files = snapshot.get('files_cancelled', 0)

            print(f"Total Files:            {total_files}")
            if nested_files:
                print(f"  of which nested:      {nested_files}"
                      f" (from {containers} container(s))")
            print(f"Files Completed:        {completed_files}")
            print(f"Files Stored:           {files_stored}")
            print(f"Duplicate Files:        {duplicate_files}")
            if skipped_files:
                print(f"Files Skipped:          {skipped_files}")
            if unsupported_files:
                print(f"Files Unsupported:      {unsupported_files}")
            if failed_files > 0:
                print(f"Failed Files:           {failed_files}")
            if retryable_files:
                print(f"Retryable Files:        {retryable_files}")
            if locked_files:
                print(f"Locked Files:           {locked_files}"
                      f"  (retryable once the holder releases them)")
            if cancelled_files:
                print(f"Cancelled Files:        {cancelled_files}")
            if pending_files:
                print(f"UNPROCESSED (pending):  {pending_files}"
                      f"  <-- run is INCOMPLETE")
            
            # File type distribution
            if results:
                file_types = {}
                for result in results:
                    if result and result.get("Metadata"):
                        file_path = result.get("Metadata", {}).get("path", "unknown")
                        extension = os.path.splitext(file_path)[1].lower() or "no_extension"
                        file_types[extension] = file_types.get(extension, 0) + 1
                
                if file_types:
                    print("\nFile Type Distribution:")
                    sorted_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)
                    for ext, count in sorted_types[:10]:  # Show top 10
                        percentage = (count / total_files * 100) if total_files > 0 else 0
                        print(f"  {ext or '(no extension)':<15} {count:>4} files ({percentage:>5.1f}%)")
            
            print(f"{'='*70}\n")
            
            record_command_line_action(
                "FUNCTION_CALL", 
                "Folder processing completed (threaded with priority)",
                {
                    "folder_path": folder_path,
                    "total_files": total_files,
                    "files_stored": files_stored,
                    "duplicate_files": duplicate_files,
                    "completed": stats['completed'],
                    "failed": stats['failed']
                }
            )
            
            return results
            
    except Exception as e:
        error_msg = f"Error in threaded processing: {str(e)}"
        logger.error(error_msg)
        record_command_line_action(
            "ERROR", 
            error_msg, 
            {"folder_path": folder_path, "error": str(e)}, 
            level="ERROR"
        )
        
        if FALLBACK_AVAILABLE:
            logger.info("→ Falling back to sequential processing...")
            return main_read_folder_sequential(
                folder_path,
                storage_source=storage_source,
                storage_side=storage_side
            )
        return []
    

def main_read_file_sequential(file_path):
    """
    ORIGINAL sequential file processing (kept for fallback)
    This is your original main_read_file() function
    """
    if not FALLBACK_AVAILABLE:
        safe_print("[ERROR] Sequential processing not available (imports failed)")
        return None
        
    record_command_line_action(
        "FUNCTION_CALL", 
        f"Processing file (sequential): {os.path.basename(file_path)}", 
        {"file_path": file_path, "function": "main_read_file_sequential"}
    )
    
    def _process_file():
        file_info = get_standardized_metadata(file_path)
        
        if file_info is None:
            error_msg = f"Could not read file info for: {file_path}"
            safe_print(f"[ERROR] {error_msg}")
            record_command_line_action(
                "ERROR", error_msg, 
                {"file_path": file_path}, 
                level="ERROR"
            )
            return None

        result = main_specify_method_of_reading_the_file(file_info, collect=True)
        return result
    
    result = print_execution_time(
        f"Processing file: {os.path.basename(file_path)}", 
        _process_file
    )
    
    if result:
        calculate_processing_statistics([result])
        record_command_line_action(
            "FILE_OP", 
            f"File processed successfully (sequential): {os.path.basename(file_path)}",
            {"file_path": file_path, "success": True}
        )
    else:
        record_command_line_action(
            "FILE_OP", 
            f"File processing failed (sequential): {os.path.basename(file_path)}",
            {"file_path": file_path, "success": False}, 
            level="ERROR"
        )
    
    return result


def main_read_folder_sequential(folder_path, storage_source=None, storage_side=None, checkpoint_file=None):
    """
    ORIGINAL sequential folder processing (kept for fallback)
    This is your original main_read_folder() function
    NOW WITH STORAGE SUPPORT AND CHECKPOINT/RESUME
    
    Args:
        folder_path: Path to folder to process
        storage_source: Storage source name
        storage_side: Storage side name
        checkpoint_file: Optional checkpoint file path for resume functionality
    """
    if not FALLBACK_AVAILABLE:
        safe_print("[ERROR] Sequential processing not available (imports failed)")
        return []
        
    record_command_line_action(
        "FUNCTION_CALL", 
        f"Processing folder (sequential): {os.path.basename(folder_path)}",
        {"folder_path": folder_path, "function": "main_read_folder_sequential", "checkpoint_file": checkpoint_file}
    )
    
    # Initialize storage pipeline if source/side provided
    storage_pipeline = None
    db_hub = None
    if storage_source and storage_side:
        try:         
            from pipeline.storage_pipeline import StoragePipeline
            from settings import get_storage_config
            
            storage_cfg = get_storage_config()
            # DatabaseHub is optional - StoragePipeline works without it
            db_hub = None
            try:
                from database import DatabaseHub
                db_hub = DatabaseHub(db_name=storage_cfg.db_name)
            except (ImportError, AttributeError) as e:
                # DatabaseHub doesn't exist - this is OK, storage works without it
                logger.info(f"DatabaseHub not available (optional): {e}. Storage will work with db_service only.")
            except Exception as e:
                logger.warning(f"Failed to create DatabaseHub (optional): {e}")
            
            storage_pipeline = StoragePipeline(
                db_hub=db_hub,  # Can be None
                source_name=storage_source,
                side_name=storage_side
            )
            safe_print(f"[STORAGE] Storage enabled: Source='{storage_source}', Side='{storage_side}'")
        except Exception as e:
            logger.error(f"Failed to initialize storage pipeline: {e}")
            storage_pipeline = None
            if db_hub:
                try:
                    db_hub.close()
                except:
                    pass
                db_hub = None
    
    # Initialize checkpoint manager if checkpoint file provided
    checkpoint_manager = None
    if checkpoint_file:
        try:
            from core.checkpoint_manager import CheckpointManager
            checkpoint_manager = CheckpointManager(
                checkpoint_file=checkpoint_file,
                folder_path=folder_path,
                storage_source=storage_source,
                storage_side=storage_side,
                auto_save_interval=50  # OPTIMIZED: Save checkpoint every 50 files (reduced I/O)
            )
            safe_print(f"[CHECKPOINT] Checkpoint manager initialized: {checkpoint_file}")
        except Exception as e:
            logger.warning(f"Failed to initialize checkpoint manager: {e}")
            checkpoint_manager = None
    
    def _process_folder():
        print(f"Scanning folder: {folder_path}...")
        record_command_line_action("FILE_OP", "Scanning folder", {"folder_path": folder_path})
        
        tree = read_tree(folder_path)
        files = [item for item in tree if item.get('type') == 'FILE']
        print(f"Found {len(files)} files\n")
        
        # Filter out already processed files if checkpoint manager is active
        if checkpoint_manager:
            files, skipped_count = checkpoint_manager.filter_processed_files(files)
            if skipped_count > 0:
                print(f"Resuming: {skipped_count} files already processed, {len(files)} remaining")
        
        record_command_line_action(
            "FILE_OP", 
            f"Found {len(files)} files in folder",
            {"folder_path": folder_path, "file_count": len(files)}
        )
        
        if not files:
            if checkpoint_manager:
                checkpoint_stats = checkpoint_manager.get_statistics()
                print(f"\n✅ All files already processed! (Total: {checkpoint_stats['processed_count']} files)")
            else:
                print("No files to process.")
            record_command_line_action("INFO", "No files to process", {"folder_path": folder_path})
            return []
        
        results = specify_method_of_reading_the_file_list(tree, collect=True)
        return results
    
    results = print_execution_time(
        f"Processing folder: {os.path.basename(folder_path)}", 
        _process_folder
    )
    
    if not results:
        return []
    
    # STORE TO DATABASE if storage is enabled
    if storage_pipeline:
        safe_print(f"\n[STORAGE] Storing {len(results)} files to database...")
        stored_count = 0
        failed_count = 0
        duplicate_count = 0
        extracted_count = 0  # Counter for extracted files
        
        def _store_extracted_files_sequential(result, parent_path_id=None):
            """Store extracted files from archives/emails in sequential mode"""
            nonlocal extracted_count  # Allow modification of outer variable
            
            if not result or not isinstance(result, dict):
                return
            
            content = result.get("Content", {})
            if not isinstance(content, dict):
                return
            
            # Check for archive extracted files
            if "extracted_files" in content and isinstance(content["extracted_files"], list):
                for extracted_result in content["extracted_files"]:
                    if isinstance(extracted_result, dict) and extracted_result.get("Metadata"):
                        extracted_file_info = extracted_result.get("Metadata", {})
                        extracted_content = extracted_result.get("Content", {})
                        
                        # Store ALL files, even if they have errors (metadata will be stored)
                        # is_failure = bool(isinstance(extracted_content, dict) and extracted_content.get("error"))
                        # if is_failure:
                        #     continue
                        
                        try:
                            # Build hierarchy path
                            parent_path = result.get("Metadata", {}).get("path", "")
                            extracted_path = extracted_file_info.get("path", "")
                            extracted_name = extracted_file_info.get("name", os.path.basename(extracted_path))
                            hierarchy_path = f"{parent_path}::{extracted_path}" if parent_path else extracted_path
                            
                            # Validate storage pipeline is available
                            if not storage_pipeline:
                                logger.error(
                                    f"❌ Cannot store extracted file '{extracted_name}': storage_pipeline is None. "
                                    f"Parent: {os.path.basename(parent_path) if parent_path else 'unknown'}"
                                )
                                continue
                            
                            # Validate source and side are provided
                            if not storage_source or not storage_side:
                                logger.error(
                                    f"❌ Cannot store extracted file '{extracted_name}': "
                                    f"storage_source or storage_side is missing. "
                                    f"Source: {storage_source}, Side: {storage_side}"
                                )
                                continue
                            
                            logger.debug(
                                f"Storing extracted file: {extracted_name} "
                                f"(parent_path_id: {parent_path_id}, hierarchy: {hierarchy_path[:100]})"
                            )
                            
                            # Store extracted file individually
                            extracted_path_id = storage_pipeline.store_file_complete(
                                extracted_file_info,
                                extracted_result,
                                source_name=storage_source,
                                side_name=storage_side,
                                parent_path_id=parent_path_id,
                                hierarchy_path=hierarchy_path,
                                use_async=False
                            )
                            
                            if extracted_path_id:
                                extracted_count += 1
                                logger.info(
                                    f"✅ Stored extracted file: {extracted_name} → Database (Path ID: {extracted_path_id})"
                                )
                                # Recursively store nested extracted files
                                _store_extracted_files_sequential(extracted_result, extracted_path_id)
                            else:
                                logger.error(
                                    f"❌ STORAGE FAILED: Extracted file '{extracted_name}' was NOT stored in database. "
                                    f"store_file_complete returned None. "
                                    f"Parent: {os.path.basename(parent_path) if parent_path else 'unknown'}"
                                )
                                # Log more details for debugging
                                extracted_content = extracted_result.get("Content", {})
                                if isinstance(extracted_content, dict) and extracted_content.get("error"):
                                    logger.error(f"   Content error: {extracted_content.get('error')}")
                        except Exception as e:
                            extracted_name = extracted_file_info.get("name", "unknown") if extracted_file_info else "unknown"
                            logger.error(
                                f"❌ Exception storing extracted file '{extracted_name}': "
                                f"{type(e).__name__}: {e}"
                            )
                            import traceback
                            logger.debug(f"Extracted file storage exception traceback: {traceback.format_exc()}")
            
            # Check for email attachments
            if "attachments" in content and isinstance(content["attachments"], dict):
                attachments_data = content["attachments"]
                if "extracted_files" in attachments_data and isinstance(attachments_data["extracted_files"], list):
                    for attachment_result in attachments_data["extracted_files"]:
                        if isinstance(attachment_result, dict) and attachment_result.get("Metadata"):
                            attachment_file_info = attachment_result.get("Metadata", {})
                            attachment_content = attachment_result.get("Content", {})
                            
                            # Store ALL files, even if they have errors (metadata will be stored)
                            # is_failure = bool(isinstance(attachment_content, dict) and attachment_content.get("error"))
                            # if is_failure:
                            #     continue
                            
                            try:
                                parent_path = result.get("Metadata", {}).get("path", "")
                                attachment_path = attachment_file_info.get("path", "")
                                attachment_name = attachment_file_info.get("name", os.path.basename(attachment_path))
                                hierarchy_path = f"{parent_path}::attachment::{attachment_path}" if parent_path else attachment_path
                                
                                # Validate storage pipeline is available
                                if not storage_pipeline:
                                    logger.error(
                                        f"❌ Cannot store email attachment '{attachment_name}': storage_pipeline is None. "
                                        f"Parent: {os.path.basename(parent_path) if parent_path else 'unknown'}"
                                    )
                                    continue
                                
                                # Validate source and side are provided
                                if not storage_source or not storage_side:
                                    logger.error(
                                        f"❌ Cannot store email attachment '{attachment_name}': "
                                        f"storage_source or storage_side is missing. "
                                        f"Source: {storage_source}, Side: {storage_side}"
                                    )
                                    continue
                                
                                logger.debug(
                                    f"Storing email attachment: {attachment_name} "
                                    f"(parent_path_id: {parent_path_id}, hierarchy: {hierarchy_path[:100]})"
                                )
                                
                                attachment_path_id = storage_pipeline.store_file_complete(
                                    attachment_file_info,
                                    attachment_result,
                                    source_name=storage_source,
                                    side_name=storage_side,
                                    parent_path_id=parent_path_id,
                                    hierarchy_path=hierarchy_path,
                                    use_async=False
                                )
                                
                                if attachment_path_id:
                                    extracted_count += 1
                                    logger.info(
                                        f"✅ Stored email attachment: {attachment_name} → Database (Path ID: {attachment_path_id})"
                                    )
                                    _store_extracted_files_sequential(attachment_result, attachment_path_id)
                                else:
                                    logger.error(
                                        f"❌ STORAGE FAILED: Email attachment '{attachment_name}' was NOT stored in database. "
                                        f"store_file_complete returned None. "
                                        f"Parent: {os.path.basename(parent_path) if parent_path else 'unknown'}"
                                    )
                                    # Log more details for debugging
                                    attachment_content = attachment_result.get("Content", {})
                                    if isinstance(attachment_content, dict) and attachment_content.get("error"):
                                        logger.error(f"   Content error: {attachment_content.get('error')}")
                            except Exception as e:
                                attachment_name = attachment_file_info.get("name", "unknown") if attachment_file_info else "unknown"
                                logger.error(
                                    f"❌ Exception storing email attachment '{attachment_name}': "
                                    f"{type(e).__name__}: {e}"
                                )
                                import traceback
                                logger.debug(f"Email attachment storage exception traceback: {traceback.format_exc()}")
        
        for idx, result in enumerate(results, 1):
            if not result:
                continue
            
            # Validate result is a dictionary
            if not isinstance(result, dict):
                logger.warning(f"Invalid result type at index {idx}: {type(result).__name__}, expected dict. Skipping.")
                failed_count += 1
                continue
                
            # Safely check for errors
            content = result.get("Content", {})
            is_failure = bool(isinstance(content, dict) and content.get("error"))
            
            # Store even if failed (to track all files)
            try:
                file_info = result.get("Metadata", {})
                if not isinstance(file_info, dict):
                    logger.warning(f"Invalid Metadata type at index {idx}: {type(file_info).__name__}, expected dict. Skipping.")
                    failed_count += 1
                    continue
                
                if file_info:
                    # Get stats before storage to detect duplicates
                    stats_before = storage_pipeline.get_statistics()
                    prev_stored = stats_before.get('files_stored', 0)
                    prev_duplicates = stats_before.get('files_duplicates', 0)
                    
                    path_id = storage_pipeline.store_file_complete(
                        file_info,
                        result,
                        source_name=storage_source,
                        side_name=storage_side,
                        use_async=False
                    )
                    
                    # Get stats after storage to determine what happened
                    stats_after = storage_pipeline.get_statistics()
                    current_stored = stats_after.get('files_stored', 0)
                    current_duplicates = stats_after.get('files_duplicates', 0)
                    
                    if path_id:
                        # Mark file as processed in checkpoint manager (for both new and duplicate)
                        # Non-blocking, error-safe to avoid interfering with storage
                        if checkpoint_manager and file_info:
                            try:
                                checkpoint_manager.mark_processed(file_info)
                            except Exception as cp_err:
                                # Don't let checkpoint errors interfere with storage success
                                logger.debug(f"Checkpoint marking failed (non-critical): {cp_err}")
                        
                        # Check if this was a new file or duplicate
                        if current_stored > prev_stored:
                            # New file was stored
                            stored_count += 1
                            # Store extracted files from archives/emails
                            _store_extracted_files_sequential(result, path_id)
                        elif current_duplicates > prev_duplicates:
                            # This was a duplicate
                            duplicate_count += 1
                            # Still process extracted files for duplicates
                            _store_extracted_files_sequential(result, path_id)
                        else:
                            # File was processed but not stored (shouldn't happen, but handle it)
                            logger.warning(f"File {idx} returned path_id but stats didn't change")
                            stored_count += 1
                            _store_extracted_files_sequential(result, path_id)
                        
                        if idx % 100 == 0:
                            print(f"  Stored {idx}/{len(results)} files (extracted: {extracted_count})...", end='\r')
                    else:
                        # path_id is None - storage failed
                        failed_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"Storage error for file {idx}: {e}", exc_info=True)
        
        safe_print("\n[STORAGE] Storage complete:")
        print(f"   Stored: {stored_count}")
        print(f"   Extracted files stored: {extracted_count}")
        print(f"   Duplicates: {duplicate_count}")
        print(f"   Failed: {failed_count}")
        
        # Get storage statistics
        storage_stats = storage_pipeline.get_statistics()
        print(f"   Storage stats: {storage_stats}")
        
        # Shutdown storage pipeline
        storage_pipeline.shutdown()
        
        # Close database connection
        if storage_pipeline and storage_pipeline.db_hub:
            storage_pipeline.db_hub.close()
    
    # Finalize checkpoint if active
    if checkpoint_manager:
        checkpoint_manager.finalize()
        checkpoint_stats = checkpoint_manager.get_statistics()
        safe_print(f"\n💾 Checkpoint saved: {checkpoint_stats['processed_count']} files processed")
    
    # Calculate statistics
    # stats = calculate_processing_statistics(results)
    
    # Display summary
    successful = sum(1 for r in results if r and not r.get("Content", {}).get("error"))
    failed = len(results) - successful
    
    print(f"\n{'='*70}")
    print("📊 PROCESSING STATISTICS")
    print(f"{'='*70}")
    
    # This sequential fallback path tracks its own counters and has no progress
    # ledger (the ledger belongs to the streaming reader used by cli_main and
    # the web front end), so the summary is built from the numbers this path
    # actually knows. An earlier change read the ledger through an undefined
    # `stats` name here, which raised NameError before any of the summary
    # printed - the numbers below can never be fabricated and a file that was
    # discovered but not stored is still reported as pending.
    total_files = len(results)
    # Files extracted from containers are counted separately below ("Extracted
    # Files Stored"): they are additional artifacts, not a subset of the
    # top-level total, so they must not be reported as "of which nested".
    nested_files = 0
    files_stored = stored_count if 'stored_count' in locals() else 0
    duplicate_files = duplicate_count if 'duplicate_count' in locals() else 0
    storage_failed = failed_count if 'failed_count' in locals() else 0
    pending_files = storage_failed

    print(f"Total Files:            {total_files}")
    if nested_files:
        print(f"  of which nested:      {nested_files}")
    print(f"Processing Successful:  {successful}")
    if failed > 0:
        print(f"Processing Failed:      {failed}")
    print(f"Files Stored:           {files_stored}")
    print(f"Duplicate Files:        {duplicate_files}")
    if storage_failed > 0:
        print(f"Storage Failed:         {storage_failed}")
    if 'extracted_count' in locals() and extracted_count > 0:
        print(f"Extracted Files Stored:  {extracted_count}")
    if pending_files:
        print(f"UNPROCESSED (pending):   {pending_files}  <-- run is INCOMPLETE")
    
    # File type distribution
    if results:
        file_types = {}
        for result in results:
            if result and result.get("Metadata"):
                file_path = result.get("Metadata", {}).get("path", "unknown")
                extension = os.path.splitext(file_path)[1].lower() or "no_extension"
                file_types[extension] = file_types.get(extension, 0) + 1
        
        if file_types:
            print("\nFile Type Distribution:")
            sorted_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)
            for ext, count in sorted_types[:10]:  # Show top 10
                percentage = (count / total_files * 100) if total_files > 0 else 0
                print(f"  {ext or '(no extension)':<15} {count:>4} files ({percentage:>5.1f}%)")
    
    print(f"{'='*70}\n")
    
    record_command_line_action(
        "FUNCTION_CALL", 
        "Folder processing completed (sequential)",
        {
            "folder_path": folder_path,
            "total_files": len(results),
            "successful": successful,
            "failed": failed,
            "stored": stored_count if 'stored_count' in locals() else 0
        }
    )
    
    return results


# ============================================================================
# DATABASE SOURCE/SIDE MANAGEMENT
# ============================================================================

def _validate_source_name(name: str) -> tuple[bool, str]:
    """
    Validate source name
    
    Returns:
        (is_valid, error_message)
    """
    if not name or not name.strip():
        return False, "Source name cannot be empty"
    
    name = name.strip()
    
    # Check for command-like patterns (common invalid inputs)
    if name.startswith(('&', '|', ';', '&&', '||')):
        return False, "Source name cannot start with command operators"
    
    if any(char in name for char in ['\n', '\r', '\t']):
        return False, "Source name cannot contain newlines or tabs"
    
    # Check for file paths (common mistake)
    if '\\' in name or '/' in name or ':' in name:
        return False, "Source name appears to be a file path. Please enter a descriptive name instead."
    
    # Check length
    if len(name) > 200:
        return False, "Source name is too long (max 200 characters)"
    
    return True, ""


def _validate_side_name(name: str) -> tuple[bool, str]:
    """
    Validate side name
    
    Returns:
        (is_valid, error_message)
    """
    if not name or not name.strip():
        return False, "Side name cannot be empty"
    
    name = name.strip()
    
    # Check for command-like patterns
    if name.startswith(('&', '|', ';', '&&', '||')):
        return False, "Side name cannot start with command operators"
    
    if any(char in name for char in ['\n', '\r', '\t']):
        return False, "Side name cannot contain newlines or tabs"
    
    # Check for file paths
    if '\\' in name or '/' in name or ':' in name:
        return False, "Side name appears to be a file path. Please enter a descriptive name instead."
    
    # Check length
    if len(name) > 200:
        return False, "Side name is too long (max 200 characters)"
    
    return True, ""


def _format_source_display(source: dict, max_name_len: int = 50) -> str:
    """Format source for display with truncation"""
    name = source.get('name', 'Unknown')
    if len(name) > max_name_len:
        name = name[:max_name_len-3] + "..."
    
    country = source.get('country', 'Unknown')
    job = source.get('job', 'Unknown')
    source_id = source.get('id', '?')
    
    # Truncate country and job if too long
    if len(country) > 20:
        country = country[:17] + "..."
    if len(job) > 20:
        job = job[:17] + "..."
    
    return f"{name:<{max_name_len}} | ID: {source_id:<5} | Country: {country:<20} | Job: {job:<20}"


def get_or_select_source() -> Optional[str]:
    """
    Get source name from user - either select existing or create new
    
    Returns:
        Source name string or None if cancelled/error (no defaults)
    """
    try:
        while True:
            print("\n" + "="*70)
            print("📋 SOURCE SELECTION")
            print("="*70)
            print("Options:")
            print("  1. List existing sources")
            print("  2. Search sources (by name, country, or job)")
            print("  3. Create new source")
            print("  4. Enter source name directly")
            print("="*70)
            
            choice = input("\nEnter your choice (1-4): ").strip()
            
            if choice == "1":
                sources = list_sources(limit=50)
                if not sources:
                    safe_print("\n[WARNING] No sources found. Please create a source first (option 3).")
                    continue
                
                print(f"\n📋 Available Sources ({len(sources)} found):")
                print("-" * 70)
                print(f"{'Name':<50} | {'ID':<5} | {'Country':<20} | {'Job':<20}")
                print("-" * 70)
                for i, source in enumerate(sources, 1):
                    print(f"  {i:2}. {_format_source_display(source)}")
                
                source_choice = input("\nEnter source number or name (or 'q' to go back): ").strip()
                
                if source_choice.lower() == 'q':
                    continue
                
                # Try to parse as number
                try:
                    idx = int(source_choice) - 1
                    if 0 <= idx < len(sources):
                        return sources[idx]['name']
                    else:
                        safe_print(f"[WARNING] Invalid number. Please enter 1-{len(sources)}")
                        continue
                except ValueError:
                    pass
                
                # Try to find by name (partial match)
                matches = [s for s in sources if source_choice.lower() in s['name'].lower()]
                if len(matches) == 1:
                    return matches[0]['name']
                elif len(matches) > 1:
                    safe_print(f"\n[WARNING] Multiple sources found matching '{source_choice}':")
                    for i, match in enumerate(matches, 1):
                        print(f"  {i}. {match['name']}")
                    continue
                else:
                    # Not found - tell user to create it
                    safe_print(f"\n[WARNING] Source '{source_choice}' not found.")
                    print("Please use option 3 to create a new source with all required fields.")
                    continue
            
            elif choice == "2":
                search_term = input("Enter search term (searches name, country, and job): ").strip()
                if not search_term:
                    safe_print("[WARNING] Empty search term. Please try again.")
                    continue
                
                sources = search_sources(search_term, limit=50)
                
                if not sources:
                    safe_print(f"\n[WARNING] No sources found matching '{search_term}'.")
                    print("Please use option 3 to create a new source with all required fields.")
                    continue
                
                print(f"\n📋 Search Results for '{search_term}' ({len(sources)} found):")
                print("-" * 70)
                print(f"{'Name':<50} | {'ID':<5} | {'Country':<20} | {'Job':<20}")
                print("-" * 70)
                for i, source in enumerate(sources, 1):
                    print(f"  {i:2}. {_format_source_display(source)}")
                
                source_choice = input("\nEnter source number or name (or 'q' to go back): ").strip()
                
                if source_choice.lower() == 'q':
                    continue
                
                try:
                    idx = int(source_choice) - 1
                    if 0 <= idx < len(sources):
                        return sources[idx]['name']
                    else:
                        safe_print(f"[WARNING] Invalid number. Please enter 1-{len(sources)}")
                        continue
                except ValueError:
                    pass
                
                matches = [s for s in sources if source_choice.lower() in s['name'].lower()]
                if len(matches) == 1:
                    return matches[0]['name']
                elif len(matches) > 1:
                    safe_print(f"\n[WARNING] Multiple sources found matching '{source_choice}':")
                    for i, match in enumerate(matches, 1):
                        print(f"  {i}. {match['name']}")
                    continue
                else:
                    safe_print(f"[WARNING] Source '{source_choice}' not found in search results.")
                    print("Please use option 3 to create a new source with all required fields.")
                    continue
            
            elif choice == "3":
                # Create new source with all fields
                safe_print("\n[INFO] Creating New Source")
                print("=" * 70)
                
                source_name = input("Source name (required): ").strip()
                is_valid, error_msg = _validate_source_name(source_name)
                if not is_valid:
                    safe_print(f"[WARNING] {error_msg}")
                    continue
                
                if not source_name:
                    safe_print("[WARNING] Source name is required. Please try again.")
                    continue
                
                # Check if already exists
                existing = get_source_by_name(source_name)
                if existing:
                    safe_print(f"[WARNING] Source '{source_name}' already exists (ID: {existing['id']})")
                    use_existing = input("Use existing source? (y/n): ").strip().lower()
                    if use_existing == 'y':
                        return source_name
                    continue
                
                # Required fields
                job = input("Job/Role (required): ").strip()
                if not job:
                    safe_print("[WARNING] Job is required. Please try again.")
                    continue
                
                try:
                    importance_input = input("Importance (0.0-1.0, required): ").strip()
                    importance = float(importance_input) if importance_input else None
                    if importance is None:
                        safe_print("[WARNING] Importance is required. Please try again.")
                        continue
                    importance = max(0.0, min(1.0, importance))  # Clamp to 0-1
                except ValueError:
                    safe_print("[WARNING] Invalid importance value. Please enter a number between 0.0 and 1.0.")
                    continue
                
                country = input("Country (required): ").strip()
                if not country:
                    safe_print("[WARNING] Country is required. Please try again.")
                    continue
                
                # Optional fields
                print("\nOptional fields (press Enter to skip):")
                city = input("City: ").strip() or None
                description = input("Description: ").strip() or None
                accounts = input("Accounts: ").strip() or None
                note = input("Note: ").strip() or None
                attachments = input("Attachments: ").strip() or None
                
                print("\nOwnership options: Private, Government, Corporate, Non-Profit, Public, Other")
                ownership = input("Ownership: ").strip() or None
                if ownership and ownership not in ['Private', 'Government', 'Corporate', 'Non-Profit', 'Public', 'Other']:
                    safe_print(f"[WARNING] Invalid ownership value '{ownership}'. Setting to None.")
                    ownership = None
                
                print("\nAccess Status options: Open, Restricted, Classified, Confidential, Public, Limited")
                access_status = input("Access Status: ").strip() or None
                if access_status and access_status not in ['Open', 'Restricted', 'Classified', 'Confidential', 'Public', 'Limited']:
                    safe_print(f"[WARNING] Invalid access_status value '{access_status}'. Setting to None.")
                    access_status = None
                
                entry_date_input = input("Date Source Discovery (YYYY-MM-DD): ").strip() or None
                entry_date = None
                if entry_date_input:
                    try:
                        from datetime import datetime
                        entry_date = datetime.strptime(entry_date_input, "%Y-%m-%d").date()
                    except ValueError:
                        safe_print(f"[WARNING] Invalid date format '{entry_date_input}'. Expected YYYY-MM-DD. Setting to None.")
                        entry_date = None
                
                category_id_input = input("Category ID: ").strip() or None
                id_categorys = None
                if category_id_input:
                    try:
                        id_categorys = int(category_id_input)
                    except ValueError:
                        safe_print(f"[WARNING] Invalid category_id '{category_id_input}'. Setting to None.")
                        id_categorys = None
                
                source_id = insert_source(
                    name=source_name,
                    job=job,
                    importance=importance,
                    country=country,
                    city=city,
                    description=description,
                    accounts=accounts,
                    note=note,
                    attachments=attachments,
                    ownership=ownership,
                    access_status=access_status,
                    entry_date=entry_date,
                    id_categorys=id_categorys
                )
                if source_id:
                    safe_print(f"[OK] Source '{source_name}' created successfully (ID: {source_id})!")
                    return source_name
                else:
                    safe_print("[WARNING] Failed to create source. Please try again.")
                    continue
            
            elif choice == "4":
                # Direct input
                source_name = input("Enter source name: ").strip()
                is_valid, error_msg = _validate_source_name(source_name)
                if not is_valid:
                    safe_print(f"[WARNING] {error_msg}")
                    continue
                
                if not source_name:
                    safe_print("[WARNING] Empty name. Please try again.")
                    continue
                
                # Try to get existing
                existing = get_source_by_name(source_name)
                if existing:
                    return source_name
                else:
                    safe_print(f"[WARNING] Source '{source_name}' not found. Please create it first (option 3).")
                    continue
            
            else:
                safe_print("[WARNING] Invalid choice. Please enter 1-4.")
                continue
                
    except KeyboardInterrupt:
        safe_print("\n\n[WARNING] Cancelled by user.")
        return None
    except Exception as e:
        safe_print(f"[WARNING] Error managing source: {e}")
        return None


def get_or_select_side() -> Optional[str]:
    """
    Get side name from user - either select existing or create new
    
    Returns:
        Side name string or None if cancelled/error (no defaults)
    """
    try:
        while True:
            print("\n" + "="*70)
            print("📋 SIDE SELECTION")
            print("="*70)
            print("Options:")
            print("  1. List existing sides")
            print("  2. Search sides")
            print("  3. Create new side")
            print("  4. Enter side name directly")
            print("="*70)
            
            choice = input("\nEnter your choice (1-4): ").strip()
            
            if choice == "1":
                sides = list_sides(limit=50)
                if not sides:
                    safe_print("\n[WARNING] No sides found. Please create a side first (option 3).")
                    continue
                
                print(f"\n📋 Available Sides ({len(sides)} found):")
                print("-" * 70)
                print(f"{'Name':<60} | {'ID':<5}")
                print("-" * 70)
                for i, side in enumerate(sides, 1):
                    name = side.get('name', 'Unknown')
                    if len(name) > 60:
                        name = name[:57] + "..."
                    print(f"  {i:2}. {name:<60} | {side.get('id', '?'):<5}")
                
                side_choice = input("\nEnter side number or name (or 'q' to go back): ").strip()
                
                if side_choice.lower() == 'q':
                    continue
                
                # Try to parse as number
                try:
                    idx = int(side_choice) - 1
                    if 0 <= idx < len(sides):
                        return sides[idx]['name']
                    else:
                        safe_print(f"[WARNING] Invalid number. Please enter 1-{len(sides)}")
                        continue
                except ValueError:
                    pass
                
                # Try to find by name (partial match)
                matches = [s for s in sides if side_choice.lower() in s['name'].lower()]
                if len(matches) == 1:
                    return matches[0]['name']
                elif len(matches) > 1:
                    safe_print(f"\n[WARNING] Multiple sides found matching '{side_choice}':")
                    for i, match in enumerate(matches, 1):
                        print(f"  {i}. {match['name']}")
                    continue
                else:
                    safe_print(f"\n[WARNING] Side '{side_choice}' not found.")
                    safe_print(f"[WARNING] Side '{side_choice}' not found.")
                    print("Please use option 3 to create a new side with all required fields.")
                    continue
            
            elif choice == "2":
                search_term = input("Enter search term: ").strip()
                if not search_term:
                    safe_print("[WARNING] Empty search term. Please try again.")
                    continue
                
                sides = search_sides(search_term, limit=50)
                
                if not sides:
                    safe_print(f"\n[WARNING] No sides found matching '{search_term}'.")
                    print("Please use option 3 to create a new side with all required fields.")
                    continue
                
                print(f"\n📋 Search Results for '{search_term}' ({len(sides)} found):")
                print("-" * 70)
                print(f"{'Name':<60} | {'ID':<5}")
                print("-" * 70)
                for i, side in enumerate(sides, 1):
                    name = side.get('name', 'Unknown')
                    if len(name) > 60:
                        name = name[:57] + "..."
                    print(f"  {i:2}. {name:<60} | {side.get('id', '?'):<5}")
                
                side_choice = input("\nEnter side number or name (or 'q' to go back): ").strip()
                
                if side_choice.lower() == 'q':
                    continue
                
                try:
                    idx = int(side_choice) - 1
                    if 0 <= idx < len(sides):
                        return sides[idx]['name']
                    else:
                        safe_print(f"[WARNING] Invalid number. Please enter 1-{len(sides)}")
                        continue
                except ValueError:
                    pass
                
                matches = [s for s in sides if side_choice.lower() in s['name'].lower()]
                if len(matches) == 1:
                    return matches[0]['name']
                elif len(matches) > 1:
                    safe_print(f"\n[WARNING] Multiple sides found matching '{side_choice}':")
                    for i, match in enumerate(matches, 1):
                        print(f"  {i}. {match['name']}")
                    continue
                else:
                    safe_print(f"[WARNING] Side '{side_choice}' not found in search results.")
                    safe_print(f"[WARNING] Side '{side_choice}' not found.")
                    print("Please use option 3 to create a new side with all required fields.")
                    continue
            
            elif choice == "3":
                # Create new side with all fields
                safe_print("\n[INFO] Creating New Side")
                print("=" * 70)
                
                side_name = input("Side name (required): ").strip()
                is_valid, error_msg = _validate_side_name(side_name)
                if not is_valid:
                    safe_print(f"[WARNING] {error_msg}")
                    continue
                
                if not side_name:
                    safe_print("[WARNING] Side name is required. Please try again.")
                    continue
                
                # Check if already exists
                existing = get_side_by_name(side_name)
                if existing:
                    safe_print(f"[WARNING] Side '{side_name}' already exists (ID: {existing['id']})")
                    use_existing = input("Use existing side? (y/n): ").strip().lower()
                    if use_existing == 'y':
                        return side_name
                    continue
                
                # Required fields
                try:
                    importance_input = input("Importance (0.0-1.0, required): ").strip()
                    importance = float(importance_input) if importance_input else None
                    if importance is None:
                        safe_print("[WARNING] Importance is required. Please try again.")
                        continue
                    importance = max(0.0, min(1.0, importance))  # Clamp to 0-1
                except ValueError:
                    safe_print("[WARNING] Invalid importance value. Please enter a number between 0.0 and 1.0.")
                    continue
                
                # Optional fields
                print("\nOptional fields (press Enter to skip):")
                date_creation_input = input("Date Creation (YYYY-MM-DD): ").strip() or None
                date_creation = None
                if date_creation_input:
                    try:
                        from datetime import datetime
                        date_creation = datetime.strptime(date_creation_input, "%Y-%m-%d").date()
                    except ValueError:
                        safe_print(f"[WARNING] Invalid date format '{date_creation_input}'. Expected YYYY-MM-DD. Using today's date.")
                        date_creation = None
                
                side_id = insert_side(
                    name=side_name,
                    importance=importance,
                    date_creation=date_creation
                )
                if side_id:
                    safe_print(f"[OK] Side '{side_name}' created successfully (ID: {side_id})!")
                    return side_name
                else:
                    safe_print("[WARNING] Failed to create side. Please try again.")
                    continue
            
            elif choice == "4":
                # Direct input
                side_name = input("Enter side name: ").strip()
                is_valid, error_msg = _validate_side_name(side_name)
                if not is_valid:
                    safe_print(f"[WARNING] {error_msg}")
                    continue
                
                if not side_name:
                    safe_print("[WARNING] Empty name. Please try again.")
                    continue
                
                # Try to get existing
                existing = get_side_by_name(side_name)
                if existing:
                    return side_name
                else:
                    safe_print(f"[WARNING] Side '{side_name}' not found. Please create it first (option 3).")
                    continue
            
            else:
                safe_print("[WARNING] Invalid choice. Please enter 1-4.")
                continue
                
    except KeyboardInterrupt:
        safe_print("\n\n[WARNING] Cancelled by user.")
        return None
    except Exception as e:
        safe_print(f"[WARNING] Error managing side: {e}")
        return None


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    """Main application entry point (INTERACTIVE, legacy).

    DEPRECATED: the web frontend (Operations -> Input / Ingestion) is the
    primary interface. This interactive flow is retained for terminal-only
    environments and drives the same underlying engine via the same
    functions used before; prefer the web UI or ``cli_main``.
    """
    safe_print("[DEPRECATION] The interactive CLI is deprecated; "
               "use the web Operations UI (or: python -m apps.cli.main --help).\n")
    
    # Start action recording
    log_file = start_action_recording()
    record_command_line_action("SYSTEM", "Application started", {"log_file": str(log_file)})
    safe_print(f"[INFO] Action recording started: {log_file}\n")
    
    # Compute policy: report the effective mode and the layer that set it, and
    # refuse up front when the selection cannot be honoured on this host.
    if apply_compute_mode(None) != 0:
        safe_print("\n[ERROR] Cannot start: the selected compute mode cannot be "
                   "honoured. Fix the configuration and start again.")
        try:
            stop_action_recording()
        except Exception:
            pass
        return 1

    # Display mode
    mode = "THREADED" if USE_THREADING else "SEQUENTIAL"
    safe_print(f"[INFO] Processing Mode: {mode}")
    if USE_THREADING:
        print(f"   Workers: {MAX_WORKERS}")
        print(f"   Monitoring: {'Enabled' if ENABLE_MONITORING else 'Disabled'}")
    print()
    
    try:
        # STEP 1: Get file/folder path from user
        print("="*70)
        safe_print("[STEP 1] FILE/FOLDER PATH")
        print("="*70)
        input_path_user = input("Enter file or folder path: ").strip()
        input_path_user = input_path_user.strip('"').strip("'")
        
        record_command_line_action("USER_INPUT", "User entered path", {"path": input_path_user})
        
        if not os.path.exists(input_path_user):
            error_msg = f"Path does not exist: {input_path_user}"
            safe_print(f"\n[ERROR] {error_msg}")
            record_command_line_action("ERROR", error_msg, {"path": input_path_user}, level="ERROR")
            return
        
        # STEP 2: Get source from user
        storage_source = get_or_select_source()
        if not storage_source:
            safe_print("\n[WARNING] Source selection cancelled or failed. Cannot proceed without a source.")
            return
        record_command_line_action("USER_INPUT", "User selected source", {"source": storage_source})
        
        # STEP 3: Get side from user
        storage_side = get_or_select_side()
        if not storage_side:
            safe_print("\n[WARNING] Side selection cancelled or failed. Cannot proceed without a side.")
            return
        record_command_line_action("USER_INPUT", "User selected side", {"side": storage_side})
        
        # STEP 4: Checkpoint/Resume configuration (only for folders)
        checkpoint_file = None
        if os.path.isdir(input_path_user):
            print("\n" + "="*70)
            safe_print("[STEP 4] CHECKPOINT/RESUME CONFIGURATION")
            print("="*70)
            print("Checkpoint allows resuming from where you left off if processing is interrupted.")
            print("Options:")
            print("  1. Enable checkpoint (recommended)")
            print("  2. Disable checkpoint")
            print("  3. Clear existing checkpoint and start fresh")
            
            checkpoint_choice = input("\nSelect option (1/2/3) [1]: ").strip() or "1"
            
            if checkpoint_choice == "1":
                # Auto-generate checkpoint file path
                from pathlib import Path
                checkpoint_dir = Path("data/checkpoints")
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                
                # Create unique checkpoint filename from folder path
                folder_hash = hashlib.sha256(str(Path(input_path_user).resolve()).encode()).hexdigest()[:16]
                checkpoint_file = checkpoint_dir / f"checkpoint_{folder_hash}_{storage_source}_{storage_side}.json"
                
                # Check if checkpoint exists
                if checkpoint_file.exists():
                    try:
                        import json
                        with open(checkpoint_file, 'r', encoding='utf-8') as f:
                            checkpoint_data = json.load(f)
                            processed_count = checkpoint_data.get('processed_count', 0)
                            last_updated = checkpoint_data.get('last_updated', 'unknown')
                            safe_print("\n[RESUME] Found existing checkpoint:")
                            safe_print(f"   Processed files: {processed_count}")
                            safe_print(f"   Last updated: {last_updated}")
                            safe_print(f"   Checkpoint file: {checkpoint_file}")
                            resume_confirm = input("\nResume from checkpoint? (Y/n): ").strip().lower()
                            if resume_confirm and resume_confirm != 'y':
                                # User wants to start fresh - clear checkpoint
                                checkpoint_file.unlink()
                                safe_print("[INFO] Checkpoint cleared. Starting fresh.")
                                checkpoint_file = checkpoint_dir / f"checkpoint_{folder_hash}_{storage_source}_{storage_side}.json"
                    except Exception as e:
                        logger.warning(f"Error reading checkpoint: {e}. Starting fresh.")
                
                if not checkpoint_file.exists():
                    safe_print(f"\n[CHECKPOINT] Checkpoint enabled: {checkpoint_file}")
                record_command_line_action("USER_INPUT", "Checkpoint enabled", {"checkpoint_file": str(checkpoint_file)})
            elif checkpoint_choice == "2":
                safe_print("\n[CHECKPOINT] Checkpoint disabled")
                record_command_line_action("USER_INPUT", "Checkpoint disabled", {})
            elif checkpoint_choice == "3":
                # Clear existing checkpoint
                from pathlib import Path
                checkpoint_dir = Path("data/checkpoints")
                folder_hash = hashlib.sha256(str(Path(input_path_user).resolve()).encode()).hexdigest()[:16]
                checkpoint_file_to_clear = checkpoint_dir / f"checkpoint_{folder_hash}_{storage_source}_{storage_side}.json"
                
                if checkpoint_file_to_clear.exists():
                    checkpoint_file_to_clear.unlink()
                    safe_print(f"\n[CHECKPOINT] Cleared existing checkpoint: {checkpoint_file_to_clear}")
                else:
                    safe_print("\n[CHECKPOINT] No existing checkpoint found to clear")
                
                # Create new checkpoint file
                checkpoint_file = checkpoint_dir / f"checkpoint_{folder_hash}_{storage_source}_{storage_side}.json"
                safe_print(f"[CHECKPOINT] New checkpoint enabled: {checkpoint_file}")
                record_command_line_action("USER_INPUT", "Checkpoint cleared and new one created", {"checkpoint_file": str(checkpoint_file)})
            else:
                safe_print(f"\n[WARNING] Invalid choice '{checkpoint_choice}'. Checkpoint disabled.")
                record_command_line_action("USER_INPUT", "Invalid checkpoint choice", {"choice": checkpoint_choice})
        else:
            # Single file processing - checkpoint not needed
            safe_print("\n[INFO] Checkpoint not needed for single file processing")
        
        # Display final configuration
        print("\n" + "="*70)
        safe_print("[CONFIG] CONFIGURATION SUMMARY")
        print("="*70)
        print(f"Path:   {input_path_user}")
        print(f"Source: {storage_source}")
        print(f"Side:   {storage_side}")
        print(f"Mode:   {mode}")
        if USE_THREADING:
            print(f"Workers: {MAX_WORKERS}")
        if checkpoint_file:
            print(f"Checkpoint: {checkpoint_file}")
        print("="*70)
        print()
        
        # Confirm before proceeding
        confirm = input("Proceed with processing? (Y/n): ").strip().lower()
        if confirm and confirm != 'y':
            safe_print("[CANCELLED] Processing cancelled by user.")
            return
        
        # PROCESS BASED ON TYPE
        if os.path.isfile(input_path_user): 
            safe_print(f"\n[FILE] Processing file: {input_path_user}\n")
            safe_print(f"[STORAGE] Storage: Source='{storage_source}', Side='{storage_side}'\n")
            record_command_line_action("FILE_OP", "Detected file type", {
                "path": input_path_user, 
                "type": "FILE",
                "source": storage_source,
                "side": storage_side
            })
            
            # Choose processing mode
            if USE_THREADING:
                result = main_read_file_threaded(
                    input_path_user,
                    storage_source=storage_source,
                    storage_side=storage_side
                )
            else:
                result = main_read_file_sequential(input_path_user)
                
            if result:
                # Check storage statistics to provide accurate message
                storage_stats = result.get('_storage_stats', {})
                stored = storage_stats.get('completed', 0) > 0
                duplicate = storage_stats.get('duplicates', 0) > 0
                failed = storage_stats.get('failed', 0) > 0
                
                if stored:
                    safe_print("\n[OK] File processed and stored successfully!")
                elif duplicate:
                    safe_print("\n[OK] File processed successfully (skipped as duplicate - already exists in database)")
                elif failed:
                    safe_print("\n[WARNING] File processed but storage failed. Check logs for details.")
                else:
                    safe_print("\n[OK] File processed successfully!")
                    safe_print("   Note: Storage statistics show 0 files stored. File may be duplicate or storage may have failed.")
        
        elif os.path.isdir(input_path_user):
            safe_print(f"\n[FOLDER] Processing folder: {input_path_user}\n")
            safe_print(f"[STORAGE] Storage: Source='{storage_source}', Side='{storage_side}'\n")
            record_command_line_action("FILE_OP", "Detected folder type", {
                "path": input_path_user, 
                "type": "DIRECTORY",
                "source": storage_source,
                "side": storage_side
            })
            
            # Choose processing mode. Both entry points print their own
            # summary and statistics, so neither return value is consumed.
            if USE_THREADING:
                main_read_folder_threaded(
                    input_path_user,
                    storage_source=storage_source,
                    storage_side=storage_side,
                    checkpoint_file=str(checkpoint_file) if checkpoint_file else None
                )
            else:
                main_read_folder_sequential(
                    input_path_user,
                    storage_source=storage_source,
                    storage_side=storage_side,
                    checkpoint_file=str(checkpoint_file) if checkpoint_file else None
                )
            
        else:
            error_msg = f"Invalid path type: {input_path_user}"
            safe_print(f"\n[ERROR] {error_msg}")
            record_command_line_action("ERROR", error_msg, {"path": input_path_user}, level="ERROR")
            return
    
    finally:
        # Stop recording when done
        stop_action_recording()
        if is_recording_enabled():
            safe_print(f"\n[INFO] Action recording stopped. Log saved to: {log_file}")

def extract_compute_mode_flag(argv):
    """Pull ``--compute-mode VALUE`` (or ``--compute-mode=VALUE``) out of argv.

    The interactive front-end has no argument parser, so the policy flag is
    extracted here and applied before either front-end runs. Returns
    ``(value_or_None, remaining_argv)``; raises ValueError when the flag is
    present without a value so the caller can report it.
    """
    rest, value = [], None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--compute-mode":
            if i + 1 >= len(argv):
                raise ValueError("--compute-mode requires a value "
                                 "(cpu, gpu, cpu+gpu or auto)")
            value = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--compute-mode="):
            value = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return value, rest


def apply_compute_mode(value=None, *, quiet=False, printer=None):
    """Resolve, report and validate the compute policy. Returns an exit code.

    This is the single application point for the operator's mode selection:
    the command line (``--compute-mode``) is pinned first, then the effective
    mode is reported with the configuration layer it came from, the host's
    ability to honour it is checked *before* any work starts, and a mode that
    cannot be honoured fails here with an actionable error instead of
    degrading silently. Returns 0 when the run may proceed, 1 otherwise.
    """
    from core.compute import policy as compute_policy

    printer = printer or safe_print
    try:
        if value is not None:
            selection = compute_policy.set_mode_override(
                value, source=f"command line (--compute-mode {value})"
            )
        else:
            selection = compute_policy.mode_selection()
    except compute_policy.ComputeModeError as exc:
        printer(f"[ERROR] {exc}")
        return 1

    support = compute_policy.mode_support(selection)
    compute_policy.record(selection, support)
    if not quiet:
        compute_policy.emit(selection, support, printer=printer)
    if not support.ok:
        if quiet:
            # ``emit`` was suppressed, so the reason must still be visible.
            printer(f"[ERROR] {support.problem}")
            if support.action:
                printer(f"[ACTION] {support.action}")
        return 1
    if support.degraded and not quiet:
        printer(f"[WARNING] {support.problem}")
        if support.action:
            printer(f"[ACTION] {support.action}")
    return 0


def _batch_document_from_result(result, request):
    from services.reporting.ingestion_batch import (
        counts_from_stats, document_from_parts, document_from_result,
    )

    try:
        return document_from_result(result, request)
    except Exception:
        return document_from_parts(
            identity={
                "source": getattr(request, "source", None),
                "side": getattr(request, "side", None),
                "path": getattr(request, "path", None),
                "started_at": getattr(result, "started_at", None),
                "finished_at": getattr(result, "finished_at", None),
            },
            counts=counts_from_stats(getattr(result, "stats", None)),
            type_outcomes=(getattr(result, "batch_report", None) or {}).get("type_outcomes"),
            path_ids=(getattr(result, "batch_report", None) or {}).get("path_ids"),
            errors=getattr(result, "errors", None),
            warnings=getattr(result, "warnings", None),
        )


def _write_batch_html(document, report_html):
    from services.reporting.ingestion_batch import default_html_path, write_html

    destination = default_html_path(document.get("identity") or {}) \
        if report_html in (None, "AUTO") else report_html
    return write_html(document, destination)


def _report_payload(document):
    return {
        "identity": document.get("identity") or {},
        "counts": document.get("counts") or {},
        "types": document.get("types") or [],
        "categories": document.get("categories") or [],
        "category_labels": document.get("category_labels") or [],
        "keywords": document.get("keywords") or [],
    }


def _emit_job_batch_report(job_id, *, output_json, quiet, report_html, printer):
    from services.jobs.manager import JobManager
    from services.reporting.ingestion_batch import document_from_job, render_text

    job = JobManager.get_instance().get(job_id)
    if job is None:
        msg = f"Job not found: {job_id}"
        if output_json:
            print(__import__("json").dumps({"success": False, "error": msg}))
        else:
            printer(f"[ERROR] {msg}")
        return 1
    try:
        document = document_from_job(job)
    except Exception as exc:
        msg = f"Could not build the batch report: {exc}"
        if output_json:
            print(__import__("json").dumps({"success": False, "error": msg}))
        else:
            printer(f"[ERROR] {msg}")
        return 3
    html_path = None
    if report_html:
        try:
            html_path = _write_batch_html(document, report_html)
        except Exception as exc:
            printer(f"[WARNING] Could not write HTML report: {exc}")
    payload = {"success": True, "job_id": job_id, "report": _report_payload(document)}
    if html_path:
        payload["report_html"] = html_path
    if output_json:
        print(__import__("json").dumps(payload, default=str))
    else:
        printer(render_text(document).rstrip())
        if html_path:
            printer(f"[OK] Wrote printable report: {html_path}")
    return 0


def cli_main(argv=None) -> int:
    """Non-interactive CLI entry point (thin adapter over IngestionService).

    The interactive frontend and this command share the exact same service
    layer; this adapter only parses arguments and renders output.

    Usage examples:
        python -m apps.cli.main --path /data/inbox --source web --side a --json
        python -m apps.cli.main --path /data/inbox --source web --side a \
            --workers 4 --checkpoint run1 --quiet

    Exit codes: 0 success, 1 usage/configuration error, 2 partial failures,
    3 complete failure.
    """
    import argparse as _argparse
    import json as _json
    from pathlib import Path as _Path

    parser = _argparse.ArgumentParser(
        prog="file-analysis-cli",
        description="Ingest files into the SYLTHARAE database (non-interactive)",
    )
    parser.add_argument("--path", required=False,
                        help="File or directory to ingest")
    parser.add_argument("--source", required=False, help="Source name for storage")
    parser.add_argument("--side", required=False, help="Side name for storage")
    parser.add_argument("--report-job", dest="report_job", default=None,
                        metavar="JOB_ID",
                        help="Print the dedicated batch report for an existing "
                             "ingestion job (no ingest)")
    parser.add_argument("--report-html", dest="report_html", nargs="?",
                        const="AUTO", default=None, metavar="FILE",
                        help="Write the batch report as printable HTML "
                             "(default path under the app data directory)")
    parser.add_argument("--workers", type=int, default=0,
                        help="Worker threads (0 = configured default)")
    parser.add_argument("--compute-mode", dest="compute_mode", default=None,
                        metavar="MODE",
                        help="Compute policy: cpu (CPU-only), gpu (GPU-only, "
                             "strict - refuses rather than substituting the "
                             "CPU), cpu+gpu (both worker sets) or auto "
                             "(automatic routing). Overrides "
                             "processing.compute_mode in data/settings.json; "
                             "default: the configured value, else auto.")
    parser.add_argument("--checkpoint", default=None,
                        help="Checkpoint name for crash recovery/resume")
    parser.add_argument("--no-recursive", action="store_true",
                        help="Do not recurse into subdirectories")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="Output format")
    parser.add_argument("--json", action="store_true", help="Shorthand for --format json")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    # Resolved before the compute-policy block below, which needs to know
    # whether the caller asked for machine-readable output (the startup proof
    # is suppressed for --json so the JSON payload stays parseable). Reading it
    # after that block raised NameError on every invocation.
    output_json = args.json or args.format == "json"

    # The compute policy is applied before anything else: an impossible
    # request (e.g. GPU-ONLY on a host with no GPU) must fail here, not after
    # files have been read, and the selected mode must be visible at startup.
    #
    # In --json mode the human-readable policy lines are buffered instead of
    # discarded: the stream must stay parseable, but a refusal must still say
    # why it refused. Discarding them (as this call did before) made a
    # GPU-ONLY run on a GPU-less host exit 1 with no output at all.
    policy_lines: List[str] = []
    policy_exit = apply_compute_mode(
        args.compute_mode, quiet=args.quiet,
        printer=(lambda line: policy_lines.append(line)) if output_json else None,
    )
    if policy_exit:
        if output_json:
            print(_json.dumps({
                "success": False,
                "compute_mode": args.compute_mode or "configured",
                "error": "compute policy refused this run",
                "details": {"policy": policy_lines},
            }))
        return policy_exit

    def _emit(payload_dict=None, text=None):
        if output_json:
            print(_json.dumps(payload_dict or {}))
        elif text is not None and not args.quiet:
            safe_print(text)

    if args.report_job:
        return _emit_job_batch_report(
            args.report_job, output_json=output_json, quiet=args.quiet,
            report_html=args.report_html,
            printer=lambda text: None if args.quiet and not output_json else safe_print(text),
        )

    if not args.path or not args.source or not args.side:
        msg = "Provide --path, --source and --side, or --report-job JOB_ID"
        if output_json:
            print(_json.dumps({"success": False, "error": msg}))
        else:
            safe_print(f"[ERROR] {msg}")
        return 1

    # -- Build the service request (same object the web frontend sends) ----
    from services.ingesting.options import IngestionOptions
    from services.ingesting.service import (
        IngestionRequest, IngestionService, IngestionValidationError,
    )

    options = IngestionOptions(
        max_workers=args.workers,
        checkpoint="auto" if args.checkpoint else "off",
    )
    request = IngestionRequest(
        path=str(_Path(args.path).expanduser()),
        source=args.source,
        side=args.side,
        recursive=not args.no_recursive,
        options=options,
    )
    if args.checkpoint:
        # Named checkpoints keep the operator's existing naming scheme.
        try:
            from core.app_paths import get_checkpoints_dir

            ckpt_dir = get_checkpoints_dir()
        except Exception:
            ckpt_dir = _Path("data/checkpoints")
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        options.checkpoint = "auto"

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    service = IngestionService()
    try:
        service.validate(request)
    except IngestionValidationError as exc:
        msg = f"Invalid ingestion request: {exc}"
        _emit({"success": False, "error": msg}, f"[ERROR] {msg}")
        return 1

    def _progress(snapshot):
        if args.quiet or output_json:
            return
        done = snapshot.get("files_done", 0)
        total = snapshot.get("total_files", 0)
        pct = snapshot.get("percent", 0)
        if sys.stdout.isatty():
            # Interactive redraw only when attached to a terminal.
            print(f"\rProgress: {done}/{total} ({pct}%)", end="", flush=True)
        else:
            # Captured output must never carry a partial line: log records
            # glue themselves onto it ("Progress: 2/11INFO:...").  A full
            # line through the logger stays ordered with every other record.
            logger.info(f"Progress: {done}/{total} ({pct}%)")

    result = service.run(request, progress_cb=_progress)

    if result.paused:
        msg = "Ingestion paused at a safe boundary"
        _emit({"success": False, "paused": True, "error": msg}, f"[PAUSED] {msg}")
        return 3
    if result.cancelled:
        msg = "Ingestion cancelled"
        _emit({"success": False, "cancelled": True, "error": msg}, f"[CANCELLED] {msg}")
        return 3
    if not result.success or result.errors:
        msg = "Ingestion failed"
        _emit({"success": False, "error": msg,
               "details": {"errors": result.errors[:20]}}, f"[ERROR] {msg}")
        return 3

    failed = int(result.stats.get("files_failed") or 0)
    from core.compute import policy as compute_policy

    compute_record = compute_policy.result_block()
    payload = {
        "success": True,
        "path": request.path,
        "source": args.source,
        "side": args.side,
        "summary": {
            "discovered": result.stats.get("files_total"),
            "completed": result.stats.get("files_stored"),
            "duplicates": result.stats.get("files_duplicates"),
            "failed": failed,
        },
        # The compute policy travels with the results: requested mode, its
        # configuration layer, the devices that actually executed work and any
        # fallback or capability refusal.
        "compute": compute_record,
    }
    actual = ", ".join(f"{device} x{count}"
                       for device, count in sorted(compute_record["actual_devices"].items()))
    summary_line = (f"[OK] Ingestion complete: {payload['summary']} | "
                    f"Compute Mode: {compute_record['mode_label']}"
                    + (f" | Actual Device: {actual}" if actual else ""))
    document = _batch_document_from_result(result, request)
    payload["report"] = _report_payload(document)
    html_path = None
    try:
        html_path = _write_batch_html(document, args.report_html or "AUTO")
        payload["report_html"] = html_path
        if isinstance(getattr(result, "batch_report", None), dict):
            result.batch_report["html_path"] = html_path
    except Exception as exc:
        if not args.quiet:
            safe_print(f"[WARNING] Could not write HTML report: {exc}")
    if output_json:
        _emit(payload)
    else:
        _emit(None, summary_line)
        if not args.quiet:
            from services.reporting.ingestion_batch import render_text

            safe_print(render_text(document).rstrip())
            if html_path:
                safe_print(f"[OK] Wrote printable report: {html_path}")
    return 2 if failed else 0

if __name__ == "__main__":
    # Dispatch: any flag -> non-interactive service adapter (cli_main);
    # policy-only or no flags -> legacy interactive flow (deprecated, kept for
    # terminal-only environments). ``--compute-mode`` is accepted by both
    # front-ends, so it is recognised here rather than being mistaken for an
    # ingestion flag with missing --path/--source/--side.
    try:
        _requested_mode, _remaining = extract_compute_mode_flag(sys.argv[1:])
    except ValueError as _mode_error:
        safe_print(f"[ERROR] {_mode_error}")
        sys.exit(1)

    if any(a.startswith("-") for a in _remaining):
        sys.exit(cli_main())
    if _requested_mode is not None:
        if apply_compute_mode(_requested_mode) != 0:
            sys.exit(1)
    try:
        sys.exit(main() or 0)
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        record_command_line_action("SYSTEM", "Application interrupted by user", {}, level="WARNING")
        stop_action_recording()
        input("\nPress Enter to exit...")
        
    except Exception as e:
        safe_print(f"\n[ERROR] Unexpected error: {str(e)}")
        import traceback
        error_trace = traceback.format_exc()
        record_command_line_action(
            "ERROR", 
            f"Unexpected error: {str(e)}", 
            {"error": str(e), "traceback": error_trace}, 
            level="ERROR"
        )
        traceback.print_exc()
        stop_action_recording()
        input("\nPress Enter to exit...")

# ===========================================================================
# CLI-01: Non-interactive mode for automation and CI
# ===========================================================================
