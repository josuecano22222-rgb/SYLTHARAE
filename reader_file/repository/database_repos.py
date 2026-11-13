"""
Database Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages database file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_database import DatabaseFileReader
from typing import Dict, Any, Optional


class DatabaseReaderRepository(BaseReaderRepository):
    """
    Repository for database file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages DatabaseFileReader instance
    - Provides repository interface for database operations
    """
    
    def __init__(self):
        """Initialize repository with DatabaseFileReader instance"""
        reader = DatabaseFileReader()
        super().__init__(reader)
    
    def read_database_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read database file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with database content or error information
        """
        return self.read_file(file_info)
