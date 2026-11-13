"""
Term Processor
Main processor that routes terms to appropriate handlers
"""

from typing import Optional
from apps.importing.data.models import Term, TermType
from apps.importing.processors.word_processor import WordProcessor
from apps.importing.processors.phrase_processor import PhraseProcessor
from apps.importing.validators.term_validator import TermValidator
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)


class TermProcessor:
    """Routes terms to appropriate processor based on type"""
    
    def __init__(
        self,
        word_processor: WordProcessor,
        phrase_processor: PhraseProcessor,
        validator: TermValidator
    ):
        """
        Initialize term processor
        
        Args:
            word_processor: Word processor instance
            phrase_processor: Phrase processor instance
            validator: Term validator instance
        """
        self.word_processor = word_processor
        self.phrase_processor = phrase_processor
        self.validator = validator
        self.logger = logger
    
    def process(self, term: Term, category_id: int) -> Optional[int]:
        """
        Process a term by routing to appropriate processor
        
        Args:
            term: Term to process (word or phrase)
            category_id: Category ID to associate with
            
        Returns:
            ID (word_id or keyword_id) if successful, None otherwise
        """
        # Validate term first
        is_valid, error = self.validator.validate(term)
        if not is_valid:
            self.logger.debug(f"Invalid term '{term.text}': {error}")
            return None
        
        # Route to appropriate processor
        if term.is_single_word():
            return self.word_processor.process(term, category_id)
        elif term.is_phrase():
            return self.phrase_processor.process(term, category_id)
        else:
            self.logger.error(f"Unknown term type: {term.term_type}")
            return None
    
