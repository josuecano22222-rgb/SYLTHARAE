"""
PDF Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages PDF file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_pdf import PDFFileReader
from typing import Dict, Any, Optional


class PDFReaderRepository(BaseReaderRepository):
    """
    Repository for PDF file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages PDFFileReader instance
    - Provides repository interface for PDF operations
    """
    
    def __init__(self):
        """Initialize repository with PDFFileReader instance"""
        reader = PDFFileReader()
        super().__init__(reader)
    
    def read_pdf_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read PDF file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with PDF content or error information
        """
        return self.read_file(file_info)
