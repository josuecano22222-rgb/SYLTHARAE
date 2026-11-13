"""
Error Handling Module
Centralized error handling with categories and severity levels
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, Tuple
from contextlib import contextmanager

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors"""
    FILE_PROCESSING = "file_processing"
    DATABASE = "database"
    DATABASE_CONNECTION = "database_connection"
    VALIDATION = "validation"
    NETWORK = "network"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class ErrorSeverity(Enum):
    """Severity levels for errors"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def is_connection_error(error: Exception) -> bool:
    """
    Determine if an error is a database connection error
    
    Args:
        error: The exception to check
        
    Returns:
        True if it's a connection error, False otherwise
    """
    if not PSYCOPG2_AVAILABLE:
        return False
    
    # Check for specific psycopg2 connection error types
    if isinstance(error, (psycopg2.InterfaceError, psycopg2.OperationalError)):
        return True
    
    # Check for InFailedSqlTransaction (needs rollback/reconnect)
    if isinstance(error, psycopg2.InternalError):
        error_str = str(error).lower()
        if 'in failed sql transaction' in error_str or 'current transaction is aborted' in error_str:
            return True
    
    # Check error message for connection-related keywords
    error_str = str(error).lower()
    connection_keywords = [
        'connection', 'database', 'server', 'network', 'timeout',
        'closed', 'lost', 'refused', 'unreachable', 'broken pipe',
        'could not connect', 'connection reset', 'connection refused',
        'connection already closed', 'cursor already closed', 
        'connection pointer is null', 'server closed the connection'
    ]
    
    return any(keyword in error_str for keyword in connection_keywords)


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if an error is retryable.
    
    Transaction abort errors are NOT retryable - they indicate a failed transaction
    that must be rolled back. Transient connection/deadlock errors are retryable.
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error is retryable, False otherwise
    """
    error_str = str(error).lower()
    
    # Transaction abort errors are NOT retryable - transaction must be rolled back
    if 'transaction is aborted' in error_str or 'in failed sql transaction' in error_str:
        return False
    
    # Connection errors are retryable
    if is_connection_error(error):
        return True
    
    if not PSYCOPG2_AVAILABLE:
        return False
    
    # Deadlock and lock timeout errors are retryable
    if isinstance(error, psycopg2.extensions.TransactionRollbackError):
        if 'deadlock' in error_str or 'lock' in error_str:
            return True
    
    # NOTE: the ingestion path no longer creates temporary tables or drives
    # COPY, so the former rules for 'tmp_words does not exist' and 'no copy in
    # progress' were removed.  Those messages were symptoms of two threads
    # sharing one connection; retrying them merely repeated the failure.
    
    # Check for transient errors
    transient_keywords = ['timeout', 'temporary', 'retry', 'busy', 'locked']
    return any(keyword in error_str for keyword in transient_keywords)


def get_error_category(error: Exception) -> ErrorCategory:
    """
    Automatically categorize an error
    
    Args:
        error: The exception to categorize
    
    Returns:
        ErrorCategory enum value
    """
    if is_connection_error(error):
        return ErrorCategory.DATABASE_CONNECTION
    
    if PSYCOPG2_AVAILABLE and isinstance(error, (psycopg2.Error, psycopg2.DatabaseError)):
        return ErrorCategory.DATABASE
    
    # Check for file-related errors
    error_type = type(error).__name__.lower()
    error_str = str(error).lower()
    
    if isinstance(error, (FileNotFoundError, PermissionError, OSError)):
        if 'file' in error_str or 'path' in error_str or 'directory' in error_str:
            return ErrorCategory.FILE_PROCESSING
    
    if 'file' in error_str or 'file not found' in error_str or 'cannot read' in error_str:
        return ErrorCategory.FILE_PROCESSING
    
    if 'validation' in error_str or 'invalid' in error_str or 'required' in error_str:
        return ErrorCategory.VALIDATION
    
    if 'network' in error_str or 'connection' in error_str:
        return ErrorCategory.NETWORK
    
    if 'config' in error_str or 'configuration' in error_str:
        return ErrorCategory.CONFIGURATION
    
    return ErrorCategory.UNKNOWN


def get_error_severity(error: Exception, category: ErrorCategory) -> ErrorSeverity:
    """
    Automatically determine error severity
    
    Args:
        error: The exception
        category: Error category
        
    Returns:
        ErrorSeverity enum value
    """
    if category == ErrorCategory.DATABASE_CONNECTION:
        return ErrorSeverity.HIGH
    
    if category == ErrorCategory.DATABASE:
        return ErrorSeverity.MEDIUM
    
    if category == ErrorCategory.VALIDATION:
        return ErrorSeverity.MEDIUM
    
    if category == ErrorCategory.CONFIGURATION:
        return ErrorSeverity.HIGH
    
    return ErrorSeverity.MEDIUM


@contextmanager
def ErrorContext(category: ErrorCategory, severity: ErrorSeverity, context: Optional[Dict[str, Any]] = None):
    """Context manager for structured error handling"""
    try:
        yield
    except Exception as e:
        handle_error(e, category=category, severity=severity, context=context or {})
        raise


def handle_error(
    error: Exception,
    category: Optional[ErrorCategory] = None,
    severity: Optional[ErrorSeverity] = None,
    context: Optional[Dict[str, Any]] = None,
    suggested_action: Optional[str] = None,
    record_to_monitor: bool = True
):
    """
    Handle errors with structured logging and categorization
    
    Args:
        error: The exception that occurred
        category: Category of the error (auto-detected if None)
        severity: Severity level (auto-detected if None)
        context: Additional context information
        suggested_action: Optional suggested action to resolve the error
        record_to_monitor: Whether to record error to monitoring service
    """
    context = context or {}
    
    # Auto-detect category and severity if not provided
    if category is None:
        category = get_error_category(error)
    if severity is None:
        severity = get_error_severity(error, category)
    
    # Build detailed error message
    error_type = type(error).__name__
    error_message = str(error)
    
    log_message = f"[{category.value.upper()}] {error_type}: {error_message}"
    
    if context:
        context_str = ', '.join(f"{k}={v}" for k, v in context.items())
        log_message += f" | Context: {context_str}"
    
    if suggested_action:
        log_message += f" | Suggested Action: {suggested_action}"
    elif is_connection_error(error):
        log_message += " | Suggested Action: Check database connection and retry"
    elif is_retryable_error(error):
        log_message += " | Suggested Action: Retry operation after short delay"
    
    # Log with appropriate level
    if severity == ErrorSeverity.CRITICAL:
        logger.critical(log_message, exc_info=True)
    elif severity == ErrorSeverity.HIGH:
        logger.error(log_message, exc_info=True)
    elif severity == ErrorSeverity.MEDIUM:
        logger.warning(log_message)
    else:
        logger.info(log_message)
    
    # Record to monitoring service
    if record_to_monitor:
        try:
            from .error_monitoring import record_error
            record_error(
                error=error,
                category=category.value,
                severity=severity.value,
                context=context
            )
        except Exception as monitor_error:
            # Don't fail if monitoring fails
            logger.debug(f"Failed to record error to monitor: {monitor_error}")


def format_validation_error(field_name: str, value: Any, reason: str) -> str:
    """
    Format a validation error message
    
    Args:
        field_name: Name of the field that failed validation
        value: The invalid value
        reason: Reason for validation failure
        
    Returns:
        Formatted error message
    """
    return f"Validation failed for field '{field_name}': {reason} (value: {value})"

