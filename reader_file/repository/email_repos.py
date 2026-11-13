"""
Email Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages email file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_email import EmailFileReader
from typing import Dict, Any, Optional


class EmailReaderRepository(BaseReaderRepository):
    """
    Repository for email file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages EmailFileReader instance
    - Provides repository interface for email operations
    """
    
    def __init__(self):
        """Initialize repository with EmailFileReader instance"""
        reader = EmailFileReader()
        super().__init__(reader)
    
    def read_email_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read email file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with email content or error information
        """
        return self.read_file(file_info)
