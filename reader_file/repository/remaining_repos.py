"""
Remaining Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages remaining file reader operations (text-based files).
"""

from .base_repos import BaseReaderRepository
from ..readers.read_remaining import RemainingFileReader
from typing import Dict, Any, Optional


class RemainingReaderRepository(BaseReaderRepository):
    """
    Repository for remaining file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages RemainingFileReader instance
    - Provides repository interface for remaining file operations
    """
    
    def __init__(self):
        """Initialize repository with RemainingFileReader instance"""
        reader = RemainingFileReader()
        super().__init__(reader)
    
    def read_remaining_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read remaining file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with file content or error information
        """
        return self.read_file(file_info)
