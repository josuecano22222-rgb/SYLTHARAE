"""
Word Processor
Processes single words for import
"""

from typing import Optional
from apps.importing.data.models import Term
from apps.importing.database.repositories.word_repository import WordRepository
from apps.importing.database.repositories.category_repository import CategoryRepository
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)


class WordProcessor:
    """Processes single words for database insertion"""
    
    def __init__(
        self,
        word_repo: WordRepository,
        category_repo: CategoryRepository
    ):
        """
        Initialize word processor
        
        Args:
            word_repo: Word repository instance
            category_repo: Category repository instance
        """
        self.word_repo = word_repo
        self.category_repo = category_repo
        self.logger = logger
    
    def process(self, term: Term, category_id: int) -> Optional[int]:
        """
        Process a single word term
        
        Process:
        1. Insert word into words table
        2. Link word to category in words_categorys table
        
        Args:
            term: Single word term to process
            category_id: Category ID to link to
            
        Returns:
            Word ID if successful, None otherwise
        """
        if not term.is_single_word():
            self.logger.error(f"Term '{term.text}' is not a single word")
            return None
        
        try:
            # Insert word (or get existing ID)
            word_id = self.word_repo.insert(term.text)
            
            if not word_id:
                self.logger.warning(f"Failed to insert word: '{term.text}'")
                return None
            
            # Link word to category
            linked = self.category_repo.link_word_to_category(word_id, category_id)
            
            if not linked:
                self.logger.warning(
                    f"Word '{term.text}' inserted but failed to link to category"
                )
                return None
            
            self.logger.debug(f"Processed word: '{term.text}' (ID: {word_id})")
            return word_id
            
        except Exception as e:
            self.logger.error(f"Failed to process word '{term.text}': {e}")
            return None
