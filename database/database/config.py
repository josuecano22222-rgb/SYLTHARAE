"""
Database configuration management - Backward Compatibility Wrapper
This module provides backward compatibility while using the unified settings system.

All functionality is provided by settings.config as the single source of truth.
"""
import os
from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    """
    Database configuration dataclass - Backward compatibility wrapper.
    
    This class now uses the unified settings system from settings.config.
    All database configuration should go through the unified settings system.
    """
    dbname: str
    user: str
    password: str
    host: str
    port: int
    min_connections: int = 1
    max_connections: int = 20
    
    @classmethod
    def from_env(cls) -> 'DatabaseConfig':
        """
        Create configuration from unified settings system.
        
        This method uses the unified settings system as the single source of truth.
        Environment variables are used as fallback only.
        """
        try:
            # Use unified settings system
            from settings import get_database_config
            db_config = get_database_config()

            # ARCH-02: environment variables take precedence over persisted
            # settings AT READ TIME (defaults < file < env < secret provider).
            host = os.environ.get('DB_HOST', db_config.host)
            port = int(os.environ.get('DB_PORT', db_config.port))
            dbname = os.environ.get('DB_NAME', db_config.database)
            user = os.environ.get('DB_USER', db_config.user)
            password = db_config.password
            if os.environ.get('DB_PASSWORD') is not None:
                password = os.environ['DB_PASSWORD']

            # Apply safe pool size from resource coordinator if available
            try:
                from core.resource_coordinator import get_safe_db_pool_size
                safe_max_conn = get_safe_db_pool_size(db_config.pool_max_conn)
                max_connections = safe_max_conn
                if safe_max_conn != db_config.pool_max_conn:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.info(f"DB pool size adjusted by resource coordinator: {db_config.pool_max_conn} -> {safe_max_conn}")
            except Exception as e:
                # If resource coordinator not available, use settings value
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f"Resource coordinator not available, using settings pool size: {e}")
                max_connections = db_config.pool_max_conn

            return cls(
                dbname=dbname,
                user=user,
                password=password or os.getenv('DB_PASSWORD', ''),
                host=host,
                port=port,
                min_connections=db_config.pool_min_conn,
                max_connections=max_connections
            )
        except Exception as e:
            # Fallback to environment variables if settings not available
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not load database config from unified settings: {e}, using environment variables")
            
            return cls(
                dbname=os.getenv('DB_NAME', 'analysis'),
                user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD', ''),
                host=os.getenv('DB_HOST', 'localhost'),
                port=int(os.getenv('DB_PORT', '5432')),
                # AUDIT-CONF-01: .env.example and the installer write the
                # DB_POOL_* names; accept them as well as the older names.
                min_connections=int(os.getenv('DB_MIN_CONNECTIONS')
                                    or os.getenv('DB_POOL_MIN_CONNECTIONS') or '1'),
                max_connections=int(os.getenv('DB_MAX_CONNECTIONS')
                                    or os.getenv('DB_POOL_MAX_CONNECTIONS') or '20')
            )
    
    @classmethod
    def from_dict(cls, config_dict: dict) -> 'DatabaseConfig':
        """Create configuration from dictionary."""
        return cls(**config_dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for psycopg2 connection."""
        return {
            'dbname': self.dbname,
            'user': self.user,
            'password': self.password,
            'host': self.host,
            'port': self.port
        }
