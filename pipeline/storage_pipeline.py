
"""
Storage Pipeline - Complete file storage implementation
Handles extraction, tokenization, and database storage
Refactored to use ContentDBService with proper transaction management
Part 1: Core pipeline and text extraction
"""

import logging
import os
import re
import threading
from collections import OrderedDict
import math
from typing import Dict, Any, Optional, List, Tuple
import time
from datetime import date

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from Hdg_Err_Ex_Log import (
    handle_error, is_connection_error, is_retryable_error,
    ErrorCategory, ErrorSeverity
)
from core.content_markers import strip_structural_markers


logger = logging.getLogger(__name__)

# Maximum characters of structured raw text stored per file for display
# fidelity (contents_raw). Larger files fall back to the word-join
# reconstruction; word search is never affected.
MAX_RAW_DISPLAY_CHARS = 5 * 1024 * 1024


#: Cap for paths.status_detail so a pathological error string cannot bloat the
#: row. Module level on purpose: the resolver is pure and should not need
#: instance state to be called or tested.
STATUS_DETAIL_MAX_LENGTH = 2000


def _parse_file_size_to_bytes(size_value):
    """
    Convert file size to integer bytes.
    Handles both integer bytes and formatted strings like "15.70 KB", "1.5 MB", etc.
    
    Args:
        size_value: File size as int, str, or None
        
    Returns:
        Integer bytes, or 0 if conversion fails
    """
    if size_value is None:
        return 0
    
    # If already an integer, return it.  Never pass a negative size to the
    # database CHECK constraint; malformed metadata must not abort ingestion.
    if isinstance(size_value, int):
        return max(0, size_value)

    if isinstance(size_value, float) and not math.isfinite(size_value):
        return 0
    
    # If it's a string, try to parse it
    if isinstance(size_value, str):
        # Remove any whitespace
        size_str = size_value.strip()
        
        # Try to parse as integer first (plain number)
        try:
            parsed = float(size_str)
            return max(0, int(parsed)) if math.isfinite(parsed) else 0
        except (ValueError, TypeError, OverflowError):
            pass
        
        # Try to parse formatted size (e.g., "15.70 KB", "1.5 MB", "500 B")
        import re
        # Match patterns like "15.70 KB", "1.5MB", "500B", "1024"
        match = re.match(r'^([\d.]+)\s*([KMGT]?B?|BYTES?)?$', size_str, re.IGNORECASE)
        if match:
            number = float(match.group(1))
            if not math.isfinite(number):
                return 0
            unit_str = (match.group(2) or '').upper()
            
            # Extract unit letter (K, M, G, T) or empty for bytes
            if unit_str.startswith('K'):
                unit = 'K'
            elif unit_str.startswith('M'):
                unit = 'M'
            elif unit_str.startswith('G'):
                unit = 'G'
            elif unit_str.startswith('T'):
                unit = 'T'
            else:
                unit = ''  # Bytes or no unit specified
            
            multipliers = {
                '': 1,
                'K': 1024,
                'M': 1024 * 1024,
                'G': 1024 * 1024 * 1024,
                'T': 1024 * 1024 * 1024 * 1024
            }
            
            multiplier = multipliers.get(unit, 1)
            return int(number * multiplier)
        
        # If parsing fails, try to get actual file size from path
        return 0
    
    # For other types, try to convert to int
    try:
        return int(size_value)
    except (ValueError, TypeError):
        return 0


class StoragePipeline:
    """
    Orchestrates file storage to database using ContentDBService.
    
    This class has been refactored to align with database design principles:
    - Uses ContentDBService for all database operations
    - Uses transactions for multi-step operations
    - Proper error handling with rollback on failures
    """
    
    #: Ceiling on the stored text of one artifact (characters). Above this the
    #: tail is cut with an explicit marker: a 3 000 000-cell spreadsheet is a
    #: legitimate file, a 181 MB text row is not.
    MAX_CONTENT_TEXT_CHARS = 10_000_000

    # Shared DatabaseHub instance to prevent connection pool exhaustion
    _shared_db_hub = None
    _shared_db_hub_lock = None
    
    #: How many per-file store outcomes to retain for the reader's accounting.
    _OUTCOME_KEEP = 10000

    def __init__(self, db_hub=None, source_name: str = None, side_name: str = None, db_service=None):
        """
        Initialize storage pipeline
        
        Args:
            db_hub: DatabaseHub instance (optional, kept for backward compatibility)
            source_name: Source name (optional, but must be provided in _store_file_sync if not here)
            side_name: Side name (optional, but must be provided in _store_file_sync if not here)
            db_service: ContentDBService instance (optional, will be created if not provided)
        """
        # Initialize lock for thread-safe singleton access
        if StoragePipeline._shared_db_hub_lock is None:
            StoragePipeline._shared_db_hub_lock = threading.Lock()
        
        # Initialize ContentDBService (preferred method)
        if db_service is None:
            from database.services.contents_db_service import ContentDBService
            from database.database.database import Database
            try:
                db = Database()  # Uses connection pooling and environment config
                db_service = ContentDBService(db)
            except Exception as e:
                logger.error(f"Failed to create ContentDBService: {e}")
                raise
        
        self.db_service = db_service
        
        # Keep db_hub for backward compatibility (content processor, etc.)
        # But prefer db_service for database operations
        # NOTE: DatabaseHub may not exist - it's optional for storage functionality
        # Storage works with db_service alone, db_hub is only needed for advanced features
        # CRITICAL FIX: Use shared DatabaseHub instance to prevent connection pool exhaustion
        db_hub_owned = db_hub is not None
        if db_hub is None:
            # Use singleton pattern to prevent multiple connection pools
            with StoragePipeline._shared_db_hub_lock:
                if StoragePipeline._shared_db_hub is not None:
                    # Re-check readiness: the shared hub is dropped when the
                    # database configuration changes (settings/config.py
                    # invalidate_database_connections) or when a caller closed
                    # it.  Rebuilding it here self-heals the pipeline instead
                    # of leaving every later store without a hub - the previous
                    # behaviour that silently degraded duplicate detection and
                    # triggered per-file "connection unhealthy" handling.
                    try:
                        if not StoragePipeline._shared_db_hub.db._pool:
                            StoragePipeline._shared_db_hub = None
                    except Exception:
                        StoragePipeline._shared_db_hub = None
                if StoragePipeline._shared_db_hub is None:
                    try:
                        from database import DatabaseHub
                        from settings import get_storage_config
                        storage_cfg = get_storage_config()
                        StoragePipeline._shared_db_hub = DatabaseHub(db_name=storage_cfg.db_name)
                        logger.info("Created shared DatabaseHub instance for connection pooling")
                    except (ImportError, AttributeError) as e:
                        # DatabaseHub doesn't exist or can't be imported - this is OK
                        # Storage functionality works without it using db_service
                        logger.info(f"DatabaseHub not available (optional): {e}. Storage will work with db_service only.")
                        StoragePipeline._shared_db_hub = None
                    except Exception as e:
                        logger.warning(f"Failed to create DatabaseHub (some features may be limited): {e}")
                        StoragePipeline._shared_db_hub = None
                db_hub = StoragePipeline._shared_db_hub
        
        self.db_hub = db_hub
        #: True when this pipeline created its own hub and may therefore close
        #: it.  The class-shared hub is owned by the pipeline class: a nested
        #: reader that closes it would break every other reader in the process
        #: (the "Database connection unhealthy, attempting reconnect..." loop).
        self.db_hub_owned = db_hub_owned
        
        # Store source and side names (optional in constructor, but mandatory in _store_file_sync)
        # No defaults - source and side must be explicitly provided
        self._source_name = source_name
        self._side_name = side_name
        
        # Statistics (DATA-04: full counter set with consistency relationships:
        # discovered >= completed(stored+duplicates) + failed + skipped;
        # storage failures are tracked separately and never folded into success)
        #: Outcome of the most recent store attempt per file path.  Workers
        #: process distinct files concurrently, so a path-keyed map is safe and
        #: lets the reader classify a resolved duplicate as "nothing new was
        #: stored" instead of counting it as a fresh success.  Bounded by
        #: :data:`_OUTCOME_KEEP`.
        #: name -> id caches for source/side resolution (see _resolve_source_id).
        #: Small by construction: a run uses a handful of names, and each entry
        #: is one integer.  Invalidated on reconnect/reconfiguration.
        self._source_id_cache: Dict[str, int] = {}
        self._side_id_cache: Dict[str, int] = {}
        self._entity_lock = threading.Lock()

        self._store_outcomes: "OrderedDict[str, str]" = OrderedDict()
        self._outcome_lock = threading.Lock()

        self.stats = {
            'files_discovered': 0,
            'files_queued': 0,
            'files_started': 0,
            'files_processed': 0,      # completed processing (any outcome)
            'files_stored': 0,
            'files_duplicates': 0,     # this exact file was already recorded
            'files_content_reused': 0, # new file, content already stored
            'files_failed': 0,
            'files_skipped': 0,
            'files_unsupported': 0,
            'storage_failed': 0,
            'files_extracted': 0,
        }

    def record_discovered(self, count: int = 1) -> None:
        """Record discovered files (called by the discovery phase)."""
        self.stats['files_discovered'] = self.stats.get('files_discovered', 0) + count

    def record_skipped(self, count: int = 1, reason: str = '') -> None:
        """Record skipped files with an optional reason (kept in stats)."""
        self.stats['files_skipped'] = self.stats.get('files_skipped', 0) + count
        if reason:
            skipped = self.stats.setdefault('skip_reasons', {})
            skipped[reason] = skipped.get(reason, 0) + 1

    def get_stats_summary(self) -> Dict[str, Any]:
        """Return stats plus consistency validation (DATA-04)."""
        s = dict(self.stats)
        completed = s.get('files_stored', 0) + s.get('files_duplicates', 0)
        issues = []
        discovered = s.get('files_discovered', 0)
        if discovered:
            if discovered < completed + s.get('files_failed', 0) + s.get('files_skipped', 0):
                issues.append('counter inconsistency: discovered < completed+failed+skipped')
            if discovered != (completed + s.get('files_failed', 0)
                              + s.get('files_skipped', 0) + s.get('files_unsupported', 0)
                              + max(0, discovered - (completed + s.get('files_failed', 0)
                                                     + s.get('files_skipped', 0) + s.get('files_unsupported', 0)))):
                # discovered may exceed the sum while work is in flight; that is
                # not an error - only the '<' case above is a real violation.
                pass
        s['consistency_issues'] = issues
        return s
    
    # ------------------------------------------------------------------
    # Source / side resolution (cached, keyed)
    # ------------------------------------------------------------------
    def _resolve_source_id(self, name: str) -> Optional[int]:
        """Return the id for ``name``, creating it if needed.

        Cached per name: a run stores thousands of files under one source, and
        the previous implementation re-read the entire ``sources`` table for
        each of them.  On a cache miss the repository's ``ON CONFLICT`` upsert
        resolves-or-creates in one statement, so concurrent workers cannot
        race (which used to cost the file its real source).
        """
        with self._entity_lock:
            cached = self._source_id_cache.get(name)
        if cached is not None:
            return cached
        try:
            source_id = self.db_service.get_or_create_source(name=name, importance=1.0)
        except Exception as exc:
            logger.error(f"Failed to resolve source '{name}': {exc}")
            source_id = None
        if source_id:
            with self._entity_lock:
                self._source_id_cache[name] = source_id
            logger.debug(f"Resolved source: {name} (ID: {source_id})")
            return source_id

        # Fallback chain, preserved from the previous implementation: a known
        # fallback source, then whatever source exists, so a file is never lost
        # because its declared source could not be created.
        try:
            fallback_name = "__FALLBACK_SOURCE__"
            fallback_id = self.db_service.get_or_create_source(
                name=fallback_name, importance=0.5
            )
            if fallback_id:
                with self._entity_lock:
                    self._source_id_cache[fallback_name] = fallback_id
                logger.warning(f"Using fallback source '{fallback_name}' (ID: {fallback_id})")
                return fallback_id
        except Exception as fallback_error:
            logger.error(f"Failed to create fallback source: {fallback_error}")
        try:
            existing = self.db_service.get_all_sources()
            if existing:
                logger.warning(f"Using first available source (ID: {existing[0][0]}) as last resort")
                return existing[0][0]
        except Exception as exc:
            logger.debug(f"Could not list sources: {exc}")
        logger.error("No sources available and cannot create one - file cannot be stored")
        return None

    def _resolve_side_id(self, name: str) -> Optional[int]:
        """Return the id for ``name``, creating it if needed (see above)."""
        with self._entity_lock:
            cached = self._side_id_cache.get(name)
        if cached is not None:
            return cached
        try:
            side_id = self.db_service.get_or_create_side(name=name, importance=1.0)
        except Exception as exc:
            logger.error(f"Failed to resolve side '{name}': {exc}")
            side_id = None
        if side_id:
            with self._entity_lock:
                self._side_id_cache[name] = side_id
            logger.debug(f"Resolved side: {name} (ID: {side_id})")
            return side_id

        try:
            fallback_name = "__FALLBACK_SIDE__"
            fallback_id = self.db_service.get_or_create_side(
                name=fallback_name, importance=0.5
            )
            if fallback_id:
                with self._entity_lock:
                    self._side_id_cache[fallback_name] = fallback_id
                logger.warning(f"Using fallback side '{fallback_name}' (ID: {fallback_id})")
                return fallback_id
        except Exception as fallback_error:
            logger.error(f"Failed to create fallback side: {fallback_error}")
        try:
            existing = self.db_service.get_all_sides()
            if existing:
                logger.warning(f"Using first available side (ID: {existing[0][0]}) as last resort")
                return existing[0][0]
        except Exception as exc:
            logger.debug(f"Could not list sides: {exc}")
        logger.error("No sides available and cannot create one - file cannot be stored")
        return None

    def _store_file_sync(
        self,
        file_info: Dict[str, Any],
        result: Dict[str, Any],
        source_name: Optional[str] = None,
        side_name: Optional[str] = None,
        parent_path_id: Optional[int] = None,
        hierarchy_path: Optional[str] = None
        ) -> Optional[int]:
        """
        Store single file synchronously with improved error handling
        
        Args:
            file_info: File metadata dictionary
            result: File processing result
            source_name: Source name (overrides constructor default)
            side_name: Side name (overrides constructor default)
            parent_path_id: Parent path ID (for nested files)
            hierarchy_path: Hierarchy path string
        
        Returns:
            Path ID or None if failed
        """

        # The reader supplies the occurrence location for archive members in
        # file_info (see IntegratedFileReader._occurrence_location). Using it
        # here keeps the store and the pre-processing skip check on one key.
        if hierarchy_path is None and isinstance(file_info, dict):
            hierarchy_path = file_info.get('hierarchy_path')
        # Require explicit source/side names (no defaults, no auto-creation)
        # Source and side must be explicitly provided - either in constructor or in this call
        effective_source_name = source_name if source_name is not None else getattr(self, '_source_name', None)
        effective_side_name = side_name if side_name is not None else getattr(self, '_side_name', None)
        
        # CRITICAL: Validate source/side but don't fail - use fallback values to ensure file is stored
        if not effective_source_name:
            logger.error("source_name is MANDATORY - must be explicitly provided. No defaults allowed.")
            # Use fallback to ensure file is still stored with error information
            effective_source_name = "__ERROR_NO_SOURCE__"
            logger.warning(f"Using fallback source name '{effective_source_name}' to ensure file is stored")
        
        if not effective_side_name:
            logger.error("side_name is MANDATORY - must be explicitly provided. No defaults allowed.")
            # Use fallback to ensure file is still stored with error information
            effective_side_name = "__ERROR_NO_SIDE__"
            logger.warning(f"Using fallback side name '{effective_side_name}' to ensure file is stored")
        
        # Get or create source and side using ContentDBService
        #
        # Resolution is cached per name and done with a single keyed statement.
        # The previous code called ``get_all_sources()`` (an unbounded
        # ``SELECT`` over the whole table) and then linearly searched it - on
        # *every stored file*.  Cost therefore grew with database cardinality
        # times file count: with 100k sources and 10M files that is a terabyte
        # of row transfer and a python-level scan per file.  The names used by a
        # run are stable, so one lookup per name is enough, and the upsert is
        # already concurrency-safe.
        source_id = self._resolve_source_id(effective_source_name)
        if source_id is None:
            self.stats['files_failed'] += 1
            return None

        side_id = self._resolve_side_id(effective_side_name)
        if side_id is None:
            self.stats['files_failed'] += 1
            return None

        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                # Reconnect only when the database link is genuinely gone.
                #
                # The previous check treated *load* as failure: when the pool
                # was busy (or the pool object had already been closed by
                # another component) it declared the database unhealthy and
                # called ``_reconnect()``, which tore down and rebuilt the pool
                # from the callback of a store transaction.  With several
                # workers doing this repeatedly the rebuilds multiplied
                # connections until PostgreSQL answered
                # "FATAL: sorry, too many clients already".  ``rebuild_pool``
                # keeps a usable pool untouched, is rate-limited across
                # threads, and is verified by a health check, so a reconnect
                # can no longer be triggered by a busy pool.
                if self.db_hub:
                    try:
                        if not self.db_hub.db.rebuild_pool():
                            logger.error("Failed to reconnect to database - will retry in outer loop")
                            if retry_count >= max_retries:
                                # Only fail after all retries exhausted
                                logger.error("All retries exhausted - cannot store file")
                                self.stats['files_failed'] += 1
                                return None
                            retry_count += 1
                            time.sleep(1.0 * retry_count)
                            continue
                    except Exception as hub_err:
                        logger.debug(f"Connection health check unavailable: {hub_err}")
                
                # Extract metadata and content (with validation)
                metadata = result.get('Metadata', {})
                if not isinstance(metadata, dict):
                    logger.warning(f"Invalid Metadata type: {type(metadata).__name__}, using empty dict")
                    metadata = {}
                
                content = result.get('Content', {})
                if not isinstance(content, dict):
                    logger.warning(f"Invalid Content type: {type(content).__name__}, using empty dict")
                    content = {}
                
                # Validate required fields - create minimal file_info if missing
                if not file_info or not isinstance(file_info, dict):
                    logger.error("Invalid file_info: must be a non-empty dictionary - creating minimal file_info")
                    # CRITICAL: Create minimal file_info to ensure file can still be stored
                    file_info = {
                        'path': result.get('path', 'unknown'),
                        'name': os.path.basename(result.get('path', 'unknown')),
                        'extension': '',
                        'size': 0
                    }
                    logger.warning(f"Using minimal file_info: {file_info}")
                
                # Check for errors (but ALWAYS store metadata - errors are part of the record)
                has_content_error = bool(content.get('error'))
                if has_content_error:
                    error_msg = content.get('error', 'Unknown error')
                    logger.info(f"File has content error: {error_msg}. Will store metadata and error information.")
                
                # Step 1: Calculate/validate hash (DB-03)
                # Content identity is ALWAYS the streamed SHA-256 of the file
                # bytes. Metadata-derived and time-based fallback hashes were
                # removed: a file that cannot be hashed is a processing
                # FAILURE (counted in stats), never stored under a fake
                # identity.
                # HASH-01: a hash supplied by an upstream producer is only
                # trusted if it is a well-formed digest. Sentinels such as
                # SKIPPED_LARGE_FILE are recomputed here rather than stored.
                from core.hashing import is_valid_digest
                file_hash = metadata.get('hash') or file_info.get('hash')
                file_hash_valid = is_valid_digest(file_hash)
                if not file_hash_valid:
                    from core.hashing import hash_file, HashingError
                    file_path_for_hash = file_info.get('path')
                    if not file_path_for_hash:
                        logger.error("File path is missing from file_info - cannot compute content hash")
                        self.stats['files_failed'] = self.stats.get('files_failed', 0) + 1
                        self.stats.setdefault('storage_failed', 0)
                        self.stats['storage_failed'] = self.stats.get('storage_failed', 0) + 1
                        return None
                    try:
                        file_hash = hash_file(file_path_for_hash)
                        logger.debug("Computed content hash for %s", os.path.basename(file_path_for_hash))
                    except HashingError as hash_exc:
                        logger.error(
                            "Hashing failed for %s - refusing to store under a fake identity: %s",
                            file_path_for_hash, hash_exc,
                        )
                        self.stats['files_failed'] = self.stats.get('files_failed', 0) + 1
                        self.stats.setdefault('storage_failed', 0)
                        self.stats['storage_failed'] = self.stats.get('storage_failed', 0) + 1
                        return None

                # Validate hash shape; never replace with a synthetic value.
                if not is_valid_digest(file_hash):
                    logger.error("Invalid hash value %r - refusing to store", file_hash[:16] if file_hash else file_hash)
                    self.stats['files_failed'] = self.stats.get('files_failed', 0) + 1
                    self.stats.setdefault('storage_failed', 0)
                    self.stats['storage_failed'] = self.stats.get('storage_failed', 0) + 1
                    return None
                
                # Step 2: Occurrence-level duplicate check (One Content,
                # Many Contexts).  Only this exact physical encounter (same
                # content, same context, same location) is a duplicate to
                # skip; the same bytes elsewhere are a legitimate new
                # occurrence and are stored below without duplicating any
                # canonical content-derived data.
                is_duplicate, existing_path_id = self.check_duplicate_for_pipeline(
                    file_hash, source_id, side_id,
                    file_path=file_info.get('path'),
                    hierarchy_path=hierarchy_path,
                )
                if is_duplicate and existing_path_id:
                    logger.info(
                        f"Duplicate occurrence (already recorded): {file_info.get('name', 'unknown')} "
                        f"(hash: {file_hash[:16]}..., existing path_id: {existing_path_id})"
                    )
                    self.stats['files_duplicates'] += 1
                    self.stats['files_processed'] += 1
                    # ``file_path`` is bound later in this method, so the
                    # outcome key is built from the file info directly.
                    self._record_store_outcome(
                        file_info.get('path', ''), 'duplicate'
                    )
                    return existing_path_id
                
                # Step 3: Prepare file metadata for storage
                # Extract file information
                file_name = file_info.get('name', os.path.basename(file_info.get('path', 'unknown')))
                file_path = file_info.get('path', '')
                
                # Get file size and ensure it's an integer (bytes)
                # Try size_bytes first, then size, then get from file system
                file_size = file_info.get('size_bytes') or file_info.get('size') or metadata.get('size_bytes') or metadata.get('size')
                
                # Convert to integer bytes if it's a string
                file_size = _parse_file_size_to_bytes(file_size)
                
                # If still 0, try to get actual file size from file system
                if file_size == 0 and file_path and os.path.exists(file_path):
                    try:
                        file_size = os.path.getsize(file_path)
                    except Exception:
                        file_size = 0
                
                # Some readers intentionally emit ``unknown`` metadata (for
                # example a PST whose parser is unavailable). Do not let that
                # sentinel hide a verified extension or the declared suffix.
                metadata_type = metadata.get('file_type')
                if str(metadata_type or '').strip().lower() in {'', 'unknown', 'none'}:
                    metadata_type = None
                detected_type = (
                    content.get('type_detection', {}).get('effective_extension')
                    if isinstance(content.get('type_detection'), dict) else None
                )
                file_type = (metadata_type or file_info.get('effective_extension')
                             or detected_type or file_info.get('extension', 'unknown'))
                
                # Get actual file creation date tied to the file itself (embedded metadata or file mtime/birthtime)
                # file_date stores the file's creation/modification date, not the system copy/execution date
                from core.file_utils import get_file_creation_and_modification_date
                created_d, _ = get_file_creation_and_modification_date(file_path, content)
                file_date = created_d
                
                # Extract text content for processing
                text = None
                if not has_content_error:
                    text = self._extract_text_from_content(content)
                
                # Check if OCR was attempted/successful for images (even if text extraction fails)
                ocr_attempted = content.get('ocr_attempted', False) if isinstance(content, dict) else False
                ocr_successful = content.get('ocr_successful', False) if isinstance(content, dict) else False
                extraction_info = content.get('extraction_info', {}) if isinstance(content, dict) else {}
                has_extracted_text = extraction_info.get('extracted', False) or extraction_info.get('stored', False)
                
                # OPTIMIZED: Use unified ContentProcessor for all extraction (faster, preserves entities)
                content_words = []
                title_words = None
                content_date = None  # Will be extracted from content if dates are mentioned
                
                if text:
                    # INDEX vs DISPLAY: structural markers (sheet names, slide
                    # numbers, page headers, styles, email labels, ...) are
                    # display scaffolding. Word indexing, classification and
                    # therefore search must see ONLY the real content, so the
                    # tokenizer works on the marker-stripped copy. The display
                    # store (raw_text) keeps the verbatim text.
                    index_text = strip_structural_markers(text)

                    # PRODUCTION: Handle very large text content (>100MB) with chunked processing
                    # For terabyte-scale files, process text in chunks to prevent memory exhaustion
                    text_len = len(index_text) if isinstance(index_text, str) else 0
                    use_chunked_processing = text_len > 100 * 1024 * 1024  # > 100MB
                    
                    if use_chunked_processing:
                        logger.info(f"Large text content detected ({text_len / (1024*1024):.2f}MB), using chunked processing")
                        # Process text in chunks and accumulate words
                        content_words = []
                        chunk_size = 50 * 1024 * 1024  # 50MB chunks
                        try:
                            from database.processors import get_content_processor
                            processor = get_content_processor()
                            
                            # Process in chunks
                            for chunk_start in range(0, text_len, chunk_size):
                                chunk_end = min(chunk_start + chunk_size, text_len)
                                text_chunk = index_text[chunk_start:chunk_end]
                                
                                # Process chunk
                                chunk_tokens = processor.extract_words_with_punctuation_chunked(text_chunk)
                                chunk_words = [token[0] for token in chunk_tokens if token[0]]
                                content_words.extend(chunk_words)
                                
                                # Log progress for very large files
                                if chunk_start % (chunk_size * 10) == 0:
                                    progress_pct = (chunk_end / text_len) * 100
                                    logger.debug(f"Processed {chunk_end / (1024*1024):.2f}MB / {text_len / (1024*1024):.2f}MB ({progress_pct:.1f}%)")
                            
                            # Extract dates from first chunk (most likely to contain dates)
                            sample_text = index_text[:min(10 * 1024 * 1024, text_len)]  # First 10MB
                            content_date = self._extract_date_from_text(sample_text, processor)
                        except MemoryError as mem_err:
                            logger.error(f"Memory error during chunked text processing: {mem_err}")
                            # Fallback: process smaller chunks
                            try:
                                chunk_size = 10 * 1024 * 1024  # 10MB chunks
                                processor = get_content_processor()
                                for chunk_start in range(0, text_len, chunk_size):
                                    chunk_end = min(chunk_start + chunk_size, text_len)
                                    text_chunk = index_text[chunk_start:chunk_end]
                                    chunk_tokens = processor.extract_words_with_punctuation_chunked(text_chunk, chunk_size=5 * 1024 * 1024)
                                    chunk_words = [token[0] for token in chunk_tokens if token[0]]
                                    content_words.extend(chunk_words)
                                content_date = self._extract_date_from_text(index_text[:min(5 * 1024 * 1024, text_len)], processor)
                            except Exception as fallback_err:
                                logger.error(f"Fallback chunked processing also failed: {fallback_err}")
                                content_words = []
                                content_date = None
                        except Exception as e:
                            logger.warning(f"Chunked content processor extraction failed: {e}")
                            # Fallback: try standard processing with smaller chunk size
                            try:
                                from database.processors import get_content_processor
                                processor = get_content_processor()
                                # Use smaller chunk size for tokenization
                                tokens = processor.extract_words_with_punctuation_chunked(index_text, chunk_size=5 * 1024 * 1024)
                                content_words = [token[0] for token in tokens if token[0]]
                                content_date = self._extract_date_from_text(text[:min(10 * 1024 * 1024, text_len)], processor)
                            except Exception as fallback_err:
                                logger.error(f"Fallback processing failed: {fallback_err}")
                                content_words = []
                                content_date = None
                    else:
                        # Standard processing for smaller text
                        try:
                            from database.processors import get_content_processor
                            processor = get_content_processor()
                            tokens = processor.extract_words_with_punctuation_chunked(index_text)
                            # Extract just the words (first element of each token tuple)
                            content_words = [token[0] for token in tokens if token[0]]
                            
                            # Extract dates from content text for content_date field
                            # content_date stores a date mentioned in the content itself
                            content_date = self._extract_date_from_text(index_text, processor)
                        except Exception as e:
                            logger.warning(f"Content processor extraction failed: {e}")
                            # Fallback only if processor completely fails
                            content_words = []
                            content_date = None
                
                # Set file_status: 'Read' only when the artifact yielded content.
                # 'Read' means "this file had readable content", not "this file
                # was touched": a deliberately skipped artifact (for example an
                # icon below the image reader's minimum size), an unsupported
                # type, or a container whose readers produced nothing is
                # 'Unread' with its own processing_status explaining why. Every
                # input to is_read is content evidence that a reader produced:
                # indexed words from the extracted text, OCR text the image
                # reader actually returned, or the reader's own
                # extraction_info['extracted'/'stored'] signal. Metadata the
                # pipeline adds about the file must never be counted here (see
                # the identity note in _extract_text_from_content).
                # This ensures images with extracted text are marked as 'Read' even if tokenization fails
                has_content = bool(content_words)
                has_ocr_text = bool(str(content.get('text') or '').strip())
                is_read = has_content or (ocr_successful and has_ocr_text) or has_extracted_text
                file_status = 'Read' if is_read else 'Unread'
                
                # Log status determination for debugging
                if ocr_attempted and not is_read:
                    logger.debug(
                        f"[STATUS] File marked as 'Unread' despite OCR attempt - "
                        f"OCR successful: {ocr_successful}, "
                        f"Has text: {has_ocr_text}, "
                        f"Has content_words: {has_content}, "
                        f"Extraction info: {extraction_info}"
                    )
                
                # Extract coordinates from content (GPS coordinates in images or elsewhere)
                coordinates = self._extract_coordinates(content)
                
                # Extract title if available (using same optimized processor)
                title = self._extract_title(result, file_info)
                if title:
                    try:
                        from database.processors import get_content_processor
                        processor = get_content_processor()
                        title_words = processor.extract_words_simple(title)
                    except Exception as e:
                        logger.warning(f"Title extraction failed: {e}")
                        title_words = []
                
                # Step 4: Store document using ContentDBService.process_full_document()
                # This handles hash, path, content, word linking, keywords, and title in a single transaction
                
                # Log what will be stored
                storage_info = []
                storage_info.append(f"File: {file_name}")
                storage_info.append(f"Type: {file_type}")
                storage_info.append(f"Status: {file_status}")
                storage_info.append(f"Content Words: {len(content_words)}")
                if title_words:
                    storage_info.append(f"Title Words: {len(title_words)}")
                if coordinates:
                    storage_info.append(f"GPS Coordinates: {coordinates}")
                if content_date:
                    storage_info.append(f"Content Date: {content_date}")
                
                logger.info(f"[STORAGE] 💾 Storing to database - {' | '.join(storage_info)}")
                
                try:
                    # PRODUCTION: process_full_document uses its own transaction
                    # If it fails, it will rollback and we can retry with a fresh transaction
                    extraction_provenance = self._build_extraction_provenance(content)
                    processing_status, status_detail = self._resolve_processing_status(
                        content, file_status
                    )
                    # Cap the raw display text so one huge file cannot bloat
                    # the database; larger files fall back to the word-join
                    # reconstruction for display (search is unaffected).
                    raw_display_text = text[:MAX_RAW_DISPLAY_CHARS] if text else None
                    storage_result = self.db_service.process_full_document(
                        hash_value=file_hash,
                        source_id=source_id,
                        side_id=side_id,
                        file_name=file_name,
                        file_path=file_path,
                        file_size=file_size,
                        file_type=file_type,
                        file_status=file_status,
                        file_date=file_date,
                        content_words=content_words,
                        title_words=title_words,
                        coordinates=coordinates,
                        content_date=content_date,
                        extraction_provenance=extraction_provenance,
                        processing_status=processing_status,
                        status_detail=status_detail,
                        attempts=1,
                        raw_text=raw_display_text,
                        parent_path_id=parent_path_id,
                        hierarchy_path=hierarchy_path,
                    )
                    
                    # Handle case where storage_result is None
                    # CRITICAL: Try fallback storage method to ensure file metadata is stored
                    if storage_result is None:
                        logger.error(
                            f"process_full_document returned None for file '{file_name}' - "
                            f"transaction may have failed, attempting fallback storage. "
                            f"File: {file_path[:100] if len(file_path) > 100 else file_path}"
                        )
                        # Try to store at least basic metadata using the
                        # identity authority (registration is cheap even when
                        # full processing failed: the occurrence keeps its
                        # failed status and error detail).
                        fallback_path_id = None
                        try:
                            registration = self.db_service.register_occurrence(
                                hash_value=file_hash,
                                source_id=source_id,
                                side_id=side_id,
                                path_row={
                                    "file_name": file_name,
                                    "file_path": file_path,
                                    "file_size": file_size,
                                    "file_type": file_type,
                                    "file_status": "Unread",
                                    "file_date": file_date,
                                    "date_creation": date.today(),
                                    "coordinates": None,
                                    "processing_status": "failed",
                                    "status_detail": (
                                        'full processing failed; minimal record '
                                        'created by the storage fallback path'
                                    ),
                                    "attempts": 1,
                                    "parent_path_id": parent_path_id,
                                    "hierarchy_path": hierarchy_path,
                                },
                            )
                            fallback_path_id = registration.get("path_id")
                            hash_id = registration.get("hash_id")
                        except Exception as fallback_error:
                            logger.error(
                                f"Fallback storage failed for file '{file_name}': {fallback_error}. "
                                f"Error type: {type(fallback_error).__name__}"
                            )
                            import traceback
                            logger.debug(f"Fallback error traceback: {traceback.format_exc()}")

                        if fallback_path_id:
                            logger.warning(
                                f"✅ Stored file using fallback method (Path ID: {fallback_path_id}) - "
                                f"full processing failed but metadata saved. File: {file_name}"
                            )
                            self.stats['files_stored'] += 1
                            self.stats['files_processed'] += 1
                            return fallback_path_id
                        else:
                            logger.error(f"Fallback path insertion returned None for file: {file_name}")

                        # If fallback also fails, increment retry count and try again
                        if retry_count < max_retries:
                            retry_count += 1
                            logger.warning(
                                f"Retrying storage for '{file_name}' (attempt {retry_count}/{max_retries})..."
                            )
                            time.sleep(1.0 * retry_count)
                            continue
                        else:
                            logger.error(
                                f"❌ All storage attempts failed for file '{file_name}'. "
                                f"File processed but NOT stored in database. Path: {file_path[:100]}"
                            )
                            self.stats['files_failed'] += 1
                            return None
                    
                    if storage_result.get('success'):
                        path_id = storage_result.get('path_id')
                        hash_id = storage_result.get('hash_id')
                        error_msg = storage_result.get('error')

                        # Degraded-but-stored: derived steps that are contained
                        # in savepoints (raw display text, keywords, title)
                        # report here instead of failing the document.  Logged,
                        # never silent.
                        warnings = storage_result.get('warnings') or []
                        for warning in warnings:
                            logger.warning(
                                "[STORAGE] ⚠️  '%s' stored with degraded data: %s",
                                file_name, warning,
                            )
                        if warnings:
                            self.stats['files_degraded'] = (
                                self.stats.get('files_degraded', 0) + 1
                            )
                        
                        # Check if this is a duplicate
                        if error_msg and 'Duplicate' in error_msg:
                            logger.info(
                                f"[STORAGE] 🔄 Duplicate file detected - "
                                f"path_id={path_id}, hash_id={hash_id}"
                            )
                            self.stats['files_duplicates'] += 1
                            self.stats['files_processed'] += 1
                            self._record_store_outcome(file_path, 'duplicate')
                        else:
                            self._record_store_outcome(file_path, 'stored')
                            # Log successful storage with details
                            stored_details = []
                            stored_details.append(f"Path ID: {path_id}")
                            stored_details.append(f"Hash ID: {hash_id}")
                            stored_details.append(f"Status: {file_status}")
                            stored_details.append(f"Words: {len(content_words)}")
                            if coordinates:
                                stored_details.append(f"GPS: {coordinates}")
                            
                            logger.info(
                                f"[STORAGE] ✅ Successfully stored - {' | '.join(stored_details)}"
                            )
                            logger.info(
                                f"Document stored successfully using ContentDBService: "
                                f"path_id={path_id}, hash_id={hash_id}"
                            )
                            # Update statistics
                            self.stats['files_stored'] += 1
                            self.stats['files_processed'] += 1
                            if storage_result.get('content_reused'):
                                # Stored as a new occurrence of content that
                                # already existed: not a skipped duplicate.
                                self.stats['files_content_reused'] = (
                                    self.stats.get('files_content_reused', 0) + 1)

                        # PARENT-01: children of a container are stored before
                        # their parent, so the parent's id is only known now.
                        # Runs for duplicates too - they resolve to an existing
                        # path_id and their children still need a parent.
                        if path_id:
                            self._link_extracted_children(content, path_id)

                        # Build success message
                        file_size_mb = file_size / (1024 * 1024) if file_size > 0 else 0
                        is_duplicate = error_msg and 'Duplicate' in error_msg if error_msg else False
                        
                        if is_duplicate:
                            storage_details = [
                                "⚠️  DUPLICATE FILE DETECTED (already processed)",
                                f"   File: {file_name}",
                                f"   Path ID: {path_id} (existing)",
                                f"   Hash ID: {hash_id}",
                                f"   Source: {effective_source_name} (ID: {source_id})",
                                f"   Side: {effective_side_name} (ID: {side_id})",
                                f"   Hash: {file_hash[:16]}...",
                            ]
                        else:
                            storage_details = [
                                "✅ FILE SUCCESSFULLY STORED IN DATABASE (using ContentDBService with transactions)",
                                f"   File: {file_name}",
                                f"   Path ID: {path_id}",
                                f"   Hash ID: {hash_id}",
                                f"   Source: {effective_source_name} (ID: {source_id})",
                                f"   Side: {effective_side_name} (ID: {side_id})",
                                f"   Hash: {file_hash[:16]}...",
                            ]
                        
                        if file_size_mb > 0:
                            storage_details.append(f"   Size: {file_size_mb:.2f} MB ({file_size:,} bytes)")
                        
                        if not is_duplicate:
                            storage_details.append(f"   Words: {len(content_words):,}")
                            if title_words:
                                storage_details.append(f"   Title: {len(title_words)} words")
                        storage_details.append(f"   Status: {file_status}")
                        storage_details.append(f"   Database Record: paths.id = {path_id}")
                        
                        success_message = "\n".join(storage_details)
                        # Single output path: logger.info only.  The print()
                        # twin wrote the same block to stdout while the logger
                        # wrote it to stderr - merged logs showed the whole
                        # "FILE SUCCESSFULLY STORED" block twice per file, and
                        # the buffered stdout copy glued mid-line onto the
                        # next log record ("...paths.id = 18INFO:pipeline...").
                        logger.info(success_message)

                        return path_id
                    else:
                        error_msg = storage_result.get('error', 'Unknown error')
                        logger.error(f"Failed to store document: {error_msg}")
                        handle_error(
                            Exception(error_msg),
                            category=ErrorCategory.DATABASE,
                            severity=ErrorSeverity.HIGH,
                            context={
                                'operation': 'process_full_document',
                                'file_path': file_path[:100],
                                'file_hash': file_hash[:16] + '...'
                            },
                            suggested_action='Check database connection and constraints'
                        )
                        self.stats['files_failed'] += 1
                        return None
                        
                except Exception as doc_error:
                    error_type = type(doc_error).__name__
                    error_msg = str(doc_error)
                    file_name_display = file_name if 'file_name' in locals() else 'unknown'
                    
                    logger.error(
                        f"❌ Error storing document '{file_name_display}' with ContentDBService: "
                        f"{error_type}: {error_msg}"
                    )
                    
                    # Log more details for debugging
                    import traceback
                    logger.debug(f"Storage error traceback for '{file_name_display}': {traceback.format_exc()}")
                    
                    # RETRY POLICY: only transient failures are retried.
                    # The previous else-branch retried EVERY exception until
                    # retry_count >= max_retries, so a deterministic defect
                    # (e.g. NameError from a code bug) burned all three
                    # attempts and printed three full tracebacks for the
                    # identical failure.  Non-retryable errors now fail fast:
                    # one report, one traceback, files_failed += 1.
                    is_conn_err = is_connection_error(doc_error)
                    if is_conn_err and retry_count < max_retries:
                        retry_count += 1
                        logger.warning(
                            f"Connection error detected, retrying storage for '{file_name_display}' "
                            f"(attempt {retry_count}/{max_retries})..."
                        )
                        time.sleep(1.0 * retry_count)
                        continue
                    elif is_retryable_error(doc_error) and retry_count < max_retries:
                        retry_count += 1
                        logger.warning(
                            f"Retryable storage error for '{file_name_display}', retrying "
                            f"(attempt {retry_count}/{max_retries})..."
                        )
                        time.sleep(1.0 * retry_count)
                        continue
                    
                    handle_error(
                        doc_error,
                        category=ErrorCategory.DATABASE_CONNECTION if is_conn_err else ErrorCategory.DATABASE,
                        severity=ErrorSeverity.HIGH,
                        context={
                            'operation': 'process_full_document',
                            'file_path': file_path[:100] if 'file_path' in locals() else 'unknown',
                            'file_name': file_name_display,
                            'file_hash': file_hash[:16] + '...' if 'file_hash' in locals() else 'unknown',
                            'error_type': error_type,
                            'retry_count': retry_count
                        }
                    )
                    self.stats['files_failed'] += 1
                    return None
                
                
            except Exception as e:
                # Handle any unexpected errors in the main storage flow
                # Use improved error detection from error_handling module
                if is_connection_error(e):
                    # Connection errors - try to reconnect
                    handle_error(
                        e,
                        category=ErrorCategory.DATABASE_CONNECTION,
                        severity=ErrorSeverity.MEDIUM if retry_count < max_retries else ErrorSeverity.HIGH,
                        context={
                            'operation': '_store_file_sync',
                            'file_path': file_info.get('path', 'unknown')[:100] if file_info else None,
                            'retry_count': retry_count,
                            'max_retries': max_retries
                        },
                        suggested_action='Attempting to reconnect and retry' if retry_count < max_retries else 'Check database server status'
                    )
                    
                    if retry_count < max_retries:
                        retry_count += 1
                        logger.info(f"Attempting to reconnect (attempt {retry_count}/{max_retries})...")
                        if self.db_hub and self.db_hub._reconnect():
                            # Wait a bit before retrying
                            time.sleep(1.0)
                            continue
                        else:
                            logger.error("Failed to reconnect, will retry entire operation")
                            time.sleep(2.0 * retry_count)  # Exponential backoff
                            continue
                    
                    handle_error(
                        Exception(f"Failed to reconnect after {retry_count} attempts"),
                        category=ErrorCategory.DATABASE_CONNECTION,
                        severity=ErrorSeverity.HIGH,
                        context={'operation': '_store_file_sync', 'file_path': file_info.get('path', 'unknown')[:100] if file_info else None}
                    )
                    self.stats['files_failed'] += 1
                    return None
                elif is_retryable_error(e) and retry_count < max_retries:
                    # Other retryable errors
                    retry_count += 1
                    wait_time = 1.0 * (2 ** (retry_count - 1))  # Exponential backoff
                    handle_error(
                        e,
                        category=ErrorCategory.DATABASE,
                        severity=ErrorSeverity.MEDIUM,
                        context={
                            'operation': '_store_file_sync',
                            'file_path': file_info.get('path', 'unknown')[:100] if file_info else None,
                            'retry_count': retry_count,
                            'max_retries': max_retries
                        },
                        suggested_action=f'Retrying operation in {wait_time:.1f}s (attempt {retry_count}/{max_retries})'
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    # Non-retryable errors or max retries reached
                    handle_error(
                        e,
                        category=ErrorCategory.DATABASE if not is_connection_error(e) else ErrorCategory.DATABASE_CONNECTION,
                        severity=ErrorSeverity.HIGH,
                        context={
                            'operation': '_store_file_sync',
                            'file_path': file_info.get('path', 'unknown')[:100] if file_info else None,
                            'retry_count': retry_count,
                            'max_retries': max_retries
                        },
                        suggested_action='Check error details and database state' if retry_count >= max_retries else None
                    )
                    self.stats['files_failed'] += 1
                    return None
                
                # Fail-safe: every supported path above returns (a path_id or
                # None). Reaching this point means control fell through a branch
                # that did not, so the file's outcome would otherwise be lost -
                # count it as failed and say so loudly rather than continuing.
                logger.critical(
                    "Storage fell through every return path for %s; the file is "
                    "counted as failed and no path row was written.",
                    file_info.get('path', 'unknown') if file_info else 'unknown',
                )
                self.stats['files_failed'] += 1
                return None
                
                
        
        # If we get here, all retries failed
        self.stats['files_failed'] += 1
        return None
    
    def find_committed_occurrence(
        self,
        file_hash: str,
        source_name: Optional[str],
        side_name: Optional[str],
        location: Optional[str],
    ) -> Optional[int]:
        """Path id of an already committed, processed occurrence, or None.

        Fails open: any error returns None, so the file is processed normally.
        Correctness does not depend on this lookup, because the store path
        performs its own duplicate check before writing.
        """
        if not (file_hash and source_name and side_name and location):
            return None
        try:
            return self.db_service.find_committed_occurrence(
                file_hash, source_name, side_name, location)
        except Exception as exc:
            logger.debug("Committed-occurrence lookup failed; processing the file: %s", exc)
            return None

    def check_duplicate_for_pipeline(
        self,
        file_hash: str,
        source_id: Optional[int] = None,
        side_id: Optional[int] = None,
        file_path: Optional[str] = None,
        hierarchy_path: Optional[str] = None,
    ) -> Tuple[bool, Optional[int]]:
        """Occurrence-level duplicate check via the identity authority.

        ``DeduplicationService.check_duplicate``: with a location (file_path
        or hierarchy_path) only this exact physical encounter is a
        duplicate; without one the check answers "is this content already
        present in this context?".
        """
        if source_id is None or side_id is None:
            logger.error("source_id and side_id are MANDATORY for identity checks - no defaults allowed")
            return False, None

        return self.db_service.check_duplicate(
            file_hash, source_id, side_id,
            file_path=file_path, hierarchy_path=hierarchy_path,
        )

    #: Separator for the human-readable hierarchy chain (archive::child::grandchild).
    #: Processing states this pipeline can determine truthfully.
    #:
    #: 'queued', 'processing' and 'retrying' are deliberately absent: they
    #: describe work in flight, which this synchronous write path cannot
    #: observe. Recording them here would fabricate a state. They belong to the
    #: job layer (m0006), not to the point where a row is first written.
    TERMINAL_PROCESSING_STATES = (
        "processed",
        "partially_processed",
        "failed",
        "unsupported",
        "skipped",
    )

    def _resolve_processing_status(
        self,
        content: Optional[Dict[str, Any]],
        file_status: str,
    ) -> Tuple[str, Optional[str]]:
        """Derive (processing_status, status_detail) from what actually happened.

        ``file_status`` says only whether content exists. This distinguishes the
        outcomes that otherwise cannot be told apart: a corrupt file, a
        deliberately skipped icon and an unrecognised type all come back
        'Unread', and nothing recorded which. Order matters - unsupported is
        checked before failed, because an unsupported type also carries an error
        string and would otherwise be misreported as a failure.
        """
        if not isinstance(content, dict):
            content = {}

        info = content.get("extraction_info")
        info = info if isinstance(info, dict) else {}
        error = content.get("error")
        error_text = str(error) if error else ""

        # 1. Unsupported input - the reader could not identify the type at all.
        lowered = error_text.lower()
        if error_text and (
            "unsupported file type" in lowered or "no file extension" in lowered
        ):
            return "unsupported", error_text[:STATUS_DETAIL_MAX_LENGTH]

        # 2. Explicit extraction failure - except a held file, which is not a
        #    failed one. The artifact is fine; something else has it open.
        #    The stored vocabulary has no 'locked' state (the CHECK constraint
        #    from migration 0007), so the distinction is carried visibly in
        #    status_detail and counted as OUTCOME_LOCKED by the run's ledger.
        #    The marker list is shared with classify_result, so the row and the
        #    accounting cannot disagree about what "locked" means.
        if error_text:
            from pipeline.progress_ledger import LOCK_ERROR_MARKERS

            if any(marker in lowered for marker in LOCK_ERROR_MARKERS):
                return "failed", f"locked: {error_text}"[:STATUS_DETAIL_MAX_LENGTH]
            return "failed", error_text[:STATUS_DETAIL_MAX_LENGTH]

        # 3. Deliberately skipped (icon, below the reader's size floor, ...).
        if info.get("skipped"):
            reason = info.get("skip_reason") or "skipped"
            return "skipped", str(reason)[:STATUS_DETAIL_MAX_LENGTH]

        # 4. Partial success - some pages or sub-extractors did not succeed.
        pages = content.get("pages")
        if isinstance(pages, list) and pages:
            bad = [
                pg for pg in pages
                if isinstance(pg, dict) and (
                    pg.get("error")
                    or str(pg.get("method", "")).startswith(
                        ("conversion_failed", "ocr_failed", "ocr_skipped")
                    )
                )
            ]
            if bad and len(bad) < len(pages):
                return (
                    "partially_processed",
                    f"{len(bad)} of {len(pages)} pages failed or were skipped"
                    [:STATUS_DETAIL_MAX_LENGTH],
                )
            if bad:
                return (
                    "failed",
                    f"all {len(pages)} pages failed or were skipped"
                    [:STATUS_DETAIL_MAX_LENGTH],
                )

        warnings = []

        # 4b. A container that was only partly read. The reader names the
        #     members it could not decode; recording this as 'processed' would
        #     claim the whole archive was read, and recording it as 'failed'
        #     would discard the members that were read. (Only RAR needs an
        #     external decoder, so this is the archive case.)
        members_unreadable = info.get("members_unreadable")
        if isinstance(members_unreadable, dict) and members_unreadable:
            unread_count = sum(
                value for value in members_unreadable.values()
                if isinstance(value, int)
            )
            total = info.get("members_total")
            read = info.get("members_read")
            reasons = ", ".join(sorted(
                str(reason).replace("_", " ") for reason in members_unreadable
            ))
            if total is None:
                detail = (
                    f"{unread_count} archive member(s) could not be read"
                    + (f" ({reasons})" if reasons else "")
                )
            else:
                detail = f"{unread_count} of {total} archive members could not be read"
                if reasons:
                    detail += f" ({reasons})"
                if read is not None:
                    detail += f"; {read} read"
            warnings.append(detail)
        if content.get("ocr_attempted") and not content.get("ocr_successful"):
            warnings.append("ocr attempted but produced no text")
        if info.get("engine_error"):
            warnings.append(f"ocr engine error: {info['engine_error']}")
        if info.get("truncated"):
            warnings.append("input truncated at the configured row limit")
        if warnings:
            return "partially_processed", "; ".join(warnings)[:STATUS_DETAIL_MAX_LENGTH]

        # 5. Clean success. A file with no extractable text was still processed
        #    successfully - that is a different fact from failing to read it.
        if content.get("text") or pages or file_status == "Read":
            return "processed", None
        # The reader knows *why* there was no text (an SVG with no text
        # elements, an icon below the size floor, a scanned page with no OCR
        # engine); reporting the generic phrase loses that. It is still a
        # successful read, so the state stays 'processed'.
        if info.get("reason"):
            return "processed", f"no extractable text: {info['reason']}"[
                :STATUS_DETAIL_MAX_LENGTH
            ]
        return "processed", "no extractable text"

    HIERARCHY_SEPARATOR = "::"

    def _link_extracted_children(
        self,
        content: Optional[Dict[str, Any]],
        parent_path_id: int,
        parent_hierarchy: Optional[str] = None,
    ) -> int:
        """Record the container each extracted child came from (PARENT-01).

        Extracted members are stored while their container is still being
        processed, so at that moment no parent id exists to write. Once the
        container's own row exists this walks ``extracted_files`` and links each
        child, recursing so that archive -> child -> nested child keeps its
        chain. Returns the number of links written.

        Failures are logged, not raised: a child that cannot be linked is still
        a valid, indexed record, and losing the link must not fail the ingest.
        """
        if not parent_path_id or not isinstance(content, dict):
            return 0

        children = content.get("extracted_files")
        if not isinstance(children, list):
            return 0
        if not self.db_service or not getattr(self.db_service, "paths_repo", None):
            return 0

        if parent_hierarchy is None:
            parent_hierarchy = self._hierarchy_of(parent_path_id)

        linked = 0
        for child in children:
            if not isinstance(child, dict):
                continue
            child_id = child.get("database_path_id")
            if not child_id:
                continue

            child_name = (
                (child.get("Metadata") or {}).get("name")
                or os.path.basename(str((child.get("Metadata") or {}).get("path", "")))
                or "unknown"
            )
            # Build the chain from the ROW's own name, not the archive member's,
            # so a row's file_name and hierarchy_path can never contradict each
            # other (consistent by construction and idempotent under repeats).
            stored_name = self._file_name_of(child_id) or child_name
            child_hierarchy = (
                f"{parent_hierarchy}{self.HIERARCHY_SEPARATOR}{stored_name}"
                if parent_hierarchy else stored_name
            )
            try:
                self.db_service.paths_repo.update_lineage(
                    child_id, parent_path_id, child_hierarchy
                )
            except Exception as exc:
                logger.warning(
                    f"[LINEAGE] Could not link child path_id={child_id} to "
                    f"parent path_id={parent_path_id}: {exc}"
                )
                continue

            # Occurrence reconciliation (One Content, Many Contexts): the
            # hierarchy chain is the stable identity of an in-container
            # member.  A re-imported container re-extracts its members to new
            # temporary paths, so the repeat is only recognisable here - if
            # this context already recorded that member, collapse the repeat
            # onto the earlier row (keeping its processing history).  Two
            # members with identical bytes but different names have different
            # chains and both are kept: they are two occurrences.
            effective_child_id = child_id
            try:
                with self.db_service._dedup_session() as dedup:
                    reconciliation = dedup.reconcile_occurrence(
                        child_id, child_hierarchy
                    )
                if reconciliation.get("removed"):
                    effective_child_id = reconciliation["kept_path_id"]
                    logger.info(
                        f"[LINEAGE] Repeat occurrence collapsed: "
                        f"'{child_hierarchy}' kept path_id={effective_child_id}"
                    )
                else:
                    linked += 1
            except Exception as exc:
                logger.warning(
                    f"[LINEAGE] Occurrence reconciliation failed for "
                    f"path_id={child_id}: {exc}"
                )
                linked += 1

            # Recurse so nested containers keep the full chain.  Descendants
            # attach to the surviving row so a repeat import can never leave
            # them parented to a removed row.
            linked += self._link_extracted_children(
                child.get("Content"), effective_child_id, child_hierarchy
            )

        if linked:
            logger.info(
                f"[LINEAGE] Linked {linked} extracted item(s) under "
                f"path_id={parent_path_id}"
            )
        return linked

    def _file_name_of(self, path_id: int) -> str:
        """The stored file_name for a path, or '' if it cannot be read."""
        try:
            row = self.db_service.paths_repo.get_lineage(path_id)
        except Exception:
            return ""
        if not row:
            return ""
        if isinstance(row, dict):
            return row.get("file_name") or ""
        if isinstance(row, (tuple, list)):
            return row[0] or ""
        return ""

    def _hierarchy_of(self, path_id: int) -> str:
        """The stored hierarchy chain for a path, defaulting to its file name."""
        try:
            row = self.db_service.paths_repo.get_lineage(path_id)
        except Exception:
            return ""
        if not row:
            return ""
        if isinstance(row, dict):
            return row.get("hierarchy_path") or row.get("file_name") or ""
        if isinstance(row, (tuple, list)) and len(row) >= 2:
            return row[1] or row[0] or ""
        return ""

    def _build_extraction_provenance(
        self, content: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Assemble per-extractor provenance for storage in paths.

        The readers already report how each piece of data was derived; this
        collects that into the JSONB shape persisted by migration 0007, so
        recognised text is never indistinguishable from authored text and a
        consumer can tell which engine produced what.

        Returns None when the content carries no provenance, which is the
        truthful value for extractors that do not report any - never an empty
        dict, which would claim provenance was recorded when it was not.
        """
        if not content or not isinstance(content, dict):
            return None

        provenance: Dict[str, Any] = {}

        # ---- OCR (images, and per-page for PDFs) ----
        if content.get("ocr_attempted") is not None or "pages" in content:
            ocr = self._ocr_provenance(content)
            if ocr:
                provenance["ocr"] = ocr

        # ---- type detection, when the reader recorded it ----
        # The forensic record of identity: original name and extension,
        # detected format (exact variant), MIME type, version and container
        # features, and every declared-vs-detected discrepancy. Recorded for
        # every artifact, not only the ones whose reader happened to report it.
        detection = _detection_record(content)
        if detection:
            provenance["detection"] = detection

        # ---- scale: was a cap reached? ----
        # Not diagnostics about a failure but about how much of the container
        # was read: a sheet stopped at the cell cap and a sheet that ended
        # there both store text, and only this record keeps the difference.
        scale = {key: content.get(key)
                 for key in ("truncated", "cells_read", "cells_available",
                             "rows_read", "members_skipped")
                 if content.get(key) is not None}
        # A reader that caps per container reports the cap per container; sum
        # it here rather than losing it, and let the total say "a cap was hit"
        # even when no single sheet flags itself.
        sheets = content.get("sheets")
        if isinstance(sheets, dict):
            sheet_list = [s for s in sheets.values() if isinstance(s, dict)]
            if sheet_list:
                if "cells_read" not in scale:
                    read = [s.get("cells_read") for s in sheet_list
                            if isinstance(s.get("cells_read"), int)]
                    if read:
                        scale["cells_read"] = sum(read)
                if "cells_available" not in scale:
                    available = [s.get("cells_available") for s in sheet_list
                                 if isinstance(s.get("cells_available"), int)]
                    if available:
                        scale["cells_available"] = sum(available)
                if "truncated" not in scale and any(s.get("truncated")
                                                    for s in sheet_list):
                    scale["truncated"] = True
        if scale:
            provenance["scale"] = scale

        # ---- container forensics ----
        # PDF incremental updates are a *count* (how many times the file was
        # appended to, so how many earlier revisions are still inside it), not
        # a name and not a sentence. Round 17 recorded this key as dropped
        # text; measuring the reader showed there is no text in it, so it is
        # stored here as structure instead of being pushed into the body where
        # "3" would be noise.
        forensics: Dict[str, Any] = {}
        revisions = content.get("revisions")
        if isinstance(revisions, dict) and revisions:
            forensics["revisions"] = {
                key: revisions.get(key) for key in
                ("incremental_updates", "eof_markers", "scan_bytes", "scan_truncated")
                if revisions.get(key) is not None
            }
        if forensics:
            provenance["forensics"] = forensics

        # ---- media containers ----
        # Duration, streams and the container's own tags are structure, so they
        # are queryable here; the searchable copy is in the text channel (see
        # _media_metadata_text). speech_extracted is recorded even when it is
        # False: "we read the container and not the speech" is a fact about the
        # artifact that must survive storage.
        media: Dict[str, Any] = {}
        for key in ("duration_seconds", "duration_formatted", "resolution",
                    "width", "height", "codec", "fps", "audio_codec",
                    "audio_sample_rate", "has_audio", "metadata_source",
                    "metadata_only", "speech_extracted", "missing_dependency"):
            if content.get(key) is not None:
                media[key] = content.get(key)
        if isinstance(content.get("tags"), dict) and content["tags"]:
            media["tags"] = content["tags"]
        if media:
            provenance["media"] = media

        # ---- extraction diagnostics ----
        info = content.get("extraction_info")
        if isinstance(info, dict) and info:
            diagnostics = {
                key: info.get(key)
                for key in (
                    "error", "reason", "skipped", "skip_reason",
                    "engine_error", "preprocessing",
                    # Archive containers: how much of the member set was read
                    # and what stopped the rest (a missing decoder, most
                    # often), so the condition is queryable after the run.
                    "decoder_missing", "members_total", "members_read",
                    "members_unreadable",
                    # Scale: whether a cap was reached, and how much of the
                    # container was read. A spreadsheet stopped at 250 000
                    # cells and one that ended there both store text; without
                    # these keys the archive cannot tell them apart, and a
                    # capped read silently claims completeness.
                    "truncated", "cells_read", "cells_available",
                    "rows_read", "members_skipped",
                    # HTML: where a document's characters went (visible text
                    # versus inline script/style and excluded comments).
                    "visible_text_chars", "script_chars", "style_chars",
                    "comment_chars_excluded", "inline_code_truncated",
                    "embedded_code_chars",
                )
                if info.get(key) is not None
            }
            if diagnostics:
                provenance["diagnostics"] = diagnostics

        return provenance or None

    @staticmethod
    def _ocr_provenance(content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """OCR provenance for either an image result or a multi-page PDF."""
        pages = content.get("pages")
        if isinstance(pages, list) and pages:
            ocr_pages = [p for p in pages if str(p.get("method", "")).startswith("ocr_")]
            if not ocr_pages:
                return None
            engines = sorted({p.get("ocr_engine") for p in ocr_pages if p.get("ocr_engine")})
            confidences = [
                p["ocr_confidence"] for p in ocr_pages
                if isinstance(p.get("ocr_confidence"), (int, float))
            ]
            provenance = {
                "engine": engines[0] if len(engines) == 1 else (engines or None),
                "engines": engines,
                "engine_version": next(
                    (p.get("ocr_engine_version") for p in ocr_pages
                     if p.get("ocr_engine_version")), None),
                "derived": True,
                "ocr_pages": len(ocr_pages),
                "total_pages": len(pages),
                "confidence": (sum(confidences) / len(confidences)) if confidences else None,
                "language": next(
                    (p.get("ocr_language") for p in ocr_pages
                     if p.get("ocr_language")), None),
                "input_variant": next(
                    (p.get("ocr_input_variant") for p in ocr_pages
                     if p.get("ocr_input_variant")), None),
            }
            rotated = [p.get("page_number") for p in ocr_pages if p.get("ocr_rotation")]
            if rotated:
                # Pages read after turning them (their text was sideways or
                # upside down in the file).
                provenance["rotated_pages"] = rotated
            missing = sorted({code for p in ocr_pages
                              for code in (p.get("missing_ocr_languages") or [])})
            if missing:
                # Requested languages whose models were not installed: these
                # pages were read without them (installed_ocr_languages).
                provenance["missing_languages"] = missing
            return provenance

        if content.get("ocr_attempted") is None:
            return None
        provenance = {
            "engine": content.get("ocr_engine"),
            "engine_version": content.get("ocr_engine_version"),
            "derived": bool(content.get("ocr_derived")),
            "attempted": bool(content.get("ocr_attempted")),
            "successful": bool(content.get("ocr_successful")),
            "confidence": content.get("ocr_confidence"),
            "language": content.get("ocr_language"),
            "input_variant": content.get("ocr_input_variant"),
        }
        if content.get("ocr_rotation"):
            # Degrees (counter-clockwise) the image was turned before reading.
            provenance["rotation"] = content["ocr_rotation"]
        info = content.get("extraction_info") or {}
        if info.get("missing_ocr_languages"):
            # Requested languages whose models were not installed: the image
            # was read without them (installed_ocr_languages).
            provenance["missing_languages"] = list(info["missing_ocr_languages"])
        blocks_error = info.get("ocr_blocks_error")
        if blocks_error:
            # Why ``confidence`` is None although text was recognised.
            provenance["confidence_error"] = blocks_error
        return provenance

    def _extract_coordinates(self, content: Dict) -> Optional[str]:
        """
        Extract GPS coordinates from content
        
        Handles multiple coordinate formats:
        - Direct location dict with coordinates string
        - Location dict with latitude/longitude floats
        - Coordinates in metadata
        """
        try:
            # Check for location data in images (from EXIF GPS data)
            location = content.get('location')
            if location and isinstance(location, dict):
                # Try coordinates string first (format: "lat, lon")
                coords = location.get('coordinates')
                if coords:
                    return str(coords)
                
                # Try latitude/longitude floats
                lat = location.get('latitude')
                lon = location.get('longitude')
                if lat is not None and lon is not None:
                    try:
                        return f"{float(lat):.6f}, {float(lon):.6f}"
                    except (ValueError, TypeError):
                        pass
            
            # Check for coordinates in metadata
            coords = content.get('coordinates')
            if coords:
                return str(coords)
            
            # Check for GPS coordinates in metadata
            gps = content.get('gps')
            if gps and isinstance(gps, dict):
                lat = gps.get('latitude')
                lon = gps.get('longitude')
                if lat is not None and lon is not None:
                    try:
                        return f"{float(lat):.6f}, {float(lon):.6f}"
                    except (ValueError, TypeError):
                        pass
        
        except Exception as e:
            logger.debug(f"Error extracting coordinates: {e}")
        
        return None
    
    def _extract_date_from_text(self, text: str, processor) -> Optional[date]:
        """
        Extract the first date mentioned in content text.
        
        content_date stores a date mentioned in the content itself.
        If no date is mentioned, it will be empty (None).
        
        Args:
            text: Text content to search for dates
            processor: ContentProcessor instance with date patterns
        
        Returns:
            date object if date found, None otherwise
        """
        if not text or not processor:
            return None
        
        try:
            # Extract dates using ContentProcessor patterns
            dates_found = []
            
            # Try ISO date pattern first (most reliable)
            for match in processor.patterns['date_iso'].finditer(text):
                date_str = match.group(0)
                try:
                    # Parse ISO format: YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
                    date_str_clean = date_str.replace('/', '-').replace('.', '-')
                    parts = date_str_clean.split('-')
                    if len(parts) == 3:
                        year = int(parts[0])
                        month = int(parts[1])
                        day = int(parts[2])
                        parsed_date = date(year, month, day)
                        dates_found.append(parsed_date)
                        break  # Use first valid date found
                except (ValueError, IndexError):
                    continue
            
            # If no ISO date found, try comprehensive date pattern
            if not dates_found:
                for match in processor.patterns['date'].finditer(text):
                    date_str = match.group(0)
                    try:
                        # Try to parse various date formats
                        # Remove common separators and try parsing
                        date_str_clean = date_str.strip()
                        
                        # Try ISO format first
                        if '/' in date_str_clean or '-' in date_str_clean or '.' in date_str_clean:
                            # Try to extract YYYY-MM-DD format
                            import re
                            iso_match = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', date_str_clean)
                            if iso_match:
                                year = int(iso_match.group(1))
                                month = int(iso_match.group(2))
                                day = int(iso_match.group(3))
                                parsed_date = date(year, month, day)
                                dates_found.append(parsed_date)
                                break
                        
                        # Try parsing with dateutil if available (handles more formats)
                        try:
                            from dateutil import parser
                            parsed_date = parser.parse(date_str_clean, fuzzy=False).date()
                            dates_found.append(parsed_date)
                            break
                        except ImportError:
                            # dateutil not available, skip
                            pass
                        except (ValueError, TypeError):
                            # Parsing failed, continue
                            continue
                    except (ValueError, IndexError, TypeError):
                        continue
            
            # Return first valid date found, or None
            return dates_found[0] if dates_found else None
            
        except Exception as e:
            logger.debug(f"Error extracting date from text: {e}")
            return None
    
    def _extract_title(self, result: Dict, file_info: Dict) -> str:
        """Extract title from result or filename"""
        # Try to get title from content
        content = result.get('Content', {})
        
        # Check various title fields
        title = (content.get('title') or
                content.get('subject') or
                content.get('name') or
                file_info.get('name', 'untitled'))
        
        if isinstance(title, list):
            title = title[0] if title else 'untitled'
        
        return str(title)
    
    #: Reader keys that describe a media container rather than text inside it.
    _MEDIA_FIELDS = (
        ("duration_formatted", "Duration"),
        ("resolution", "Resolution"),
        ("codec", "Video codec"),
        ("fps", "Frame rate"),
        ("frame_count", "Frame count"),
        ("audio_codec", "Audio codec"),
        ("audio_sample_rate", "Audio sample rate"),
        ("has_audio", "Has audio"),
        ("quality", "Quality"),
        ("file_size_formatted", "File size"),
        ("metadata_source", "Metadata source"),
    )

    def _media_metadata_text(self, content: Dict) -> str:
        """The container facts of an audio/video artifact, as stored text.

        A video or audio read produces metadata, not prose, and the text
        builder used to drop all of it: the readers reported duration,
        resolution, codec and container tags, and the store kept zero
        characters for every media file in the corpus. Duration and codec are
        facts an examiner searches for, so they are stored as labelled lines;
        the honest scope of the read is stored with them.
        """
        if not isinstance(content, dict):
            return ""
        if not (content.get("extraction_scope") == "container_metadata"
                or content.get("duration_seconds") is not None
                or content.get("resolution")
                or content.get("audio_codec")):
            return ""
        if any(isinstance(content.get(key), list) and content.get(key)
               for key in ("pages", "slides", "sheets", "paragraphs")):
            # A container that also yielded text (an embedded transcript, for
            # instance) is handled by its own branch; the metadata is added
            # alongside, not instead.
            pass
        parts: List[str] = []
        lines: List[str] = []
        for key, label in self._MEDIA_FIELDS:
            value = content.get(key)
            if value in (None, "", False) and key != "has_audio":
                continue
            if key == "has_audio" and value is None:
                continue
            if key == "duration_formatted" and content.get("duration_seconds"):
                value = f"{value} ({content['duration_seconds']:.2f} s)"
            lines.append(f"{label}: {value}")
        tags = content.get("tags")
        if isinstance(tags, dict):
            for key, value in tags.items():
                if value:
                    lines.append(f"Tag {key}: {value}")
        if content.get("speech_extracted") is False:
            lines.append("Speech: not extracted (no transcription engine)")
        if lines:
            parts.append("\n".join(lines))
        note = content.get("extraction_note")
        if isinstance(note, str) and note.strip():
            parts.append(note.strip())
        return "\n".join(parts)

    def _extract_text_from_content(self, content: Dict) -> str:
        """
        Extract text from content dictionary (all types)
        PRESERVES SPATIAL ORDER through structured extraction based on location
        
        ORGANIZATION STRATEGY:
        - Content is extracted in spatial order: top-to-bottom, left-to-right
        - Table/Excel cells are separated by TAB characters (\t) for clear parsing
        - This enables accurate display reconstruction of tables with proper column boundaries
        - Headers and metadata use pipe (|) or newline separators
        - All content remains text-based for storage compatibility
        - Spatial coordinates are used when available to ensure proper reading order
        
        Args:
            content: Content dictionary from file processing
        
        Returns:
            Combined text string in spatial order with organized delimiters
        """
        text_parts = []
        
        try:
            # Use ordered list to preserve document structure and spatial location
            # Format: (spatial_position, type, index, text, coordinates)
            # spatial_position: numeric value for sorting (page_number * 1000000 + y_coord * 1000 + x_coord)
            # coordinates: optional dict with x, y, width, height for spatial reference
            ordered_content = []

            # 0. MEDIA CONTAINER METADATA
            # Before any body text: a media artifact's facts are its content.
            media_text = self._media_metadata_text(content)
            if media_text:
                ordered_content.append((-1, 'media_metadata', 0, media_text, None))

            # 1. IMAGES (OCR text only - no metadata)
            # Store ONLY the OCR text, preserving formatting
            # Detect images by checking for OCR-related keys (ocr_attempted, ocr_successful, ocr_language)
            is_image_content = (
                'ocr_attempted' in content or 
                'ocr_successful' in content or 
                'ocr_language' in content or
                'extraction_info' in content
            )
            
            if is_image_content:
                # Extract ONLY the OCR text - no metadata
                img_text = content.get('text', '')
                ocr_coords_data = None
                
                if 'ocr_coordinates' in content and isinstance(content['ocr_coordinates'], list):
                    ocr_coords_data = content['ocr_coordinates']
                
                # Get extraction info for logging
                extraction_info = content.get('extraction_info', {})
                ocr_attempted = content.get('ocr_attempted', False)
                ocr_successful = content.get('ocr_successful', False)
                ocr_language = content.get('ocr_language', 'unknown')
                
                # Store OCR text if extracted (formatting preserved)
                if img_text and img_text.strip():
                    text_to_store = img_text.rstrip()
                    text_length = len(text_to_store)
                    word_count = len(text_to_store.split())
                    coord_count = len(ocr_coords_data) if ocr_coords_data else 0
                    
                    logger.info(
                        f"[STORAGE] 📝 Image OCR Content - "
                        f"Extracted: {text_length} chars, {word_count} words | "
                        f"Coordinates: {coord_count} words | "
                        f"Language: {ocr_language} | "
                        f"Attempted: {ocr_attempted} | "
                        f"Successful: {ocr_successful}"
                    )
                    
                    # Store only OCR text (no metadata)
                    ordered_content.append((0, 'direct_text', 0, text_to_store, ocr_coords_data))
                else:
                    # Log why no text was stored. Never print a placeholder:
                    # if a reader states no reason, say that the reader stated
                    # none - "unknown" reads like a property of the file.
                    reason = extraction_info.get('reason') or (
                        'no reason reported by the reader'
                    )
                    error = extraction_info.get('error', '')
                    skipped = extraction_info.get('skipped', False)
                    
                    if skipped:
                        logger.info(
                            f"[STORAGE] ⚠️  Image OCR Content - Skipped: {extraction_info.get('skip_reason', 'unknown')}"
                        )
                    elif error:
                        logger.warning(
                            f"[STORAGE] ⚠️  Image OCR Content - Error: {error}"
                        )
                    else:
                        logger.info(
                            f"[STORAGE] ⚠️  Image OCR Content - No text extracted | "
                            f"Attempted: {ocr_attempted} | "
                            f"Successful: {ocr_successful} | "
                            f"Reason: {reason}"
                        )
            
            # 2. EXTRACTED IMAGES (from Office docs)
            if 'extracted_images' in content:
                for idx, img in enumerate(content.get('extracted_images', [])):
                    if isinstance(img, dict) and 'text' in img:
                        # Spatial position based on image index (maintains extraction order)
                        spatial_pos = 1000000 + idx * 1000
                        ordered_content.append((spatial_pos, 'extracted_image', idx, img['text'], None))
            
            # 3. EMAIL CONTENT (from email_content key - processed by _process_email_result)
            if 'email_content' in content:
                email_data = content['email_content']
                
                # Single message dict
                if isinstance(email_data, dict):
                    msg_text = self._extract_email_message_text(email_data)
                    if msg_text:
                        # Email messages at position 2000000 (maintains chronological order)
                        ordered_content.append((2000000, 'email_message', 0, msg_text, None))
                    else:
                        # Log if message has no extractable text (helps diagnose PST issues)
                        logger.debug(f"Email message has no extractable text: {email_data.get('subject', 'N/A')[:500]}")
                
                # Multiple messages list (PST/MBOX files)
                elif isinstance(email_data, list):
                    messages_processed = 0
                    messages_with_content = 0
                    for idx, msg in enumerate(email_data):
                        messages_processed += 1
                        msg_text = self._extract_email_message_text(msg)
                        if msg_text:
                            # Maintain message order: 2000000 + index * 100
                            spatial_pos = 2000000 + idx * 100
                            ordered_content.append((spatial_pos, 'email_message', idx, msg_text, None))
                            messages_with_content += 1
                        else:
                            # Log if message has no extractable text
                            logger.debug(f"Email message {idx} has no extractable text: {msg.get('subject', 'N/A')[:500]}")
                    
                    # Log summary for PST/MBOX files
                    if messages_processed > 0:
                        logger.info(f"Processed {messages_processed} email messages, {messages_with_content} with content")
            
            # 4. EMAIL MESSAGE (direct from email readers - EML/MSG single message)
            if 'message' in content and isinstance(content['message'], dict):
                msg_text = self._extract_email_message_text(content['message'])
                if msg_text:
                    ordered_content.append((2000000, 'email_message', 0, msg_text, None))
            
            # 5. EMAIL MESSAGES (from messages key - MBOX/PST multiple messages)
            if 'messages' in content and isinstance(content['messages'], list):
                for idx, msg in enumerate(content['messages']):
                    msg_text = self._extract_email_message_text(msg)
                    if msg_text:
                        # Maintain message order: 2000000 + index * 100
                        spatial_pos = 2000000 + idx * 100
                        ordered_content.append((spatial_pos, 'email_message', idx, msg_text, None))
            
            # 6. PDF PAGES
            if 'pages' in content and isinstance(content['pages'], list):
                # Extract PDF metadata first (if available)
                pdf_metadata_parts = []
                if 'num_pages' in content:
                    pdf_metadata_parts.append(f"Total Pages: {content['num_pages']}")
                if 'is_encrypted' in content and content['is_encrypted']:
                    pdf_metadata_parts.append("Encrypted: Yes")
                if 'ocr_used' in content:
                    pdf_metadata_parts.append(f"OCR Used: {'Yes' if content['ocr_used'] else 'No'}")
                if 'ocr_languages' in content:
                    pdf_metadata_parts.append(f"OCR Languages: {', '.join(content['ocr_languages'])}")
                if 'metadata' in content and isinstance(content['metadata'], dict):
                    meta = content['metadata']
                    if meta.get('title'):
                        pdf_metadata_parts.append(f"Title: {meta['title']}")
                    if meta.get('author'):
                        pdf_metadata_parts.append(f"Author: {meta['author']}")
                    if meta.get('subject'):
                        pdf_metadata_parts.append(f"Subject: {meta['subject']}")
                    if meta.get('creator'):
                        pdf_metadata_parts.append(f"Creator: {meta['creator']}")
                    if meta.get('producer'):
                        pdf_metadata_parts.append(f"Producer: {meta['producer']}")
                    # The rest of the document information dictionary. Keywords
                    # are words an analyst searches for and the two dates say
                    # when the document was written and last changed; all five
                    # were read by the reader and dropped here. Labelled, so a
                    # consumer can tell them from body text.
                    for key, label in (("keywords", "Keywords"),
                                       ("creationDate", "Created"),
                                       ("modDate", "Modified"),
                                       ("format", "PDF version"),
                                       ("trapped", "Trapped")):
                        value = meta.get(key)
                        if isinstance(value, (list, tuple)):
                            value = ", ".join(str(item) for item in value if item)
                        if value and str(value).strip():
                            pdf_metadata_parts.append(f"{label}: {str(value).strip()}")
                
                if pdf_metadata_parts:
                    # Metadata at position 0 (before all pages)
                    ordered_content.append((0, 'pdf_metadata', 0, '\n'.join(pdf_metadata_parts), None))
                
                # Sort pages by page_number to ensure correct order
                sorted_pages = sorted(
                    [p for p in content['pages'] if isinstance(p, dict)],
                    key=lambda x: x.get('page_number', 0)
                )
                
                for page in sorted_pages:
                    # Extract page text - handle None, empty strings, and ensure string type
                    page_text = page.get('text')
                    if page_text is None:
                        page_text = ""
                    else:
                        page_text = str(page_text)
                    
                    # Extract page metadata
                    page_meta_parts = []
                    page_number = page.get('page_number', 0)
                    if page_number:
                        page_meta_parts.append(f"Page {page_number}")
                    
                    method = page.get('method', '')
                    if method:
                        page_meta_parts.append(f"Method: {method}")
                    
                    text_length = page.get('text_length', 0)
                    if text_length:
                        page_meta_parts.append(f"Length: {text_length} chars")
                    
                    error = page.get('error')
                    if error:
                        page_meta_parts.append(f"Error: {error}")
                    
                    # Combine page metadata with text
                    if page_meta_parts:
                        page_header = ' | '.join(page_meta_parts) + '\n'
                        page_text = page_header + page_text
                    
                    # Calculate spatial position: page_number * 1000000 ensures pages are ordered
                    # This ensures pages are processed in order (1, 2, 3, ...)
                    spatial_position = page_number * 1000000 if page_number else 3000000
                    
                    # Include all pages (even empty ones) to preserve page order
                    # Format: (spatial_position, type, index, text, coordinates)
                    ordered_content.append((spatial_position, 'pdf_page', page_number, page_text, None))
            
            # 7. EXCEL/ODS SHEETS
            if 'sheets' in content and isinstance(content['sheets'], dict):
                # Process sheets in order (preserve sheet order from document)
                sheet_items = list(content['sheets'].items())
                for sheet_idx, (sheet_name, sheet_data) in enumerate(sheet_items):
                    if isinstance(sheet_data, dict):
                        # Extract sheet name and metadata as header
                        sheet_header_parts = []
                        if sheet_name:
                            sheet_header_parts.append(f"Sheet: {sheet_name}")
                        
                        # Add metadata if available
                        if 'max_row' in sheet_data:
                            sheet_header_parts.append(f"Rows: {sheet_data['max_row']}")
                        if 'max_column' in sheet_data:
                            sheet_header_parts.append(f"Columns: {sheet_data['max_column']}")
                        
                        sheet_header = '\n'.join(sheet_header_parts) + '\n' if sheet_header_parts else ""
                        
                        # Extract headers if available
                        # Use tab delimiter for clear column separation (easier to parse for display)
                        headers_text = ""
                        if 'headers' in sheet_data and isinstance(sheet_data['headers'], list):
                            header_cells = [str(h) if h is not None else '' for h in sheet_data['headers']]
                            headers_text = '\t'.join(header_cells) + '\n'
                        
                        # Extract data if available - ensure row order is preserved
                        sheet_text = ""
                        if 'data' in sheet_data:
                            # Blank rows are not stored as rows (they are not
                            # content), but they are addresses: the reader
                            # records which row numbers it kept so the gap can
                            # be written back as empty lines. Without it a value
                            # in Sheet1!D6 was cited as Sheet1!D4 - the shift
                            # is exactly the number of blank rows above it.
                            sheet_text = self._extract_sheet_text(
                                sheet_data['data'], sheet_data.get('row_numbers'))
                        
                        # Combine all parts
                        combined_sheet_text = sheet_header + headers_text + sheet_text
                        
                        # Calculate spatial position: 4000000 + sheet_index * 10000
                        # This ensures sheets are processed in order, and rows within sheets maintain order
                        spatial_position = 4000000 + sheet_idx * 10000
                        
                        # Include all sheets (even empty ones) to preserve sheet order
                        # Format: (spatial_position, type, index, text, coordinates)
                        ordered_content.append((spatial_position, 'excel_sheet', sheet_idx, combined_sheet_text, None))
            
            # 7.5. WORD DOCUMENT ELEMENTS (in original order - paragraphs and tables interleaved)
            # This preserves the exact location of text, tables, and other elements as they appear in the document
            if 'elements' in content and isinstance(content['elements'], list):
                # Process elements in their original document order
                for element in content['elements']:
                    if not isinstance(element, dict):
                        continue
                    
                    element_type = element.get('type', '')
                    element_position = element.get('position', 0)
                    
                    if element_type == 'paragraph':
                        para_text = element.get('text', '')
                        style = element.get('style', '')
                        
                        # Add style information if not Normal
                        if style and style != 'Normal':
                            para_text = f"[Style: {style}] {para_text}"
                        
                        # Calculate spatial position: 5000000 + position * 10
                        # This ensures paragraphs appear in document order
                        spatial_position = 5000000 + element_position * 10
                        ordered_content.append((spatial_position, 'word_paragraph', element.get('index', 0), para_text, None))
                    
                    elif element_type == 'table':
                        table_number = element.get('table_number', 0)
                        table_header_parts = [f"Table {table_number}"]
                        
                        # Extract rows if available
                        table_text = ""
                        if 'rows' in element and isinstance(element['rows'], list):
                            table_text = self._extract_table_text(element['rows'])
                        
                        table_header = ' | '.join(table_header_parts) + '\n' if table_header_parts else ""
                        combined_table_text = table_header + table_text if table_text else table_header
                        
                        # Calculate spatial position: 5000000 + position * 10 + 1
                        # Tables get position + 1 to appear after paragraphs at same position
                        spatial_position = 5000000 + element_position * 10 + 1
                        ordered_content.append((spatial_position, 'word_table', table_number - 1, combined_table_text, None))
            
            # 8. TABLES (legacy format - for backward compatibility)
            if 'tables' in content and isinstance(content['tables'], list) and 'elements' not in content:
                for idx, table in enumerate(content['tables']):
                    if isinstance(table, dict):
                        # Extract table caption/title and number if available
                        table_header_parts = []
                        table_number = table.get('table_number', idx + 1)
                        table_header_parts.append(f"Table {table_number}")
                        
                        if 'caption' in table:
                            table_header_parts.append(f"Caption: {table['caption']}")
                        elif 'title' in table:
                            table_header_parts.append(f"Title: {table['title']}")
                        
                        table_header = ' | '.join(table_header_parts) + '\n' if table_header_parts else ""
                        
                        # Extract rows if available
                        if 'rows' in table:
                            table_text = self._extract_table_text(table['rows'])
                            combined_table_text = table_header + table_text if table_text else table_header
                            # Include all tables (even empty ones) to preserve table order
                            spatial_position = 5000000 + idx * 10000
                            ordered_content.append((spatial_position, 'table', idx, combined_table_text, None))
                        elif table_header.strip():
                            # Table exists but has no rows - still include header
                            spatial_position = 5000000 + idx * 10000
                            ordered_content.append((spatial_position, 'table', idx, table_header, None))
            
            # 9. POWERPOINT/ODP SLIDES
            if 'slides' in content and isinstance(content['slides'], list):
                # Extract total slides count if available
                if 'total_slides' in content:
                    ordered_content.append((6000000, 'slides_metadata', 0, f"Total Slides: {content['total_slides']}", None))
                
                for slide_idx, slide in enumerate(content['slides']):
                    if not isinstance(slide, dict):
                        continue
                    
                    slide_number = slide.get('slide_number', slide_idx + 1)
                    
                    # Process slide elements in their original order (if available)
                    if 'elements' in slide and isinstance(slide['elements'], list):
                        # Explicit slide boundary marker: the display layer
                        # turns these into slide-by-slide navigation.
                        ordered_content.append(
                            (6000000 + slide_number * 100000 - 1, 'slide_header', slide_idx,
                             f"Slide {slide_number}", None))
                        # New format: elements are ordered by spatial position
                        for element in slide['elements']:
                            if not isinstance(element, dict):
                                continue
                            
                            element_type = element.get('type', '')
                            element_position = element.get('position', 0)
                            
                            # Calculate spatial position: slide_number * 100000 + element_position * 100
                            spatial_position = 6000000 + slide_number * 100000 + element_position * 100
                            
                            if element_type == 'text' or element_type == 'text_frame':
                                text_content = element.get('text', '')
                                if text_content:
                                    ordered_content.append((spatial_position, 'slide_text', slide_idx, text_content, None))
                            
                            elif element_type == 'table':
                                table_number = element.get('table_number', 0)
                                table_header = f"Table {table_number}\n"
                                
                                table_text = ""
                                if 'rows' in element and isinstance(element['rows'], list):
                                    table_text = self._extract_table_text(element['rows'])
                                
                                combined_table_text = table_header + table_text if table_text else table_header
                                # Tables get +1 to appear after text at same position
                                ordered_content.append((spatial_position + 1, 'slide_table', slide_idx, combined_table_text, None))
                            
                            elif element_type == 'image':
                                image_name = element.get('image_name', '')
                                image_text = f"[Image: {image_name}]"
                                # Images get +2 to appear after tables at same position
                                ordered_content.append((spatial_position + 2, 'slide_image', slide_idx, image_text, None))
                    
                    # Legacy format: extract from texts array (for backward compatibility)
                    else:
                        slide_header_parts = [f"Slide {slide_number}"]
                        slide_texts = []
                        
                        if 'title' in slide:
                            slide_texts.append(f"Title: {slide['title']}")
                        if 'texts' in slide:
                            slide_texts.extend(str(t) for t in slide['texts'] if t)
                        if 'text' in slide:
                            slide_texts.append(str(slide['text']))
                        if 'notes' in slide:
                            slide_texts.append(f"Notes: {slide['notes']}")
                        
                        slide_header = ' | '.join(slide_header_parts) + '\n' if slide_header_parts else ""
                        slide_text = '\n'.join(slide_texts) if slide_texts else ""
                        combined_slide_text = slide_header + slide_text
                        
                        # Spatial position for legacy format
                        spatial_position = 6000000 + slide_number * 100000
                        ordered_content.append((spatial_position, 'slide', slide_idx, combined_slide_text, None))
            
            # 9b. LEGACY WORD .doc flat text (when paragraphs/elements were not built)
            if (
                'text' in content
                and isinstance(content.get('text'), str)
                and content['text'].strip()
                and 'elements' not in content
                and 'paragraphs' not in content
            ):
                ordered_content.append(
                    (5000000, 'word_text', 0, content['text'].strip(), None)
                )

            # 10. WORD/ODT PARAGRAPHS (legacy format - only if elements not available)
            if 'paragraphs' in content and 'elements' not in content:
                paragraphs = content['paragraphs']
                
                # List of paragraph dicts
                if isinstance(paragraphs, list):
                    for idx, para in enumerate(paragraphs):
                        if isinstance(para, dict):
                            para_text = para.get('text', '')
                            # Extract paragraph style if available
                            style = para.get('style', '')
                            if style and style != 'Normal':
                                para_text = f"[Style: {style}] {para_text}"
                        else:
                            para_text = str(para)
                        
                        # Include all paragraphs (even empty ones) to preserve document structure
                        # Empty paragraphs represent spacing/formatting in the document
                        if para_text is not None:
                            spatial_position = 5000000 + idx * 10
                            ordered_content.append((spatial_position, 'paragraph', idx, para_text, None))
                
                # Also handle paragraphs as simple list of strings (from DOC files)
                elif isinstance(paragraphs, list) and paragraphs and isinstance(paragraphs[0], str):
                    for idx, para_text in enumerate(paragraphs):
                        if para_text is not None:
                            spatial_position = 5000000 + idx * 10
                            ordered_content.append((spatial_position, 'paragraph', idx, str(para_text), None))
            
            # 11. DIRECT TEXT CONTENT (from text files, DOC files, etc.)
            # The text is stored VERBATIM - file statistics (line/word counts,
            # encoding) belong to extraction provenance, not to the displayed
            # document: injecting them here polluted every text file's display
            # and made words like "Encoding" match every text file in search.
            direct_text_stored = False
            if 'content' in content:
                direct_content = content['content']
                if isinstance(direct_content, str) and direct_content.strip():
                    ordered_content.append((8, 'direct_content', 0, direct_content))
                    direct_text_stored = True
            
            # 12. LINES (from text files) - only when the verbatim text was
            # not already stored above (the reader returns both shapes; storing
            # both duplicated every text file's content).
            if not direct_text_stored and 'lines' in content and isinstance(content['lines'], list):
                # Include all lines (even empty ones) to preserve file structure
                lines_text = '\n'.join(str(line) for line in content['lines'])
                ordered_content.append((9, 'lines', 0, lines_text))
            
            # 13. READABLE CONTENT (ICS files)
            if 'readable_content' in content:
                readable = content['readable_content']
                if isinstance(readable, str) and readable.strip():
                    ordered_content.append((10, 'readable_content', 0, readable))
            
            # 13b. ICS EVENTS, TODOS, JOURNALS (structured data)
            if 'events' in content and isinstance(content['events'], list):
                for idx, event in enumerate(content['events']):
                    if isinstance(event, dict):
                        event_parts = []
                        if event.get('summary'):
                            event_parts.append(f"Event: {event['summary']}")
                        if event.get('description'):
                            event_parts.append(f"Description: {event['description']}")
                        if event.get('location'):
                            event_parts.append(f"Location: {event['location']}")
                        if event.get('dtstart'):
                            event_parts.append(f"Start: {event['dtstart']}")
                        if event.get('dtend'):
                            event_parts.append(f"End: {event['dtend']}")
                        if event.get('organizer'):
                            event_parts.append(f"Organizer: {event['organizer']}")
                        if event.get('attendee'):
                            event_parts.append(f"Attendees: {', '.join(event['attendee'])}")
                        if event_parts:
                            ordered_content.append((10, 'ics_event', idx, '\n'.join(event_parts)))
            
            if 'todos' in content and isinstance(content['todos'], list):
                for idx, todo in enumerate(content['todos']):
                    if isinstance(todo, dict):
                        todo_parts = []
                        if todo.get('summary'):
                            todo_parts.append(f"Todo: {todo['summary']}")
                        if todo.get('description'):
                            todo_parts.append(f"Description: {todo['description']}")
                        if todo.get('due'):
                            todo_parts.append(f"Due: {todo['due']}")
                        if todo.get('status'):
                            todo_parts.append(f"Status: {todo['status']}")
                        if todo_parts:
                            ordered_content.append((10, 'ics_todo', idx, '\n'.join(todo_parts)))
            
            if 'journals' in content and isinstance(content['journals'], list):
                for idx, journal in enumerate(content['journals']):
                    if isinstance(journal, dict):
                        journal_parts = []
                        if journal.get('summary'):
                            journal_parts.append(f"Journal: {journal['summary']}")
                        if journal.get('description'):
                            journal_parts.append(f"Description: {journal['description']}")
                        if journal_parts:
                            ordered_content.append((10, 'ics_journal', idx, '\n'.join(journal_parts)))
            
            # 14. JSON/YAML DATA (convert to text)
            if 'data' in content:
                import json
                try:
                    data_text = json.dumps(content['data'], indent=2)
                    if data_text.strip():
                        ordered_content.append((11, 'json_data', 0, data_text))
                except:
                    pass
            
            # 15. EBOOK CHAPTERS
            if 'chapters' in content and isinstance(content['chapters'], list):
                # Extract e-book metadata first (if available)
                ebook_meta_parts = []
                if 'title' in content:
                    ebook_meta_parts.append(f"Title: {content['title']}")
                if 'author' in content:
                    ebook_meta_parts.append(f"Author: {content['author']}")
                if 'publisher' in content:
                    ebook_meta_parts.append(f"Publisher: {content['publisher']}")
                if 'language' in content:
                    ebook_meta_parts.append(f"Language: {content['language']}")
                if 'date' in content:
                    ebook_meta_parts.append(f"Date: {content['date']}")
                if 'description' in content:
                    ebook_meta_parts.append(f"Description: {content['description']}")
                if 'subject' in content:
                    ebook_meta_parts.append(f"Subject: {content['subject']}")
                if 'chapter_count' in content:
                    ebook_meta_parts.append(f"Chapters: {content['chapter_count']}")
                if 'total_words' in content:
                    ebook_meta_parts.append(f"Total Words: {content['total_words']}")
                
                if ebook_meta_parts:
                    ordered_content.append((12, 'ebook_metadata', 0, '\n'.join(ebook_meta_parts)))
                
                for idx, chapter in enumerate(content['chapters']):
                    if isinstance(chapter, dict):
                        # Extract chapter title if available
                        chapter_header_parts = []
                        chapter_id = chapter.get('id', f"Chapter {idx + 1}")
                        chapter_header_parts.append(f"Chapter ID: {chapter_id}")
                        
                        if 'title' in chapter:
                            chapter_header_parts.append(f"Title: {chapter['title']}")
                        elif 'name' in chapter:
                            chapter_header_parts.append(f"Name: {chapter['name']}")
                        
                        if 'length' in chapter:
                            chapter_header_parts.append(f"Length: {chapter['length']} chars")
                        if 'word_count' in chapter:
                            chapter_header_parts.append(f"Words: {chapter['word_count']}")
                        
                        chapter_header = ' | '.join(chapter_header_parts) + '\n\n' if chapter_header_parts else ""
                        
                        # Extract chapter text
                        chapter_text = chapter.get('text', '')
                        if chapter_text is not None:
                            combined_chapter_text = chapter_header + str(chapter_text)
                            # Include all chapters (even empty ones) to preserve chapter order
                            ordered_content.append((12, 'chapter', idx, combined_chapter_text))
                        elif chapter_header.strip():
                            # Chapter exists but has no text - still include header
                            ordered_content.append((12, 'chapter', idx, chapter_header))
            
            # 16. HTML TEXT CONTENT
            if 'text_content' in content:
                html_parts = []
                
                # Extract HTML title if available
                if 'title' in content:
                    html_parts.append(f"Title: {content['title']}")
                
                # Extract headings if available
                if 'headings' in content and isinstance(content['headings'], dict):
                    headings = content['headings']
                    if headings.get('h1'):
                        html_parts.append(f"H1: {' | '.join(headings['h1'])}")
                    if headings.get('h2'):
                        html_parts.append(f"H2: {' | '.join(headings['h2'])}")
                    if headings.get('h3'):
                        html_parts.append(f"H3: {' | '.join(headings['h3'])}")
                
                # Extract links if available
                if 'links' in content and isinstance(content['links'], list):
                    link_texts = []
                    for link in content['links'][:20]:  # Limit to first 20 links
                        if isinstance(link, dict):
                            href = link.get('href', '')
                            text = link.get('text', '')
                            if href:
                                link_texts.append(f"{text} -> {href}")
                    if link_texts:
                        html_parts.append(f"Links ({len(content['links'])} total):\n" + '\n'.join(link_texts))
                
                # Extract images if available
                if 'images' in content and isinstance(content['images'], list):
                    image_texts = []
                    for img in content['images'][:20]:  # Limit to first 20 images
                        if isinstance(img, dict):
                            src = img.get('src', '')
                            alt = img.get('alt', '')
                            if src:
                                image_texts.append(f"Image: {alt or 'No alt'} -> {src}")
                    if image_texts:
                        html_parts.append(f"Images ({len(content['images'])} total):\n" + '\n'.join(image_texts))
                
                # Add main text content
                html_text = content['text_content']
                if isinstance(html_text, str) and html_text.strip():
                    html_header = '\n'.join(html_parts) + '\n\n--- Content ---\n' if html_parts else ''
                    ordered_content.append((13, 'html_text', 0, html_header + html_text))
            
            # 17. CSV ROWS
            if 'rows' in content and isinstance(content['rows'], list):
                csv_text = self._extract_csv_text(content['rows'], content.get('headers', []))
                if csv_text:
                    ordered_content.append((14, 'csv_rows', 0, csv_text))
            
            # 18. DATABASE SCHEMA AND DATA
            if 'tables' in content and isinstance(content['tables'], list) and content.get('format') == 'SQLite':
                # This is a database file
                db_meta_parts = []
                if 'sqlite_version' in content:
                    db_meta_parts.append(f"SQLite Version: {content['sqlite_version']}")
                if 'table_count' in content:
                    db_meta_parts.append(f"Tables: {content['table_count']}")
                
                if db_meta_parts:
                    ordered_content.append((15, 'database_metadata', 0, '\n'.join(db_meta_parts)))
                
                for idx, table in enumerate(content['tables']):
                    if isinstance(table, dict):
                        table_parts = []
                        table_name = table.get('name', f"Table {idx + 1}")
                        table_parts.append(f"Table: {table_name}")
                        
                        if 'column_count' in table:
                            table_parts.append(f"Columns: {table['column_count']}")
                        if 'row_count' in table:
                            table_parts.append(f"Rows: {table['row_count']}")
                        
                        table_header = ' | '.join(table_parts) + '\n'
                        
                        # Extract column names
                        if 'columns' in table and isinstance(table['columns'], list):
                            col_names = [col.get('name', '') for col in table['columns'] if isinstance(col, dict)]
                            if col_names:
                                table_header += f"Columns: {', '.join(col_names)}\n"
                        
                        # Extract sample data
                        if 'sample_data' in table and isinstance(table['sample_data'], list):
                            sample_rows = []
                            for row in table['sample_data'][:10]:  # Limit to first 10 rows
                                if isinstance(row, (list, tuple)):
                                    # Use tab delimiter for clear cell separation (easier to parse for display)
                                    row_cells = [str(cell) if cell is not None else '' for cell in row]
                                    sample_rows.append('\t'.join(row_cells))
                            if sample_rows:
                                table_header += '\nSample Data:\n' + '\n'.join(sample_rows)
                        
                        ordered_content.append((15, 'database_table', idx, table_header))
            
            # Sort by: spatial_position → type → index (preserves spatial and structural order)
            # This ensures content is extracted in the correct spatial order:
            # - Pages are ordered by page number
            # - Sheets are ordered by sheet index
            # - Within each element, content maintains its original location-based order
            ordered_content.sort(key=lambda x: (
                x[0] if len(x) > 0 and isinstance(x[0], (int, float)) else 0,  # spatial_position
                x[1] if len(x) > 1 else '',  # type
                x[2] if len(x) > 2 and isinstance(x[2], (int, float)) else 0  # index
            ))
            
            # Combine all text in order (4th element is the text)
            text_parts = [item[3] if len(item) > 3 else '' for item in ordered_content]
            
            # Join with double newline for readability
            combined_text = '\n\n'.join(text_parts)

            # FORENSIC-01: evidence that lives outside the visible body must be
            # searchable. Comments, tracked deletions, hidden text, headers and
            # footers, notes, spreadsheet formulas, PDF annotations and
            # JavaScript, and macro source are all content; before this they
            # were extracted but never indexed, so an examiner searching for a
            # term inside a tracked change found nothing. The readers place this
            # flattened text in 'forensic_text' (additive key: absent for
            # readers that have not been extended). It IS content of this
            # artifact, so it joins the content channel and counts as evidence
            # that the artifact could be read.
            forensic_text = str(content.get('forensic_text') or '').strip()
            if forensic_text:
                combined_text = (f"{combined_text}\n\n{forensic_text}"
                                 if combined_text.strip() else forensic_text)

            # SIZE-01: there is no bound on how much text a file may contain.
            # testRecordSizeExceeded.xlsx is 12 MB on disk and produced a 181 MB
            # text body from 3 000 000 cells, which is both a memory hazard and a
            # row that no search index wants. The tail is cut here, once, with a
            # marker that says so - truncation is recorded, never silent - and
            # the reader-side per-workbook cell caps keep most files from ever
            # reaching this point.
            limit = getattr(self, "MAX_CONTENT_TEXT_CHARS",
                            StoragePipeline.MAX_CONTENT_TEXT_CHARS)
            if len(combined_text) > limit:
                kept = combined_text[:limit]
                dropped = len(combined_text) - limit
                combined_text = (
                    f"{kept}\n\n[TRUNCATED: {dropped} characters beyond the "
                    f"{limit} character limit were not stored]"
                )

            # IDENTITY (original name, declared extension, detected
            # format/MIME/version, container features and every
            # declared-vs-detected discrepancy) is metadata *about* the artifact,
            # not content *of* it, and it is already recorded structurally:
            # _build_extraction_provenance persists it per artifact as
            # paths.extraction_provenance -> 'detection', which the lineage API
            # surfaces (see docs/operations.md). Folding the flattened identity
            # lines into this content text used to make an artifact look
            # readable when nothing had been read - a 20x20 icon that the image
            # reader deliberately skipped was stored as file_status='Read' with
            # 200 characters of "content" that were nothing but labels such as
            # "Original name: icon.png" - which broke the documented meaning of
            # file_status and the Unread status contract
            # (tests/integration/test_status_persisted.py), polluted the
            # full-text word index with ~10 boilerplate labels per artifact, and
            # put the labels in front of the examiner in the content display.
            # Identity stays searchable where it belongs: as a structured,
            # queryable record (SELECT ... WHERE extraction_provenance ->
            # 'detection' ->> 'extension_mismatch' = 'true'), not as fake body
            # text. Do not re-add it here.
            
            # Log extraction summary for debugging
            if ordered_content:
                content_types = {}
                for item in ordered_content:
                    content_type = item[1]
                    content_types[content_type] = content_types.get(content_type, 0) + 1
                logger.info(f"Extracted content: {len(ordered_content)} items, types: {content_types}, total length: {len(combined_text)} chars")
            
            # Return text (preserve leading/trailing whitespace if it's meaningful)
            # Only strip if the entire text is whitespace
            if combined_text.strip():
                return combined_text
            else:
                # If all whitespace, return empty string
                return ""

        except Exception as e:
            logger.error(f"Error extracting text: {e}")
            return ""
        
    def _extract_email_message_text(self, msg: Dict) -> str:
        """Extract text from email message dict - comprehensive extraction"""
        parts = []
        
        # Add message index if available (for MBOX/PST)
        if 'message_index' in msg:
            parts.append(f"Message #{msg['message_index']}")
        
        # Add metadata fields
        if msg.get('from'):
            parts.append(f"From: {msg['from']}")
        if msg.get('to'):
            parts.append(f"To: {msg['to']}")
        if msg.get('subject'):
            parts.append(f"Subject: {msg['subject']}")
        if msg.get('date'):
            parts.append(f"Date: {msg['date']}")
        if msg.get('cc'):
            parts.append(f"CC: {msg['cc']}")
        if msg.get('bcc'):
            parts.append(f"BCC: {msg['bcc']}")
        if msg.get('message_id'):
            parts.append(f"Message-ID: {msg['message_id']}")
        
        # Add attachment info if available
        if 'attachment_count' in msg:
            parts.append(f"Attachments: {msg['attachment_count']}")
        if 'has_attachments' in msg and msg['has_attachments']:
            if 'attachments_folder' in msg:
                parts.append(f"Attachments Folder: {msg['attachments_folder']}")
        
        # Add source filepath if available
        if 'source_filepath' in msg:
            parts.append(f"Source: {msg['source_filepath']}")
        
        # Add content/body - check multiple possible keys
        content = msg.get('content') or msg.get('body') or msg.get('text') or ''
        if content:
            # Ensure content is a string
            if not isinstance(content, str):
                content = str(content)
            # Only add content section if content is not empty
            if content.strip():
                parts.append('\n--- Message Content ---\n' + content)
            else:
                # Log empty content for debugging
                logger.debug(f"Message has empty content field: {msg.get('subject', 'N/A')[:50]}")
        else:
            # Log if no content field found at all
            logger.debug(f"Message has no content field: {msg.get('subject', 'N/A')[:50]}")
        
        return '\n'.join(parts)
    
    @staticmethod
    def _cell_text(value) -> str:
        """One cell as a single line of text.

        A stored row is ONE line with tab-separated cells, so a cell value
        containing a newline (a multi-paragraph table cell) would split that
        row in two, and a tab inside a value would look like an extra column.
        Either way the reconstructed grid no longer matches the document -
        which is the whole point of storing the cells separately.

        Only the intra-cell break is folded to a space; the words themselves
        are kept, so search, classification and word counts are unaffected.
        """
        if value is None:
            return ''
        text = str(value)
        if '\n' in text or '\r' in text or '\t' in text:
            text = re.sub(r'[\r\n\t]+', ' ', text)
        return text

    def _extract_sheet_text(self, data: List[List], row_numbers: Optional[List[int]] = None) -> str:
        """Extract text from a sheet grid, one line per row, tab-delimited.

        ``row_numbers`` are the sheet's own 1-based row numbers for ``data``
        (when the reader skipped entirely empty rows). They are used to write
        the skipped rows back as empty lines, because the address of every
        cell below them depends on those lines existing. A format that keeps
        empty rows in the grid simply passes nothing.
        """
        text_parts = []
        
        if not data:
            return ""

        previous_number = 0
        for position, row in enumerate(data):
            # Handle both list of cells and other formats
            if isinstance(row, (list, tuple)):
                # Convert to strings; a cell never spans a line
                # Use tab delimiter for clear cell separation (easier to parse for display)
                row_cells = [self._cell_text(cell) for cell in row]
                # Join with tab character for explicit cell boundaries
                row_text = '\t'.join(row_cells)
            else:
                # Single value row
                row_text = self._cell_text(row)
            
            number = None
            if row_numbers and position < len(row_numbers):
                try:
                    number = int(row_numbers[position])
                except (TypeError, ValueError):
                    number = None
            if number is not None and number > previous_number + 1:
                text_parts.extend([''] * (number - previous_number - 1))
            # Include all rows (even empty ones) to preserve structure
            text_parts.append(row_text)
            if number is not None:
                previous_number = number

        return '\n'.join(text_parts)
    
    def _extract_table_text(self, rows: List[List]) -> str:
        """
        Extract text from table rows (Word tables, slide tables)
        Uses tab delimiters for clear cell separation to enable accurate display

        One row per line, cells separated by tabs, is the contract the display
        layer reads back. The one value that cannot be written literally is an
        all-empty row: a row that serializes to an empty line is
        indistinguishable from the blank line the extractor puts between two
        document elements, and the display layer would read it as the END of
        the table - printing the remaining rows as prose. Such a row is
        written with an explicit tab (an empty cell boundary) instead, which
        the display layer turns back into an empty row.
        """
        text_parts = []

        if not rows:
            return ""

        for row in rows:
            if isinstance(row, (list, tuple)):
                cells = [self._cell_text(cell) for cell in row]
                if not any(cells):
                    cells = cells or ['']
                    if len(cells) == 1:
                        # Single-column table: the tab is what keeps this row
                        # a row instead of a block separator.
                        cells = ['', '']
                row_text = '\t'.join(cells)
            else:
                row_text = self._cell_text(row)
                if not row_text:
                    row_text = '\t'

            # Include all rows (even empty ones) to preserve structure
            text_parts.append(row_text)

        return '\n'.join(text_parts)
    
    def _extract_csv_text(self, rows: List[List], headers: List) -> str:
        """
        Extract text from CSV data
        Uses tab delimiters for clear cell separation to enable accurate display
        """
        text_parts = []
        
        if headers:
            # Use tab delimiter for clear column separation
            header_cells = [self._cell_text(h) for h in headers]
            text_parts.append('\t'.join(header_cells))
        
        for row in rows:
            # Use tab delimiter for clear cell separation
            row_cells = [self._cell_text(cell) for cell in row]
            row_text = '\t'.join(row_cells)
            # Keep EMPTY rows too: row positions are part of the document
            # (cell B5 must stay B5); dropping blank rows shifted every
            # following row up. The display layer counts blank lines as rows.
            text_parts.append(row_text)
        
        return '\n'.join(text_parts)
    
    def get_statistics(self) -> Dict[str, int]:
        """Get pipeline statistics"""
        return self.stats.copy()

    def _record_store_outcome(self, file_path: str, outcome: str) -> None:
        """Remember how a file was stored (``stored`` or ``duplicate``)."""
        if not file_path:
            return
        with self._outcome_lock:
            self._store_outcomes[file_path] = outcome
            self._store_outcomes.move_to_end(file_path)
            while len(self._store_outcomes) > self._OUTCOME_KEEP:
                self._store_outcomes.popitem(last=False)

    def get_store_outcome(self, file_path: str) -> Optional[str]:
        """Outcome of the last store attempt for ``file_path``, or None.

        ``'duplicate'`` means the content is already stored under an existing
        path, so nothing new was written for this file.
        """
        if not file_path:
            return None
        with self._outcome_lock:
            return self._store_outcomes.get(file_path)
    
    def verify_file_stored(self, path_id: int) -> Optional[Dict[str, Any]]:
        """
        Verify that a file was successfully stored in the database.
        Returns detailed information about the stored file.
        
        Args:
            path_id: Path ID returned from store_file_complete or _store_file_sync
        
        Returns:
            Dictionary with file storage details, or None if not found
        """
        if not path_id:
            return None
        
        try:
            # Get path metadata using ContentDBService
            path_info = self.db_service.paths_repo.get_file_by_id(path_id) if self.db_service else None
            if not path_info:
                return None
            
            # Convert path_info to dict if it's a tuple/row.
            # FileQueries.get_file_by_id shape:
            # (id, file_name, file_path, file_size, file_type, file_status,
            #  file_date, date_creation, hash_id, source_id, side_id,
            #  source_name, side_name)
            if isinstance(path_info, tuple):
                keys = ('id', 'file_name', 'file_path', 'file_size', 'file_type',
                        'file_status', 'file_date', 'date_creation', 'hash_id',
                        'source_id', 'side_id', 'source_name', 'side_name')
                path_info = dict(zip(keys, path_info))
            elif not isinstance(path_info, dict):
                # If it's some other type, try to convert
                logger.warning(f"Unexpected path_info type: {type(path_info)}, attempting to convert")
                path_info = {'id': path_id} if path_info else None
                if not path_info:
                    return None
            
            # Canonical content identity of the occurrence (hash string).
            hash_id = path_info.get('hash_id')
            hash_value = None
            if self.db_service:
                hash_value = self.db_service.paths_repo.get_path_hash_by_id(path_id)
            
            # Check if content was stored using ContentDBService
            content_exists = False
            content_length = 0
            try:
                # Use ContentDBService to get content word IDs (indicates content exists)
                content_word_ids = self.db_service.get_content_word_ids(path_id) if self.db_service else []
                if content_word_ids:
                    content_exists = True
                    content_length = len(content_word_ids)  # Number of word IDs in content
            except Exception as content_error:
                logger.debug(f"Error checking content existence: {content_error}")
                pass
            
            # Build verification result
            verification = {
                'stored': True,
                'path_id': path_id,
                'file_name': path_info.get('file_name'),
                'file_path': path_info.get('file_path'),
                'file_size': path_info.get('file_size'),
                'file_type': path_info.get('file_type'),
                'file_status': path_info.get('file_status'),
                'file_date': str(path_info.get('file_date')) if path_info.get('file_date') else None,
                'date_creation': str(path_info.get('date_creation')) if path_info.get('date_creation') else None,
                'hash_id': hash_id,
                'hash_value': hash_value[:32] + '...' if hash_value and len(hash_value) > 32 else hash_value,
                'content_stored': content_exists,
                'content_length': content_length,
                'coordinates': path_info.get('coordinates')
            }
            
            return verification
        except Exception as e:
            logger.error(f"Error verifying file storage: {e}")
            return None
    
    def store_file_complete(
        self,
        file_info: Dict[str, Any],
        result: Dict[str, Any],
        parent_path_id: Optional[int] = None,
        hierarchy_path: Optional[str] = None,
        use_async: bool = False,
        source_name: Optional[str] = None,
        side_name: Optional[str] = None
         ) -> Optional[int]:
        """
        Store file with complete processing (wrapper for _store_file_sync)
        
        Args:
            file_info: File metadata dictionary
            result: File processing result
            parent_path_id: Parent path ID (for nested files)
            hierarchy_path: Hierarchy path string
            use_async: Whether to use async storage (currently not implemented, always sync)
            source_name: Source name (optional, overrides constructor default)
            side_name: Side name (optional, overrides constructor default)
        
        Returns:
            Path ID or None if failed
        """
        # Validate inputs
        if not isinstance(file_info, dict):
            logger.error(f"Invalid file_info type: {type(file_info).__name__}, expected dict")
            return None
        
        if not isinstance(result, dict):
            logger.error(f"Invalid result type: {type(result).__name__}, expected dict")
            return None

        # DATA-04: count unsupported file types so the statistics service
        # reflects reality (previously files_unsupported was never wired up).
        if result.get('error') and 'Unsupported file type' in str(result.get('error')):
            self.stats['files_unsupported'] = self.stats.get('files_unsupported', 0) + 1

        # Currently only sync is supported
        return self._store_file_sync(
            file_info, 
            result, 
            source_name=source_name,
            side_name=side_name,
            parent_path_id=parent_path_id, 
            hierarchy_path=hierarchy_path
        )
    
    def shutdown(self):
        """Shutdown storage pipeline and cleanup resources"""
        # Close database connection if needed
        # Currently db_hub is managed externally, so just log
        logger.info("Storage pipeline shutdown")

#: Provenance kept about identification. Bounded on purpose: the DB copy must
#: stay small enough to write for millions of artifacts, so long lists are
#: truncated with an explicit count rather than silently dropped.
_DETECTION_SCALAR_KEYS = (
    "declared_name", "declared_extension", "detected_extension",
    "detection_method", "detection_confidence", "extension_mismatch",
    "format_id", "format_family", "mime_type", "format_version",
)
_DETECTION_LIST_LIMIT = 25


def _detection_record(content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The stored identity record for one artifact, or None when absent.

    Accepts both shapes that exist in the pipeline: the reader service's
    ``type_detection`` decision and the richer ``format_identification`` block a
    reader may attach. Nothing is invented: a field is recorded only when the
    identifier produced it.
    """
    decision = content.get("type_detection")
    identification = content.get("format_identification")
    if not isinstance(decision, dict):
        decision = {}
    if not isinstance(identification, dict):
        identification = decision.get("format_identification")
    if not isinstance(identification, dict):
        identification = {}

    merged: Dict[str, Any] = {}
    for key in _DETECTION_SCALAR_KEYS:
        value = decision.get(key)
        if value is None:
            value = identification.get(key)
        if value is not None:
            merged[key] = value

    features = identification.get("features") or decision.get("format_features")
    if isinstance(features, dict) and features:
        trimmed: Dict[str, Any] = {}
        for key, value in features.items():
            if isinstance(value, (list, tuple, set)):
                items = list(value)
                if len(items) > _DETECTION_LIST_LIMIT:
                    trimmed[key] = items[:_DETECTION_LIST_LIMIT]
                    trimmed[f"{key}_total"] = len(items)
                else:
                    trimmed[key] = items
            elif isinstance(value, (str, int, float, bool)) or value is None:
                trimmed[key] = value
        merged["features"] = trimmed

    evidence = identification.get("evidence")
    if isinstance(evidence, (list, tuple)) and evidence:
        merged["evidence"] = list(evidence)[:_DETECTION_LIST_LIMIT]

    discrepancies = decision.get("format_discrepancies") or identification.get("discrepancies")
    if isinstance(discrepancies, (list, tuple)) and discrepancies:
        merged["discrepancies"] = [
            item if isinstance(item, dict) else {"kind": str(item)}
            for item in list(discrepancies)[:_DETECTION_LIST_LIMIT]
        ]

    confidence = identification.get("confidence")
    if confidence and "detection_confidence" not in merged:
        merged["detection_confidence"] = confidence

    return merged or None


