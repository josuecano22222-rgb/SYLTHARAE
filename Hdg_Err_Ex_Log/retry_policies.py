"""
Retry Policies Configuration
Provides configurable retry policies with exponential backoff
"""

import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any
from enum import Enum

from .error_handling import is_retryable_error, ErrorCategory

logger = logging.getLogger(__name__)


class RetryPolicyType(Enum):
    """Types of retry policies"""
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIXED = "fixed"
    CUSTOM = "custom"


@dataclass
class RetryPolicy:
    """Retry policy configuration"""
    max_retries: int = 3
    initial_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0
    policy_type: RetryPolicyType = RetryPolicyType.EXPONENTIAL
    jitter: bool = True  # Add random jitter to prevent thundering herd
    retryable_check: Optional[Callable[[Exception], bool]] = None
    
    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for retry attempt
        
        Args:
            attempt: Current attempt number (0-indexed)
            
        Returns:
            Delay in seconds
        """
        if self.policy_type == RetryPolicyType.EXPONENTIAL:
            delay = self.initial_delay * (self.exponential_base ** attempt)
        elif self.policy_type == RetryPolicyType.LINEAR:
            delay = self.initial_delay * (attempt + 1)
        elif self.policy_type == RetryPolicyType.FIXED:
            delay = self.initial_delay
        else:
            delay = self.initial_delay * (self.exponential_base ** attempt)
        
        # Cap at max_delay
        delay = min(delay, self.max_delay)
        
        # Add jitter if enabled (up to 25% of delay)
        if self.jitter:
            import random
            jitter_amount = delay * 0.25 * random.random()
            delay += jitter_amount
        
        return delay


# Predefined retry policies
QUICK_RETRY = RetryPolicy(
    max_retries=2,
    initial_delay=0.1,
    max_delay=1.0,
    exponential_base=2.0,
    policy_type=RetryPolicyType.EXPONENTIAL
)

STANDARD_RETRY = RetryPolicy(
    max_retries=3,
    initial_delay=0.5,
    max_delay=10.0,
    exponential_base=2.0,
    policy_type=RetryPolicyType.EXPONENTIAL
)

AGGRESSIVE_RETRY = RetryPolicy(
    max_retries=5,
    initial_delay=1.0,
    max_delay=30.0,
    exponential_base=2.0,
    policy_type=RetryPolicyType.EXPONENTIAL
)

DATABASE_RETRY = RetryPolicy(
    max_retries=3,
    initial_delay=0.5,
    max_delay=15.0,
    exponential_base=2.0,
    policy_type=RetryPolicyType.EXPONENTIAL,
    retryable_check=lambda e: is_retryable_error(e)
)

NETWORK_RETRY = RetryPolicy(
    max_retries=5,
    initial_delay=1.0,
    max_delay=60.0,
    exponential_base=2.0,
    policy_type=RetryPolicyType.EXPONENTIAL
)


def retry_with_policy(
    policy: RetryPolicy,
    on_retry: Optional[Callable[[Exception, int, int], None]] = None
):
    """
    Decorator for retrying operations with a specific policy
    
    Args:
        policy: Retry policy configuration
        on_retry: Optional callback called before each retry
        
    Returns:
        Decorated function
    """
    from functools import wraps
    from .error_handling import handle_error, ErrorSeverity
    
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(policy.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Check if error is retryable
                    is_retryable = False
                    if policy.retryable_check:
                        is_retryable = policy.retryable_check(e)
                    else:
                        is_retryable = is_retryable_error(e)
                    
                    # If not retryable or max retries reached, raise
                    if not is_retryable or attempt >= policy.max_retries:
                        if attempt >= policy.max_retries:
                            handle_error(
                                e,
                                category=ErrorCategory.UNKNOWN,
                                severity=ErrorSeverity.HIGH,
                                context={
                                    'operation': func.__name__,
                                    'attempt': attempt + 1,
                                    'max_retries': policy.max_retries
                                },
                                suggested_action='Max retries exceeded'
                            )
                        raise
                    
                    # Calculate delay
                    delay = policy.calculate_delay(attempt)
                    
                    # Call retry callback if provided
                    if on_retry:
                        try:
                            on_retry(e, attempt + 1, policy.max_retries)
                        except Exception:
                            pass
                    
                    logger.warning(
                        f"Retryable error in {func.__name__} (attempt {attempt + 1}/{policy.max_retries}): {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
            
            # Should never reach here
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Unexpected error in retry wrapper for {func.__name__}")
        
        return wrapper
    return decorator

