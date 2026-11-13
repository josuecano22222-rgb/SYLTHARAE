"""
Main file routing module that determines which reader to use based on file extension.
Aligned with database design principles - all functions within classes

This module provides backward-compatible wrapper functions that use the
FileRouterService class, following the same design pattern as the database layer.
All functionality is now implemented in classes (FileRouterService, FileReaderService).
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

import logging
logger = logging.getLogger(__name__)

from .services.file_router_service import FileRouterService, get_file_router_service

# Create singleton instance for backward compatibility
_file_router_service = get_file_router_service()


def main_specify_method_of_reading_the_file(
    file_info: Dict[str, Any],
    collect: bool = True,
    depth: int = 0,
    storage_source: Optional[str] = None,
    storage_side: Optional[str] = None,
    storage_pipeline: Optional[Any] = None,
    store_result: bool = True,
    progress_ledger: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
    """
    Read a file using appropriate reader and optionally collect results.

    Backward compatibility wrapper that delegates to FileRouterService.
    All functionality is now implemented in the FileRouterService class.

    DATA-01/ARCH-04: storage context is passed explicitly by the caller
    (previously it was smuggled through function attributes, which is hidden
    global state). ``store_result=False`` lets the storage-pipeline owner
    (IntegratedFileReader) persist the top-level file exactly once while the
    router still persists child artifacts (extracted members, attachments).

    Args:
        file_info: Dictionary containing file information
        collect: Whether to collect results
        depth: Current recursion depth
        storage_source: Optional storage source name
        storage_side: Optional storage side name
        storage_pipeline: Optional storage pipeline (explicit dependency)
        store_result: Whether the router should persist the top-level file
        progress_ledger: Optional shared work ledger. Containers pass it down so
            dynamically discovered children grow the same progress denominator
            the calling job is reporting against.

    Returns:
        Dictionary with processing result or None
    """
    return _file_router_service.process_file(
        file_info,
        collect=collect,
        depth=depth,
        storage_source=storage_source,
        storage_side=storage_side,
        storage_pipeline=storage_pipeline,
        store_result=store_result,
        progress_ledger=progress_ledger,
    )


def specify_method_of_reading_the_file_list(
    list_tree: List[Dict[str, Any]],
    collect: bool = True
) -> List[Dict[str, Any]]:
    """
    Process a list of files.
    
    Backward compatibility wrapper that delegates to FileRouterService.
    All functionality is now implemented in the FileRouterService class.
    
    Args:
        list_tree: List of file information dictionaries
        collect: Whether to collect results
    
    Returns:
        List of processing results
    """
    return _file_router_service.process_file_list(list_tree, collect=collect)


def _process_extracted_files(
    extraction_path: str,
    extraction_type: str,
    parent_file: str,
    collect: bool,
    depth: int,
    use_parallel: bool = True,
    storage_source: Optional[str] = None,
    storage_side: Optional[str] = None,
    progress_ledger: Optional[Any] = None
    ) -> Dict[str, Any]:
    """
    Process extracted files from archives or emails.
    
    Backward compatibility wrapper that delegates to FileRouterService.
    All functionality is now implemented in the FileRouterService class.
    
    Args:
        extraction_path: Path to extracted files directory
        extraction_type: Type of extraction (e.g., 'archive', 'email_attachment')
        parent_file: Path to parent file
        collect: Whether to collect results
        depth: Current recursion depth
        use_parallel: Whether to use parallel processing
        storage_source: Optional storage source name
        storage_side: Optional storage side name
        progress_ledger: Optional shared work ledger

    Returns:
        Dictionary with extraction results
    """
    # Get storage_pipeline from function attributes if available
    storage_pipeline = getattr(main_specify_method_of_reading_the_file, '_storage_pipeline', None)

    return _file_router_service._process_extracted_files(
        extraction_path,
        extraction_type,
        parent_file,
        collect,
        depth,
        use_parallel,
        storage_source,
        storage_side,
        storage_pipeline,
        progress_ledger
    )


def _process_email_result(
    email_result: Dict[str, Any],
    file_path: str,
    collect: bool,
    depth: int,
    storage_source: Optional[str] = None,
    storage_side: Optional[str] = None,
    storage_pipeline: Optional[Any] = None,
    progress_ledger: Optional[Any] = None
    ) -> Dict[str, Any]:
    """
    Process email results with message content and attachments separated.
    
    Backward compatibility wrapper that delegates to FileRouterService.
    All functionality is now implemented in the FileRouterService class.
    
    Args:
        email_result: Email extraction result
        file_path: Path to email file
        collect: Whether to collect results
        depth: Current recursion depth
        storage_source: Optional storage source name
        storage_side: Optional storage side name
        storage_pipeline: Optional storage pipeline instance
    
    Returns:
        Dictionary with email processing results
    """
    return _file_router_service._process_email_result(
        email_result,
        file_path,
        collect,
        depth,
        storage_source,
        storage_side,
        storage_pipeline,
        progress_ledger
     )
