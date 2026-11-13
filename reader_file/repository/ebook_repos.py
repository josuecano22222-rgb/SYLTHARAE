"""
Ebook Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages ebook file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_ebook import EbookFileReader
from typing import Dict, Any, Optional


class EbookReaderRepository(BaseReaderRepository):
    """
    Repository for ebook file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages EbookFileReader instance
    - Provides repository interface for ebook operations
    """
    
    def __init__(self):
        """Initialize repository with EbookFileReader instance"""
        reader = EbookFileReader()
        super().__init__(reader)
    
    def read_ebook_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read ebook file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with ebook content or error information
        """
        return self.read_file(file_info)
