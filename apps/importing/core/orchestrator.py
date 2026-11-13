"""
Import Orchestrator
Coordinates the entire domain import process
"""

from typing import List, Dict, Any
from apps.importing.data.loader import DataLoaderFactory
from apps.importing.data.parser import DataParser
from apps.importing.data.models import Domain, ImportResult
from apps.importing.database.repositories.word_repository import WordRepository
from apps.importing.database.repositories.category_repository import CategoryRepository
from apps.importing.processors.term_processor import TermProcessor
from apps.importing.core.statistics import StatisticsManager
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)


class ImportOrchestrator:
    """Orchestrates the domain import process"""
    
    def __init__(
        self,
        data_loader: DataLoaderFactory,
        data_parser: DataParser,
        word_repo: WordRepository,
        category_repo: CategoryRepository,
        term_processor: TermProcessor,
        stats_manager: StatisticsManager
    ):
        """
        Initialize import orchestrator
        
        Args:
            data_loader: Data loader factory
            data_parser: Data parser
            word_repo: Word repository
            category_repo: Category repository
            term_processor: Term processor
            stats_manager: Statistics manager
        """
        self.data_loader = data_loader
        self.data_parser = data_parser
        self.word_repo = word_repo
        self.category_repo = category_repo
        self.term_processor = term_processor
        self.stats_manager = stats_manager
        self.logger = logger
    
    def import_all(self, data_file: str) -> Dict[str, Any]:
        """
        Import all domains from data file
        
        Process:
        1. Load data from file (Excel/CSV/JSON)
        2. Parse into Domain objects
        3. For each domain:
           a. Create domain category
           b. Process all terms (words and phrases)
        4. Collect and report statistics
        
        Args:
            data_file: Path to data file
            
        Returns:
            Dictionary with import statistics
        """
        self.logger.info(f"Starting domain import from {data_file}")
        
        try:
            # Step 1: Load raw data
            self.logger.info("Loading data file...")
            raw_data = self.data_loader.load(data_file)
            self.logger.info(f"Loaded {len(raw_data)} domains")
            
            # Step 2: Parse into Domain objects
            self.logger.info("Parsing domain data...")
            domains = self.data_parser.parse(raw_data)
            self.logger.info(f"Parsed {len(domains)} domains")
            
            # Step 3: Import each domain
            self.logger.info("Importing domains to database...")
            for domain in domains:
                result = self._import_domain(domain)
                self.stats_manager.add_domain_result(result)
                self.logger.info(f"Imported {domain.name}: {result}")
            
            # Step 4: Report statistics
            self.stats_manager.print_summary()
            
            return self.stats_manager.get_summary()
            
        except Exception as e:
            self.logger.error(f"Domain import failed: {e}", exc_info=True)
            raise
    
    def _import_domain(self, domain: Domain) -> ImportResult:
        """
        Import a single domain
        
        Process:
        1. Create domain word entry
        2. Create category for domain
        3. Process all terms in domain
        
        Args:
            domain: Domain object to import
            
        Returns:
            ImportResult with statistics
        """
        result = ImportResult(
            domain_name=domain.name,
            total_terms=len(domain)
        )
        
        try:
            # Step 1: Insert domain name as a word
            domain_word_id = self.word_repo.insert(domain.name)
            if not domain_word_id:
                self.logger.error(f"Failed to create domain word '{domain.name}'")
                result.errors = len(domain)
                return result
            
            # Step 2: Create category for domain
            category_id = self.category_repo.create_category(domain_word_id)
            if not category_id:
                self.logger.error(f"Failed to create category for '{domain.name}'")
                result.errors = len(domain)
                return result
            
            self.logger.info(
                f"Created domain '{domain.name}' "
                f"(word_id: {domain_word_id}, category_id: {category_id})"
            )
            
            # Step 3: Process all terms
            for term in domain.terms:
                term_id = self.term_processor.process(term, category_id)
                
                if term_id:
                    if term.is_single_word():
                        result.imported_words += 1
                    else:
                        result.imported_phrases += 1
                else:
                    result.skipped += 1
            
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to import domain '{domain.name}': {e}")
            result.errors = len(domain) - result.imported_words - result.imported_phrases
            return result
    
