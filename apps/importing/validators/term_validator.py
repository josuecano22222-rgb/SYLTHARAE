"""
Term Validator
Validates terms (words and phrases) before processing
"""

from typing import Tuple
from apps.importing.data.models import Term
from apps.importing.utils.logger import get_logger
from apps.importing.utils.exceptions import ValidationException

logger = get_logger(__name__)


class TermValidator:
    """Validates terms according to business rules"""
    
    def __init__(self):
        self.logger = logger
    
    def validate(self, term: Term) -> Tuple[bool, str]:
        """
        Validate a term
        
        Args:
            term: Term to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if term is empty
        if not term.text or not term.text.strip():
            return False, "Term is empty"
        
        # Check for whitespace in single words
        if term.is_single_word():
            if ' ' in term.text or '\t' in term.text or '\n' in term.text:
                return False, "Single word contains whitespace"
        
        # Check minimum words for phrases
        if term.is_phrase():
            if term.word_count < 2:
                return False, "Phrase must have at least 2 words"
        
        return True, ""
    
    def validate_batch(self, terms: list[Term]) -> Tuple[list[Term], list[Tuple[Term, str]]]:
        """
        Validate a batch of terms
        
        Args:
            terms: List of terms to validate
            
        Returns:
            Tuple of (valid_terms, invalid_terms_with_reasons)
        """
        valid = []
        invalid = []
        
        for term in terms:
            is_valid, error = self.validate(term)
            if is_valid:
                valid.append(term)
            else:
                invalid.append((term, error))
                self.logger.debug(f"Invalid term '{term.text}': {error}")
        
        return valid, invalid
