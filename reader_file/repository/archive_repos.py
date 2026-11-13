"""
Archive Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages archive file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_archive import ArchiveFileReader
from typing import Dict, Any, Optional


class ArchiveReaderRepository(BaseReaderRepository):
    """
    Repository for archive file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages ArchiveFileReader instance
    - Provides repository interface for archive operations
    """
    
    def __init__(self):
        """Initialize repository with ArchiveFileReader instance"""
        reader = ArchiveFileReader()
        super().__init__(reader)
    
    def extract_archive(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract archive file.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with extraction path or error information
        """
        return self.read_file(file_info)
