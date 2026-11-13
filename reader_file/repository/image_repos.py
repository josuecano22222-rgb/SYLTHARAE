"""
Image Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages image file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_img_fast import ImageFileReader
from typing import Dict, Any, Optional


class ImageReaderRepository(BaseReaderRepository):
    """
    Repository for image file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages ImageFileReader instance
    - Provides repository interface for image operations
    """
    
    def __init__(self):
        """Initialize repository with ImageFileReader instance"""
        reader = ImageFileReader()
        super().__init__(reader)
    
    def read_image_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read image file and extract content.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with image content or error information
        """
        return self.read_file(file_info)
