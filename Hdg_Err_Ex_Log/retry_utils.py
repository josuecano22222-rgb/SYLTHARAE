"""
Retry Utilities for Data Operations
Provides retry logic with exponential backoff for transient errors
"""

import time
import logging
from typing import Callable, TypeVar, Optional, Tuple, Any
from functools import wraps

from .error_handling import (
    handle_error, is_retryable_error, is_connection_error,
    ErrorCategory, ErrorSeverity
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_on_error(
    max_retries: int = 3,
    initial_delay: float = 0.5,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    retryable_check: Optional[Callable[[Exception], bool]] = None,
    on_retry: Optional[Callable[[Exception, int, int], None]] = None
):
    """
    Decorator for retrying operations on transient errors
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay in seconds (default: 0.5)
        max_delay: Maximum delay in seconds (default: 10.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        retryable_check: Optional function to check if error is retryable
        on_retry: Optional callback called before each retry (error, attempt, max_retries)
    
    Returns:
        Decorated function that retries on errors
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Check if error is retryable
                    is_retryable = False
                    if retryable_check:
                        is_retryable = retryable_check(e)
                    else:
                        is_retryable = is_retryable_error(e)
                    
                    # If not retryable or max retries reached, raise
                    if not is_retryable or attempt >= max_retries:
                        if attempt >= max_retries:
                            handle_error(
                                e,
                                category=ErrorCategory.DATABASE if is_connection_error(e) else ErrorCategory.UNKNOWN,
                                severity=ErrorSeverity.HIGH,
                                context={
                                    'operation': func.__name__,
                                    'attempt': attempt + 1,
                                    'max_retries': max_retries
                                },
                                suggested_action='Max retries exceeded, check error details'
                            )
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        initial_delay * (exponential_base ** attempt),
                        max_delay
                    )
                    
                    # Call retry callback if provided
                    if on_retry:
                        try:
                            on_retry(e, attempt + 1, max_retries)
                        except Exception:
                            pass  # Don't fail on callback errors
                    
                    logger.warning(
                        f"Retryable error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
            
            # Should never reach here, but just in case
            if last_exception:
                raise last_exception
            
            raise RuntimeError(f"Unexpected error in retry wrapper for {func.__name__}")
        
        return wrapper
    return decorator


def retry_with_reconnect(
    max_retries: int = 3,
    initial_delay: float = 0.5,
    reconnect_func: Optional[Callable[[], bool]] = None
):
    """
    Decorator for retrying database operations with automatic reconnection
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        reconnect_func: Function to call for reconnection (should return bool)
    
    Returns:
        Decorated function that retries with reconnection
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Check if it's a connection error
                    if is_connection_error(e) and reconnect_func:
                        if attempt < max_retries:
                            logger.warning(
                                f"Connection error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                                f"Attempting to reconnect..."
                            )
                            
                            if reconnect_func():
                                delay = initial_delay * (2 ** attempt)
                                logger.info(f"Reconnected successfully. Retrying in {delay:.2f}s...")
                                time.sleep(delay)
                                continue
                            else:
                                logger.error("Reconnection failed")
                                if attempt < max_retries:
                                    delay = initial_delay * (2 ** attempt)
                                    time.sleep(delay)
                                    continue
                    
                    # Check if error is retryable
                    if is_retryable_error(e) and attempt < max_retries:
                        delay = initial_delay * (2 ** attempt)
                        logger.warning(
                            f"Retryable error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        time.sleep(delay)
                        continue
                    
                    # Not retryable or max retries reached
                    if attempt >= max_retries:
                        handle_error(
                            e,
                            category=ErrorCategory.DATABASE_CONNECTION if is_connection_error(e) else ErrorCategory.DATABASE,
                            severity=ErrorSeverity.HIGH,
                            context={
                                'operation': func.__name__,
                                'attempt': attempt + 1,
                                'max_retries': max_retries
                            }
                        )
                    raise
            
            # Should never reach here
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Unexpected error in retry wrapper for {func.__name__}")
        
        return wrapper
    return decorator


def safe_execute(
    func: Callable[..., T],
    *args,
    max_retries: int = 2,
    default_return: Optional[T] = None,
    error_context: Optional[dict] = None,
    **kwargs
) -> Optional[T]:
    """
    Safely execute a function with retry logic and error handling
    
    Args:
        func: Function to execute
        *args: Positional arguments for function
        max_retries: Maximum retry attempts
        default_return: Value to return on failure (None if not provided)
        error_context: Additional context for error logging
        **kwargs: Keyword arguments for function
    
    Returns:
        Function result or default_return on failure
    """
    last_exception = None
    error_context = error_context or {}
    
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            if is_retryable_error(e) and attempt < max_retries:
                delay = 0.5 * (2 ** attempt)
                logger.warning(
                    f"Retryable error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                time.sleep(delay)
                continue
            
            # Log error
            handle_error(
                e,
                context={
                    'operation': func.__name__,
                    'attempt': attempt + 1,
                    'max_retries': max_retries,
                    **error_context
                }
            )
            
            if attempt >= max_retries:
                return default_return
    
    return default_return

