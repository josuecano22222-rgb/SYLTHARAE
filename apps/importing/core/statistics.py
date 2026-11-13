"""
Statistics Manager
Collects and reports import statistics
"""

from typing import Dict, Any
from apps.importing.data.models import Statistics, ImportResult
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)


class StatisticsManager:
    """Manages import statistics collection and reporting"""
    
    def __init__(self):
        self.stats = Statistics()
        self.logger = logger
    
    def add_domain_result(self, result: ImportResult):
        """Add a domain import result"""
        self.stats.add_result(result)
        self.stats.total_domains += 1
    
    def get_summary(self) -> Dict[str, Any]:
        """Get statistics summary"""
        return {
            'total_domains': self.stats.total_domains,
            'total_terms': self.stats.total_terms,
            'total_words': self.stats.total_words,
            'total_phrases': self.stats.total_phrases,
            'total_skipped': self.stats.total_skipped,
            'total_errors': self.stats.total_errors,
        }
    
    def print_summary(self):
        """Print formatted summary to console"""
        self.logger.info("\n" + "=" * 80)
        self.logger.info("IMPORT SUMMARY")
        self.logger.info("=" * 80)
        
        summary = self.get_summary()
        self.logger.info(f"Total Domains Processed: {summary['total_domains']}")
        self.logger.info(f"Total Terms: {summary['total_terms']}")
        self.logger.info(f"  - Single Words: {summary['total_words']}")
        self.logger.info(f"  - Multi-word Phrases: {summary['total_phrases']}")
        
        if summary['total_skipped'] > 0:
            self.logger.info(f"Skipped: {summary['total_skipped']}")
        
        if summary['total_errors'] > 0:
            self.logger.warning(f"Errors: {summary['total_errors']}")
        
        self.logger.info("\nPer-Domain Results:")
        self.logger.info("-" * 80)
        
        for result in self.stats.results:
            self.logger.info(
                f"  {result.domain_name}: "
                f"{result.imported_words} words, "
                f"{result.imported_phrases} phrases"
            )
            if result.skipped > 0:
                self.logger.info(f"    (skipped: {result.skipped})")
            if result.errors > 0:
                self.logger.warning(f"    (errors: {result.errors})")
        
        self.logger.info("=" * 80)
