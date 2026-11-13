"""
Enhanced Error Handler
Provides user-friendly error messages, recovery mechanisms, and structured error handling
"""

import logging
import traceback
from typing import Optional, Dict, Any, Callable, Type
from functools import wraps

from .error_handling import (
    handle_error, ErrorCategory, ErrorSeverity,
    is_retryable_error, is_connection_error
)

logger = logging.getLogger(__name__)


class UserFriendlyError(Exception):
    """Exception with user-friendly message"""
    def __init__(self, message: str, technical_details: Optional[str] = None,
                 recovery_action: Optional[str] = None):
        self.message = message
        self.technical_details = technical_details
        self.recovery_action = recovery_action
        super().__init__(self.message)


def get_user_friendly_message(error: Exception, context: Optional[Dict[str, Any]] = None) -> str:
    """
    Convert technical error to user-friendly message
    
    Args:
        error: The exception
        context: Additional context
        
    Returns:
        User-friendly error message
    """
    error_type = type(error).__name__
    error_str = str(error).lower()
    context = context or {}
    
    # Database connection errors
    if is_connection_error(error):
        return "Unable to connect to the database. Please check your database settings and ensure the database server is running."
    
    # File not found errors
    if isinstance(error, FileNotFoundError):
        file_path = context.get('file_path', 'file')
        return f"The file '{file_path}' could not be found. Please check the file path and try again."
    
    # Permission errors
    if isinstance(error, PermissionError):
        resource = context.get('resource', 'resource')
        return f"You don't have permission to access '{resource}'. Please check file permissions."
    
    # Validation errors
    if 'validation' in error_str or 'invalid' in error_str:
        field = context.get('field', 'input')
        return f"Invalid {field}. Please check your input and try again."
    
    # Network errors
    if 'network' in error_str or 'connection' in error_str or 'timeout' in error_str:
        return "A network error occurred. Please check your internet connection and try again."
    
    # Configuration errors
    if 'config' in error_str or 'configuration' in error_str:
        return "A configuration error occurred. Please check your settings and try again."
    
    # Process/thread errors
    if 'process' in error_str or 'thread' in error_str:
        return "A system process error occurred. The operation may have been interrupted."
    
    # Generic database errors
    if 'database' in error_str or 'sql' in error_str:
        return "A database error occurred. Please try again, or contact support if the problem persists."
    
    # Default user-friendly message
    operation = context.get('operation', 'operation')
    return f"An error occurred while {operation}. Please try again, or contact support if the problem persists."


# NOTE: Recovery functions (safe_execute_with_recovery, handle_with_recovery, error_handler, safe_operation)
# have been removed as duplicates. Use the following from error_recovery module instead:
# - @with_recovery() decorator (replaces error_handler and safe_operation)
# - ErrorRecoveryManager.attempt_recovery() (replaces handle_with_recovery)
# - safe_execute() from retry_utils (replaces safe_execute_with_recovery for retry scenarios)

def format_error_for_user(error: Exception, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Format error for user display
    
    Args:
        error: The exception
        context: Additional context
        
    Returns:
        Dictionary with user-friendly error information
    """
    user_message = get_user_friendly_message(error, context)
    
    return {
        'message': user_message,
        'error_type': type(error).__name__,
        'technical_details': str(error) if context.get('include_technical', False) else None,
        'recovery_action': context.get('recovery_action'),
        'is_retryable': is_retryable_error(error),
        'category': get_error_category(error).value if hasattr(error, '__class__') else 'unknown'
    }


def get_error_category(error: Exception) -> ErrorCategory:
    """Get error category (re-export from error_handling)"""
    from .error_handling import get_error_category as _get_error_category
    return _get_error_category(error)

