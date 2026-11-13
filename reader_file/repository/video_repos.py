"""
Video Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages video file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_video import VideoFileReader
from typing import Dict, Any, Optional


class VideoReaderRepository(BaseReaderRepository):
    """
    Repository for video file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages VideoFileReader instance
    - Provides repository interface for video operations
    """
    
    def __init__(self):
        """Initialize repository with VideoFileReader instance"""
        reader = VideoFileReader()
        super().__init__(reader)
    
    def read_video_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read video file and extract metadata.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with video metadata or error information
        """
        return self.read_file(file_info)
