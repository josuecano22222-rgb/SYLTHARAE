"""
Data Validation Helpers
Provides validation functions for data operations
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import logging

from Hdg_Err_Ex_Log import (
    handle_error, ErrorCategory, ErrorSeverity
)

logger = logging.getLogger(__name__)


def validate_file_path(file_path: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate file path
    
    Args:
        file_path: File path to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file_path:
        return False, "File path is required"
    
    if not isinstance(file_path, (str, Path)):
        return False, f"File path must be a string or Path, got {type(file_path).__name__}"
    
    file_path = str(file_path)
    
    if not file_path.strip():
        return False, "File path cannot be empty"
    
    if not os.path.exists(file_path):
        return False, f"File does not exist: {file_path}"
    
    if not os.path.isfile(file_path):
        return False, f"Path is not a file: {file_path}"
    
    if not os.access(file_path, os.R_OK):
        return False, f"File is not readable: {file_path}"
    
    return True, None


def validate_file_info(file_info: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate file_info dictionary
    
    Args:
        file_info: File information dictionary
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file_info:
        return False, "file_info is required"
    
    if not isinstance(file_info, dict):
        return False, f"file_info must be a dictionary, got {type(file_info).__name__}"
    
    if not file_info:
        return False, "file_info cannot be empty"
    
    # Check required fields
    file_path = file_info.get('path')
    if not file_path:
        return False, "file_info must contain 'path' field"
    
    # Validate path
    is_valid, error_msg = validate_file_path(file_path)
    if not is_valid:
        return False, f"Invalid file path in file_info: {error_msg}"
    
    # Validate optional fields
    file_name = file_info.get('name')
    if file_name is not None:
        if not isinstance(file_name, str):
            return False, "file_info 'name' must be a string"
        if len(file_name) > 500:
            return False, "file_info 'name' must be 500 characters or less"
    
    file_size = file_info.get('size_bytes')
    if file_size is not None:
        if not isinstance(file_size, (int, float)):
            return False, "file_info 'size_bytes' must be a number"
        if file_size < 0:
            return False, "file_info 'size_bytes' must be non-negative"
    
    return True, None


def validate_database_id(id_value: Any, id_name: str = "ID") -> Tuple[bool, Optional[str]]:
    """
    Validate database ID
    
    Args:
        id_value: ID value to validate
        id_name: Name of the ID field (for error messages)
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if id_value is None:
        return False, f"{id_name} cannot be None"
    
    if not isinstance(id_value, int):
        return False, f"{id_name} must be an integer, got {type(id_value).__name__}"
    
    if id_value <= 0:
        return False, f"{id_name} must be a positive integer, got {id_value}"
    
    return True, None


def validate_result_dict(result: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate result dictionary from file processing
    
    Args:
        result: Result dictionary to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if result is None:
        return False, "Result cannot be None"
    
    if not isinstance(result, dict):
        return False, f"Result must be a dictionary, got {type(result).__name__}"
    
    # Check for required keys (at minimum should have Metadata or Content)
    if 'Metadata' not in result and 'Content' not in result:
        return False, "Result must contain at least 'Metadata' or 'Content' key"
    
    return True, None


def validate_string_field(value: Any, field_name: str, max_length: Optional[int] = None, 
                         allow_empty: bool = False) -> Tuple[bool, Optional[str]]:
    """
    Validate string field
    
    Args:
        value: Value to validate
        field_name: Name of the field (for error messages)
        max_length: Maximum length (None for no limit)
        allow_empty: Whether empty strings are allowed
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if value is None:
        return False, f"{field_name} cannot be None"
    
    if not isinstance(value, str):
        return False, f"{field_name} must be a string, got {type(value).__name__}"
    
    if not allow_empty and not value.strip():
        return False, f"{field_name} cannot be empty"
    
    if max_length is not None and len(value) > max_length:
        return False, f"{field_name} must be {max_length} characters or less, got {len(value)}"
    
    return True, None


def validate_list_field(value: Any, field_name: str, min_length: Optional[int] = None,
                       max_length: Optional[int] = None, allow_empty: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate list field
    
    Args:
        value: Value to validate
        field_name: Name of the field (for error messages)
        min_length: Minimum length (None for no limit)
        max_length: Maximum length (None for no limit)
        allow_empty: Whether empty lists are allowed
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if value is None:
        return False, f"{field_name} cannot be None"
    
    if not isinstance(value, (list, tuple)):
        return False, f"{field_name} must be a list or tuple, got {type(value).__name__}"
    
    if not allow_empty and len(value) == 0:
        return False, f"{field_name} cannot be empty"
    
    if min_length is not None and len(value) < min_length:
        return False, f"{field_name} must have at least {min_length} items, got {len(value)}"
    
    if max_length is not None and len(value) > max_length:
        return False, f"{field_name} must have at most {max_length} items, got {len(value)}"
    
    return True, None


def safe_get_file_size(file_path: str) -> Optional[int]:
    """
    Safely get file size
    
    Args:
        file_path: Path to file
    
    Returns:
        File size in bytes or None on error
    """
    try:
        if not os.path.exists(file_path):
            return None
        return os.path.getsize(file_path)
    except OSError as e:
        handle_error(
            e,
            category=ErrorCategory.FILE_PROCESSING,
            severity=ErrorSeverity.LOW,
            context={'operation': 'safe_get_file_size', 'file_path': file_path}
        )
        return None


def validate_and_normalize_file_info(file_info: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Validate and normalize file_info dictionary
    
    Args:
        file_info: File information dictionary
    
    Returns:
        Tuple of (normalized_file_info, error_message)
    """
    # Validate
    is_valid, error_msg = validate_file_info(file_info)
    if not is_valid:
        return {}, error_msg
    
    # Create normalized copy
    normalized = file_info.copy()
    
    # Ensure required fields exist
    if 'name' not in normalized:
        normalized['name'] = os.path.basename(normalized.get('path', ''))
    
    if 'size_bytes' not in normalized:
        file_path = normalized.get('path')
        if file_path:
            size = safe_get_file_size(file_path)
            normalized['size_bytes'] = size if size is not None else 0
    
    if 'extension' not in normalized:
        file_path = normalized.get('path', '')
        if file_path:
            ext = os.path.splitext(file_path)[1]
            normalized['extension'] = ext.lstrip('.') if ext else 'unknown'
    
    return normalized, None

