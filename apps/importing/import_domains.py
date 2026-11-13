"""
Domain Import Application - Main Entry Point
Complete restructured architecture with single-responsibility classes
"""

import sys
from pathlib import Path

# All intra-app imports use full package paths (apps.importing.*); the old
# sys.path insertions that shadowed core packages (``database``, ``utils``)
# were removed - they caused circular imports when the app ran in-process.
from settings import get_database_config, get_settings
from apps.importing.utils.logger import LoggerFactory, get_logger
from apps.importing.utils.exceptions import DomainImportException
from apps.importing.utils.constants import DEFAULT_DATA_FILE, DATA_FILE_SEARCH_PATHS

# Data components
from apps.importing.data.loader import DataLoaderFactory
from apps.importing.data.parser import DataParser

# Database components - use importing app's local database module
from apps.importing.database.connection_pool import ConnectionPool
from apps.importing.database.transaction_manager import TransactionManager
from apps.importing.database.repositories.word_repository import WordRepository
from apps.importing.database.repositories.category_repository import CategoryRepository
from apps.importing.database.repositories.keyword_repository import KeywordRepository

# Validators
from apps.importing.validators.term_validator import TermValidator

# Processors
from apps.importing.processors.word_processor import WordProcessor
from apps.importing.processors.phrase_processor import PhraseProcessor
from apps.importing.processors.term_processor import TermProcessor

# Core components - use absolute import since orchestrator.py uses absolute imports
from apps.importing.core.orchestrator import ImportOrchestrator
from apps.importing.core.statistics import StatisticsManager


class Application:
    """Main application class - dependency injection container"""
    
    def __init__(self):
        """Initialize application with all dependencies"""
        # Configure logging
        LoggerFactory.configure()
        self.logger = get_logger(__name__)
        
        # Initialize components
        self._init_database()
        self._init_repositories()
        self._init_validators()
        self._init_processors()
        self._init_core()
    
    def _init_database(self):
        """Initialize database components"""
        self.logger.info("Initializing database connection pool...")
        
        # Get database config and convert to dict format expected by ConnectionPool
        db_config_obj = get_database_config()
        db_config = {
            'host': db_config_obj.host,
            'port': db_config_obj.port,
            'database': db_config_obj.database,
            'user': db_config_obj.user,
            'password': db_config_obj.password or '',
            'min_connections': db_config_obj.pool_min_conn,
            'max_connections': db_config_obj.pool_max_conn,
        }
        self.connection_pool = ConnectionPool(db_config)
        self.transaction_manager = TransactionManager(self.connection_pool)
    
    def _init_repositories(self):
        """Initialize repository layer"""
        self.logger.info("Initializing repositories...")
        
        self.word_repo = WordRepository(self.transaction_manager)
        self.category_repo = CategoryRepository(self.transaction_manager)
        self.keyword_repo = KeywordRepository(self.transaction_manager)
    
    def _init_validators(self):
        """Initialize validators"""
        self.logger.info("Initializing validators...")
        
        self.term_validator = TermValidator()
    
    def _init_processors(self):
        """Initialize processing layer"""
        self.logger.info("Initializing processors...")
        
        self.word_processor = WordProcessor(
            self.word_repo,
            self.category_repo
        )
        
        self.phrase_processor = PhraseProcessor(
            self.word_repo,
            self.keyword_repo
        )
        
        self.term_processor = TermProcessor(
            self.word_processor,
            self.phrase_processor,
            self.term_validator
        )
    
    def _init_core(self):
        """Initialize core components"""
        self.logger.info("Initializing core components...")
        
        self.data_loader = DataLoaderFactory()
        self.data_parser = DataParser()
        self.stats_manager = StatisticsManager()
        
        self.orchestrator = ImportOrchestrator(
            self.data_loader,
            self.data_parser,
            self.word_repo,
            self.category_repo,
            self.term_processor,
            self.stats_manager
        )
    
    def run(self, data_file=None):
        """Run the import process"""
        try:
            self.logger.info("=" * 80)
            self.logger.info("Domain Import Application")
            self.logger.info("=" * 80)
            
            # Determine data file path
            if not data_file:
                # Try to find data file in search paths
                for search_path in DATA_FILE_SEARCH_PATHS:
                    potential_file = Path(search_path) / DEFAULT_DATA_FILE
                    if potential_file.exists():
                        data_file = str(potential_file)
                        break
                
                # If still not found, use default
                if not data_file:
                    data_file = DEFAULT_DATA_FILE
            
            self.logger.info(f"Data file: {data_file}")
            self.logger.info("")
            
            # Run import
            results = self.orchestrator.import_all(data_file)
            
            self.logger.info("\n✅ Import completed successfully")
            return results
            
        except DomainImportException as e:
            self.logger.error(f"❌ Import failed: {e}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Unexpected error: {e}", exc_info=True)
            return None
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Cleaning up resources...")
        self.connection_pool.close_all()


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Import domain classification data')
    parser.add_argument('--data-file', type=str, help='Path to data file (Excel, CSV, or JSON)')
    args = parser.parse_args()
    
    try:
        app = Application()
        results = app.run(data_file=args.data_file)
        
        if results:
            sys.exit(0)
        else:
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
