"""
E-book reader - Extract text and metadata from e-books
Aligned with database design principles - all functions within class
Supports: EPUB, MOBI, AZW, AZW3, FB2
"""

import os
import logging
from typing import Dict, Any, Optional, Set
from pathlib import Path

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

logger = logging.getLogger(__name__)


class EbookFileReader(BaseReader):
    """
    Reader for e-book files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported e-book extensions"""
        return {
            '.epub', '.mobi', '.azw', '.azw3', '.fb2', '.lit', '.pdb'
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read e-book file and extract content with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with e-book content or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)
        
        try:
            if ext == '.epub':
                return self.read_epub_file(file_path)
            elif ext in ('.mobi', '.azw', '.azw3'):
                return self.read_mobi_file(file_path)
            elif ext == '.fb2':
                return self.read_fb2_file(file_path)
            else:
                error_msg = f"Unsupported e-book format: {ext or file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def read_epub_file(self, filepath: str) -> Dict[str, Any]:
        """Read EPUB file using ebooklib with improved error handling"""
        result: Dict[str, Any] = {
            "filepath": filepath,
            "format": "EPUB"
        }
        
        # Validate filepath
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_epub_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_epub_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            result["file_size"] = os.path.getsize(filepath)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_epub_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        try:
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
            
            book = epub.read_epub(filepath)
            
            # Metadata - handle both single values and lists
            def get_metadata(dc_type, field_name):
                try:
                    metadata = book.get_metadata('DC', field_name)
                    if metadata:
                        # metadata is usually a list of tuples
                        if isinstance(metadata, list) and metadata:
                            return [str(item[0]) for item in metadata]
                        return str(metadata)
                except:
                    pass
                return None
            
            result["title"] = get_metadata('DC', 'title')
            result["author"] = get_metadata('DC', 'creator')
            result["language"] = get_metadata('DC', 'language')
            result["publisher"] = get_metadata('DC', 'publisher')
            result["date"] = get_metadata('DC', 'date')
            result["identifier"] = get_metadata('DC', 'identifier')
            result["description"] = get_metadata('DC', 'description')
            result["subject"] = get_metadata('DC', 'subject')
            result["rights"] = get_metadata('DC', 'rights')
            
            # Extract text from chapters
            chapters = []
            total_words = 0
            
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    text = soup.get_text()
                    
                    if text.strip():
                        words = len(text.split())
                        total_words += words
                        
                        chapters.append({
                            "id": item.get_id(),
                            "text": text.strip(),
                            "length": len(text.strip()),
                            "word_count": words
                        })
            
            result["chapters"] = chapters
            result["chapter_count"] = len(chapters)
            result["total_text_length"] = sum(ch["length"] for ch in chapters)
            result["total_words"] = total_words
            result["avg_words_per_chapter"] = total_words // len(chapters) if chapters else 0
            
            # Estimate reading time (average 200 words per minute)
            result["estimated_reading_time_minutes"] = total_words // 200
            result["estimated_reading_time_formatted"] = self._format_reading_time(total_words)
            
            return result
            
        except ImportError as e:
            error_msg = "ebooklib not installed. Install with: pip install ebooklib beautifulsoup4"
            handle_error(
                e,
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_epub_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        except Exception as e:
            error_msg = f"Error reading EPUB file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_epub_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def read_mobi_file(self, filepath: str) -> Dict[str, Any]:
        """Read MOBI/AZW file with improved error handling"""
        result: Dict[str, Any] = {
            "filepath": filepath,
            "format": Path(filepath).suffix.upper().lstrip('.')
        }
        
        # Validate filepath
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_mobi_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_mobi_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            result["file_size"] = os.path.getsize(filepath)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_mobi_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        try:
            import mobi
            
            # Extract MOBI
            tempdir, filepath_extracted = mobi.extract(filepath)
            
            # Read extracted HTML
            if os.path.exists(filepath_extracted):
                from bs4 import BeautifulSoup
                
                with open(filepath_extracted, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                soup = BeautifulSoup(content, 'html.parser')
                text = soup.get_text()
                
                result["text"] = text.strip()
                result["text_length"] = len(text.strip())
                result["word_count"] = len(text.split())
                
                # Estimate reading time
                result["estimated_reading_time_formatted"] = self._format_reading_time(result["word_count"])
                
                # Try to extract metadata from HTML
                title_tag = soup.find('title')
                if title_tag:
                    result["title"] = title_tag.get_text()
                
                # Look for metadata tags
                for meta in soup.find_all('meta'):
                    name = meta.get('name', '').lower()
                    content = meta.get('content', '')
                    if name and content:
                        if 'author' in name:
                            result["author"] = content
                        elif 'publisher' in name:
                            result["publisher"] = content
            
            return result
            
        except ImportError as e:
            error_msg = "mobi not installed. Install with: pip install mobi"
            handle_error(
                e,
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_mobi_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        except Exception as e:
            error_msg = f"Error reading MOBI file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_mobi_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def read_fb2_file(self, filepath: str) -> Dict[str, Any]:
        """Read FB2 (FictionBook) file with improved error handling"""
        result: Dict[str, Any] = {
            "filepath": filepath,
            "format": "FB2"
        }
        
        # Validate filepath
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_fb2_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_fb2_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            result["file_size"] = os.path.getsize(filepath)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_fb2_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        try:
            import xml.etree.ElementTree as ET
            
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            # FB2 uses namespaces
            ns = {'fb': 'http://www.gribuser.ru/xml/fictionbook/2.0'}
            
            # Extract metadata
            desc = root.find('.//fb:title-info', ns)
            if desc is not None:
                title = desc.find('fb:book-title', ns)
                if title is not None:
                    result["title"] = title.text
                
                authors = desc.findall('.//fb:author', ns)
                if authors:
                    result["author"] = []
                    for author in authors:
                        first = author.find('fb:first-name', ns)
                        last = author.find('fb:last-name', ns)
                        name_parts = []
                        if first is not None and first.text:
                            name_parts.append(first.text)
                        if last is not None and last.text:
                            name_parts.append(last.text)
                        if name_parts:
                            result["author"].append(' '.join(name_parts))
                
                genre = desc.find('fb:genre', ns)
                if genre is not None:
                    result["genre"] = genre.text
                
                annotation = desc.find('fb:annotation', ns)
                if annotation is not None:
                    result["description"] = ''.join(annotation.itertext())
            
            # Extract text
            body = root.find('.//fb:body', ns)
            if body is not None:
                text = ''.join(body.itertext())
                result["text"] = text.strip()
                result["text_length"] = len(text.strip())
                result["word_count"] = len(text.split())
                result["estimated_reading_time_formatted"] = self._format_reading_time(result["word_count"])
            
            return result
            
        except Exception as e:
            error_msg = f"Error reading FB2 file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_fb2_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def _format_reading_time(self, word_count):
        """Format estimated reading time"""
        if not word_count or word_count <= 0:
            return "N/A"
        
        minutes = word_count // 200  # Average 200 words per minute
        
        if minutes < 60:
            return f"{minutes} minutes"
        
        hours = minutes // 60
        remaining_minutes = minutes % 60
        
        if remaining_minutes == 0:
            return f"{hours} hour{'s' if hours > 1 else ''}"
        
        return f"{hours} hour{'s' if hours > 1 else ''} {remaining_minutes} minutes"


