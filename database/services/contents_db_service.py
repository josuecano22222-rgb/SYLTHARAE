from datetime import date
from typing import Any, List, Optional, Dict, Tuple
from contextlib import contextmanager
import re
import logging
import psycopg2

from database.database.database import Database
from database.database.repository.transaction_scope import TransactionScope
from database.exceptions import QueryError, TransactionAbortedError

from core.word_limits import MAX_WORD_BYTES, word_exceeds_db_limit

try:  # imported for the "reference data not installed" branch only
    from services.geo.gazetteer import GazetteerUnavailable as _GazetteerUnavailable
except Exception:  # pragma: no cover - keeps this module importable alone
    class _GazetteerUnavailable(Exception):
        """Fallback when the geo package cannot be imported."""

logger = logging.getLogger(__name__)

#: Processing states that are recorded findings rather than problems: the
#: artifact was read (or deliberately not read) and the row says why. A document
#: in one of these states with no word entries is an expected outcome.
_RECORDED_OUTCOMES = ("processed", "skipped", "unsupported")


def _empty_content_is_recorded(processing_status: Optional[str],
                               status_detail: Optional[str]) -> bool:
    """Whether "stored with no words" is an outcome the row already explains.

    An icon below the reader's size floor and a vector-only SVG are stored with
    no word entries by design, and both carry their reason in ``status_detail``.
    Logging those as warnings put dozens of alarming lines in a normal run and
    buried the documents that really had nothing to index and nothing recorded
    explaining it.
    """
    return (processing_status in _RECORDED_OUTCOMES) and bool(status_detail)

from database.database.repository.sources_repo import SourcesRepository
from database.database.repository.contents_repo import ContentsRepository
from database.database.repository.words_repo import WordsRepository
from database.database.repository.paths_repo import PathsRepository
from database.database.repository.keywords_repo import KeywordsRepository
from database.database.repository.categorys_repo import CategorysRepository
from database.database.repository.sides_repo import SidesRepository
from database.database.repository.words_hashs_repo import WordsHashsRepository
from database.database.repository.keywords_hashs_repo import KeywordsHashsRepository
from database.database.repository.words_categorys_repo import WordsCategorysRepository
from database.services.dedup_service import DeduplicationService
from database.database.repository.titles_content_repo import TitlesContentRepository
from database.database.repository.punctuation_repo import PunctuationRepository
from database.database.repository.alerts_repo import AlertsRepository
from core.serialization import pack_int_list, unpack_int_list


def _as_id(value):
    """Normalize a repository result into a plain integer id (or None).

    Repositories sometimes return the id directly, sometimes a one-element
    tuple (depending on the driver path taken by ``execute``).  Callers in
    this module need one representation.
    """
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
        if value is None:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def _keyword_occurrence_counts(sequence, patterns):
    """Count overlapping occurrences of id patterns inside ``sequence``.

    ``sequence`` is a list of ids (word ids, possibly ``None`` for unknown
    gaps); ``patterns`` maps a keyword id to the list of word ids that make up
    the keyword phrase.

    Positions are indexed by the first id of each pattern, so the work is
    proportional to the number of candidate positions instead of scanning the
    whole document once per keyword.  The previous nested loop was
    O(document length x keywords) and was a visible contributor to the CPU
    saturation reported by the resource monitor during large ingests.

    Returns:
        ``{keyword_id: count}`` for the patterns that occur at least once.
    """
    if not sequence or not patterns:
        return {}

    positions = {}
    for index, value in enumerate(sequence):
        positions.setdefault(value, []).append(index)

    counts = {}
    length_of = len(sequence)
    for keyword_id, pattern in patterns.items():
        if not pattern:
            continue
        pattern_length = len(pattern)
        candidates = positions.get(pattern[0])
        if not candidates:
            continue
        if pattern_length == 1:
            counts[keyword_id] = len(candidates)
            continue
        last_start = length_of - pattern_length
        tail = pattern[1:]
        count = 0
        for start in candidates:
            if start <= last_start and sequence[start + 1:start + pattern_length] == tail:
                count += 1
        if count:
            counts[keyword_id] = count
    return counts


class _SharedDedupSession:
    """Yields a DeduplicationService bound to an existing transaction."""

    def __init__(self, dedup):
        self._dedup = dedup

    def __enter__(self):
        return self._dedup

    def __exit__(self, *exc):
        return False


class _OwnDedupSession:
    """Runs identity work on its own short-lived pooled connection."""

    def __init__(self, db):
        self._db = db
        self._conn = None

    def __enter__(self):
        self._conn = self._db.connect()
        return DeduplicationService(lambda: self._conn)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._conn is not None:
                if exc_type is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
        finally:
            if self._conn is not None:
                try:
                    self._db.putconn(self._conn)
                except Exception:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
        return False


class ContentDBService:

    def __init__(self, db: Optional[Database] = None):
      
        self.db = db or Database()
        
        self._init_repositories()
    
    @contextmanager
    def transaction(self):
        """
        Transaction context manager for service-level operations.

        All repository operations on this service share one connection for the
        duration of the block, so hash, path, content, word links and title are
        committed together or not at all.

        Thread safety: the connection is bound in a
        :class:`TransactionScope` (context-local), **not** by assigning
        connection-bound clones onto this shared service instance.  Two worker
        threads storing documents at the same time therefore keep their own
        connections; the previous attribute-swapping implementation let thread
        B overwrite thread A's repositories, which sent thread A's statements
        to thread B's session (``relation "tmp_words" does not exist``,
        ``no COPY in progress``, then the aborted-transaction cascade).

        Inside an existing transaction the block becomes a savepoint: a nested
        failure rolls back only its own statements, never the surrounding unit
        of work.

        Usage:
            with service.transaction():
                service.register_occurrence(..., commit=False)
                service.create_content(words, hash_id)
                # All operations commit together or rollback on error
        """
        scope = self._scope

        # Nested transaction: reuse the outer connection, contain failures.
        if scope.active:
            with scope.savepoint():
                yield self
            return

        conn = self.db.connect()
        token = scope.bind(conn)
        try:
            yield self
            conn.commit()
            logger.debug("Service transaction committed successfully")
        except BaseException as exc:
            # Roll back once, here, at the only party that owns the unit of
            # work - and then surface the original exception (callers classify
            # on TransactionAbortedError / QueryError).
            try:
                if conn and not conn.closed:
                    conn.rollback()
                    logger.error("Service transaction rolled back: %s", exc)
            except Exception as rollback_err:
                logger.error("Error during rollback: %s", rollback_err)
            raise
        finally:
            scope.release(token)
            try:
                self.db.putconn(conn)
            except Exception as put_err:
                logger.error("Error returning connection to pool: %s", put_err)
                try:
                    if conn and not conn.closed:
                        conn.close()
                except Exception:
                    pass

    @contextmanager
    def savepoint(self, name: Optional[str] = None):
        """Run a block inside a ``SAVEPOINT`` of the current transaction.

        Used for optional work (raw display text, keyword links, titles): a
        failure inside the block is contained and cannot poison the rest of
        the document transaction.
        """
        with self._scope.savepoint(name):
            yield self

    def _init_repositories(self):
        """Initialize all repository instances.

        Every repository shares one :class:`TransactionScope`.  When
        :meth:`transaction` binds a connection to the calling thread/task, all
        of them pick it up for the duration of the block - without mutating
        this (shared) service instance, which is what made concurrent storage
        run statements on another thread's connection.
        """
        self._scope = TransactionScope(name="contents_db_service")
        self.sources_repo = SourcesRepository(self.db, scope=self._scope)
        self.sides_repo = SidesRepository(self.db, scope=self._scope)
        self.paths_repo = PathsRepository(self.db, scope=self._scope)
        self.words_repo = WordsRepository(self.db, scope=self._scope)
        self.contents_repo = ContentsRepository(self.db, scope=self._scope)
        self.words_categorys_repo = WordsCategorysRepository(self.db, scope=self._scope)
        self.categorys_repo = CategorysRepository(self.db, scope=self._scope)
        self.keywords_repo = KeywordsRepository(self.db, scope=self._scope)
        self.keywords_hashs_repo = KeywordsHashsRepository(self.db, scope=self._scope)
        self.words_hashs_repo = WordsHashsRepository(self.db, scope=self._scope)
        self.titles_content_repo = TitlesContentRepository(self.db, scope=self._scope)
        self.punctuation_repo = PunctuationRepository(self.db, scope=self._scope)
        self.alerts_repo = AlertsRepository(self.db, scope=self._scope)

    # ------------------------------------------------------------------
    # Identity access (One Content, Many Contexts)
    # ------------------------------------------------------------------

    def _dedup_session(self):
        """Context manager yielding the identity authority.

        Inside a service transaction it shares the transaction's connection
        (``commit=False`` semantics: the outer unit of work owns commit).
        Outside one it opens a short-lived pooled connection and commits its
        own work.
        """
        conn = self._scope.current
        if conn is not None:
            return _SharedDedupSession(DeduplicationService(lambda: conn))
        return _OwnDedupSession(self.db)

    def resolve_hash_id(self, path_id: int) -> Optional[int]:
        """Resolve an occurrence (path) to its canonical content id.

        Content-derived data is keyed by canonical content (hash_id); every
        path-addressed read goes through this single resolution so the two id
        spaces can never be confused.
        """
        return _as_id(self.paths_repo.get_hash_id_for_path(path_id))

    def resolve_context_id(self, path_id: int) -> Optional[int]:
        """Resolve an occurrence (path) to its context id."""
        row = self.paths_repo.execute(
            "SELECT context_id FROM paths WHERE id = %s",
            (path_id,), fetchone=True,
        )
        return _as_id(row)

    # def categories(self):
    #     result = self.categorys_repo.select_categorys_word_id()
    #     print(result)
    #     result = self.categorys_repo.select_category_by_word_id(35)
    #     print(result)
    #     result = self.categorys_repo.get_categories_by_file(27)
    #     print(result)
    #     result = self.categorys_repo.search_categories('word', 10, 0)
    #     print(result)
    #     result = self.categorys_repo.get_categories_with_stats(5)
    #     print(result)
    #     result = self.categorys_repo.update_category_by_id(36, 10)
    #     print(result)
    #     result = self.categorys_repo.update_category_by_word_id(10, 36)
    #     print(result)

    # ============================================================
    # SOURCE OPERATIONS
    # ============================================================

    def get_all_sources(self) -> List[Tuple[int, str]]:
     
        return self.sources_repo.select_info_sources()

    def create_source(
        self,
        name: str,
        country: str,
        job: str,
        importance: float,
        city: str = "",
        description: str = "",
        accounts: str = "",
        note: str = "",
        attachments: str = "",
        ownership: str = "",
        access_status: str = "",
        entry_date: Optional[date] = None,
        id_categorys: Optional[int] = None
    ) -> int:

        # Use provided entry_date or default to today
        if entry_date is None:
            entry_date = date.today()
        
        return self.sources_repo.insert_info_sources(
            name, country, job, importance, city, description,
            accounts, note, attachments, ownership, access_status,
            entry_date, date.today(), id_categorys
        )

    def get_source_by_id(self, source_id: int) -> Optional[Dict]:
        """Get source details by ID."""
        # Implement based on your repository method
        pass

    # ============================================================
    # SIDE OPERATIONS
    # ============================================================

    def get_all_sides(self) -> List[Tuple[int, str]]:
       
        return self.sides_repo.select_info_sides()

    def create_side(self, name: str, importance: float, date_creation: Optional[date] = None) -> int:
        """Create a new side"""
        if date_creation is None:
            date_creation = date.today()
        return self.sides_repo.insert_info_sides(name, importance, date_creation)

    def get_or_create_source(self, name: str, importance: float = 1.0,
                             country: str = "", job: str = "") -> Optional[int]:
        """Return the id of ``name``, creating the source if it is missing.

        Uses the repository's ``INSERT ... ON CONFLICT (name) DO UPDATE ...
        RETURNING id`` upsert, so concurrent workers storing their first file
        of an ingestion cannot race each other into
        ``duplicate key value violates unique constraint "sources_name_key"``
        - the previous read-then-insert pattern made every worker but the
        winner fail, which cost the file its real source (the code fell back to
        ``__FALLBACK_SOURCE__``).
        """
        row = self.sources_repo.get_or_create_source(
            name, job or "", importance, country or "", date.today()
        )
        return _as_id(row)

    def get_or_create_side(self, name: str, importance: float = 1.0) -> Optional[int]:
        """Return the id of ``name``, creating the side if it is missing.

        Concurrency-safe counterpart of :meth:`create_side` (see
        :meth:`get_or_create_source`).
        """
        row = self.sides_repo.get_or_create_side(name, importance, date.today())
        return _as_id(row)

    # ============================================================
    # HASH OPERATIONS
    # ============================================================

    def register_occurrence(
        self,
        hash_value: str,
        source_id: int,
        side_id: int,
        path_row: Dict[str, Any],
        commit: bool = True,
    ) -> Dict[str, Any]:
        """Register content identity + context identity + one occurrence.

        The single registration path: ``DeduplicationService.register_content``
        (One Content, Many Contexts).  See that service for the layered
        duplicate semantics.
        """
        with self._dedup_session() as dedup:
            return dedup.register_content(
                hash_value, source_id, side_id, path_row, commit=commit
            )

    def find_committed_occurrence(self, hash_value: str, source_name: str,
                                  side_name: str, location: str) -> Optional[int]:
        """Read-only lookup of a committed occurrence (see DeduplicationService)."""
        with self._dedup_session() as dedup:
            return dedup.find_committed_occurrence(hash_value, source_name, side_name, location)

    def hash_exists(self, hash_value: str, source_id: int, side_id: Optional[int] = None) -> bool:
        """Check if the content has any live occurrence for the source (and optionally side)."""
        # AUDIT-DB-01: the side-scoped branch called ``self.hashs_repo``, an
        # attribute this service never had (AttributeError on every call).
        with self._dedup_session() as dedup:
            return dedup.hash_exists_with_live_path(hash_value, source_id, side_id)

    def check_duplicate(
        self,
        hash_value: str,
        source_id: int,
        side_id: int,
        file_path: Optional[str] = None,
        hierarchy_path: Optional[str] = None,
    ):
        """Layered duplicate check; see DeduplicationService.check_duplicate."""
        with self._dedup_session() as dedup:
            return dedup.check_duplicate(
                hash_value, source_id, side_id,
                file_path=file_path, hierarchy_path=hierarchy_path,
            )

    # ============================================================
    # SPACING MAPPING
    # ============================================================
    
    # Spacing type constants
    SPACING_NONE = 0      # No spacing
    SPACING_SPACE = 1     # Single space
    SPACING_TAB = 2       # Tab character (\t)
    SPACING_NEWLINE = 3   # Newline character (\n)
    SPACING_PIPE = 4      # Pipe symbol (|) for headings
    
    @staticmethod
    def get_spacing_id(space_char: str) -> int:
        """
        Get spacing ID for a space character.
        
        Args:
            space_char: Space character string
        
        Returns:
            Spacing ID constant
        """
        if not space_char or space_char == '':
            return ContentDBService.SPACING_NONE
        elif space_char == ' ':
            return ContentDBService.SPACING_SPACE
        elif space_char == '\t':
            return ContentDBService.SPACING_TAB
        elif space_char == '\n' or space_char == '\r\n':
            return ContentDBService.SPACING_NEWLINE
        elif space_char == '|':
            return ContentDBService.SPACING_PIPE
        else:
            # Default to space for unknown characters
            return ContentDBService.SPACING_SPACE
    
    @staticmethod
    def calculate_char_position(page_number: int, y_coord: int, x_coord: int) -> int:
        """
        Calculate character position using spatial formula.
        
        Formula: page_number * 1000000 + y_coord * 1000 + x_coord
        
        Args:
            page_number: Page number (0-indexed or 1-indexed)
            y_coord: Y coordinate (vertical position)
            x_coord: X coordinate (horizontal position)
        
        Returns:
            Calculated character position
        """
        return page_number * 1000000 + y_coord * 1000 + x_coord
    
    # ============================================================
    # CONTENT OPERATIONS
    # ============================================================

    def create_content(
        self,
        words: List[str],
        hash_id: int,
        content_date: Optional[date] = None
    ) -> List[int]:
        """
        Create content from a list of words.

        Steps (all inside the caller's transaction):
          1. insert the words missing from the dictionary;
          2. resolve the ids of every word of this document;
          3. store the content as compressed pickled symbol pairs.

        Args:
            words: List of word strings
            hash_id: Canonical content id to associate content with
            content_date: Optional date mentioned in content (None if no date mentioned)

        Returns:
            List of word IDs (integers from words table)

        Note: Content is stored as compressed, pickled symbol pairs:
        [(word_id, punct_before_id, punct_after_id, spacing_id, char_position), ...]
        The content preserves complete formatting information including punctuation, spacing, and positions.

        Note: content_date stores a date mentioned in the content itself.
        If no date is mentioned, it will be empty (None).
        """
        # content_date should remain None if no date was found in content
        # Do not default to today's date
        if words and len(words) > 100000:
            logger.info(
                "Processing large word list (%s words) for hash_id %s",
                f"{len(words):,}", hash_id,
            )

        # 1. Bulk insert words - the repository batches internally for large lists.
        # Nothing else may run on this connection in between: a failure here
        # aborts the transaction and the caller rolls it back (the previous
        # implementation continued after swallowed errors and referenced ids
        # that no longer existed).
        self.words_repo.bulk_insert_words([(w,) for w in words])

        # 2. Resolve the word ids (only the words of this document are looked up).
        word_ids = self.words_repo.select_content_ids_by_words(words)

        # 3. Store content as compressed symbol pairs.
        self.contents_repo.store_text_content(word_ids, content_date, hash_id)

        return word_ids

    def get_content_word_ids(self, path_id: int) -> List[int]:
        """
        Get content as word IDs (numbers from words table).
        
        Args:
            path_id: Path ID to get content for
        
        Returns:
            List of word IDs (integers from words table)
        
        Note: Content consists of numbers (word IDs) from the words table.
        This method returns the raw word IDs without converting to text.
        """
        hash_id = self.resolve_hash_id(path_id)
        return self.contents_repo.load_content_word_ids(hash_id) if hash_id else []
    
    def get_content_as_text(self, path_id: int) -> str:
        """
        Get content as text (converts word IDs to words).
        
        Args:
            path_id: Path ID to get content for
        
        Returns:
            Space-separated string of words
        
        Note: This method retrieves word IDs from content and converts
        them to actual words by looking them up in the words table.
        """
        hash_id = self.resolve_hash_id(path_id)
        return self.contents_repo.load_text_content(hash_id) if hash_id is not None else ""
    
    def get_content_as_array(self, path_id: int) -> List[Dict[str, any]]:
        """
        Get content as an array with words, tags, and word order.
        
        Args:
            path_id: Path ID to get content for
        
        Returns:
            List of dictionaries, each containing:
            - word: str - The word text
            - word_id: int - Word ID from words table
            - tags: List[str] - Category names (tags) associated with the word
            - order: int - Position/order of the word in the content (0-indexed)
        
        Example:
            [
                {
                    "word": "example",
                    "word_id": 123,
                    "tags": ["noun", "common"],
                    "order": 0
                },
                {
                    "word": "test",
                    "word_id": 456,
                    "tags": ["verb"],
                    "order": 1
                }
            ]
        
        Note: Content consists of numbers (word IDs) from the words table.
        This method enriches the word IDs with word text, tags (categories), and order.
        """
        # Get word IDs with positions
        word_ids = self.get_content_word_ids(path_id)
        if not word_ids:
            return []
        
        # Get all words dictionary {id: word}
        word_rows = self.words_repo.select_all_words()
        words_dict = dict(word_rows) if word_rows else {}
        
        # Get categories (tags) for all words
        # Build a map of word_id -> list of category names
        tags_map = {}
        unique_word_ids = set(word_ids)
        
        # Get all category-word relationships at once for efficiency
        for word_id in unique_word_ids:
            category_rows = self.words_categorys_repo.get_categories_by_word_id(word_id)
            tags = []
            if category_rows:
                for cat_row in category_rows:
                    if cat_row and len(cat_row) >= 2:
                        # Query returns: (category_id, category_name)
                        category_name = cat_row[1]  # category_name from query
                        tags.append(category_name)
            tags_map[word_id] = tags
        
        # Build result array with word, tags, and order
        result = []
        
        # Import CPU management for large loops
        try:
            from core.resource_coordinator import should_yield, get_yield_duration
            import time
            cpu_management_available = True
        except ImportError:
            cpu_management_available = False
        
        for order, word_id in enumerate(word_ids):
            # Yield periodically during large loops
            if cpu_management_available and order > 0 and order % 1000 == 0:
                if should_yield():
                    time.sleep(get_yield_duration())
            word_text = words_dict.get(word_id, f"[ID:{word_id}]")
            tags = tags_map.get(word_id, [])
            
            result.append({
                "word": word_text,
                "word_id": word_id,
                "tags": tags,
                "order": order
            })
        
        return result
    
    def create_content_from_symbols(
        self,
        symbols: List[Tuple[str, str, str, str, int, int, int]],
        hash_id: int,
        content_date: Optional[date] = None
    ) -> List[Tuple[int, Optional[int], Optional[int], int, int]]:
        """
        Create content from extracted symbols.
        
        Args:
            symbols: List of symbol tuples, each as:
                (word, punct_before, punct_after, space, page_number, y_coord, x_coord)
            hash_id: Canonical content id to associate content with
            content_date: Optional date mentioned in content (None if no date mentioned)
        
        Returns:
            List of symbol pairs: (word_id, punct_before_id, punct_after_id, spacing_id, char_position)
        
        Note: This method:
        1. Extracts unique words and punctuation marks
        2. Resolves word IDs (batch operation)
        3. Resolves punctuation IDs (batch operation)
        4. Creates symbol pairs with spatial positions
        5. Stores content as compressed Pickle symbol pairs: [(word_id, punct_before_id, punct_after_id, spacing_id, char_position), ...]
        
        Note: content_date stores a date mentioned in the content itself.
        If no date is mentioned, it will be empty (None).
        """
        # content_date should remain None if no date was found in content
        # Do not default to today's date
        
        if not symbols:
            return []
        
        # Extract unique words and punctuation marks
        unique_words = set()
        unique_punctuation = set()
        
        for word, punct_before, punct_after, space, _, _, _ in symbols:
            if word:
                unique_words.add(word)
            if punct_before:
                unique_punctuation.add(punct_before)
            if punct_after:
                unique_punctuation.add(punct_after)
        
        # Resolve word IDs (batch operation)
        word_id_map = self.words_repo.resolve_word_ids_batch(list(unique_words))
        
        # Resolve punctuation IDs (batch operation)
        punct_id_map = self.punctuation_repo.resolve_punctuation_ids_batch(list(unique_punctuation))
        
        # Create symbol pairs
        symbol_pairs = []
        for word, punct_before, punct_after, space, page_num, y_coord, x_coord in symbols:
            word_id = word_id_map.get(word, 0)
            if word_id == 0:
                continue  # Skip if word not found
            
            punct_before_id = punct_id_map.get(punct_before) if punct_before else None
            punct_after_id = punct_id_map.get(punct_after) if punct_after else None
            spacing_id = self.get_spacing_id(space)
            char_position = self.calculate_char_position(page_num, y_coord, x_coord)
            
            symbol_pairs.append((
                word_id,
                punct_before_id,
                punct_after_id,
                spacing_id,
                char_position
            ))
        
        # Store symbol pairs
        self.contents_repo.store_symbol_pairs(symbol_pairs, content_date, hash_id)
        
        return symbol_pairs
    
    def extract_symbols_from_text(
        self,
        text: str,
        page_number: int = 0,
        start_y: int = 0,
        start_x: int = 0,
        line_height: int = 20
    ) -> List[Tuple[str, str, str, str, int, int, int]]:
        """
        Extract symbols from text with spatial coordinates.
        
        Symbol formula: (word, preceding punctuation, following punctuation, space, character position)
        
        Args:
            text: Text to extract symbols from
            page_number: Page number for spatial calculation
            start_y: Starting Y coordinate
            start_x: Starting X coordinate
            line_height: Height of each line (for Y coordinate calculation)
        
        Returns:
            List of symbol tuples: (word, punct_before, punct_after, space, page_number, y_coord, x_coord)
        
        Note:
        - Table cells are separated by tabs (\t)
        - Headings use pipe symbols (|) or newline breaks
        - Preserves punctuation and spaces for accurate reconstruction
        """
        if not text:
            return []
        
        symbols = []
        current_y = start_y
        current_x = start_x
        
        # Split text into lines to handle newlines
        lines = text.split('\n')
        
        # Import CPU management for large text processing
        try:
            from core.resource_coordinator import should_yield, get_yield_duration
            import time
            cpu_management_available = True
        except ImportError:
            cpu_management_available = False
        
        for line_idx, line in enumerate(lines):
            # Yield periodically during large text processing
            if cpu_management_available and line_idx > 0 and line_idx % 500 == 0:
                if should_yield():
                    time.sleep(get_yield_duration())
            if line_idx > 0:
                # Add newline symbol for line breaks (except first line)
                symbols.append(('', '', '', '\n', page_number, current_y, current_x))
                current_y += line_height
                current_x = start_x
            
            # Handle table cells (separated by tabs)
            if '\t' in line:
                cells = line.split('\t')
                for cell_idx, cell in enumerate(cells):
                    # Yield periodically during cell processing
                    if cpu_management_available and cell_idx > 0 and cell_idx % 100 == 0:
                        if should_yield():
                            time.sleep(get_yield_duration())
                    if cell_idx > 0:
                        # Add tab symbol
                        symbols.append(('', '', '', '\t', page_number, current_y, current_x))
                        current_x += 100  # Approximate tab width
                    
                    # Process cell content
                    cell_symbols = self._extract_symbols_from_line(
                        cell, page_number, current_y, current_x
                    )
                    symbols.extend(cell_symbols)
                    if cell_symbols:
                        current_x = cell_symbols[-1][6] + 50  # Update X position
            else:
                # Process regular line
                line_symbols = self._extract_symbols_from_line(
                    line, page_number, current_y, current_x
                )
                symbols.extend(line_symbols)
                if line_symbols:
                    current_x = line_symbols[-1][6] + 50  # Update X position
        
        return symbols
    
    def _extract_symbols_from_line(
        self,
        line: str,
        page_number: int,
        y_coord: int,
        start_x: int
    ) -> List[Tuple[str, str, str, str, int, int, int]]:
        """
        Extract symbols from a single line of text.
        
        Args:
            line: Line of text
            page_number: Page number
            y_coord: Y coordinate
            start_x: Starting X coordinate
        
        Returns:
            List of symbol tuples
        """
        if not line:
            return []
        
        symbols = []
        current_x = start_x
        word_width = 50  # Approximate character width
        
        # Pattern to match words with surrounding punctuation
        # Matches: optional punctuation, word, optional punctuation, optional space
        pattern = r'([^\w\s]*)(\w+)([^\w\s]*)(\s*)'
        
        matches = re.finditer(pattern, line)
        
        for match in matches:
            punct_before = match.group(1) or ''
            word = match.group(2)
            punct_after = match.group(3) or ''
            space = match.group(4) or ''
            
            # Handle pipe symbols (headings)
            if '|' in line and (punct_before == '|' or punct_after == '|'):
                space = '|'
            
            symbols.append((
                word,
                punct_before,
                punct_after,
                space,
                page_number,
                y_coord,
                current_x
            ))
            
            # Update X position (approximate)
            current_x += len(word) * word_width
            if punct_before:
                current_x += len(punct_before) * word_width
            if punct_after:
                current_x += len(punct_after) * word_width
            if space:
                current_x += len(space) * word_width
        
        return symbols
    
    def get_content_symbol_pairs(self, path_id: int) -> List[Tuple[int, Optional[int], Optional[int], int, int]]:
        """
        Get content as symbol pairs.
        
        Args:
            path_id: Path ID to get content for
        
        Returns:
            List of symbol pairs: (word_id, punct_before_id, punct_after_id, spacing_id, char_position)
        """
        hash_id = self.resolve_hash_id(path_id)
        return self.contents_repo.load_symbol_pairs(hash_id) if hash_id else []
    
    def reconstruct_text_from_symbols(self, path_id: int) -> str:
        """
        Reconstruct text from stored symbol pairs.
        
        Args:
            path_id: Path ID to reconstruct content for
        
        Returns:
            Reconstructed text string
        """
        symbol_pairs = self.get_content_symbol_pairs(path_id)
        if not symbol_pairs:
            return ""
        
        # Get word and punctuation dictionaries
        word_rows = self.words_repo.select_all_words()
        words_dict = dict(word_rows) if word_rows else {}
        
        punct_rows = self.punctuation_repo.select_all_punctuation()
        punctuation_dict = {punct_id: punct_text for punct_id, punct_text in punct_rows} if punct_rows else {}
        
        # Sort by char_position to maintain spatial order
        symbol_pairs_sorted = sorted(symbol_pairs, key=lambda x: x[4])  # Sort by char_position
        
        # Reconstruct text
        text_parts = []
        for word_id, punct_before_id, punct_after_id, spacing_id, _ in symbol_pairs_sorted:
            # Add preceding punctuation
            if punct_before_id:
                punct_text = punctuation_dict.get(punct_before_id, '')
                text_parts.append(punct_text)
            
            # Add word
            word_text = words_dict.get(word_id, f"[ID:{word_id}]")
            text_parts.append(word_text)
            
            # Add following punctuation
            if punct_after_id:
                punct_text = punctuation_dict.get(punct_after_id, '')
                text_parts.append(punct_text)
            
            # Add spacing
            if spacing_id == self.SPACING_SPACE:
                text_parts.append(' ')
            elif spacing_id == self.SPACING_TAB:
                text_parts.append('\t')
            elif spacing_id == self.SPACING_NEWLINE:
                text_parts.append('\n')
            elif spacing_id == self.SPACING_PIPE:
                text_parts.append('|')
            # SPACING_NONE adds nothing
        
        return ''.join(text_parts)
    
    # ============================================================
    # CATEGORY OPERATIONS
    # ============================================================

    def create_category(self, category_word: str, parent_category_word: Optional[str] = None) -> Optional[int]:
        try:
            word_id = self.words_repo.insert_by_word(category_word)
            parent_id = None
            if parent_category_word:
                parent_word_id = self.words_repo.insert_by_word(parent_category_word)
                parent_cat = self.categorys_repo.select_category_by_word_id(parent_word_id)
                if parent_cat:
                    parent_id = parent_cat
            return self.categorys_repo.insert_category(word_id, parent_id)
        except Exception as e:
            print(f"Error creating category: {e}")
            return None

    def link_word_to_category(self, word: str, category_word: str) -> bool:
       
        try:
            word_id = self.words_repo.insert_by_word(word)
            category_word_id = self.words_repo.insert_by_word(category_word)
            
            category_id = self.categorys_repo.select_category_by_word_id(category_word_id)
            
            if category_id:
                self.words_categorys_repo.insert_word_category(word_id, category_id)
                return True
            return False
        except Exception as e:
            print(f"Error linking word to category: {e}")
            return False

    # ============================================================
    # KEYWORD OPERATIONS
    # ============================================================

    def create_keyword(self, keyword_phrase: str, category_word: str) -> bool:
        
        try:
            # Split phrase into words
            words = keyword_phrase.split()
            
            # Bulk insert words
            self.words_repo.bulk_insert_words([(w,) for w in words])
            
            # Get word IDs
            keyword_ids = self.words_repo.select_content_ids_by_words(words)
            
            # Get category ID
            category_word_id = self.words_repo.insert_by_word(category_word)
            category_id = self.categorys_repo.select_category_by_word_id(category_word_id)
            
            # Insert keyword
            self.keywords_repo.insert_keywords(category_id, keyword_ids)
            return True
        except Exception as e:
            print(f"Error creating keyword: {e}")
            return False

    def get_all_keywords(self) -> Dict[int, List[int]]:
      
        return self.keywords_repo.select_all_keywords()

    def get_keywords_as_words(self) -> List[List[str]]:
        
        keywords_dict = self.get_all_keywords()
        keyword_words = []
        
        for kw_id, word_ids in keywords_dict.items():
            # ``word_ids`` are ids: resolve them to text (the previous call to
            # select_content_ids_by_words() treated the ids as words, so this
            # method always returned empty keyword lists).
            words = self.words_repo.select_words_by_ids(word_ids)
            keyword_words.append(words)
        
        return keyword_words

    # ============================================================
    # WORD-PATH RELATIONSHIP
    # ============================================================

    def link_words_to_content(self, hash_id: int, content_ids: List[int]) -> int:
        # Build position tracking
        word_positions: Dict[int, List[int]] = {}
        
        # Import CPU management for large loops
        try:
            from core.resource_coordinator import should_yield, get_yield_duration
            import time
            cpu_management_available = True
        except ImportError:
            cpu_management_available = False
        
        for position, word_id in enumerate(content_ids):
            # Yield periodically during large loops
            if cpu_management_available and position > 0 and position % 1000 == 0:
                if should_yield():
                    time.sleep(get_yield_duration())
            word_positions.setdefault(word_id, []).append(position)

        # Prepare bulk data
        bulk_data = [
            (hash_id, word_id, len(positions), pack_int_list(positions))
            for word_id, positions in word_positions.items()
        ]
        # print(bulk_data)
        result = self.words_hashs_repo.bulk_insert_words_hashs(bulk_data)
        # Return number of words linked (or True if no explicit return)
        return len(bulk_data) if result is None else result

    # ============================================================
    # KEYWORD-PATH RELATIONSHIP
    # ============================================================

    def process_keywords_for_content(self, hash_id: int, content_ids: List[int]) -> bool:
        """Link the document to the keywords it contains.

        Returns True when at least one keyword matched.  Failures propagate:
        the caller decides (the ingestion path contains them in a savepoint and
        logs them) instead of this method printing and pretending success.
        """
        if True:
            keywords_dict = self.get_all_keywords()

            # Search for keyword patterns in content.  The matcher indexes
            # positions by first id, so a document is not rescanned once per
            # keyword.
            keyword_count = _keyword_occurrence_counts(content_ids, keywords_dict)

            # Insert keyword-path relationships
            if keyword_count:
                self.keywords_hashs_repo.bulk_insert_keywords_hashs(hash_id, keyword_count)
                return True

            return False

    def refresh_keyword_associations(self, keyword_ids=None) -> Dict[str, int]:
        """Reconcile keyword-to-file links after keyword definitions change.

        Keyword links were historically created only while a file was ingested,
        so adding a keyword later left existing files invisible to that keyword.
        Rebuild only the affected keywords and candidate paths immediately.
        """
        try:
            if keyword_ids:
                keyword_rows = []
                for keyword_id in keyword_ids:
                    row = self.keywords_repo.execute(
                        "SELECT id, keyword FROM keywords WHERE id = %s",
                        (int(keyword_id),), fetchone=True,
                    )
                    if row:
                        keyword_rows.append(row)
            else:
                keyword_rows = self.keywords_repo.execute(
                    "SELECT id, keyword FROM keywords", None, fetchall=True
                )

            decoded = {}
            candidate_words = set()
            for keyword_id, blob in keyword_rows or []:
                try:
                    pattern = unpack_int_list(blob) if blob else []
                except Exception:
                    pattern = []
                if pattern:
                    decoded[int(keyword_id)] = pattern
                    candidate_words.update(pattern)
            if not decoded:
                return {"contents_checked": 0, "associations_added": 0}

            # Only paths containing at least one term can match. This avoids
            # rescanning every document when a keyword is added.
            placeholders = ",".join(["%s"] * len(candidate_words))
            candidate_rows = self.words_hashs_repo.execute(
                f"SELECT DISTINCT hash_id FROM words_hashs WHERE word_id IN ({placeholders})",
                tuple(candidate_words), fetchall=True,
            )
            added = 0
            checked = 0
            for (hash_id,) in candidate_rows or []:
                positions = self.words_hashs_repo.get_word_positions_by_hash(hash_id)
                if not positions:
                    continue
                max_position = max((max(v) for v in positions.values() if v), default=-1)
                sequence = [None] * (max_position + 1)
                for word_id, indexes in positions.items():
                    for index in indexes:
                        if 0 <= index < len(sequence):
                            sequence[index] = word_id
                matches = _keyword_occurrence_counts(sequence, decoded)
                if matches:
                    self.keywords_hashs_repo.bulk_insert_keywords_hashs(hash_id, matches)
                    added += len(matches)
                checked += 1
            return {"contents_checked": checked, "associations_added": added}
        except Exception:
            logger.exception("Failed to refresh keyword associations")
            raise

    # ============================================================
    # TITLE CONTENT OPERATIONS
    # ============================================================

    def create_title_content(
        self,
        title_words: List[str],
        hash_id: int,
        title_status: str = "Main",
        ) -> List[int]:
        
        # PRODUCTION: Skip path_id validation to avoid false negatives in aborted transactions
        # If transaction was aborted, the bulk_insert_words or insert_titles_content will fail
        # with InFailedSqlTransaction, which we'll catch and handle properly
        # This prevents "Path ID does not exist" errors when the transaction is actually aborted
        # We trust that hash_id exists since it was just created in the same transaction
        
        # Bulk insert words
        word_tuples = [(w,) for w in title_words]
        self.words_repo.bulk_insert_words(word_tuples)

        # Get word IDs
        word_ids = self.words_repo.select_content_ids_by_words(title_words)

        # Insert title content
        self.titles_content_repo.insert_titles_content(
            word_ids, hash_id, title_status
        )

        return word_ids

    def get_title_content(self, record_id: int) -> Optional[Dict]:
       
        return self.titles_content_repo.Get_titles_content(record_id)
    

    # def 

    # ============================================================
    # TRANSACTION/BATCH OPERATIONS
    # ============================================================

    def process_full_document(
        self,
        hash_value: str,
        source_id: int,
        side_id: int,
        file_name: str,
        file_path: str,
        file_size: int,
        file_type: str,
        file_status: str,
        file_date: date,
        content_words: List[str],
        title_words: Optional[List[str]] = None,
        coordinates: Optional[str] = None,
        content_date: Optional[date] = None,
        extraction_provenance: Optional[dict] = None,
        processing_status: str = "discovered",
        status_detail: Optional[str] = None,
        attempts: int = 0,
        raw_text: Optional[str] = None,
        parent_path_id: Optional[int] = None,
        hierarchy_path: Optional[str] = None,
    ) -> Dict[str, any]:
        """
        Store one occurrence of a document in a single transaction.

        One Content, Many Contexts (migration m0011):

        1. Identity - canonical content (hash), context (hash + source +
           side) and the physical occurrence (path row) are registered
           through ``DeduplicationService``.  A repeat *occurrence* resolves
           to its existing row; a repeat *content hash* in a new context gets
           a new occurrence WITHOUT duplicating anything content-derived.
        2. Process or reuse - when the canonical content already carries an
           extraction, steps 4-8 are skipped entirely (no re-extraction, no
           duplicated index rows).  They run only the first time a content
           hash is seen.
        3. Content-derived writes (extraction, display text, word index,
           keyword index, titles) are keyed by canonical content id.

        Args:
            hash_value: Content hash (SHA-256 of the bytes)
            source_id / side_id: context of this occurrence
            file_name / file_path: provenance of this occurrence
            parent_path_id / hierarchy_path: container lineage (optional);
                ``hierarchy_path`` identifies in-container occurrences
            content_words / title_words / raw_text: extraction results (used
                only when the canonical extraction is not reused)

        Returns:
            Dictionary with success status and operation results
            (``hash_id``, ``context_id``, ``path_id``, ``content_ids``,
            ``title_ids``, ``duplicate``, ``content_reused``, ``warnings``,
            ``error``).

        Note:
            - file_date stores the file's creation date, not any other date
            - file_status is 'Read' if file contains content, otherwise 'Unread'
            - coordinates stores any GPS coordinates in images or elsewhere
            - content_date stores a date mentioned in the content itself. If no date is mentioned, it will be empty (None).
            - extraction_provenance records how each extractor derived its data
              (engine, version, confidence, derived flag). Stored as JSONB in
              paths.extraction_provenance so recognised text is never
              indistinguishable from authored text.
        """
        result = {
            'success': False,
            'hash_id': None,
            'context_id': None,
            'path_id': None,
            'content_ids': None,
            'title_ids': None,
            'duplicate': False,
            'content_reused': False,
            'warnings': [],
            'error': None
        }

        # INDEX SAFETY: PostgreSQL btree indexes cap entries at ~1/3 of a
        # buffer page (2704 bytes).  A single oversized token (a multi-KB
        # base64/data-URI run survives tokenization as one "word") would
        # otherwise abort the whole document transaction with
        # ProgramLimitExceeded ("index row size ... exceeds btree version 4
        # maximum 2704 for index words_word_key"), so such tokens are
        # dropped from the word index up front.  The raw display store
        # (raw_text) keeps the verbatim text for viewing and in-document
        # search, so nothing is lost except the unindexable blob itself.
        if content_words:
            safe_words = [w for w in content_words if not word_exceeds_db_limit(w)]
            dropped = len(content_words) - len(safe_words)
            if dropped:
                logger.warning(
                    "Dropped %d oversized token(s) from %s exceeding the "
                    "%d-byte PostgreSQL btree index limit; the document is "
                    "stored normally and the raw display text is unaffected.",
                    dropped, file_name, MAX_WORD_BYTES,
                )
            content_words = safe_words
        if title_words:
            title_words = [w for w in title_words if not word_exceeds_db_limit(w)]

        path_row = {
            "file_name": file_name,
            "file_path": file_path,
            "file_size": file_size,
            "file_type": file_type,
            "file_status": file_status,
            "file_date": file_date,
            "date_creation": date.today(),
            "coordinates": coordinates,
            "extraction_provenance": extraction_provenance,
            "processing_status": processing_status,
            "status_detail": status_detail,
            "attempts": attempts,
            "parent_path_id": parent_path_id,
            "hierarchy_path": hierarchy_path,
        }

        try:
            # One transaction per document: identity, extraction, word index
            # and title commit together or not at all.  Optional/derived
            # steps (raw display text, keywords, title) are contained in
            # savepoints so their failure cannot discard the document or
            # poison the transaction for later statements.
            with self.transaction():
                # 1-3. Identity: canonical content, context, occurrence.
                registration = self.register_occurrence(
                    hash_value, source_id, side_id, path_row, commit=False
                )
                hash_id = registration["hash_id"]
                context_id = registration["context_id"]
                path_id = registration["path_id"]
                result.update(
                    hash_id=hash_id, context_id=context_id, path_id=path_id,
                )

                if registration["duplicate"]:
                    # The exact physical encounter is already recorded:
                    # report the existing occurrence, write nothing new.
                    result.update(
                        success=True,
                        duplicate=True,
                        content_reused=True,
                        error="Duplicate file - already processed",
                    )
                    logger.info(
                        "Duplicate occurrence: hash=%s..., existing path_id=%s",
                        hash_value[:16], path_id,
                    )
                    return result

                # 4. Process or reuse: canonical extraction is content-derived
                # and exists once per content hash - never once per context.
                if registration["extraction_exists"]:
                    result.update(success=True, content_reused=True)
                    logger.info(
                        "Reusing canonical extraction for %s "
                        "(hash=%s..., new occurrence path_id=%s)",
                        file_name, hash_value[:16], path_id,
                    )
                    return result

                # 5. Content (word dictionary + compressed symbol pairs) -----
                content_ids = self.create_content(
                    content_words, hash_id, content_date=content_date
                )
                result['content_ids'] = content_ids

                # 6. Raw display text (optional) -----------------------------
                # Kept in the same transaction as the word store so the two
                # never diverge, but contained in a savepoint: the word-join
                # reconstruction still displays the document if the raw text
                # is rejected (e.g. payload too large).
                raw_stored = False
                if raw_text:
                    try:
                        with self.savepoint():
                            self.contents_repo.store_raw_content(hash_id, raw_text)
                        raw_stored = True
                    except TransactionAbortedError:
                        raise
                    except Exception as raw_err:
                        self._note_optional_failure(
                            result, 'raw_text', raw_err, file_name, path_id
                        )

                # 7. Word index (core) ---------------------------------------
                # words_hashs powers search; a failure here would leave the
                # document stored but unsearchable, so it must roll back.
                if content_ids:
                    self.link_words_to_content(hash_id, content_ids)
                else:
                    reason = status_detail or processing_status
                    if _empty_content_is_recorded(processing_status, status_detail):
                        logger.info(
                            "No indexable content for %s (path_id=%s): %s",
                            file_name, path_id, reason,
                        )
                    else:
                        logger.warning(
                            "No indexable content for %s (path_id=%s): stored "
                            "with no word entries and no recorded reason "
                            "(status=%s).",
                            file_name, path_id, reason,
                        )

                # 8. Keyword index (derived) ---------------------------------
                if content_ids:
                    try:
                        with self.savepoint():
                            self.process_keywords_for_content(hash_id, content_ids)
                    except TransactionAbortedError:
                        raise
                    except Exception as kw_err:
                        self._note_optional_failure(
                            result, 'keywords', kw_err, file_name, path_id
                        )

                # 9. Title (derived) -----------------------------------------
                if title_words:
                    try:
                        with self.savepoint():
                            result['title_ids'] = self.create_title_content(
                                title_words, hash_id
                            )
                    except TransactionAbortedError:
                        raise
                    except Exception as title_err:
                        self._note_optional_failure(
                            result, 'title', title_err, file_name, path_id
                        )

                # 10. Content signals (derived): temporal, then places -----
                # Detected on the *stored* display text so every signal's
                # offsets address contents_raw. Each detector is contained
                # in its own savepoint like keywords and title; a failure is
                # recorded as a failed run (never as "nothing found") and
                # marks the document partial.
                self._detect_signals(result, hash_id, raw_text, raw_stored,
                                     file_name, path_id)

                result['success'] = True
                logger.info(
                    "Document processed successfully: path_id=%s, hash_id=%s, context_id=%s",
                    path_id, hash_id, context_id,
                )
                return result

        except Exception as e:
            # The transaction context has already rolled the unit of work
            # back; surface the failure so the caller counts this file as
            # not stored (never as a silent partial write).
            result['error'] = f"{type(e).__name__}: {e}"
            logger.error(
                "Failed to store document '%s': %s: %s",
                file_name, type(e).__name__, e, exc_info=True,
            )
            raise

    #: result key per detector (``signals`` predates the place detector).
    _SIGNAL_RESULT_KEYS = {
        "temporal": "signals",
        "places": "place_signals",
        "identifiers": "identifier_signals",
    }

    def _detect_signals(self, result, hash_id, raw_text, raw_stored, file_name, path_id):
        """Step 10: every registered detector (services/detection/detectors.py)."""
        from services.detection import detectors

        for name in detectors.NAMES:
            key = self._SIGNAL_RESULT_KEYS.get(name, f"{name}_signals")
            self._detect_one(result, key, name, hash_id, raw_text, raw_stored,
                             file_name, path_id)

    def _detect_one(self, result, key, detector, hash_id, raw_text, raw_stored,
                    file_name, path_id):
        from database.database.repository.contents_repo import _sanitise_pg_text
        from services.detection import signal_store

        trigger = signal_store.TRIGGER_INGESTION
        try:
            with self.savepoint():
                with self.contents_repo.get_cursor(commit=False) as cur:
                    if raw_text and not raw_stored:
                        # Offsets must address stored text; without it the
                        # run is a recorded failure the re-detection job
                        # will retry once display text exists.
                        signal_store.record_run(
                            cur, hash_id, status="failed", trigger=trigger,
                            detector=detector,
                            error="display text was not stored; signals need "
                                  "stored text for their offsets")
                        result[key] = {"status": "failed"}
                        return
                    text = _sanitise_pg_text(raw_text)[0] if raw_text else raw_text
                    anchor, _ = signal_store.anchor_for(cur, hash_id)
                    result[key] = signal_store.detect_and_store(
                        cur, hash_id, text, anchor, trigger=trigger, detector=detector)
        except TransactionAbortedError:
            raise
        except _GazetteerUnavailable as unavailable:
            # Reference data that was never installed is a property of the
            # deployment, not of this document. Record the run as failed (so
            # re-detection retries it once the gazetteer is loaded) and carry
            # the reason, but do not mark a fully stored document partial for
            # an optional detector that could not run at all.
            result[key] = {"status": "unavailable", "reason": str(unavailable)}
            result.setdefault('warnings', []).append(
                f"{detector} detector unavailable: {unavailable}")
            logger.warning(
                "Detector %s could not run for '%s' (path_id=%s): %s",
                detector, file_name, path_id, unavailable,
            )
            self._record_failed_signal_run(detector, hash_id, unavailable, trigger,
                                           signal_store)
        except Exception as sig_err:
            self._note_optional_failure(result, key, sig_err, file_name, path_id)
            result[key] = {"status": "failed"}
            self._record_failed_signal_run(detector, hash_id, sig_err, trigger,
                                           signal_store)

    def _record_failed_signal_run(self, detector, hash_id, error, trigger, signal_store):
        """Record the failed run row for ``detector`` (never raises)."""
        try:
            with self.savepoint():
                with self.contents_repo.get_cursor(commit=False) as cur:
                    root = error
                    while root.__cause__ is not None:  # name the driver error,
                        root = root.__cause__         # not its wrappers
                    signal_store.record_run(
                        cur, hash_id, status="failed", trigger=trigger, detector=detector,
                        error=f"{type(root).__name__}: {str(root).strip()}")
        except TransactionAbortedError:
            raise
        except Exception:
            logger.exception("Could not record the failed %s run for hash_id=%s",
                             detector, hash_id)

    def _note_optional_failure(self, result, step, error, file_name, path_id):
        """Record a contained failure of a derived-data step.

        The document is still stored, but it is **not** a clean success: the
        warning is logged loudly, carried in the result, and - because a row
        that says "processed" while its display text is missing is exactly the
        "reported successful despite incomplete content" defect - the row's
        status is set to ``partial`` with the failing step named. The update
        runs in the same transaction as the insert.
        """
        message = f"{step} step failed: {type(error).__name__}: {error}"
        result['warnings'].append(message)
        logger.error(
            "Optional %s step failed for file '%s' (path_id=%s), the document "
            "itself is stored with partial data: %s",
            step, file_name, path_id, error,
        )
        if not path_id:
            return
        # In a savepoint, so a rejected status write can never abort the
        # transaction that holds the document itself: an exception here used to
        # poison the whole store and lose the file (measured with an injected
        # raw-text failure before the savepoint existed).
        try:
            with self.savepoint():
                self.paths_repo.mark_partial(
                    path_id,
                    f"{step} step failed: {type(error).__name__}: {error}",
                )
        except TransactionAbortedError:
            raise
        except Exception as mark_error:  # never let bookkeeping hide the data
            logger.warning(
                "Could not record partial status for path_id=%s: %s",
                path_id, mark_error,
            )

    # ============================================================
    # PUNCTUATION OPERATIONS
    # ============================================================

    def create_punctuation(self, punctuation_text: str) -> Optional[int]:
        """Create or get punctuation entry."""
        try:
            result = self.punctuation_repo.get_or_create_punctuation(punctuation_text)
            if result:
                return result[0] if isinstance(result, tuple) else result
            return None
        except Exception as e:
            logger.error(f"Error creating punctuation: {e}")
            return None

    def get_all_punctuation(self) -> List[Tuple[int, str]]:
        """Get all punctuation entries."""
        return self.punctuation_repo.select_all_punctuation()

    def get_punctuation_by_id(self, punctuation_id: int) -> Optional[Tuple]:
        """Get punctuation by ID."""
        return self.punctuation_repo.get_by_id(punctuation_id)

    def get_punctuation_by_text(self, punctuation_text: str) -> Optional[Tuple]:
        """Get punctuation by text."""
        return self.punctuation_repo.get_by_text(punctuation_text)

    def search_punctuation(self, search: Optional[str] = None, limit: int = 50) -> List[Tuple]:
        """Search punctuation entries."""
        return self.punctuation_repo.search_punctuation(search, limit)

    def update_punctuation(self, punctuation_id: int, punctuation_text: str) -> bool:
        """Update punctuation text."""
        try:
            self.punctuation_repo.update_punctuation(punctuation_id, punctuation_text)
            return True
        except Exception as e:
            logger.error(f"Error updating punctuation: {e}")
            return False

    def delete_punctuation(self, punctuation_id: int) -> bool:
        """Delete punctuation entry."""
        try:
            self.punctuation_repo.delete_punctuation(punctuation_id)
            return True
        except Exception as e:
            logger.error(f"Error deleting punctuation: {e}")
            return False

    # ============================================================
    # ALERT OPERATIONS
    # ============================================================

    def create_alert(
        self,
        alert_type: str,
        priority: str,
        title: str,
        message: str,
        file_id: Optional[int] = None,
        file_name: Optional[str] = None,
        file_path: Optional[str] = None,
        event_date: Optional[date] = None,
        metadata: Optional[Dict[str, any]] = None,
        read: bool = False,
        dismissed: bool = False
        ) -> Optional[int]:
        """Create a new alert."""
        try:
            result = self.alerts_repo.insert_alert(
                alert_type, priority, title, message, file_id, file_name,
                file_path, event_date, metadata, read, dismissed
            )
            if result:
                return result[0] if isinstance(result, tuple) else result
            return None
        except Exception as e:
            logger.error(f"Error creating alert: {e}")
            return None

    def get_all_alerts(self) -> List[Tuple]:
        """Get all alerts."""
        return self.alerts_repo.select_all_alerts()

    def get_alert_by_id(self, alert_id: int) -> Optional[Tuple]:
        """Get alert by ID."""
        return self.alerts_repo.get_by_id(alert_id)

    def get_unread_alerts(self, limit: int = 50) -> List[Tuple]:
        """Get unread alerts."""
        return self.alerts_repo.get_unread_alerts(limit)

    def get_active_alerts(self, limit: int = 50) -> List[Tuple]:
        """Get active (not dismissed) alerts."""
        return self.alerts_repo.get_active_alerts(limit)

    def get_alerts_by_file_id(self, file_id: int) -> List[Tuple]:
        """Get alerts by file ID."""
        return self.alerts_repo.get_by_file_id(file_id)

    def get_alerts_by_type(self, alert_type: str, limit: int = 50) -> List[Tuple]:
        """Get alerts by type."""
        return self.alerts_repo.get_by_type(alert_type, limit)

    def get_alerts_by_priority(self, priority: str, limit: int = 50) -> List[Tuple]:
        """Get alerts by priority."""
        return self.alerts_repo.get_by_priority(priority, limit)

    def search_alerts(
        self,
        alert_type: Optional[str] = None,
        priority: Optional[str] = None,
        read: Optional[bool] = None,
        dismissed: Optional[bool] = None,
        search_text: Optional[str] = None,
        limit: int = 50
    ) -> List[Tuple]:
        """Search alerts with multiple filters."""
        return self.alerts_repo.search_alerts(
            alert_type, priority, read, dismissed, search_text, limit
        )

    def mark_alert_as_read(self, alert_id: int) -> bool:
        """Mark alert as read."""
        try:
            self.alerts_repo.mark_as_read(alert_id)
            return True
        except Exception as e:
            logger.error(f"Error marking alert as read: {e}")
            return False

    def mark_alert_as_dismissed(self, alert_id: int) -> bool:
        """Mark alert as dismissed."""
        try:
            self.alerts_repo.mark_as_dismissed(alert_id)
            return True
        except Exception as e:
            logger.error(f"Error marking alert as dismissed: {e}")
            return False

    def update_alert(
        self,
        alert_id: int,
        alert_type: Optional[str] = None,
        priority: Optional[str] = None,
        title: Optional[str] = None,
        message: Optional[str] = None,
        file_id: Optional[int] = None,
        file_name: Optional[str] = None,
        file_path: Optional[str] = None,
        event_date: Optional[date] = None,
        metadata: Optional[Dict[str, any]] = None,
        read: Optional[bool] = None,
        dismissed: Optional[bool] = None
    ) -> bool:
        """Update alert."""
        try:
            self.alerts_repo.update_alert(
                alert_id, alert_type, priority, title, message, file_id,
                file_name, file_path, event_date, metadata, read, dismissed
            )
            return True
        except Exception as e:
            logger.error(f"Error updating alert: {e}")
            return False

    def delete_alert(self, alert_id: int) -> bool:
        """Delete alert."""
        try:
            self.alerts_repo.delete_alert(alert_id)
            return True
        except Exception as e:
            logger.error(f"Error deleting alert: {e}")
            return False

    def count_unread_alerts(self) -> int:
        """Count unread alerts."""
        return self.alerts_repo.count_unread()

    def count_active_alerts(self) -> int:
        """Count active (not dismissed) alerts."""
        return self.alerts_repo.count_active()
        
