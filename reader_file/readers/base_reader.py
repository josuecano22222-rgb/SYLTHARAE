"""
Base Reader Module - Aligned with Database Design Principles

This module provides a base structure for all file readers, following the same
design principles as the database layer:
- Class-based design (all functions within classes)
- Separation of concerns
- Consistent error handling
- Proper resource management
- Standardized interface
"""

import os
import time
import logging
from typing import Dict, Any, Optional, Set
from abc import ABC, abstractmethod

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

logger = logging.getLogger(__name__)


class BaseReader(ABC):
    """
    Base class for all file readers.
    
    Follows database design principles (similar to BaseRepository):
    - All operations are class methods
    - Consistent interface
    - Proper error handling
    - Resource management
    - Validation
    
    Each reader class should inherit from this and implement:
    - get_supported_extensions() -> Set[str]
    - read_file(file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]
    """
    
    def __init__(self):
        """Initialize base reader"""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._resource_monitoring_available = None
        self._should_yield_func = None
        self._get_yield_duration_func = None
        self._init_cpu_management()
    
    def extensions_type_extract(self) -> Set[str]:
        """
        Return set of supported file extensions.
        Alias for get_supported_extensions() for backward compatibility.
        
        Returns:
            Set of file extensions (e.g., {'.pdf', '.docx'})
        """
        return self.get_supported_extensions()
    
    def specify_method_of_reading_the_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read file using appropriate method.
        Alias for read_file() for backward compatibility.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with extracted content or None on error
        """
        return self.read_file(file_info)
    
    @abstractmethod
    def get_supported_extensions(self) -> Set[str]:
        """
        Return set of supported file extensions.
        
        Returns:
            Set of file extensions (e.g., {'.pdf', '.docx'})
        """
        pass
    
    @abstractmethod
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with extracted content or None on error
        """
        pass
    
    def effective_extension(self, file_info: Dict[str, Any]) -> str:
        """Return the extension that should drive internal format dispatch.

        DETECT-01: readers must not dispatch on the filename alone. The router
        places a content-verified type in ``file_info['effective_extension']``
        when magic-byte identification disagrees with (or replaces) the
        declared extension. Readers consult it first and fall back to the
        path's own suffix, so a file named ``report.docx`` whose bytes are a
        PDF is handled by the PDF code path.

        Args:
            file_info: Dictionary with a ``path`` key and an optional
                ``effective_extension`` key.

        Returns:
            Lower-case extension including the leading dot, or ``''``.
        """
        declared = str((file_info or {}).get('effective_extension') or '').lower()
        if declared:
            return declared if declared.startswith('.') else '.' + declared
        path = str((file_info or {}).get('path') or '')
        lower = path.lower()
        # Compound archive extensions must survive: os.path.splitext('.tar.gz')
        # reports only '.gz', which would send a tarball down the gzip path.
        for compound in ('.tar.gz', '.tar.bz2', '.tar.xz'):
            if lower.endswith(compound):
                return compound
        return os.path.splitext(path)[1].lower()

    def validate_file_info(self, file_info: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate file_info dictionary.
        
        Args:
            file_info: Dictionary to validate
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not file_info or not isinstance(file_info, dict):
            error_msg = format_validation_error(
                'file_info', 
                file_info, 
                'must be a non-empty dictionary'
            )
            return False, error_msg
        
        file_path = file_info.get("path")
        if not file_path:
            error_msg = format_validation_error(
                'path',
                file_path,
                'must be provided in file_info'
            )
            return False, error_msg
        
        file_path = str(file_path)
        if not os.path.exists(file_path):
            error_msg = f"File not found: {file_path}"
            return False, error_msg
        
        if not os.access(file_path, os.R_OK):
            error_msg = f"File is not readable: {file_path}"
            return False, error_msg
        
        return True, None
    
    def handle_read_error(
        self,
        error: Exception,
        file_path: str,
        operation: str = "read_file"
    ) -> Dict[str, Any]:
        """
        Handle read errors consistently.
        
        Args:
            error: Exception that occurred
            file_path: Path to file that failed
            operation: Operation name for logging
        
        Returns:
            Error result dictionary
        """
        handle_error(
            error,
            category=ErrorCategory.FILE_PROCESSING,
            severity=ErrorSeverity.MEDIUM,
            context={
                'operation': operation,
                'file_path': file_path,
                'reader': self.__class__.__name__
            },
        )
        
        return {
            "error": str(error),
            "path": file_path
        }
    
    def create_error_result(
        self,
        error_message: str,
        file_path: str
    ) -> Dict[str, Any]:
        """
        Create standardized error result.
        
        Args:
            error_message: Error message
            file_path: Path to file
        
        Returns:
            Error result dictionary
        """
        return {
            "error": error_message,
            "path": file_path
        }
    
    def create_success_result(
        self,
        content: Dict[str, Any],
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None
         ) -> Dict[str, Any]:
        """
        Create standardized success result.
        
        Args:
            content: Extracted content
            file_path: Path to file
            metadata: Optional metadata
        
        Returns:
            Success result dictionary
        """
        result = {
            "content": content,
            "path": file_path,
            "status": "success"
        }
        
        if metadata:
            result["metadata"] = metadata
        
        return result
    
    def _init_cpu_management(self):
        """Initialize CPU management helpers"""
        try:
            from core.resource_coordinator import should_yield, get_yield_duration
            self._resource_monitoring_available = True
            self._should_yield_func = should_yield
            self._get_yield_duration_func = get_yield_duration
        except ImportError:
            self._resource_monitoring_available = False
            self._should_yield_func = lambda *args: False
            self._get_yield_duration_func = lambda: 0.0
    
    def should_yield_cpu(self, aggressive: bool = False) -> bool:
        """
        Check if current operation should yield to prevent CPU overload.
        
        Args:
            aggressive: If True, use more aggressive thresholds for yielding
        
        Returns:
            True if operation should yield/pause
        """
        if not self._resource_monitoring_available:
            return False
        return self._should_yield_func(aggressive)
    
    def yield_cpu_if_needed(self, aggressive: bool = False):
        """
        Yield CPU time if system is overloaded.
        
        Args:
            aggressive: If True, use more aggressive thresholds for yielding
        """
        if not self._resource_monitoring_available:
            return
        
        if self._should_yield_func(aggressive):
            yield_duration = self._get_yield_duration_func()
            if yield_duration > 0:
                time.sleep(yield_duration)
    
    def yield_periodically(self, iteration_count: int, interval: int = 10, aggressive: bool = False):
        """
        Yield CPU periodically during loops.
        
        Args:
            iteration_count: Current iteration number (0-indexed)
            interval: Yield every N iterations
            aggressive: If True, use more aggressive thresholds for yielding
        """
        if iteration_count > 0 and iteration_count % interval == 0:
            self.yield_cpu_if_needed(aggressive)