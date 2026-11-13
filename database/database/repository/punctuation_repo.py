from .best_repo import BaseRepository
from ..queries.punctuation_queries import PunctuationQueries

from core.word_limits import MAX_WORD_BYTES, word_exceeds_db_limit

import logging

logger = logging.getLogger(__name__)


class PunctuationRepository(BaseRepository):
    """Repository for punctuation operations"""

    def insert_punctuation(self, punctuation_text):
        """Insert a new punctuation entry and return its ID"""
        return self.execute(
            PunctuationQueries.insert_punctuation(),
            (punctuation_text,),
            fetchone=True
        )

    def update_punctuation(self, punctuation_id, punctuation_text):
        """Update punctuation text"""
        return self.execute(
            PunctuationQueries.update_punctuation(),
            (punctuation_text, punctuation_id)
        )

    def select_all_punctuation(self):
        """Get all punctuation entries"""
        return self.execute(
            PunctuationQueries.get_all(),
            None,
            fetchall=True
        )

    def get_by_id(self, punctuation_id):
        """Get punctuation by ID"""
        return self.execute(
            PunctuationQueries.get_by_id(),
            (punctuation_id,),
            fetchone=True
        )

    def get_by_text(self, punctuation_text):
        """Get punctuation by text"""
        return self.execute(
            PunctuationQueries.get_by_text(),
            (punctuation_text,),
            fetchone=True
        )

    def search_punctuation(self, search=None, limit=50):
        """Search punctuation with optional search term"""
        search_param = f"%{search}%" if search else None
        return self.execute(
            PunctuationQueries.search_punctuation(),
            (search, search_param, limit),
            fetchall=True
        )

    def delete_punctuation(self, punctuation_id):
        """Delete a punctuation entry"""
        return self.execute(
            PunctuationQueries.delete_punctuation(),
            (punctuation_id,)
        )

    def get_or_create_punctuation(self, punctuation_text):
        """Get existing punctuation or create new one"""
        return self.execute(
            PunctuationQueries.get_or_create_punctuation(),
            (punctuation_text,),
            fetchone=True
        )
    
    def get_punctuation_ids_batch(self, punctuation_texts: list[str]) -> dict:
        """
        Get punctuation IDs for multiple punctuation marks at once.
        
        Args:
            punctuation_texts: List of punctuation mark strings
        
        Returns:
            Dictionary mapping punctuation_text -> id
        """
        if not punctuation_texts:
            return {}
        rows = self.execute(
            PunctuationQueries.get_punctuation_ids_batch(),
            (punctuation_texts,),
            fetchall=True
        )
        return {punct_text: punct_id for punct_text, punct_id in rows}
    
    def bulk_insert_punctuation(self, punctuation_texts: list[str]):
        """
        Bulk insert punctuation marks (inserts only new ones).
        
        Args:
            punctuation_texts: List of punctuation mark strings to insert
        """
        if not punctuation_texts:
            return
        
        # Remove duplicates
        unique_puncts = list(set(punctuation_texts))
        if not unique_puncts:
            return
        
        # PostgreSQL btree indexes (punctuation_text is TEXT UNIQUE) cannot
        # hold entries larger than ~1/3 of a buffer page (2704 bytes); one
        # oversized entry would abort the whole statement.  Refuse such
        # entries up front (mirrors the guard in WordsRepository).
        safe_puncts = [p for p in unique_puncts if not word_exceeds_db_limit(p)]
        skipped = len(unique_puncts) - len(safe_puncts)
        if skipped:
            logger.warning(
                "Skipped %d oversized punctuation token(s) exceeding the "
                "%d-byte PostgreSQL btree index limit.",
                skipped, MAX_WORD_BYTES,
            )
        if not safe_puncts:
            return

        # Build placeholders for VALUES clause
        placeholders = ",".join(["(%s)"] * len(safe_puncts))
        query = PunctuationQueries.bulk_insert_punctuation(placeholders)
        
        # Execute with tuple of values
        self.execute(query, tuple(safe_puncts))
    
    def resolve_punctuation_ids_batch(self, punctuation_texts: list[str]) -> dict:
        """
        Resolve punctuation IDs for a list of punctuation marks.
        Inserts missing punctuation and returns mapping.
        
        Args:
            punctuation_texts: List of punctuation mark strings
        
        Returns:
            Dictionary mapping punctuation_text -> id
        """
        if not punctuation_texts:
            return {}
        
        # Remove duplicates and empty strings
        unique_puncts = list(set([p for p in punctuation_texts if p]))
        if not unique_puncts:
            return {}
        
        # Bulk insert missing punctuation
        self.bulk_insert_punctuation(unique_puncts)
        
        # Get all IDs (including newly inserted)
        return self.get_punctuation_ids_batch(unique_puncts)
