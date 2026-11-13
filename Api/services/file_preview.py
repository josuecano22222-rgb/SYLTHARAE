"""
File Preview Service
Handles in-browser preview of images, PDFs, and documents
"""

import logging
from typing import Optional, Dict, Any
from pathlib import Path
import mimetypes
import base64
from io import BytesIO
from Api.utils import get_connection, return_connection
from core.file_utils import load_spreadsheet_workbook

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pymupdf as fitz
    FITZ_AVAILABLE = True
except ImportError:
    fitz = None
    FITZ_AVAILABLE = False

try:
    # AUDIT-DEP-03: pypdf is the maintained successor of PyPDF2 (same API).
    try:
        import pypdf as PyPDF2
    except ImportError:
        import PyPDF2
    PDF_FALLBACK_AVAILABLE = True
except ImportError:
    PyPDF2 = None
    PDF_FALLBACK_AVAILABLE = False

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import openpyxl
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False



logger = logging.getLogger(__name__)


class FilePreviewService:
    """
    Service for generating file previews for in-browser viewing.
    
    Supports:
    - Image previews (JPEG, PNG, GIF, etc.)
    - PDF previews (first page as image)
    - Document previews (DOCX, XLSX text extraction)
    - Text file previews
    """
    
    # Maximum file size for preview (10MB)
    MAX_PREVIEW_SIZE = 10 * 1024 * 1024
    
    # Supported image formats
    IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}
    
    # Supported document formats
    DOCUMENT_FORMATS = {'.pdf', '.docx', '.xlsx', '.txt', '.md', '.csv'}
    
    @staticmethod
    def _with_file_info(payload: Dict[str, Any], file_name, file_type) -> Dict[str, Any]:
        """Attach display-safe identity without exposing the server path."""
        payload['file_name'] = file_name
        payload['file_type'] = file_type
        return payload

    def get_preview(
        file_id: int,
        max_width: int = 1200,
        max_height: int = 800
    ) -> Dict[str, Any]:
        """
        Get file preview data.
        
        Args:
            file_id: ID of the file in the database
            max_width: Maximum preview width in pixels
            max_height: Maximum preview height in pixels
        
        Returns:
            Dictionary with preview data:
            {
                'preview_type': 'image' | 'pdf' | 'document' | 'text' | 'unsupported',
                'mime_type': MIME type string,
                'data': Base64-encoded preview data or text content,
                'thumbnail': Optional base64-encoded thumbnail,
                'metadata': Additional metadata
            }
        """
        try:
            # Get file information from database
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT p.file_path, p.file_type, p.file_size, p.file_name
                FROM paths p
                WHERE p.id = %s
            """, (file_id,))
            
            result = cursor.fetchone()
            if not result:
                cursor.close()
                return_connection(conn)
                return {
                    'preview_type': 'error',
                    'error': 'File not found'
                }
            
            file_path, file_type, file_size, file_name = result
            cursor.close()
            return_connection(conn)
            
            # Check file size
            if file_size and file_size > FilePreviewService.MAX_PREVIEW_SIZE:
                return {
                    'preview_type': 'error',
                    'error': f'File too large for preview (max {FilePreviewService.MAX_PREVIEW_SIZE / 1024 / 1024}MB)'
                }
            
            # Determine preview type
            file_ext = Path(file_name).suffix.lower() if file_name else ''
            mime_type, _ = mimetypes.guess_type(file_name or '')
            
            # Handle archive files (extract path)
            if '::' in file_path:
                # Archive file - cannot preview directly
                return {
                    'preview_type': 'unsupported',
                    'error': 'Cannot preview files inside archives'
                }
            
            # Check if file exists
            if not Path(file_path).exists():
                return {
                    'preview_type': 'error',
                    'error': 'File not found on disk'
                }
            
            # Generate preview based on file type
            if file_ext in FilePreviewService.IMAGE_FORMATS:
                return FilePreviewService._with_file_info(
                    FilePreviewService._preview_image(file_path, max_width, max_height, mime_type),
                    file_name, file_type)
            elif file_ext == '.pdf':
                return FilePreviewService._with_file_info(
                    FilePreviewService._preview_pdf(file_path, max_width, max_height),
                    file_name, file_type)
            elif file_ext == '.docx':
                return FilePreviewService._with_file_info(
                    FilePreviewService._preview_docx(file_path),
                    file_name, file_type)
            elif file_ext == '.xlsx':
                return FilePreviewService._with_file_info(
                    FilePreviewService._preview_xlsx(file_path),
                    file_name, file_type)
            elif file_ext in {'.txt', '.md', '.csv'}:
                return FilePreviewService._with_file_info(
                    FilePreviewService._preview_text(file_path),
                    file_name, file_type)
            else:
                return {
                    'preview_type': 'unsupported',
                    'mime_type': mime_type or 'application/octet-stream',
                    'file_type': file_type,
                    'message': f'Preview not available for {file_ext or file_type} files'
                }
                
        except Exception as e:
            logger.error(f"Error generating preview for file {file_id}: {e}", exc_info=True)
            return {
                'preview_type': 'error',
                'error': 'Preview generation failed'
            }
    
    @staticmethod
    def _preview_image(
        file_path: str,
        max_width: int,
        max_height: int,
        mime_type: Optional[str]
    ) -> Dict[str, Any]:
        """Generate image preview."""
        if not PIL_AVAILABLE:
            return {
                'preview_type': 'error',
                'error': 'PIL/Pillow not available for image preview'
            }
        
        try:
            with Image.open(file_path) as img:
                # Convert RGBA to RGB if necessary
                if img.mode == 'RGBA':
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3])
                    img = background
                elif img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Calculate thumbnail size
                img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                
                # Convert to base64
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                buffer.seek(0)
                img_data = base64.b64encode(buffer.read()).decode('utf-8')
                
                return {
                    'preview_type': 'image',
                    'mime_type': mime_type or 'image/jpeg',
                    'data': f'data:image/jpeg;base64,{img_data}',
                    'width': img.width,
                    'height': img.height
                }
                
        except Exception as e:
            logger.error(f"Error previewing image {file_path}: {e}", exc_info=True)
            return {
                'preview_type': 'error',
                'error': 'Could not generate image preview'
            }
    
    @staticmethod
    def _preview_pdf(
        file_path: str,
        max_width: int,
        max_height: int
    ) -> Dict[str, Any]:
        """Render the physical first PDF page to a bounded PNG thumbnail."""
        if FITZ_AVAILABLE:
            try:
                with fitz.open(file_path) as document:
                    if not len(document):
                        return {'preview_type': 'error', 'error': 'PDF file is empty'}
                    page = document.load_page(0)
                    page_rect = page.rect
                    bounded_width = max(1, int(max_width))
                    bounded_height = max(1, int(max_height))
                    scale = min(
                        bounded_width / max(float(page_rect.width), 1.0),
                        bounded_height / max(float(page_rect.height), 1.0),
                        2.0,
                    )
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    image_data = base64.b64encode(pixmap.tobytes('png')).decode('ascii')
                    return {
                        'preview_type': 'image',
                        'preview_kind': 'pdf_first_page',
                        'mime_type': 'image/png',
                        'data': f'data:image/png;base64,{image_data}',
                        'width': pixmap.width,
                        'height': pixmap.height,
                        'page_count': len(document),
                        'metadata': {'total_pages': len(document), 'preview_page': 1},
                    }
            except Exception as render_error:
                logger.warning('PyMuPDF first-page preview failed for %s: %s',
                               file_path, render_error)

        # Keep readable first-page text as a useful fallback when PyMuPDF is
        # unavailable or a particular PDF cannot be rendered.
        if PDF_FALLBACK_AVAILABLE:
            try:
                with open(file_path, 'rb') as stream:
                    pdf_reader = PyPDF2.PdfReader(stream)
                    if not pdf_reader.pages:
                        return {'preview_type': 'error', 'error': 'PDF file is empty'}
                    text_content = pdf_reader.pages[0].extract_text() or ''
                    return {
                        'preview_type': 'pdf',
                        'mime_type': 'application/pdf',
                        'data': text_content[:5000],
                        'page_count': len(pdf_reader.pages),
                        'metadata': {
                            'total_pages': len(pdf_reader.pages),
                            'preview_page': 1,
                        },
                    }
            except Exception as fallback_error:
                logger.error('PDF preview failed for %s: %s', file_path,
                             fallback_error, exc_info=True)
                return {
                    'preview_type': 'error',
                    'error': 'Could not generate PDF preview',
                }

        if FITZ_AVAILABLE or PDF_FALLBACK_AVAILABLE:
            return {'preview_type': 'error', 'error': 'PDF file is empty'}
        return {
            'preview_type': 'error',
            'error': 'PDF preview requires PyMuPDF (fitz) or PyPDF2',
        }
    
    @staticmethod
    def _preview_docx(file_path: str) -> Dict[str, Any]:
        """Generate DOCX preview (text extraction)."""
        if not DOCX_AVAILABLE:
            return {
                'preview_type': 'error',
                'error': 'python-docx not available for DOCX preview'
            }
        
        try:
            doc = DocxDocument(file_path)
            paragraphs = [para.text for para in doc.paragraphs]
            text_content = '\n'.join(paragraphs)
            
            return {
                'preview_type': 'document',
                'mime_type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'data': text_content[:10000],  # Limit preview
                'metadata': {
                    'paragraph_count': len(paragraphs)
                }
            }
            
        except Exception as e:
            logger.error(f"Error previewing DOCX {file_path}: {e}", exc_info=True)
            return {
                'preview_type': 'error',
                'error': 'Could not generate DOCX preview'
            }
    
    @staticmethod
    def _preview_xlsx(file_path: str) -> Dict[str, Any]:
        """Generate XLSX preview (first sheet text extraction)."""
        if not XLSX_AVAILABLE:
            return {
                'preview_type': 'error',
                'error': 'openpyxl not available for XLSX preview'
            }

        wb = None
        try:
            # Read by content when the name is not a supported spreadsheet
            # suffix: openpyxl refuses a *path* by extension before it looks at
            # the bytes, so a spreadsheet stored under an inherited container
            # name (e.g. ``attachment_00520.docx``) previewed as an error even
            # though the ingestion pipeline had identified it as xlsx. The same
            # rule serves the ingestion reader - see
            # core.file_utils.OPENPYXL_PATH_SUFFIXES.
            wb = load_spreadsheet_workbook(file_path, read_only=True)
            if not wb.sheetnames:
                return {
                    'preview_type': 'error',
                    'error': 'XLSX file has no sheets'
                }

            ws = wb[wb.sheetnames[0]]

            # Extract text from first 100 rows
            rows_data = []
            for i, row in enumerate(ws.iter_rows(values_only=True), 1):
                if i > 100:
                    break
                rows_data.append([str(cell) if cell is not None else ''
                                  for cell in row])

            return {
                'preview_type': 'document',
                'mime_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'data': rows_data,
                'metadata': {
                    'sheet_name': wb.sheetnames[0],
                    'total_sheets': len(wb.sheetnames),
                    'rows_previewed': len(rows_data)
                }
            }

        except Exception as e:
            logger.error(f"Error previewing XLSX {file_path}: {e}", exc_info=True)
            return {
                'preview_type': 'error',
                'error': 'Could not generate spreadsheet preview'
            }
        finally:
            # A read-only workbook streams rows from the stream it was opened
            # from; it has to be closed here or the descriptor stays open for
            # the life of the process.
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    logger.debug("Could not close workbook for %s", file_path,
                                 exc_info=True)

    @staticmethod
    def _preview_text(file_path: str) -> Dict[str, Any]:
        """Generate text file preview."""
        try:
            # Try to read file with different encodings
            encodings = ['utf-8', 'latin-1', 'cp1252']
            content = None
            
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        content = f.read(10000)  # Limit to first 10KB
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                return {
                    'preview_type': 'error',
                    'error': 'Could not decode text file'
                }
            
            mime_type, _ = mimetypes.guess_type(file_path)
            
            return {
                'preview_type': 'text',
                'mime_type': mime_type or 'text/plain',
                'data': content,
                'metadata': {
                    'truncated': len(content) >= 10000
                }
            }
            
        except Exception as e:
            logger.error(f"Error previewing text file {file_path}: {e}", exc_info=True)
            return {
                'preview_type': 'error',
                'error': 'Could not read text preview'
            }

