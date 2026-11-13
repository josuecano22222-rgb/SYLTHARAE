"""
Reader module - File reading functionality
Aligned with database design principles for consistent error handling and structure
All file readers are independent and can be used separately.
"""

from .main_specify_method import (
    main_specify_method_of_reading_the_file,
    specify_method_of_reading_the_file_list
)

# Import base reader class
from .readers.base_reader import BaseReader

# Import service classes
from .services.file_reader_service import FileReaderService, get_file_reader_service
from .services.file_router_service import FileRouterService, get_file_router_service

# Import repository classes
from .repository.base_repos import BaseReaderRepository
from .repository.archive_repos import ArchiveReaderRepository
from .repository.audio_repos import AudioReaderRepository
from .repository.database_repos import DatabaseReaderRepository
from .repository.ebook_repos import EbookReaderRepository
from .repository.email_repos import EmailReaderRepository
from .repository.image_repos import ImageReaderRepository
from .repository.office_repos import OfficeReaderRepository
from .repository.pdf_repos import PDFReaderRepository
from .repository.remaining_repos import RemainingReaderRepository
from .repository.video_repos import VideoReaderRepository

# Import converted readers
from .readers.read_remaining import RemainingFileReader
from .readers.read_archive import ArchiveFileReader
from .readers.read_office import OfficeFileReader
from .readers.read_pdf import PDFFileReader
from .readers.read_img_fast import ImageFileReader
from .readers.read_email import EmailFileReader
from .readers.read_audio import AudioFileReader
from .readers.read_video import VideoFileReader
from .readers.read_ebook import EbookFileReader
from .readers.read_database import DatabaseFileReader

__all__ = [
    # Main functions (backward compatibility)
    'main_specify_method_of_reading_the_file',
    'specify_method_of_reading_the_file_list',
    
    # Base class
    'BaseReader',
    
    # Service classes
    'FileReaderService',
    'get_file_reader_service',
    'FileRouterService',
    'get_file_router_service',
    
    # Repository classes
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
    
    # Reader classes
    'RemainingFileReader',
    'ArchiveFileReader',
    'OfficeFileReader',
    'PDFFileReader',
    'ImageFileReader',
    'EmailFileReader',
    'AudioFileReader',
    'VideoFileReader',
    'EbookFileReader',
    'DatabaseFileReader',
]

