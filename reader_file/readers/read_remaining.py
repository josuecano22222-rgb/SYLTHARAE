"""
Remaining file reader - Extract content from text-based files
Aligned with database design principles - all functions within class
Supports: JSON, XML, TXT, YAML, HTML, BIN, ICS and many other text-based formats
"""

import os
from pathlib import Path
import logging
from typing import Dict, Any, Optional, Set

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error, is_retryable_error,
    ErrorCategory, ErrorSeverity, format_validation_error
)

logger = logging.getLogger(__name__)


#: JSON text files are not always exactly one document.  JSON Lines / NDJSON
#: (one document per line), concatenated pretty-printed documents and log
#: exports are common shapes in real corpora; ``json.load`` rejects all of them
#: with "Extra data", which used to fail the whole file and store no content.
#: Such a file is read as a *document sequence* instead.
#:
#: Retention stays bounded: the document count is exact (the file is streamed
#: once) while only the first documents are kept in memory, the same
#: bounded-memory contract the rest of the pipeline follows.
JSON_DOCUMENTS_MAX_RETAINED = 10000
JSON_DOCUMENTS_MAX_BYTES = 8 * 1024 * 1024

#: Streaming chunk size for the document-sequence reader (1 MiB).
JSON_DOCUMENTS_CHUNK_BYTES = 1024 * 1024

#: Largest single pending document buffer accepted before giving up.  A file
#: that fails strict parsing and still has a megabyte-scale unterminated
#: document is not a document sequence; refusing keeps memory bounded instead
#: of buffering an arbitrarily large blob.
JSON_DOCUMENTS_MAX_BUFFER_BYTES = 64 * 1024 * 1024


class RemainingFileReader(BaseReader):
    """
    Reader for remaining file types (text-based files).
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported remaining file extensions.

        READER-02 (audit fix): this list previously advertised ~150
        extensions while ``read_file`` implemented only nine of them - every
        other advertised format failed deterministically with "Unsupported
        file type". The list now contains exactly what ``read_file``
        genuinely handles: text-based documents, structured data, code and
        configuration formats. Binary executable/library formats were
        removed: they cannot yield meaningful extractable content.
        """
        return {
            # structured data / markup / documents
            '.json', '.xml', '.txt', '.yaml', '.yml', '.html', '.htm',
            '.rtf', '.md', '.csv', '.tsv', '.log', '.ini', '.cfg', '.conf',
            '.properties', '.toml', '.env', '.srt', '.vtt',
            # calendar
            '.ics',
            # source code / scripts / shell
            '.sql', '.sh', '.bash', '.bat', '.cmd', '.ps1',
            '.js', '.jsx', '.ts', '.tsx', '.css', '.scss', '.less', '.php',
            '.py', '.java', '.c', '.h', '.cpp', '.hpp', '.cs', '.rb', '.go',
            '.rs', '.swift', '.kt', '.scala', '.r', '.m', '.pl', '.lua',
            '.vb', '.hs', '.dart', '.jl', '.nim', '.vue', '.svelte',
            # build / config metadata
            '.gitignore', '.gitattributes', '.editorconfig', '.makefile',
            '.cmake', '.gradle', '.lock', '.graphql', '.gql', '.proto',
            '.dockerfile', '.hocon', '.config', '.settings', '.prefs',
        }

    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read remaining file types with improved error handling

        Args:
            file_info: Dictionary containing file information with 'path' key

        Returns:
            Dictionary with file content or None on error
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))

        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)

        try:
            if ext == '.json':
                return self.read_json_file(file_path)
            elif ext == '.xml':
                return self.read_xml_file(file_path)
            elif ext == '.rtf':
                return self.read_rtf_file(file_path)
            elif ext in ('.yaml', '.yml'):
                return self.read_yaml_file(file_path)
            elif ext in ('.html', '.htm'):
                return self.read_html_file(file_path)
            elif ext == '.ics':
                return self.read_ics_file(file_path)
            elif ext == '.bin':
                return self.read_binary_file(file_path)
            elif ext in (self.get_supported_extensions() | {'.bin'}):
                # READER-02: every other advertised text format is genuinely
                # supported through the encoding-aware text reader.
                return self.read_text_file(file_path)
            else:
                error_msg = f"Unsupported file type: {ext or Path(file_path).suffix}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")

    def _read_json_documents(self, filepath: str, encoding: str) -> Optional[Dict[str, Any]]:
        """Read a file that is a *sequence* of JSON documents.

        Handles JSON Lines / NDJSON and concatenated documents.  The file is
        streamed once: ``document_count`` is exact, while memory is bounded by
        the retained window (``JSON_DOCUMENTS_MAX_RETAINED`` documents /
        ``JSON_DOCUMENTS_MAX_BYTES`` of source text).

        Returns ``None`` when the file is not a clean document sequence - for
        example a valid first document followed by arbitrary text, or a single
        unterminated document running past ``JSON_DOCUMENTS_MAX_BUFFER_BYTES``.
        Refusing (rather than keeping a partial read) is deliberate: the
        original parse error is then reported and nothing is fabricated.
        """
        import json as _json

        decoder = _json.JSONDecoder()
        documents: list = []
        retained_bytes = 0
        document_count = 0
        truncated = False
        buffer = ""
        incomplete = False  # a document ran to the end of the buffer

        with open(filepath, 'r', encoding=encoding) as file:
            while True:
                chunk = file.read(JSON_DOCUMENTS_CHUNK_BYTES)
                if not chunk:
                    break
                buffer += chunk

                cursor = 0
                while True:
                    start = cursor
                    while start < len(buffer) and buffer[start] in ' \t\r\n':
                        start += 1
                    if start >= len(buffer):
                        cursor = start
                        incomplete = False
                        break
                    try:
                        value, end = decoder.raw_decode(buffer, start)
                    except ValueError:
                        cursor = start
                        incomplete = True
                        break
                    document_count += 1
                    if (len(documents) < JSON_DOCUMENTS_MAX_RETAINED
                            and retained_bytes < JSON_DOCUMENTS_MAX_BYTES):
                        documents.append(value)
                        retained_bytes += len(buffer[start:end])
                    else:
                        truncated = True
                    cursor = end
                    incomplete = False

                if cursor:
                    buffer = buffer[cursor:]
                if len(buffer) > JSON_DOCUMENTS_MAX_BUFFER_BYTES:
                    # One document larger than the buffer cap: not a document
                    # sequence, and buffering it further is unbounded.
                    return None
                if incomplete and len(buffer) > JSON_DOCUMENTS_MAX_BUFFER_BYTES:
                    return None

        # The file is a document sequence only when the tail after the last
        # decoded document is whitespace.  Anything else is not a clean
        # sequence and keeps the original strict-parse error.
        tail = buffer.strip()
        if tail:
            return None
        if document_count < 2:
            return None
        return {
            "documents": documents,
            "document_count": document_count,
            "truncated": truncated,
        }

    @staticmethod
    def _decode_rtf_bytes(raw: bytes) -> str:
        for enc in ("utf-8", "cp932", "cp1252", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def read_rtf_file(self, filepath: str) -> Dict[str, Any]:
        """Read an RTF file; strips control words when striprtf is available,
        falls back to plain text extraction otherwise (READER-02/READER-04)."""
        result: Dict[str, Any] = {"filepath": filepath}
        try:
            raw_bytes = Path(filepath).read_bytes()
            raw = self._decode_rtf_bytes(raw_bytes)
            try:
                from striprtf.striprtf import rtf_to_text

                try:
                    result["content"] = rtf_to_text(raw)
                    result["rtf_stripped"] = True
                except UnicodeDecodeError:
                    # striprtf may decode `\ ' hex escapes with a Japanese code
                    # page; fall back to the raw RTF bytes as lossy text.
                    result["content"] = raw
                    result["rtf_stripped"] = False
            except ImportError:
                result["content"] = raw
                result["rtf_stripped"] = False
        except Exception as e:
            return self.handle_read_error(e, filepath, "read_rtf_file")
        return result
    
    def read_json_file(self, filepath: str, encoding: str = 'utf-8') -> Dict[str, Any]:
        """
        Read JSON file with improved error handling
        
        Args:
            filepath: Path to JSON file
            encoding: File encoding (default: 'utf-8')
        
        Returns:
            Dictionary with file data or error information
        """
        result: Dict[str, Any] = {"filepath": filepath}
        
        # Validate inputs
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_json_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_json_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            import json
        except ImportError:
            error_msg = "json module not available"
            handle_error(
                ImportError(error_msg),
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.HIGH,
                context={'operation': 'read_json_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            # Try multiple encodings if default fails
            encodings_to_try = [encoding, 'utf-8', 'latin-1', 'cp1252']
            data = None
            encoding_used = None
            documents = None
            parse_errors = []

            for enc in encodings_to_try:
                try:
                    with open(filepath, 'r', encoding=enc) as file:
                        data = json.load(file)
                    encoding_used = enc
                    break
                except UnicodeDecodeError:
                    if enc == encodings_to_try[-1]:
                        raise
                    continue
                except json.JSONDecodeError as decode_error:
                    # Not a single document.  It may still be a sequence of
                    # documents (JSON Lines / concatenated documents); that
                    # shape is real evidence and must not be dropped.
                    parse_errors.append((enc, decode_error))
                    continue

            if data is None and parse_errors:
                for enc, decode_error in parse_errors:
                    try:
                        documents = self._read_json_documents(filepath, enc)
                    except UnicodeDecodeError:
                        continue
                    except OSError:
                        raise
                    if documents is not None:
                        encoding_used = enc
                        break
                if documents is None:
                    raise parse_errors[-1][1]
            
            if data is None and documents is None:
                error_msg = "Could not decode file with any encoding"
                handle_error(
                    UnicodeDecodeError('utf-8', b'', 0, 1, error_msg),
                    category=ErrorCategory.FILE_PROCESSING,
                    severity=ErrorSeverity.MEDIUM,
                    context={'operation': 'read_json_file', 'filepath': filepath}
                )
                result["error"] = error_msg
                return result
            
            if data is None and documents is not None:
                # A document sequence: report the population exactly (counted
                # while streaming) and the retained window explicitly, so a
                # consumer can tell the difference between "this is all of it"
                # and "this is a bounded sample of a larger file".
                result["data"] = documents["documents"]
                result["data_type"] = "list"
                result["data_format"] = "json_documents"
                result["item_count"] = documents["document_count"]
                result["document_count"] = documents["document_count"]
                result["retained_count"] = len(documents["documents"])
                if documents["truncated"]:
                    result["truncated"] = True
                if encoding_used and encoding_used != encoding:
                    result["encoding_used"] = encoding_used
                return result

            result["data"] = data
            result["data_type"] = type(data).__name__
            if encoding_used and encoding_used != encoding:
                result["encoding_used"] = encoding_used
            
            # Add some basic statistics
            if isinstance(data, dict):
                result["key_count"] = len(data)
                result["keys"] = list(data.keys())[:100]  # Limit to first 100 keys
            elif isinstance(data, list):
                result["item_count"] = len(data)
            
            return result
        except Exception as e:
            error_msg = f"Error reading JSON file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_json_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def read_xml_file(self, filepath, encoding='utf-8'):
        """Read XML file and convert to dictionary"""
        try:
            import xml.etree.ElementTree as ET
            
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            def element_to_dict(element):
                """Convert XML element to dictionary"""
                elem_dict = {
                    "tag": element.tag,
                    "attributes": element.attrib,
                    "text": element.text.strip() if element.text and element.text.strip() else None,
                    "children": []
                }
                
                for child in element:
                    elem_dict["children"].append(element_to_dict(child))
                
                return elem_dict
            
            result = {
                "filepath": filepath,
                "root_tag": root.tag,
                "root_attributes": root.attrib,
                "tree": element_to_dict(root)
            }
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}
    
    def _read_text_file_streaming(self, filepath: str, encoding: str = 'utf-8', chunk_size: int = 10 * 1024 * 1024) -> Dict[str, Any]:
        """
        Internal method for streaming text file reading with custom chunk size.
        Used for memory error recovery with smaller chunks.
        
        Args:
            filepath: Path to text file
            encoding: Preferred encoding
            chunk_size: Chunk size in bytes (default: 10MB)
        
        Returns:
            Dictionary with file content or error information
        """
        result: Dict[str, Any] = {"filepath": filepath}
        
        try:
            file_size = os.path.getsize(filepath)
            content_parts = []
            lines = []
            line_count = 0
            character_count = 0
            word_count = 0
            non_empty_lines = 0
            
            encodings = [encoding, 'utf-8', 'latin-1', 'cp1252']
            encoding_used = None
            
            for enc in encodings:
                try:
                    with open(filepath, 'r', encoding=enc, errors='replace') as file:
                        buffer = ''
                        while True:
                            chunk = file.read(chunk_size)
                            if not chunk:
                                break
                            
                            buffer += chunk
                            while '\n' in buffer:
                                line, buffer = buffer.split('\n', 1)
                                lines.append(line)
                                line_count += 1
                                if line.strip():
                                    non_empty_lines += 1
                                character_count += len(line)
                                word_count += len(line.split())
                            
                            content_parts.append(chunk)
                        
                        if buffer:
                            lines.append(buffer)
                            line_count += 1
                            if buffer.strip():
                                non_empty_lines += 1
                            character_count += len(buffer)
                            word_count += len(buffer.split())
                        
                        encoding_used = enc
                        break
                except UnicodeDecodeError:
                    if enc == encodings[-1]:
                        raise
                    continue
            
            # Combine content (limit for very large files)
            if file_size < 500 * 1024 * 1024:  # < 500MB
                content = ''.join(content_parts)
            else:
                content = ''.join(content_parts[:5])  # First 5 chunks
                result["streaming_mode"] = True
                result["file_size_mb"] = file_size / (1024 * 1024)
            
            result.update({
                "content": content if content else "",
                "lines": lines[:10000] if len(lines) > 10000 else lines,
                "line_count": line_count,
                "character_count": character_count,
                "word_count": word_count,
                "non_empty_lines": non_empty_lines,
                "encoding_used": encoding_used,
                "streaming_processed": True
            })
            
            return result
        except Exception as e:
            error_msg = f"Error in streaming read: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': '_read_text_file_streaming', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def read_text_file(self, filepath: str, encoding: str = 'utf-8') -> Dict[str, Any]:
        """
        Read text file with improved error handling and encoding detection
        
        Args:
            filepath: Path to text file
            encoding: Preferred encoding (default: 'utf-8')
        
        Returns:
            Dictionary with file content or error information
        """
        result: Dict[str, Any] = {"filepath": filepath}
        
        # Validate inputs
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_text_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_text_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            # Get file size for streaming decision
            file_size = os.path.getsize(filepath)
            # Use streaming for files larger than 100MB to conserve memory
            use_streaming = file_size > 100 * 1024 * 1024  # 100MB threshold
            
            # Try different encodings if default fails. A decode that
            # succeeds is not a decode that is right - cp1252 reads cp866
            # Cyrillic as printable nonsense and raises nothing - so a small
            # file is sniffed first and the sniffed encoding goes to the front.
            # Everything after it stays, so a wrong guess still ends somewhere.
            preferred: Optional[str] = None
            if not use_streaming and file_size <= 20 * 1024 * 1024:
                try:
                    from core.text_encoding import decode_bytes

                    with open(filepath, 'rb') as probe:
                        _, preferred = decode_bytes(probe.read(), declared=encoding)
                except Exception:
                    preferred = None
            # decode_bytes answers "empty" for a zero-byte file, which is a
            # fact about the file and not a codec name; only names Python can
            # open a stream with are allowed into the chain.
            import codecs

            def _usable(name: Optional[str]) -> bool:
                if not name:
                    return False
                try:
                    codecs.lookup(name)
                except LookupError:
                    return False
                return True

            encodings = []
            for candidate in [preferred, encoding, 'utf-8', 'latin-1', 'cp1252']:
                if _usable(candidate) and candidate not in encodings:
                    encodings.append(candidate)
            content = None
            encoding_used = None
            chunk_size = 10 * 1024 * 1024  # 10MB chunks for streaming
            
            for enc in encodings:
                try:
                    if use_streaming:
                        # Stream processing for large files - process in chunks to conserve memory
                        content_parts = []
                        lines = []
                        line_count = 0
                        character_count = 0
                        word_count = 0
                        non_empty_lines = 0
                        
                        with open(filepath, 'r', encoding=enc, errors='replace') as file:
                            # Read file in chunks for continuous processing
                            buffer = ''
                            while True:
                                chunk = file.read(chunk_size)
                                if not chunk:
                                    break
                                
                                # Process chunk
                                buffer += chunk
                                # Split by newlines, keeping incomplete line in buffer
                                while '\n' in buffer:
                                    line, buffer = buffer.split('\n', 1)
                                    lines.append(line)
                                    line_count += 1
                                    if line.strip():
                                        non_empty_lines += 1
                                    character_count += len(line)
                                    word_count += len(line.split())
                                
                                # Add chunk to content (for continuous processing)
                                content_parts.append(chunk)
                                
                                # Yield control periodically to allow other operations
                                # This enables continuous processing without blocking
                            
                            # Process remaining buffer
                            if buffer:
                                lines.append(buffer)
                                line_count += 1
                                if buffer.strip():
                                    non_empty_lines += 1
                                character_count += len(buffer)
                                word_count += len(buffer.split())
                            
                            # Combine content parts (for files that fit in memory after streaming)
                            # For very large files, we may not store full content
                            if file_size < 500 * 1024 * 1024:  # < 500MB, store full content
                                content = ''.join(content_parts)
                            else:
                                # For extremely large files, store sample and metadata only
                                content = ''.join(content_parts[:5])  # First 5 chunks
                                result["streaming_mode"] = True
                                result["file_size_mb"] = file_size / (1024 * 1024)
                        
                        encoding_used = enc
                        
                        result.update({
                            "content": content if content else "",
                            "lines": lines[:10000] if len(lines) > 10000 else lines,  # Limit lines for very large files
                            "line_count": line_count,
                            "character_count": character_count,
                            "word_count": word_count,
                            "non_empty_lines": non_empty_lines,
                            "encoding_used": encoding_used,
                            "streaming_processed": True
                        })
                    else:
                        # Standard reading for smaller files
                        with open(filepath, 'r', encoding=enc) as file:
                            content = file.read()
                        encoding_used = enc
                        
                        lines = content.splitlines()
                        
                        result.update({
                            "content": content,
                            "lines": lines,
                            "line_count": len(lines),
                            "character_count": len(content),
                            "word_count": len(content.split()),
                            "non_empty_lines": len([l for l in lines if l.strip()]),
                            "encoding_used": encoding_used
                        })
                    
                    break
                except UnicodeDecodeError:
                    if enc == encodings[-1]:
                        raise
                    continue
                except MemoryError:
                    # If memory error occurs, fall back to streaming
                    if not use_streaming:
                        use_streaming = True
                        continue
                    raise
            
            if content is None and not result.get("streaming_processed"):
                error_msg = "Could not decode file with any encoding"
                handle_error(
                    UnicodeDecodeError('utf-8', b'', 0, 1, error_msg),
                    category=ErrorCategory.FILE_PROCESSING,
                    severity=ErrorSeverity.MEDIUM,
                    context={'operation': 'read_text_file', 'filepath': filepath}
                )
                result["error"] = error_msg
                return result
            
            return result
        except MemoryError as e:
            # PRODUCTION: Enhanced memory error handling for terabyte-scale files
            # Retry with streaming if memory error occurs, with progressively smaller chunk sizes
            try:
                # Get file size to determine appropriate chunk size
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                
                # For very large files, use smaller chunk sizes
                if file_size > 10 * 1024 * 1024 * 1024:  # > 10GB
                    # Use 1MB chunks for very large files
                    logger.warning(f"Memory error on large file ({file_size / (1024*1024*1024):.2f}GB), retrying with 1MB chunks: {e}")
                    # Force streaming mode with smaller chunks by modifying the method
                    # We'll use a recursive call but with a flag to use smaller chunks
                    return self._read_text_file_streaming(filepath, encoding, chunk_size=1024 * 1024)  # 1MB chunks
                else:
                    # Standard retry with streaming
                    logger.warning("Memory error on %s, retrying with streaming: %s", filepath, e)
                    return self.read_text_file(filepath, encoding)  # Will use streaming on retry
            except MemoryError as retry_err:
                # Even streaming failed - file is too large
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                error_msg = f"File too large to process even with streaming: {filepath} ({file_size / (1024*1024*1024):.2f}GB)"
                handle_error(
                    retry_err,
                    category=ErrorCategory.FILE_PROCESSING,
                    severity=ErrorSeverity.HIGH,
                    context={
                        'operation': 'read_text_file',
                        'filepath': filepath,
                        'file_size_gb': file_size / (1024*1024*1024) if file_size > 0 else 0,
                        'suggested_action': 'Consider processing file in smaller chunks or increasing available memory'
                    }
                )
                result["error"] = error_msg
                result["file_too_large"] = True
                result["file_size_gb"] = file_size / (1024*1024*1024) if file_size > 0 else 0
                return result
            except Exception as retry_err:
                # Other error during retry
                error_msg = f"Error during streaming retry: {str(retry_err)}"
                handle_error(
                    retry_err,
                    category=ErrorCategory.FILE_PROCESSING,
                    severity=ErrorSeverity.HIGH,
                    context={'operation': 'read_text_file_retry', 'filepath': filepath}
                )
                result["error"] = error_msg
                return result
        except Exception as e:
            error_msg = f"Error reading text file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_text_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def read_yaml_file(self, filepath, encoding='utf-8'):
        """Read YAML file"""
        try:
            import yaml
        except ImportError:
            return {
                "error": "PyYAML not installed. Install with: pip install pyyaml",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            with open(filepath, 'r', encoding=encoding) as file:
                data = yaml.safe_load(file)
            
            result = {
                "filepath": filepath,
                "data": data,
                "data_type": type(data).__name__
            }
            
            if isinstance(data, dict):
                result["key_count"] = len(data)
                result["keys"] = list(data.keys())
            elif isinstance(data, list):
                result["item_count"] = len(data)
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}
    
    #: Cap on inline <script>/<style> text carried out of one HTML document.
    #: A minified bundle is megabytes of code; the cap keeps one page from
    #: dominating the word index, and any truncation is recorded.
    HTML_INLINE_CODE_LIMIT = 1_000_000

    @classmethod
    def _html_inline_code(cls, soup):
        """Return ``(script_text, style_text, truncated)`` for a parsed page.

        ``BeautifulSoup.get_text()`` silently omits ``<script>``, ``<style>``
        and comments. A standalone ``.js`` or ``.css`` file *is* an indexed
        text format in this pipeline, so ignoring the same bytes inside HTML
        treated identical content differently depending on its container - and
        for a script-heavy page (a 77 KB single-page app whose visible text is
        a few hundred characters) it dropped almost the whole document without
        recording that it had. The code text is collected here, bounded, and
        reported; the storage layer indexes it under its own label.
        """
        parts = {}
        truncated = []
        for label, selector in (("script", "script"), ("style", "style")):
            chunks = [
                tag.get_text() or "" for tag in soup.find_all(selector)
            ]
            text = "\n".join(chunk for chunk in chunks if chunk.strip())
            if len(text) > cls.HTML_INLINE_CODE_LIMIT:
                text = text[:cls.HTML_INLINE_CODE_LIMIT]
                truncated.append(label)
            parts[label] = text
        return parts["script"], parts["style"], truncated

    #: Elements that begin or end a run of text in a browser. A newline is
    #: inserted at each boundary before the text is taken, because
    #: ``get_text()`` concatenates everything: two adjacent cells became the
    #: single token "Тверская10" and the words inside it were unsearchable.
    HTML_BLOCK_TAGS = (
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
        "ul", "option", "optgroup", "select", "textarea", "button", "label",
        "caption", "legend", "summary", "details",
    )

    @classmethod
    def _html_visible_text(cls, soup) -> str:
        """The page's visible text with block boundaries preserved."""
        from copy import copy

        working = copy(soup)
        for tag in working.find_all(cls.HTML_BLOCK_TAGS):
            tag.insert_after("\n")
        text = working.get_text()
        # Collapse the runs of blank lines the inserted boundaries create,
        # keeping a single line break where a boundary was.
        lines = [" ".join(line.split()) for line in text.splitlines()]
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _decode_html_file_bytes(raw: bytes) -> str:
        """Decode HTML bytes using declared charset meta tags, then heuristics."""
        import re

        head = raw[:8192]
        declared = None
        for pattern in (
            rb"charset\s*=\s*['\"]?([a-zA-Z0-9_\-]+)",
            rb"encoding\s*=\s*['\"]?([a-zA-Z0-9_\-]+)",
        ):
            match = re.search(pattern, head, re.I)
            if match:
                declared = match.group(1).decode("ascii", errors="ignore").strip()
                break
        # The declaration is a claim, not evidence: the corpus has a page that
        # declares UTF-8 and is windows-1256 Arabic, and the old chain then
        # fell through to cp1252 and stored the page as mojibake - lossless in
        # bytes, useless as text. core.text_encoding decides between the
        # claim, a detector and a candidate chain, and reports which it used.
        from core.text_encoding import decode_bytes

        text, encoding_used = decode_bytes(raw, declared=declared)
        RemainingFileReader.last_html_encoding = encoding_used
        return text

    def read_html_file(self, filepath, encoding='utf-8'):
        """Read an HTML file: visible text, structure, and inline code.

        ``text_content`` stays the document's visible text (its semantics are
        unchanged downstream). ``script_text``/``style_text`` carry the inline
        code that BeautifulSoup's ``get_text()`` drops, and it is also placed in
        ``forensic_text`` - the channel FORENSIC-01 built for evidence outside
        the visible body - so the same bytes are indexed inside HTML as they
        are when they arrive as a standalone ``.js``/``.css`` file.
        ``extraction_info`` records the size of everything that was, or was
        not, turned into content, so "N characters from a much larger file" is
        answerable from the result instead of being a mystery in the log.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return {
                "error": "beautifulsoup4 not installed. Install with: pip install beautifulsoup4",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            raw = Path(filepath).read_bytes()
            try:
                content = raw.decode(encoding)
            except UnicodeDecodeError:
                content = self._decode_html_file_bytes(raw)
            
            soup = BeautifulSoup(content, 'html.parser')
            script_text, style_text, truncated = self._html_inline_code(soup)

            # Comments are excluded from the content (they are not document
            # text) but their size is recorded so nothing disappears silently.
            comment_chars = sum(
                len(str(comment)) for comment in soup.find_all(
                    string=lambda node: type(node).__name__ == 'Comment'
                )
            )
            
            # FORENSIC-01: inline code is content of this artifact. Labelled so
            # a word found in a script is attributable when read back.
            forensic_parts = []
            if script_text.strip():
                forensic_parts.append("--- Inline script ---\n" + script_text)
            if style_text.strip():
                forensic_parts.append("--- Inline style ---\n" + style_text)

            visible_text = self._html_visible_text(soup)

            result = {
                "filepath": filepath,
                "title": soup.title.string if soup.title else None,
                "text_content": visible_text,
                "script_text": script_text,
                "style_text": style_text,
                "links": [{"href": a.get('href'), "text": a.get_text()} for a in soup.find_all('a', href=True)],
                "images": [{"src": img.get('src'), "alt": img.get('alt')} for img in soup.find_all('img')],
                "headings": {
                    "h1": [h.get_text() for h in soup.find_all('h1')],
                    "h2": [h.get_text() for h in soup.find_all('h2')],
                    "h3": [h.get_text() for h in soup.find_all('h3')]
                },
                "link_count": len(soup.find_all('a', href=True)),
                "image_count": len(soup.find_all('img')),
                "forensic_text": "\n\n".join(forensic_parts),
                "extraction_info": {
                    "visible_text_chars": len(visible_text),
                    "script_chars": len(script_text),
                    "style_chars": len(style_text),
                    "comment_chars_excluded": comment_chars,
                    "inline_code_truncated": truncated,
                }
            }

            # The breakdown is logged because a page whose visible text is a
            # few hundred characters out of a large file looks like a failed
            # extraction until the parts are named (it was reported that way).
            logger.info(
                "[EXTRACTION] HTML: %d visible chars, %d script chars, %d style "
                "chars, %d comment chars excluded (%s)",
                result["extraction_info"]["visible_text_chars"],
                result["extraction_info"]["script_chars"],
                result["extraction_info"]["style_chars"],
                result["extraction_info"]["comment_chars_excluded"],
                os.path.basename(str(filepath)),
            )
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}

    def read_binary_file(self, filepath, max_bytes=1024):
        """Read binary file"""
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            file_size = os.path.getsize(filepath)
            
            with open(filepath, 'rb') as file:
                data = file.read(max_bytes)
            
            result = {
                "filepath": filepath,
                "file_size": file_size,
                "bytes_read": len(data),
                "hex_preview": data.hex()[:500],  # First 500 hex characters
                "first_bytes": list(data[:50])  # First 50 bytes as integers
            }
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}
    
    def read_ics_file(self, filepath, encoding='utf-8'):
        """
        Extract readable content from ICS (iCalendar) files.
        Parses calendar events, todos, journals, and freebusy information.
        """
        try:
            from icalendar import Calendar
        except ImportError:
            return {
                "error": "icalendar not installed. Install with: pip install icalendar",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            # Try different encodings if utf-8 fails
            encodings = [encoding, 'utf-8', 'latin-1', 'cp1252']
            content = None
            encoding_used = None
            
            for enc in encodings:
                try:
                    with open(filepath, 'r', encoding=enc) as file:
                        content = file.read()
                    encoding_used = enc
                    break
                except UnicodeDecodeError:
                    if enc == encodings[-1]:
                        raise
                    continue
            
            if content is None:
                return {"error": "Could not decode file with any encoding", "filepath": filepath}
            
            # Parse the calendar
            cal = Calendar.from_ical(content)
            
            events = []
            todos = []
            journals = []
            freebusy = []
            
            # Extract all components
            for component in cal.walk():
                component_type = component.name
                
                if component_type == 'VEVENT':
                    event_data = {
                        "summary": str(component.get('summary', '')),
                        "description": str(component.get('description', '')),
                        "location": str(component.get('location', '')),
                        "dtstart": str(component.get('dtstart', '')),
                        "dtend": str(component.get('dtend', '')),
                        "dtstamp": str(component.get('dtstamp', '')),
                        "uid": str(component.get('uid', '')),
                        "organizer": str(component.get('organizer', '')),
                        "attendee": [str(att) for att in component.get('attendee', [])],
                        "status": str(component.get('status', '')),
                        "url": str(component.get('url', '')),
                        "rrule": str(component.get('rrule', '')),
                        "categories": [str(cat) for cat in component.get('categories', [])],
                        "priority": str(component.get('priority', '')),
                        "transp": str(component.get('transp', '')),
                        "created": str(component.get('created', '')),
                        "last_modified": str(component.get('last-modified', ''))
                    }
                    events.append(event_data)
                
                elif component_type == 'VTODO':
                    todo_data = {
                        "summary": str(component.get('summary', '')),
                        "description": str(component.get('description', '')),
                        "location": str(component.get('location', '')),
                        "dtstart": str(component.get('dtstart', '')),
                        "due": str(component.get('due', '')),
                        "completed": str(component.get('completed', '')),
                        "status": str(component.get('status', '')),
                        "priority": str(component.get('priority', '')),
                        "percent_complete": str(component.get('percent-complete', '')),
                        "uid": str(component.get('uid', '')),
                        "categories": [str(cat) for cat in component.get('categories', [])]
                    }
                    todos.append(todo_data)
                
                elif component_type == 'VJOURNAL':
                    journal_data = {
                        "summary": str(component.get('summary', '')),
                        "description": str(component.get('description', '')),
                        "dtstart": str(component.get('dtstart', '')),
                        "status": str(component.get('status', '')),
                        "uid": str(component.get('uid', ''))
                    }
                    journals.append(journal_data)
                
                elif component_type == 'VFREEBUSY':
                    freebusy_data = {
                        "dtstart": str(component.get('dtstart', '')),
                        "dtend": str(component.get('dtend', '')),
                        "organizer": str(component.get('organizer', '')),
                        "uid": str(component.get('uid', ''))
                    }
                    freebusy.append(freebusy_data)
            
            # Build readable text content
            readable_content_parts = []
            
            if events:
                readable_content_parts.append("=== CALENDAR EVENTS ===")
                for idx, event in enumerate(events, 1):
                    readable_content_parts.append(f"\nEvent {idx}:")
                    if event['summary']:
                        readable_content_parts.append(f"  Title: {event['summary']}")
                    if event['description']:
                        readable_content_parts.append(f"  Description: {event['description']}")
                    if event['location']:
                        readable_content_parts.append(f"  Location: {event['location']}")
                    if event['dtstart']:
                        readable_content_parts.append(f"  Start: {event['dtstart']}")
                    if event['dtend']:
                        readable_content_parts.append(f"  End: {event['dtend']}")
                    if event['organizer']:
                        readable_content_parts.append(f"  Organizer: {event['organizer']}")
                    if event['attendee']:
                        readable_content_parts.append(f"  Attendees: {', '.join(event['attendee'])}")
                    if event['status']:
                        readable_content_parts.append(f"  Status: {event['status']}")
            
            if todos:
                readable_content_parts.append("\n=== TODO ITEMS ===")
                for idx, todo in enumerate(todos, 1):
                    readable_content_parts.append(f"\nTodo {idx}:")
                    if todo['summary']:
                        readable_content_parts.append(f"  Title: {todo['summary']}")
                    if todo['description']:
                        readable_content_parts.append(f"  Description: {todo['description']}")
                    if todo['status']:
                        readable_content_parts.append(f"  Status: {todo['status']}")
                    if todo['due']:
                        readable_content_parts.append(f"  Due: {todo['due']}")
                    if todo['completed']:
                        readable_content_parts.append(f"  Completed: {todo['completed']}")
            
            if journals:
                readable_content_parts.append("\n=== JOURNAL ENTRIES ===")
                for idx, journal in enumerate(journals, 1):
                    readable_content_parts.append(f"\nJournal {idx}:")
                    if journal['summary']:
                        readable_content_parts.append(f"  Title: {journal['summary']}")
                    if journal['description']:
                        readable_content_parts.append(f"  Description: {journal['description']}")
            
            if freebusy:
                readable_content_parts.append("\n=== FREE/BUSY INFORMATION ===")
                for idx, fb in enumerate(freebusy, 1):
                    readable_content_parts.append(f"\nFree/Busy {idx}:")
                    if fb['organizer']:
                        readable_content_parts.append(f"  Organizer: {fb['organizer']}")
                    if fb['dtstart']:
                        readable_content_parts.append(f"  Start: {fb['dtstart']}")
                    if fb['dtend']:
                        readable_content_parts.append(f"  End: {fb['dtend']}")
            
            readable_content = "\n".join(readable_content_parts) if readable_content_parts else "No calendar data found."
            
            # Get calendar metadata
            cal_prod_id = str(cal.get('prodid', ''))
            cal_version = str(cal.get('version', ''))
            cal_calscale = str(cal.get('calscale', ''))
            cal_method = str(cal.get('method', ''))
            
            result = {
                "filepath": filepath,
                "encoding_used": encoding_used,
                "calendar_prodid": cal_prod_id,
                "calendar_version": cal_version,
                "calendar_calscale": cal_calscale,
                "calendar_method": cal_method,
                "readable_content": readable_content,
                "events": events,
                "todos": todos,
                "journals": journals,
                "freebusy": freebusy,
                "event_count": len(events),
                "todo_count": len(todos),
                "journal_count": len(journals),
                "freebusy_count": len(freebusy),
                "total_components": len(events) + len(todos) + len(journals) + len(freebusy)
            }
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


