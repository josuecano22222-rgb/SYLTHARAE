"""
Connection Pool Manager
Manages database connection pooling with thread safety
"""

import psycopg2
from psycopg2 import pool
import threading
from typing import Dict, Any

from apps.importing.utils.logger import get_logger
from apps.importing.utils.exceptions import ConnectionPoolException

logger = get_logger(__name__)


class ConnectionPool:
    """Thread-safe connection pool manager"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize connection pool
        
        Args:
            config: Database configuration including:
                - host, port, database, user, password
                - min_connections, max_connections
        """
        self.config = config
        self.pool = None
        self.lock = threading.RLock()
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize the connection pool"""
        try:
            min_conn = self.config.get('min_connections', 5)
            max_conn = self.config.get('max_connections', 20)
            
            # Ensure min < max
            if min_conn >= max_conn:
                max_conn = min_conn + 5
                logger.warning(f"Adjusted max_connections to {max_conn}")
            
            # Extract connection parameters
            # Note: client_encoding should be set per-connection after getting from pool
            # as psycopg2 pool doesn't support it in connection params
            conn_params = {
                'host': self.config['host'],
                'port': self.config['port'],
                'database': self.config['database'],
                'user': self.config['user'],
                'password': self.config['password'],
                'connect_timeout': self.config.get('connect_timeout', 10),
                'keepalives': self.config.get('keepalives', 1),
                'keepalives_idle': self.config.get('keepalives_idle', 30),
                'keepalives_interval': self.config.get('keepalives_interval', 10),
                'keepalives_count': self.config.get('keepalives_count', 5),
            }
            
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=min_conn,
                maxconn=max_conn,
                **conn_params
            )
            
            # Check database encoding and warn if not UTF-8
            self._check_database_encoding()
            
            logger.info(f"✅ Connection pool initialized (min={min_conn}, max={max_conn})")
            
        except Exception as e:
            logger.error(f"❌ Failed to create connection pool: {e}")
            raise ConnectionPoolException(f"Pool initialization failed: {e}") from e
    
    def _check_database_encoding(self):
        """Check database encoding and warn if not UTF-8"""
        conn = None
        try:
            conn = self.pool.getconn()
            cursor = conn.cursor()
            cursor.execute("SELECT pg_database.datname, pg_database.encoding, pg_encoding_to_char(pg_database.encoding) as encoding_name FROM pg_database WHERE pg_database.datname = current_database()")
            result = cursor.fetchone()
            cursor.close()
            self.pool.putconn(conn)
            
            if result:
                db_name, encoding_code, encoding_name = result
                if encoding_name and encoding_name.upper() not in ('UTF8', 'UTF-8'):
                    logger.warning(
                        f"⚠️  Database '{db_name}' encoding is '{encoding_name}', not UTF-8. "
                        f"This may cause issues with Unicode characters (Arabic, Hebrew, etc.). "
                        f"Please recreate the database with UTF-8 encoding:\n"
                        f"  1. Backup your data\n"
                        f"  2. Drop the database: DROP DATABASE {db_name};\n"
                        f"  3. Create with UTF-8: CREATE DATABASE {db_name} ENCODING 'UTF8';"
                    )
                else:
                    logger.debug(f"Database encoding: {encoding_name}")
        except Exception as e:
            # Don't fail initialization if encoding check fails
            logger.debug(f"Could not check database encoding: {e}")
            if conn:
                try:
                    self.pool.putconn(conn)
                except Exception:
                    pass
    
    def get_connection(self):
        """
        Get connection from pool (thread-safe)
        Ensures UTF-8 encoding for proper Unicode support
        
        Returns:
            Database connection with UTF-8 encoding
        """
        with self.lock:
            if not self.pool:
                raise ConnectionPoolException("Connection pool not initialized")
            
            try:
                conn = self.pool.getconn()
                # Ensure UTF-8 encoding for Unicode support (Arabic, Hebrew, etc.)
                # Always set to ensure consistent encoding regardless of pool state
                try:
                    conn.set_client_encoding('UTF8')
                except Exception:
                    # If encoding is already UTF8 or setting fails, continue
                    # psycopg2 should handle this gracefully
                    pass
                return conn
            except Exception as e:
                raise ConnectionPoolException(f"Failed to get connection: {e}") from e
    
    def return_connection(self, conn):
        """
        Return connection to pool
        
        Args:
            conn: Connection to return
        """
        with self.lock:
            if self.pool:
                try:
                    self.pool.putconn(conn)
                except Exception as e:
                    logger.error(f"Failed to return connection: {e}")
    
    def close_all(self):
        """Close all connections in pool"""
        with self.lock:
            if self.pool:
                try:
                    self.pool.closeall()
                    self.pool = None
                    logger.info("✅ All connections closed")
                except Exception as e:
                    logger.error(f"Error closing connections: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics"""
        return {
            'initialized': self.pool is not None,
            'min_connections': self.config.get('min_connections'),
            'max_connections': self.config.get('max_connections'),
        }
