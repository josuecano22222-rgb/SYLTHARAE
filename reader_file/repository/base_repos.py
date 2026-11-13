"""
Base Repository for File Readers
Aligned with database design principles (similar to BaseRepository)

This module provides a base repository class for file reader operations,
following the same design pattern as the database layer.
"""

from typing import Optional, Any, Dict
import logging

logger = logging.getLogger(__name__)


class BaseReaderRepository:
    """
    Base repository for file reader operations.
    
    Follows database design principles (similar to BaseRepository):
    - Class-based design (all functions within classes)
    - Consistent error handling
    - Resource management
    - Standardized interface
    
    Note: Unlike database repositories, reader repositories don't need
    database connections, but they follow the same structural pattern
    for consistency.
    """
    
    def __init__(self, reader: Optional[Any] = None):
        """
        Initialize repository.
        
        Args:
            reader: Optional reader instance to manage
        """
        self.reader = reader
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def get_reader(self) -> Optional[Any]:
        """
        Get the reader instance.
        
        Returns:
            Reader instance or None
        """
        return self.reader
    
    def set_reader(self, reader: Any):
        """
        Set the reader instance.
        
        Args:
            reader: Reader instance to set
        """
        self.reader = reader
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read file using the managed reader.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with extracted content or None on error
        """
        if not self.reader:
            self.logger.error("No reader instance available")
            return {
                "error": "Reader not initialized",
                "path": file_info.get("path", "unknown")
            }
        
        try:
            return self.reader.read_file(file_info)
        except Exception as e:
            self.logger.error(f"Error reading file: {e}")
            return {
                "error": str(e),
                "path": file_info.get("path", "unknown")
            }
    
    def get_supported_extensions(self) -> set:
        """
        Get supported extensions from the reader.
        
        Returns:
            Set of supported file extensions
        """
        if not self.reader:
            return set()
        
        try:
            return self.reader.get_supported_extensions()
        except Exception as e:
            self.logger.error(f"Error getting supported extensions: {e}")
            return set()
