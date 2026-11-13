"""
Phrase Processor
Processes multi-word phrases for import
"""

from typing import Optional, List
from apps.importing.data.models import Term
from apps.importing.database.repositories.word_repository import WordRepository
from apps.importing.database.repositories.keyword_repository import KeywordRepository
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)


class PhraseProcessor:
    """Processes multi-word phrases for database insertion"""
    
    def __init__(
        self,
        word_repo: WordRepository,
        keyword_repo: KeywordRepository
    ):
        """
        Initialize phrase processor
        
        Args:
            word_repo: Word repository instance
            keyword_repo: Keyword repository instance
        """
        self.word_repo = word_repo
        self.keyword_repo = keyword_repo
        self.logger = logger
    
    def process(self, term: Term, category_id: int) -> Optional[int]:
        """
        Process a multi-word phrase
        
        Process:
        1. Split phrase into individual words
        2. Insert each word into words table
        3. Get word IDs for all words
        4. Create keyword entry with pickled word IDs
        
        Args:
            term: Multi-word phrase term to process
            category_id: Category ID to associate with
            
        Returns:
            Keyword ID if successful, None otherwise
        """
        if not term.is_phrase():
            self.logger.error(f"Term '{term.text}' is not a multi-word phrase")
            return None
        
        try:
            # Split phrase into words
            words = term.text.lower().split()
            
            if len(words) < 2:
                self.logger.warning(
                    f"Phrase '{term.text}' has less than 2 words after split"
                )
                return None
            
            # Get or insert word IDs for each word
            word_ids = self._get_word_ids(words)
            
            if len(word_ids) != len(words):
                self.logger.warning(
                    f"Failed to get all word IDs for phrase '{term.text}'"
                )
                return None
            
            # Create keyword with word IDs
            keyword_id = self.keyword_repo.create_keyword(word_ids, category_id)
            
            if keyword_id:
                self.logger.debug(
                    f"Processed phrase: '{term.text}' "
                    f"({len(word_ids)} words, ID: {keyword_id})"
                )
            else:
                self.logger.warning(f"Failed to create keyword for '{term.text}'")
            
            return keyword_id
            
        except Exception as e:
            self.logger.error(f"Failed to process phrase '{term.text}': {e}")
            return None
    
    def _get_word_ids(self, words: List[str]) -> List[int]:
        """
        Get or insert word IDs for a list of words
        
        Args:
            words: List of words
            
        Returns:
            List of word IDs (may be shorter if some words fail)
        """
        word_ids = []
        
        for word in words:
            word_id = self.word_repo.insert(word)
            if word_id:
                word_ids.append(word_id)
            else:
                self.logger.warning(f"Failed to get/insert word: '{word}'")
        
        return word_ids
