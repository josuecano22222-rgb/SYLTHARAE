# ============================================================================
# processors/__init__.py
# ============================================================================
"""Processors package"""

from .word_processor import WordProcessor
from .phrase_processor import PhraseProcessor
from .term_processor import TermProcessor

__all__ = [
    'WordProcessor',
    'PhraseProcessor',
    'TermProcessor',
]
