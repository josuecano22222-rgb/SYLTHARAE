"""
File Reader Service - Orchestrates all file readers
Aligned with database design principles (similar to ContentDBService)

This service manages all file readers and routes files to the appropriate reader
based on file extension, following the same design pattern as the database layer.
"""

import os
import logging
from typing import Dict, Any, Optional, Set, List

from core.detect_binanry_utils import (
    CONFIDENCE_STRONG,
    CONFIDENCE_WEAK,
    sniff_file_type,
)

from ..readers.base_reader import BaseReader
from ..readers.read_remaining import RemainingFileReader
from ..readers.read_archive import ArchiveFileReader
from ..readers.read_office import OfficeFileReader
from ..readers.read_pdf import PDFFileReader
from ..readers.read_img_fast import ImageFileReader
from ..readers.read_email import EmailFileReader
from ..readers.read_audio import AudioFileReader
from ..readers.read_video import VideoFileReader
from ..readers.read_ebook import EbookFileReader
from ..readers.read_database import DatabaseFileReader
from ..readers.read_diagram import DiagramFileReader

logger = logging.getLogger(__name__)


class FileReaderService:
    """
    Service class that orchestrates all file readers.
    
    Follows database design principles (similar to ContentDBService):
    - Manages all reader instances
    - Routes files to appropriate readers
    - Provides unified interface
    - Consistent error handling
    - Resource management
    """
    
    def __init__(self):
        """Initialize service and all reader instances"""
        self._init_readers()
        self._build_extension_map()
    
    def _init_readers(self):
        """Initialize all reader instances"""
        # Initialize all readers (similar to ContentDBService._init_repositories)
        self.remaining_reader = RemainingFileReader()
        self.archive_reader = ArchiveFileReader()
        self.office_reader = OfficeFileReader()
        self.pdf_reader = PDFFileReader()
        self.image_reader = ImageFileReader()
        self.email_reader = EmailFileReader()
        self.audio_reader = AudioFileReader()
        self.video_reader = VideoFileReader()
        self.ebook_reader = EbookFileReader()
        self.database_reader = DatabaseFileReader()
        self.diagram_reader = DiagramFileReader()
        
        # Store all readers in a list for iteration
        self._readers: List[BaseReader] = [
            self.remaining_reader,
            self.archive_reader,
            self.office_reader,
            self.pdf_reader,
            self.image_reader,
            self.email_reader,
            self.audio_reader,
            self.video_reader,
            self.ebook_reader,
            self.database_reader,
            self.diagram_reader,
        ]
    
    #: Extensions claimed by more than one registered reader, and the reader
    #: that must win. ROUTE-01: previously the winner was simply whichever
    #: reader happened to be registered first in ``_readers``, so adding or
    #: reordering a reader silently changed the processing path of a supported
    #: format. ``.csv`` lost this way - the structured OfficeFileReader was
    #: unreachable and every CSV was read as undifferentiated plain text.
    EXTENSION_PREFERENCES: Dict[str, str] = {
        # headers/rows/column_count; pipeline.storage_pipeline has a dedicated
        # CSV extraction path keyed on content['rows'] which was dead code
        # while the plain-text reader won.
        '.csv': 'OfficeFileReader',
        # RemainingFileReader uses striprtf, a declared dependency;
        # OfficeFileReader prefers RTFDE, which is not packaged.
        '.rtf': 'RemainingFileReader',
        # TypeScript source is far more common in a document corpus than
        # MPEG-TS video.
        '.ts': 'RemainingFileReader',
        # WebM is a video container.
        '.webm': 'VideoFileReader',
        # Tab-separated values are the same structured table as CSV, and the
        # OfficeFileReader path is the one that populates content['rows'] /
        # content['headers'] for the storage pipeline's table extraction.
        '.tsv': 'OfficeFileReader',
        '.m4a': 'AudioFileReader',
        '.avif': 'ImageFileReader',
    }

    def _build_extension_map(self):
        """Build a map of extensions to readers for fast lookup.

        Conflicts between readers are resolved from
        :data:`EXTENSION_PREFERENCES` rather than by registration order, and
        any undeclared conflict is logged so it cannot pass silently.
        """
        self._extension_map: Dict[str, BaseReader] = {}

        claims: Dict[str, List[BaseReader]] = {}
        for reader in self._readers:
            for ext in reader.get_supported_extensions():
                claims.setdefault(ext, []).append(reader)

        for ext, readers in claims.items():
            if len(readers) == 1:
                self._extension_map[ext] = readers[0]
                continue

            names = [reader.__class__.__name__ for reader in readers]
            preferred = self.EXTENSION_PREFERENCES.get(ext)
            chosen = next(
                (r for r in readers if r.__class__.__name__ == preferred), None
            )
            if chosen is None:
                chosen = readers[0]
                logger.warning(
                    "Extension %s is claimed by %s but has no declared "
                    "preference; defaulting to %s. Add it to "
                    "EXTENSION_PREFERENCES to make this explicit.",
                    ext, names, chosen.__class__.__name__,
                )
            else:
                logger.debug(
                    "Extension %s claimed by %s; resolved to %s",
                    ext, names, preferred,
                )
            self._extension_map[ext] = chosen
    
    def get_reader_for_extension(self, extension: str) -> Optional[BaseReader]:
        """
        Get the appropriate reader for a file extension.
        
        Args:
            extension: File extension (e.g., '.pdf', '.docx')
        
        Returns:
            BaseReader instance or None if no reader supports the extension
        """
        extension = extension.lower()
        if not extension.startswith('.'):
            extension = '.' + extension
        
        return self._extension_map.get(extension)

    # ------------------------------------------------------------------
    # DETECT-01: content-based type identification
    # ------------------------------------------------------------------
    #: Compound extensions whose full form carries more information than the
    #: single-component magic-byte sniff (a .tar.gz sniffs as plain '.gz').
    COMPOUND_EXTENSIONS = ('.tar.gz', '.tar.bz2', '.tar.xz',
                           '.tgz', '.tbz2', '.txz')

    #: Apple iWork packages are ZIP containers but must not be routed as plain
    #: archives (that path stores the wrapper with zero text and re-processes
    #: every member as unrelated siblings).
    IWORK_PACKAGE_EXTENSIONS = ('.pages', '.numbers')

    #: Declared media types that share a container signature with video but must
    #: stay on their dedicated reader (``.m4a`` → audio, ``.avif`` → image).
    DECLARED_MEDIA_EXTENSIONS = ('.m4a', '.avif', '.heic', '.heif')

    #: Extensions whose content *is* text. A file that decodes cleanly as text
    #: is text, whatever its first two bytes happen to spell: "BM" is enough for
    #: the signature table to call a prose file a bitmap, and testBMPfp.txt (a
    #: text fixture that begins "BM") was routed to the image reader and stored
    #: as zero characters. Only these extensions get the veto, so a real bitmap
    #: called .png still loses to its own bytes.
    TEXTUAL_EXTENSIONS = ('.txt', '.md', '.markdown', '.csv', '.tsv', '.log',
                          '.json', '.jsonl', '.xml', '.yml', '.yaml', '.py',
                          '.ini', '.cfg', '.rst', '.tex', '.sql')

    #: MIME type of an ODF package member -> the extension it stands for.
    ODF_MIMETYPES = {
        'application/vnd.oasis.opendocument.text': '.odt',
        'application/vnd.oasis.opendocument.text-template': '.ott',
        'application/vnd.oasis.opendocument.spreadsheet': '.ods',
        'application/vnd.oasis.opendocument.spreadsheet-template': '.ots',
        'application/vnd.oasis.opendocument.presentation': '.odp',
        'application/vnd.oasis.opendocument.presentation-template': '.otp',
        'application/vnd.oasis.opendocument.graphics': '.odg',
        'application/vnd.oasis.opendocument.graphics-template': '.otg',
    }

    @staticmethod
    def _decodes_as_text(file_path: str, limit: int = 8192) -> bool:
        """True when the first ``limit`` bytes are (almost) all printable text.

        Used to protect a text file from a false magic-byte match. UTF-16 and
        UTF-8 are both accepted; fewer than 2% non-printable bytes is enough,
        because a text file may legitimately contain a stray control character.
        """
        try:
            with open(file_path, 'rb') as handle:
                sample = handle.read(limit)
        except OSError:
            return False
        if not sample:
            return False
        if sample.startswith((b'\xff\xfe', b'\xfe\xff')):
            try:
                text = sample.decode('utf-16', 'replace')
            except Exception:
                return False
        else:
            text = sample.decode('utf-8', 'replace')
        if not text:
            return False
        bad = sum(1 for ch in text if ch == '\ufffd' or (ord(ch) < 32 and ch not in '\t\n\r\f\v'))
        return bad / len(text) <= 0.02

    @staticmethod
    def _odf_package_extension(file_path: str) -> Optional[str]:
        """The ODF extension of a ZIP package, read from its ``mimetype`` entry.

        The ODF specification asks for ``mimetype`` to be the first member and
        stored uncompressed. Files in the wild - LibreOffice's own test corpus
        among them - do not always comply. Reading the entry by name instead of
        trusting the order keeps a valid ODT out of the archive reader, which is
        where it used to land: zero characters, no error.
        """
        try:
            import zipfile
            with zipfile.ZipFile(file_path) as archive:
                names = archive.namelist()
                if 'content.xml' not in names:
                    return None
                if 'mimetype' not in names:
                    return None
                mimetype = archive.read('mimetype').decode('ascii', 'ignore').strip()
        except Exception:
            return None
        return FileReaderService.ODF_MIMETYPES.get(mimetype)

    @staticmethod
    def normalize_extension(extension: Optional[str]) -> str:
        """Normalize an extension to lower case with a single leading dot."""
        ext = str(extension or '').strip().lower()
        if not ext or ext in ('none', '.'):
            return ''
        return ext if ext.startswith('.') else '.' + ext

    @classmethod
    def extension_from_path(cls, file_path: str) -> str:
        """Return the declared extension of ``file_path``, compound-aware."""
        lower = str(file_path).lower()
        for compound in cls.COMPOUND_EXTENSIONS:
            if lower.endswith(compound):
                return compound
        return cls.normalize_extension(os.path.splitext(str(file_path))[1])

    def resolve_type_for_file(
        self,
        file_path: str,
        declared_extension: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Decide the processing type for a file from content *and* name.

        Content wins whenever it produces a strong magic-byte signature that a
        registered reader can actually handle; the declared extension is used
        as the fallback and as a tie-breaker when content is inconclusive. A
        file with no usable extension is no longer rejected outright.

        Args:
            file_path: Path to the file.
            declared_extension: Extension reported by discovery, if any.

        Returns:
            Decision dict with ``effective_extension``, ``declared_extension``,
            ``detected_extension``, ``detection_confidence``,
            ``detection_method``, ``extension_mismatch`` and ``detection_note``.
        """
        declared = self.normalize_extension(declared_extension) or \
            self.extension_from_path(file_path)

        detected, confidence = sniff_file_type(file_path)

        decision: Dict[str, Any] = {
            'declared_extension': declared,
            'detected_extension': detected,
            'detection_confidence': confidence,
            'detection_method': 'extension',
            'effective_extension': declared,
            'extension_mismatch': False,
            'detection_note': None,
        }

        # FORMAT-01: the signature sniffer answers "which extension"; the format
        # service answers the questions the pipeline actually needs - format
        # identity (a .docm is not a .docx), MIME type, container features
        # (macros, encryption, signatures, template/slideshow variants) and the
        # declared-vs-detected discrepancies. It is additive: every existing key
        # keeps its meaning, and callers that ignore the new keys behave exactly
        # as before.
        try:
            from core.formats import identify as identify_format

            identification = identify_format(file_path, declared_name=None)
            decision['format_id'] = identification.format_id
            decision['format_family'] = identification.family.value
            decision['mime_type'] = identification.mime
            decision['format_version'] = identification.version
            decision['format_features'] = identification.features
            decision['format_discrepancies'] = [
                discrepancy.as_dict() for discrepancy in identification.discrepancies]
            decision['format_identification'] = identification.as_dict()
            if identification.extension and identification.extension != detected:
                decision['detected_extension'] = identification.extension
                detected = identification.extension
            if identification.confidence and identification.confidence != confidence:
                decision['detection_confidence'] = identification.confidence
                confidence = identification.confidence
        except Exception as exc:  # identification is additive; never fatal
            decision['format_identification_error'] = str(exc)

        # A compound archive extension is more specific than the single
        # compression-layer signature the sniffer reports for the same bytes.
        if declared in self.COMPOUND_EXTENSIONS:
            decision['detection_method'] = 'extension'
            decision['detection_note'] = (
                'compound archive extension retained over single-layer sniff'
            )
            return decision

        if declared in self.IWORK_PACKAGE_EXTENSIONS and self.is_supported(declared):
            decision['effective_extension'] = declared
            decision['detection_method'] = 'extension'
            if detected and detected != declared:
                decision['extension_mismatch'] = True
                decision['detection_note'] = (
                    f'iWork package {declared} retained over {detected} container sniff'
                )
            return decision

        if declared in self.DECLARED_MEDIA_EXTENSIONS and self.is_supported(declared):
            decision['effective_extension'] = declared
            decision['detection_method'] = 'extension'
            if detected and detected != declared:
                decision['extension_mismatch'] = True
                decision['detection_note'] = (
                    f'declared media type {declared} retained over {detected} sniff'
                )
            return decision

        # An ODF package identifies itself inside its own container. Consult the
        # ``mimetype`` member by name: member order is not evidence, and a valid
        # ODT whose ``mimetype`` was not written first used to be routed to the
        # archive reader (0 characters, no error).
        if detected == '.zip' or declared in ('.odt', '.ods', '.odp', '.ott',
                                              '.ots', '.otp', '.odg'):
            odf_extension = self._odf_package_extension(file_path)
            if odf_extension and self.is_supported(odf_extension):
                decision['effective_extension'] = odf_extension
                decision['detection_method'] = 'odf-mimetype'
                if declared and declared != odf_extension:
                    decision['extension_mismatch'] = True
                decision['detection_note'] = (
                    f'ODF package declares {odf_extension} in its mimetype member'
                )
                return decision

        # Text that is text stays text. The signature table matches two-byte
        # prefixes ("BM" is a bitmap, "MZ" an executable), which a prose file can
        # start with by coincidence; when the declared extension is textual and
        # the bytes decode as text, the name wins.
        if (declared in self.TEXTUAL_EXTENSIONS and self.is_supported(declared)
                and self._decodes_as_text(file_path)):
            decision['effective_extension'] = declared
            decision['detection_method'] = 'extension'
            if detected and detected != declared:
                decision['extension_mismatch'] = True
                decision['detection_note'] = (
                    f'declared text {declared} retained over {detected} signature: '
                    f'the content decodes as text'
                )
            return decision

        if confidence == CONFIDENCE_STRONG and detected != '.bin':
            if self.is_supported(detected):
                decision['effective_extension'] = detected
                decision['detection_method'] = 'magic-bytes'
                if declared and declared != detected:
                    decision['extension_mismatch'] = True
                    decision['detection_note'] = (
                        f'declared extension {declared} contradicted by content '
                        f'signature {detected}; content signature used'
                    )
                return decision
            # Strong signature for a format we have no reader for: keep the
            # declared type rather than sending the file to the wrong reader.
            decision['detection_note'] = (
                f'content signature {detected} has no registered reader; '
                f'declared extension used'
            )
            if not declared:
                decision['effective_extension'] = ''
            return decision

        # A declared extension the content contradicts outright does not get to
        # choose the reader, even when the content evidence is only a heuristic.
        # A text file called .png is not a PNG: the format service rejects the
        # declared type for these bytes (it records
        # ``declared_type_rejected_for_content``), and honouring the name sent
        # the file to the image reader, which failed with "cannot identify
        # image file" - an ERROR line for a file whose text was perfectly
        # readable. Only that explicit rejection can override a usable
        # extension, so nothing else changes route.
        rejected = (
            decision.get('format_identification', {})
            .get('features', {})
            .get('declared_type_rejected_for_content')
        )
        if rejected and self.is_supported(detected):
            decision['effective_extension'] = detected
            decision['detection_method'] = 'content-heuristic'
            decision['extension_mismatch'] = True
            decision['detection_note'] = (
                f'declared extension {declared} names {rejected}, which cannot '
                f'hold this content; the bytes are {detected or "text"} and the '
                f'content decides'
            )
            return decision

        if declared and self.is_supported(declared):
            decision['detection_method'] = 'extension'
            return decision

        # No strong signature: a textual heuristic may still name the format,
        # but only to fill a gap - never to override a usable extension.
        if confidence == CONFIDENCE_WEAK and self.is_supported(detected):
            decision['effective_extension'] = detected
            decision['detection_method'] = 'content-heuristic'
            if declared:
                decision['detection_note'] = (
                    f'declared extension {declared} is unsupported; '
                    f'text heuristic identified {detected}'
                )
            return decision

        # Nothing usable. Route to the binary reader so the file is still
        # recorded rather than dropped with "no extension found".
        decision['effective_extension'] = '.bin'
        decision['detection_method'] = 'fallback-binary'
        decision['detection_note'] = (
            'no extension and no recognisable content signature; '
            'treated as binary'
        )
        return decision

    def resolve_reader_for_file(
        self,
        file_path: str,
        declared_extension: Optional[str] = None,
    ) -> tuple[Optional[BaseReader], Dict[str, Any]]:
        """Resolve both the reader and the identification decision for a file.

        Args:
            file_path: Path to the file.
            declared_extension: Extension reported by discovery, if any.

        Returns:
            ``(reader, decision)`` - ``reader`` is ``None`` when no registered
            reader handles the resolved type.
        """
        decision = self.resolve_type_for_file(file_path, declared_extension)
        reader = self.get_reader_for_extension(decision['effective_extension'])
        return reader, decision
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read a file using the appropriate reader.
        
        Args:
            file_info: Dictionary containing file information with 'path' and 'extension' keys
        
        Returns:
            Dictionary with extracted content or None on error
        """
        # Validate file_info
        if not file_info or not isinstance(file_info, dict):
            logger.error("Invalid file_info: must be a non-empty dictionary")
            return None
        
        file_path = file_info.get("path")
        extension = file_info.get("extension", "")
        
        if not file_path:
            logger.error("file_info must contain 'path' key")
            return None
        
        # DETECT-01: identify by content first, extension as fallback. The
        # resolved type is handed to the reader so its internal dispatch uses
        # the verified type rather than the filename.
        reader, decision = self.resolve_reader_for_file(file_path, extension)

        if not reader:
            effective = decision['effective_extension']
            logger.warning(
                "No reader found for %s (declared=%r detected=%r, file: %s)",
                effective or 'unknown type', decision['declared_extension'],
                decision['detected_extension'], file_path,
            )
            return {
                "error": f"Unsupported file type: {effective or extension or 'unknown'}",
                "path": file_path,
                "type_detection": decision,
            }

        file_info = dict(file_info)
        file_info['effective_extension'] = decision['effective_extension']

        # Use reader to read file
        try:
            return reader.read_file(file_info)
        except Exception as e:
            logger.error(f"Error reading file {file_path} with {reader.__class__.__name__}: {e}")
            return {
                "error": str(e),
                "path": file_path
            }
    
    def get_supported_extensions(self) -> Set[str]:
        """
        Get all supported file extensions across all readers.
        
        Returns:
            Set of all supported file extensions
        """
        all_extensions = set()
        for reader in self._readers:
            all_extensions.update(reader.get_supported_extensions())
        return all_extensions
    
    def is_supported(self, extension: str) -> bool:
        """
        Check if a file extension is supported.
        
        Args:
            extension: File extension to check
        
        Returns:
            True if extension is supported, False otherwise
        """
        extension = extension.lower()
        if not extension.startswith('.'):
            extension = '.' + extension
        return extension in self._extension_map
    
    def get_reader_info(self) -> Dict[str, Any]:
        """
        Get information about all registered readers.
        
        Returns:
            Dictionary with reader information
        """
        info = {
            "total_readers": len(self._readers),
            "total_extensions": len(self._extension_map),
            "readers": []
        }
        
        for reader in self._readers:
            extensions = reader.get_supported_extensions()
            info["readers"].append({
                "name": reader.__class__.__name__,
                "supported_extensions": sorted(list(extensions)),
                "extension_count": len(extensions)
            })
        
        return info


# Create singleton instance for backward compatibility
_file_reader_service = FileReaderService()


def get_file_reader_service() -> FileReaderService:
    """Get the singleton FileReaderService instance"""
    return _file_reader_service
