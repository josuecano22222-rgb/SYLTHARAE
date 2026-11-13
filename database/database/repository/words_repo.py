from .best_repo import BaseRepository
import logging

from ..queries.word_queries import WordQueries
from core.word_limits import MAX_WORD_BYTES, word_exceeds_db_limit

logger = logging.getLogger(__name__)

class WordsRepository(BaseRepository):

    def insert_by_word(self, word):

        return self.execute(WordQueries.insert_word(), (word,), True)
    
    def update_word(self, word_id, word):

        return self.execute(WordQueries.update_word(), (word, word_id), True)

    def bulk_insert_words(self, words):
        """
        Bulk insert words into the dictionary, ignoring duplicates.

        Uses one ``INSERT ... ON CONFLICT DO NOTHING`` per page instead of the
        old session-scoped ``tmp_words`` + ``COPY`` pipeline: it is atomic,
        needs no temporary objects and is safe to run from several threads
        against a shared connection pool.

        Very large word lists (>100K words) are still processed in batches so a
        single document cannot spike memory.

        Args:
            words: List of word tuples or strings to insert
        """
        if not words:
            return []
        
        # For very large word lists (>100K words), process in batches to keep
        # the per-call working set bounded.
        total_words = len(words)
        batch_size = 100000  # 100K words per batch
        use_batching = total_words > batch_size
        
        if use_batching:
            logger.info(f"Large word list detected ({total_words:,} words), processing in batches of {batch_size:,}")
            
            # Process in batches
            for batch_start in range(0, total_words, batch_size):
                batch_end = min(batch_start + batch_size, total_words)
                batch_words = words[batch_start:batch_end]
                
                # Process this batch
                self._bulk_insert_words_batch(batch_words)
                
                # Log progress
                if (batch_start + batch_size) % (batch_size * 10) == 0 or batch_end >= total_words:
                    progress_pct = (batch_end / total_words) * 100
                    logger.debug(f"Processed {batch_end:,} / {total_words:,} words ({progress_pct:.1f}%)")
        else:
            # Standard processing for smaller lists
            self._bulk_insert_words_batch(words)
    
    def _bulk_insert_words_batch(self, words):
        """
        Internal method inserting one batch of words into the dictionary.

        Empty and oversized tokens are filtered out first; the remaining words
        go to the database in paged ``INSERT ... ON CONFLICT DO NOTHING``
        statements (see :meth:`BaseRepository.bulk_upsert_words`).

        Args:
            words: List of word tuples or strings to insert
        """
        if not words:
            return

        valid_words = []
        oversized_skipped = 0
        oversized_longest = 0

        # Import CPU management for large bulk operations
        try:
            from core.resource_coordinator import should_yield, get_yield_duration
            import time
            cpu_management_available = True
        except ImportError:
            cpu_management_available = False

        for row_idx, row in enumerate(words):
            # Yield periodically during bulk processing
            if cpu_management_available and row_idx > 0 and row_idx % 1000 == 0:
                if should_yield():
                    time.sleep(get_yield_duration())

            # Ensure we only write the word text, not IDs
            # row should be a tuple like (word,) or just the word string
            if isinstance(row, tuple):
                word_text = row[0] if len(row) > 0 else str(row)
            else:
                word_text = str(row)

            # Handle None and empty strings, and ensure we have a valid string
            if word_text is None:
                word_text = ''
            else:
                word_text = str(word_text).strip()

            # Skip empty words (but log for debugging)
            if not word_text:
                continue

            # PostgreSQL btree indexes cannot hold entries larger than ~1/3
            # of a buffer page (2704 bytes).  A single oversized token (e.g.
            # a multi-KB base64/data-URI run that survives tokenization as
            # one "word") would abort the whole bulk insert and with it the
            # entire document transaction, so oversized tokens are refused
            # here.  The raw display store keeps the verbatim text.
            if word_exceeds_db_limit(word_text):
                oversized_skipped += 1
                if len(word_text) > oversized_longest:
                    oversized_longest = len(word_text)
                continue

            valid_words.append((word_text,))

        if oversized_skipped:
            logger.warning(
                "Skipped %d oversized token(s) (longest %d chars) that exceed "
                "the %d-byte PostgreSQL btree index limit; the rest of the "
                "document is stored normally and the raw display text is "
                "unaffected.",
                oversized_skipped, oversized_longest, MAX_WORD_BYTES,
            )

        if not valid_words:
            return

        self.bulk_upsert_words(valid_words)

    def select_all_words(self):

        return self.execute(WordQueries.get_all(), None, False, True)
    
    def select_content_words_by_ids(self, ids):

        dictionary = dict(self.execute(WordQueries.get_all(), None, False, True))
        return " ".join(dictionary[i] for i in ids)

    def select_content_ids_by_words(self, words):
        """Resolve word strings to their dictionary ids, preserving order.

        Only the words actually asked for are looked up (batched ``WHERE word
        = ANY(%s)`` queries).  The previous implementation loaded the *entire*
        words table into a Python dict on every call, which made storing one
        document cost O(number of words in the database).

        Missing words are skipped, exactly as before: callers persist content
        relative to the ids that exist.
        """
        if not words:
            return []

        # Deduplicate while preserving order so the lookup array stays small
        # even for documents that repeat the same words thousands of times.
        unique_words = list(dict.fromkeys(words))
        mapping = self.get_word_ids_batch(unique_words)

        result = []
        for word in words:
            word_id = mapping.get(word)
            if word_id is not None:
                result.append(int(word_id))
        return result

    def select_words_by_ids(self, ids):
        """Return the word strings for the given word ids, preserving order.

        Counterpart of :meth:`select_content_ids_by_words`; used wherever ids
        (not words) are the input.  Unknown ids are skipped.
        """
        if not ids:
            return []

        mapping = {}
        batch_size = 10000
        ordered = list(dict.fromkeys(ids))
        for start in range(0, len(ordered), batch_size):
            batch = ordered[start:start + batch_size]
            rows = self.execute(
                WordQueries.get_words_by_ids(),
                (batch,),
                fetchall=True
            )
            if rows:
                mapping.update({int(row[0]): row[1] for row in rows})

        return [mapping[i] for i in ids if i in mapping]
    
    def select_by_id(self, id):

        self.execute(WordQueries.get_by_id(), (id,), True)
    
    def check_word_exists(self, word: str):
        row = self.execute(
            WordQueries.check_word_exists(),
            (word,),
            fetch_one=True
        )
        return row[0] if row else None

    def get_word_ids_batch(self, words: list[str]) -> dict:
        """
        Returns: {word: id}
        """
        if not words:
            return {}
        rows = self.execute(
            WordQueries.get_word_ids_batch(),
            (words,),
            fetch_all=True
        )
        return {word: id for word, id in rows}
    
    def resolve_word_ids_batch(self, words: list[str]) -> dict:
        """
        Resolve word IDs for a list of words.
        Inserts missing words and returns mapping.
        
        Args:
            words: List of word strings
        
        Returns:
            Dictionary mapping word -> id
        """
        if not words:
            return {}
        
        # Remove duplicates
        unique_words = list(set(words))
        
        # Bulk insert missing words
        word_tuples = [(w,) for w in unique_words]
        self.bulk_insert_words(word_tuples)
        
        # Get all IDs (including newly inserted)
        return self.get_word_ids_batch(unique_words)

    def get_words_by_file(self, path_id: int, limit: int = 100):
        return self.execute(
            WordQueries.get_words_by_file(),
            (path_id, limit),
            fetch_all=True
        )

    def get_word_frequencies(self, path_id: int, limit: int = 100):
        return self.execute(
            WordQueries.get_word_frequencies(),
            (path_id, limit),
            fetch_all=True
        )

    def get_word_frequency_total(self, word_id: int) -> int:
        row = self.execute(
            WordQueries.get_word_frequency_total(),
            (word_id,),
            fetch_one=True
        )
        return row[0] or 0

    def get_word_usage_count(self, word_id: int) -> int:
        row = self.execute(
            WordQueries.get_word_usage_count(),
            (word_id,),
            fetch_one=True
        )
        return row[0] or 0

    def get_words_with_usage(self, search: str, limit: int, offset: int):
        return self.execute(
            WordQueries.get_words_with_usage(),
            (search, limit, offset),
            fetch_all=True
        )

    def get_email_words(
        self,
        like_pattern: str,
        not_like_pattern: str,
        search: str | None,
        domain: str | None,
        domain_mode: str = 'exact',
        sort_by: str = 'usage_count',
        sort_order: str = 'desc',
        limit: int = 50,
        offset: int = 0,
        source_id: int | None = None,
        side_id: int | None = None,
    ):
        """
        Get email words with filters and sorting.
        
        Args:
            like_pattern: Pattern for email validation (e.g., '%@%')
            not_like_pattern: Pattern to exclude invalid emails (e.g., '%@%@%')
            search: Optional search term
            domain: Optional domain filter
            domain_mode: 'exact', 'contains', or 'endswith'
            sort_by: 'word' or 'usage_count'
            sort_order: 'asc' or 'desc'
            limit: Page size
            offset: Pagination offset
            source_id: Optional source filter
            side_id: Optional side (aspect) filter
        """
        # Validate and normalize inputs
        domain_mode = domain_mode.lower() if domain_mode else 'exact'
        if domain_mode not in ('exact', 'contains', 'endswith'):
            domain_mode = 'exact'
        
        sort_by = sort_by.lower() if sort_by else 'usage_count'
        if sort_by not in ('word', 'usage_count'):
            sort_by = 'usage_count'
        
        sort_order = sort_order.upper() if sort_order else 'DESC'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        try:
            source_id = int(source_id) if source_id is not None else None
        except (TypeError, ValueError):
            source_id = None
        try:
            side_id = int(side_id) if side_id is not None else None
        except (TypeError, ValueError):
            side_id = None
        
        # Build domain filter patterns based on mode
        domain_exact_param = None
        domain_contains_param = None
        domain_endswith_param = None
        
        if domain:
            domain_lower = domain.lower()
            if domain_mode == 'exact':
                domain_exact_param = domain_lower
            elif domain_mode == 'contains':
                domain_contains_param = f"%{domain_lower}%"
            elif domain_mode == 'endswith':
                domain_endswith_param = f"%{domain_lower}"
        
        # Build ORDER BY clause dynamically
        if sort_by == 'word':
            order_by = f"ORDER BY w.word {sort_order}, COUNT(DISTINCT p.id) DESC"
        else:  # sort_by == 'usage_count'
            order_by = f"ORDER BY COUNT(DISTINCT p.id) {sort_order}, w.word ASC"
        
        # Build complete query with dynamic ORDER BY
        base_query = WordQueries.get_email_words()
        # Insert ORDER BY before LIMIT
        query = base_query.replace("LIMIT %s OFFSET %s", f"{order_by}\n            LIMIT %s OFFSET %s")
        
        # Build parameters - pass each domain param twice (once for IS NOT NULL check, once for value)
        params = (
            like_pattern,
            not_like_pattern,
            search,
            f"%{search}%" if search else None,
            domain,  # NULL check - if NULL, skip all domain filtering
            domain_exact_param,  # IS NOT NULL check for exact
            domain_exact_param,  # Value for exact match
            domain_contains_param,  # IS NOT NULL check for contains
            domain_contains_param,  # Value for contains match
            domain_endswith_param,  # IS NOT NULL check for endswith
            domain_endswith_param,  # Value for endswith match
            source_id,
            source_id,
            side_id,
            side_id,
            limit,
            offset,
        )
        
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Executing email words query with {len(params)} parameters")
        logger.debug(f"Parameters: domain={domain}, domain_mode={domain_mode}, search={search}, "
                     f"source_id={source_id}, side_id={side_id}, limit={limit}, offset={offset}")
        
        try:
            results = self.execute(
                query,
                params,
                fetch_all=True
            )
            logger.debug(f"Query returned {len(results) if results else 0} results")
            return results
        except Exception as e:
            logger.error(f"Error executing email words query: {e}", exc_info=True)
            # Log the actual query for debugging
            logger.debug(f"Query was: {query[:500]}...")
            raise

    def get_email_domains(
        self,
        like_pattern: str,
        not_like_pattern: str,
        search: str | None = None,
        domain: str | None = None,
        domain_mode: str = 'exact',
        limit: int = 10000
    ):
        """
        Get email domains with counts, supporting filters.
        
        Args:
            like_pattern: Pattern for email validation (e.g., '%@%')
            not_like_pattern: Pattern to exclude invalid emails (e.g., '%@%@%')
            search: Optional search term
            domain: Optional domain filter
            domain_mode: 'exact', 'contains', or 'endswith'
            limit: Maximum number of domains to return
        """
        # Validate and normalize inputs
        domain_mode = domain_mode.lower() if domain_mode else 'exact'
        if domain_mode not in ('exact', 'contains', 'endswith'):
            domain_mode = 'exact'
        
        # Build domain filter patterns based on mode (match get_email_words logic)
        domain_exact_param = None
        domain_contains_param = None
        domain_endswith_param = None
        
        if domain:
            domain_lower = domain.lower()
            if domain_mode == 'exact':
                domain_exact_param = domain_lower
            elif domain_mode == 'contains':
                domain_contains_param = f"%{domain_lower}%"
            elif domain_mode == 'endswith':
                domain_endswith_param = f"%{domain_lower}"
        
        return self.execute(
            WordQueries.get_email_domains(),
            (
                like_pattern,
                not_like_pattern,
                search,
                f"%{search}%" if search else None,
                domain,  # NULL check - if NULL, skip all domain filtering
                domain_exact_param,  # For exact match (or NULL)
                domain_contains_param,  # For contains match (or NULL)
                domain_endswith_param,  # For endswith match (or NULL)
                limit,
            ),
            fetch_all=True
        )
        


    
    
  