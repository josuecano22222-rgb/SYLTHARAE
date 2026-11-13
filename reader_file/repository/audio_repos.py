"""
Audio Reader Repository
Aligned with database design principles (similar to ContentsRepository)

This repository manages audio file reader operations.
"""

from .base_repos import BaseReaderRepository
from ..readers.read_audio import AudioFileReader
from typing import Dict, Any, Optional


class AudioReaderRepository(BaseReaderRepository):
    """
    Repository for audio file reader operations.
    
    Follows database design principles:
    - Inherits from BaseReaderRepository
    - Manages AudioFileReader instance
    - Provides repository interface for audio operations
    """
    
    def __init__(self):
        """Initialize repository with AudioFileReader instance"""
        reader = AudioFileReader()
        super().__init__(reader)
    
    def read_audio_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read audio file and extract metadata.
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with audio metadata or error information
        """
        return self.read_file(file_info)
