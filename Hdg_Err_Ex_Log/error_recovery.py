"""
Automatic Error Recovery Mechanisms
Provides automatic recovery strategies for common error scenarios.
"""

import logging
import time
from typing import Dict, Any, Optional, Callable, List
from enum import Enum
from functools import wraps

from .error_handling import ErrorCategory, ErrorSeverity, handle_error
from .error_monitoring import record_error

logger = logging.getLogger(__name__)


class RecoveryStrategy(Enum):
    """Recovery strategy types"""
    RETRY = "retry"
    FALLBACK = "fallback"
    SKIP = "skip"
    RESET = "reset"
    CACHE = "cache"


class RecoveryResult:
    """Result of a recovery attempt"""
    
    def __init__(
        self,
        success: bool,
        recovered_value: Any = None,
        strategy_used: Optional[RecoveryStrategy] = None,
        message: Optional[str] = None
    ):
        self.success = success
        self.recovered_value = recovered_value
        self.strategy_used = strategy_used
        self.message = message


class ErrorRecoveryManager:
    """
    Manages automatic error recovery for common scenarios
    """
    
    def __init__(self):
        from collections import defaultdict
        self._recovery_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """Register default recovery handlers for common errors"""
        
        # Database connection errors
        self.register_handler(
            ErrorCategory.DATABASE_CONNECTION,
            self._recover_database_connection
        )
        
        # File processing errors
        self.register_handler(
            ErrorCategory.FILE_PROCESSING,
            self._recover_file_processing
        )
        
        # Network errors
        self.register_handler(
            ErrorCategory.NETWORK,
            self._recover_network_error
        )
    
    def register_handler(
        self,
        category: ErrorCategory,
        handler: Callable[[Exception, Dict[str, Any]], Optional[RecoveryResult]]
    ):
        """Register a recovery handler for a category"""
        self._recovery_handlers[category.value].append(handler)
    
    def attempt_recovery(
        self,
        error: Exception,
        category: ErrorCategory,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[RecoveryResult]:
        """
        Attempt to recover from an error
        
        Args:
            error: The exception
            category: Error category
            context: Additional context
            
        Returns:
            RecoveryResult if recovery was attempted, None otherwise
        """
        context = context or {}
        handlers = self._recovery_handlers.get(category.value, [])
        
        for handler in handlers:
            try:
                result = handler(error, context)
                if result and result.success:
                    logger.info(
                        f"Recovery successful using {result.strategy_used.value}: "
                        f"{result.message}"
                    )
                    return result
            except Exception as recovery_error:
                logger.warning(
                    f"Recovery handler failed: {recovery_error}",
                    exc_info=True
                )
        
        return None
    
    def _recover_database_connection(
        self,
        error: Exception,
        context: Dict[str, Any]
    ) -> Optional[RecoveryResult]:
        """Recover from database connection errors"""
        try:
            # Try to reconnect
            # DatabaseHub may not exist - it's optional
            try:
                from database import DatabaseHub
                # get_database_hub is not exported, use DatabaseHub directly
                get_database_hub = lambda: DatabaseHub()
                
                hub = get_database_hub()
                if hasattr(hub, 'reconnect'):
                    hub.reconnect()
                    return RecoveryResult(
                        success=True,
                        strategy_used=RecoveryStrategy.RESET,
                        message="Database connection reestablished"
                    )
            except (ImportError, AttributeError):
                # DatabaseHub doesn't exist - this is OK
                logger.debug("DatabaseHub not available for reconnection")
        except Exception as e:
            logger.debug(f"Database reconnection failed: {e}")
        
        return None
    
    def _recover_file_processing(
        self,
        error: Exception,
        context: Dict[str, Any]
    ) -> Optional[RecoveryResult]:
        """Recover from file processing errors"""
        file_path = context.get('file_path') or context.get('path')
        
        if not file_path:
            return None
        
        # Check if file exists and is readable
        try:
            import os
            if os.path.exists(file_path):
                if os.access(file_path, os.R_OK):
                    # File exists and is readable, might be a format issue
                    # Try with a different reader
                    return RecoveryResult(
                        success=True,
                        strategy_used=RecoveryStrategy.FALLBACK,
                        message=f"File exists and readable, may need different reader: {file_path}"
                    )
                else:
                    return RecoveryResult(
                        success=False,
                        strategy_used=RecoveryStrategy.SKIP,
                        message=f"File not readable: {file_path}"
                    )
            else:
                return RecoveryResult(
                    success=False,
                    strategy_used=RecoveryStrategy.SKIP,
                    message=f"File not found: {file_path}"
                )
        except Exception as e:
            logger.debug(f"File recovery check failed: {e}")
        
        return None
    
    def _recover_network_error(
        self,
        error: Exception,
        context: Dict[str, Any]
    ) -> Optional[RecoveryResult]:
        """Recover from network errors"""
        # Network errors are often transient
        # Suggest retry with backoff
        return RecoveryResult(
            success=True,
            strategy_used=RecoveryStrategy.RETRY,
            message="Network error detected, retry recommended with exponential backoff"
        )


# Global recovery manager
_recovery_manager: Optional[ErrorRecoveryManager] = None


def get_recovery_manager() -> ErrorRecoveryManager:
    """Get or create the global recovery manager"""
    global _recovery_manager
    if _recovery_manager is None:
        _recovery_manager = ErrorRecoveryManager()
    return _recovery_manager


def with_recovery(
    category: ErrorCategory,
    default_return: Any = None,
    log_error: bool = True
):
    """
    Decorator for automatic error recovery
    
    Args:
        category: Error category
        default_return: Value to return if recovery fails
        log_error: Whether to log errors
        
    Usage:
        @with_recovery(ErrorCategory.DATABASE_CONNECTION, default_return=None)
        def my_function():
            # code that might fail
            pass
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context = {
                    'function': func.__name__,
                    'args': str(args)[:200],
                    'kwargs': str(kwargs)[:200]
                }
                
                if log_error:
                    handle_error(
                        e,
                        category=category,
                        context=context
                    )
                    record_error(
                        e,
                        category=category.value,
                        severity="medium",
                        context=context
                    )
                
                # Attempt recovery
                recovery_manager = get_recovery_manager()
                recovery_result = recovery_manager.attempt_recovery(
                    e,
                    category,
                    context
                )
                
                if recovery_result and recovery_result.success:
                    if recovery_result.strategy_used == RecoveryStrategy.RETRY:
                        # Retry the function
                        try:
                            time.sleep(0.5)  # Brief delay
                            return func(*args, **kwargs)
                        except Exception as retry_error:
                            if log_error:
                                logger.warning(
                                    f"Retry after recovery failed: {retry_error}"
                                )
                            return default_return
                    elif recovery_result.recovered_value is not None:
                        return recovery_result.recovered_value
                
                return default_return
        
        return wrapper
    return decorator

