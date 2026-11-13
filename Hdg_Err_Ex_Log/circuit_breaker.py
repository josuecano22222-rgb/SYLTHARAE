"""
Circuit Breaker Pattern Implementation
Prevents cascading failures by stopping requests to failing services
"""

import time
import logging
from enum import Enum
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5  # Number of failures before opening
    success_threshold: int = 2  # Number of successes to close from half-open
    timeout: float = 60.0  # Time in seconds before attempting half-open
    expected_exception: tuple = (Exception,)  # Exceptions that count as failures


class CircuitBreaker:
    """
    Circuit breaker implementation
    
    Prevents requests to failing services by:
    1. CLOSED: Normal operation, all requests pass through
    2. OPEN: Service failing, all requests rejected immediately
    3. HALF_OPEN: Testing if service recovered, limited requests allowed
    """
    
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize circuit breaker
        
        Args:
            name: Name of the circuit breaker (for logging)
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.lock = Lock()
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'rejected_requests': 0,
            'state_changes': []
        }
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker
        
        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
            Original exception from function if it fails
        """
        with self.lock:
            self.stats['total_requests'] += 1
            
            # Check circuit state
            if self.state == CircuitState.OPEN:
                # Check if timeout has passed
                if self.last_failure_time and \
                   (time.time() - self.last_failure_time) >= self.config.timeout:
                    self._transition_to_half_open()
                else:
                    # Circuit is open, reject request
                    self.stats['rejected_requests'] += 1
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Service unavailable. Retry after {self.config.timeout}s"
                    )
            
            # Execute function
            try:
                result = func(*args, **kwargs)
                self._on_success()
                self.stats['successful_requests'] += 1
                return result
            except self.config.expected_exception as e:
                self._on_failure()
                self.stats['failed_requests'] += 1
                raise
    
    def _on_success(self):
        """Handle successful execution"""
        with self.lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    self._transition_to_closed()
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success
                self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed execution"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                # Failure during half-open, go back to open
                self._transition_to_open()
            elif self.state == CircuitState.CLOSED:
                if self.failure_count >= self.config.failure_threshold:
                    self._transition_to_open()
    
    def _transition_to_open(self):
        """Transition circuit to OPEN state"""
        if self.state != CircuitState.OPEN:
            self.state = CircuitState.OPEN
            self.success_count = 0
            self.stats['state_changes'].append({
                'state': 'OPEN',
                'time': time.time(),
                'reason': f'Failure threshold ({self.config.failure_threshold}) exceeded'
            })
            logger.warning(
                f"Circuit breaker '{self.name}' opened after {self.failure_count} failures"
            )
    
    def _transition_to_half_open(self):
        """Transition circuit to HALF_OPEN state"""
        if self.state != CircuitState.HALF_OPEN:
            self.state = CircuitState.HALF_OPEN
            self.success_count = 0
            self.failure_count = 0
            self.stats['state_changes'].append({
                'state': 'HALF_OPEN',
                'time': time.time(),
                'reason': 'Timeout expired, testing recovery'
            })
            logger.info(f"Circuit breaker '{self.name}' transitioning to HALF_OPEN")
    
    def _transition_to_closed(self):
        """Transition circuit to CLOSED state"""
        if self.state != CircuitState.CLOSED:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            self.stats['state_changes'].append({
                'state': 'CLOSED',
                'time': time.time(),
                'reason': f'Service recovered after {self.success_count} successes'
            })
            logger.info(f"Circuit breaker '{self.name}' closed - service recovered")
    
    def reset(self):
        """Manually reset circuit breaker to CLOSED state"""
        with self.lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            logger.info(f"Circuit breaker '{self.name}' manually reset")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        with self.lock:
            return {
                'name': self.name,
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'last_failure_time': self.last_failure_time,
                'stats': self.stats.copy()
            }


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open"""
    pass


def circuit_breaker(name: str, config: Optional[CircuitBreakerConfig] = None):
    """
    Decorator for circuit breaker pattern
    
    Args:
        name: Circuit breaker name
        config: Circuit breaker configuration
        
    Returns:
        Decorated function
    """
    breaker = CircuitBreaker(name, config)
    
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            return breaker.call(func, *args, **kwargs)
        wrapper.circuit_breaker = breaker
        return wrapper
    return decorator

