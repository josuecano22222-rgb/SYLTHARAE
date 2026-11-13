"""
Reader Repository Module
Aligned with database design principles

This module provides repository classes for file reader operations,
following the same design pattern as the database layer.
"""

from .base_repos import BaseReaderRepository
from .archive_repos import ArchiveReaderRepository
from .audio_repos import AudioReaderRepository
from .database_repos import DatabaseReaderRepository
from .ebook_repos import EbookReaderRepository
from .email_repos import EmailReaderRepository
from .image_repos import ImageReaderRepository
from .office_repos import OfficeReaderRepository
from .pdf_repos import PDFReaderRepository
from .remaining_repos import RemainingReaderRepository
from .video_repos import VideoReaderRepository

__all__ = [
    'BaseReaderRepository',
    'ArchiveReaderRepository',
    'AudioReaderRepository',
    'DatabaseReaderRepository',
    'EbookReaderRepository',
    'EmailReaderRepository',
    'ImageReaderRepository',
    'OfficeReaderRepository',
    'PDFReaderRepository',
    'RemainingReaderRepository',
    'VideoReaderRepository',
]
