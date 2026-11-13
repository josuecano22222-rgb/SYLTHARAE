"""
Office Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages office file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_office import OfficeFileReader
from typing import Dict, Any, Optional


class OfficeReaderRepository(BaseReaderRepository):
    """
    Repository for office file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages OfficeFileReader instance
    - Provides repository interface for office operations
    """
    
    def __init__(self):
        """Initialize repository with OfficeFileReader instance"""
        reader = OfficeFileReader()
        super().__init__(reader)
    
    def read_office_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read office file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with office content or error information
        """
        return self.read_file(file_info)
