"""
Transaction Manager
Handles database transactions with automatic commit/rollback
"""

from typing import Optional, Any, Tuple, List

from apps.importing.database.connection_pool import ConnectionPool
from apps.importing.utils.logger import get_logger
from apps.importing.utils.exceptions import DatabaseException

logger = get_logger(__name__)


class TransactionManager:
    """Manages database transactions"""
    
    def __init__(self, connection_pool: ConnectionPool):
        """
        Initialize transaction manager
        
        Args:
            connection_pool: Connection pool instance
        """
        self.pool = connection_pool
        self.logger = logger
    
    def execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch: bool = False
    ) -> Optional[List[Tuple]]:
        """
        Execute a single query within a transaction
        
        Args:
            query: SQL query string
            params: Query parameters
            fetch: Whether to fetch results
            
        Returns:
            Query results if fetch=True, None otherwise
        """
        conn = None
        try:
            conn = self.pool.get_connection()
            cursor = conn.cursor()
            
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            if fetch:
                result = cursor.fetchall()
                conn.commit()
                cursor.close()
                return result
            else:
                conn.commit()
                cursor.close()
                return None
                
        except Exception as e:
            if conn:
                conn.rollback()
            raise DatabaseException(f"Query execution failed: {e}") from e
        finally:
            if conn:
                self.pool.return_connection(conn)
