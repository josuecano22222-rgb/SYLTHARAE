"""
Word Repository
Handles all database operations for the words table
"""

import threading
from typing import Optional, Dict

from apps.importing.database.transaction_manager import TransactionManager
from apps.importing.utils.logger import get_logger
from apps.importing.utils.exceptions import RepositoryException
from apps.importing.utils.constants import TABLE_WORDS

logger = get_logger(__name__)


class WordRepository:
    """Repository for words table operations"""
    
    def __init__(self, transaction_manager: TransactionManager):
        """
        Initialize word repository
        
        Args:
            transaction_manager: Transaction manager instance
        """
        self.tx_manager = transaction_manager
        self.logger = logger
        
        # In-memory cache for word IDs
        self.word_cache: Dict[str, int] = {}
        self.cache_lock = threading.Lock()
        self.cache_max_size = 50000
    
    def insert(self, word: str) -> Optional[int]:
        """
        Insert a word into the database
        
        The spelling is kept as given. The table's unique index is
        case-**sensitive**, so normalising here (this method used to lowercase)
        means the same word written as ``Networks`` by one writer and
        ``networks`` by another becomes two rows - and, through
        ``categorys.word_id``, two categories. Lookup is therefore
        case-insensitive and keeps whichever spelling is already stored; a new
        row keeps the caller's spelling, which is what the interface displays.

        Args:
            word: Word to insert, stored as given (whitespace trimmed)

        Returns:
            Word ID if successful, None otherwise
        """
        if not word or not word.strip():
            return None

        word = " ".join(word.split())
        key = word.casefold()

        # Check cache first
        cached_id = self._get_from_cache(key)
        if cached_id:
            return cached_id

        try:
            # Try to get existing word first, matching case-insensitively so a
            # word already stored in another casing is reused rather than
            # duplicated. An exact match wins when both spellings exist.
            existing = self._find(word)
            if existing:
                word_id = existing
                self._add_to_cache(key, word_id)
                return word_id

            # Insert new word with ON CONFLICT to handle race conditions
            # This ensures no duplicate even if two threads try to insert simultaneously
            result = self.tx_manager.execute_query(
                f"INSERT INTO {TABLE_WORDS} (word) VALUES (%s) "
                f"ON CONFLICT (word) DO NOTHING RETURNING id",
                (word,),
                fetch=True
            )

            if result:
                # Insert succeeded
                word_id = result[0][0]
                self._add_to_cache(key, word_id)
                self.logger.debug(f"Inserted word: '{word}' (ID: {word_id})")
                return word_id
            else:
                # Conflict occurred, word was inserted by another thread
                # Fetch the ID now
                existing = self._find(word)
                if existing:
                    word_id = existing
                    self._add_to_cache(key, word_id)
                    return word_id
            
            return None
            
        except Exception as e:
            error_str = str(e)
            # Check for encoding errors
            if 'encoding' in error_str.lower() and ('utf8' in error_str.lower() or 'win1252' in error_str.lower()):
                self.logger.error(
                    f"Failed to insert word '{word}': Database encoding error. "
                    f"The database encoding is not UTF-8, which is required for Unicode characters. "
                    f"Please recreate the database with UTF-8 encoding."
                )
            else:
                self.logger.error(f"Failed to insert word '{word}': {e}")
            raise RepositoryException(f"Word insertion failed: {e}") from e
    
    def _find(self, word: str) -> Optional[int]:
        """The id of ``word``, matched case-insensitively, or None.

        The exact spelling wins if both casings are present, so a lookup never
        oscillates between two rows.
        """
        rows = self.tx_manager.execute_query(
            f"SELECT id FROM {TABLE_WORDS} "
            "WHERE lower(word) = lower(%s) ORDER BY (word = %s) DESC, id LIMIT 1",
            (word, word),
            fetch=True
        )
        return rows[0][0] if rows else None

    def _get_from_cache(self, word: str) -> Optional[int]:
        """Get word ID from cache"""
        with self.cache_lock:
            return self.word_cache.get(word)
    
    def _add_to_cache(self, word: str, word_id: int):
        """Add word ID to cache"""
        with self.cache_lock:
            self.word_cache[word] = word_id
            
            # Limit cache size
            if len(self.word_cache) > self.cache_max_size:
                # Remove 10% oldest entries
                keys_to_remove = list(self.word_cache.keys())[:self.cache_max_size // 10]
                for key in keys_to_remove:
                    del self.word_cache[key]
    
