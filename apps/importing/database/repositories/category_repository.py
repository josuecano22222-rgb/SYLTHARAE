"""
Category Repository
Handles all database operations for categories and words_categorys tables
"""

from typing import Optional

from apps.importing.database.transaction_manager import TransactionManager
from apps.importing.utils.logger import get_logger
from apps.importing.utils.exceptions import RepositoryException
from apps.importing.utils.constants import TABLE_CATEGORIES, TABLE_WORDS_CATEGORIES

logger = get_logger(__name__)


class CategoryRepository:
    """Repository for category operations"""
    
    def __init__(self, transaction_manager: TransactionManager):
        """
        Initialize category repository
        
        Args:
            transaction_manager: Transaction manager instance
        """
        self.tx_manager = transaction_manager
        self.logger = logger
    
    def create_category(self, word_id: int) -> Optional[int]:
        """
        Create a category for a domain word
        
        Args:
            word_id: ID of the domain word
            
        Returns:
            Category ID if successful, None otherwise
        """
        try:
            # Check if category already exists
            existing = self.tx_manager.execute_query(
                f"SELECT id FROM {TABLE_CATEGORIES} WHERE word_id = %s",
                (word_id,),
                fetch=True
            )
            
            if existing:
                return existing[0][0]
            
            # Create new category with ON CONFLICT to handle race conditions
            result = self.tx_manager.execute_query(
                f"INSERT INTO {TABLE_CATEGORIES} (word_id) VALUES (%s) "
                f"ON CONFLICT (word_id) DO NOTHING RETURNING id",
                (word_id,),
                fetch=True
            )
            
            if result:
                # Insert succeeded
                category_id = result[0][0]
                self.logger.debug(f"Created category with ID {category_id}")
                return category_id
            else:
                # Conflict occurred, category was created by another thread
                # Fetch the ID now
                existing = self.tx_manager.execute_query(
                    f"SELECT id FROM {TABLE_CATEGORIES} WHERE word_id = %s",
                    (word_id,),
                    fetch=True
                )
                if existing:
                    return existing[0][0]
            
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to create category: {e}")
            raise RepositoryException(f"Category creation failed: {e}") from e
    
    def link_word_to_category(self, word_id: int, category_id: int) -> bool:
        """
        Link a word to a category in words_categorys table
        
        Args:
            word_id: Word ID to link
            category_id: Category ID to link to
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if link already exists
            existing = self.tx_manager.execute_query(
                f"SELECT 1 FROM {TABLE_WORDS_CATEGORIES} "
                f"WHERE word_id = %s AND category_id = %s",
                (word_id, category_id),
                fetch=True
            )
            
            if existing:
                self.logger.debug(f"Word {word_id} already linked to category {category_id}")
                return True
            
            # Create link with ON CONFLICT to handle race conditions
            # Use column-based ON CONFLICT (works with unique constraints/indexes)
            try:
                self.tx_manager.execute_query(
                    f"INSERT INTO {TABLE_WORDS_CATEGORIES} (word_id, category_id) "
                    f"VALUES (%s, %s) "
                    f"ON CONFLICT (word_id, category_id) DO NOTHING",
                    (word_id, category_id),
                    fetch=False
                )
                self.logger.debug(f"Linked word {word_id} to category {category_id}")
                return True
            except Exception as e:
                # If ON CONFLICT syntax not supported or constraint doesn't exist,
                # the unique constraint will raise an error - treat as success (link exists)
                error_msg = str(e).lower()
                if "unique constraint" in error_msg or "duplicate key" in error_msg:
                    self.logger.debug(f"Word {word_id} already linked to category {category_id} (duplicate prevented)")
                    return True
                # Re-raise other exceptions
                raise
            
        except Exception as e:
            self.logger.error(f"Failed to link word to category: {e}")
            return False
    
