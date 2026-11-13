"""
Performance Profiler
Decorators and utilities for profiling function execution
"""

import time
import functools
import logging
from typing import Callable
from collections import defaultdict

logger = logging.getLogger(__name__)

# Global statistics
_function_stats = defaultdict(lambda: {'count': 0, 'total_time': 0.0, 'min_time': float('inf'), 'max_time': 0.0})
_stats_lock = None

try:
    import threading
    _stats_lock = threading.Lock()
except ImportError:
    pass


def profile_function(threshold: float = 1.0, log_args: bool = False):
    """
    Decorator to profile function execution time
    
    Args:
        threshold: Log warning if execution time exceeds this (seconds)
        log_args: Whether to log function arguments (for debugging)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # Update statistics
                if _stats_lock:
                    with _stats_lock:
                        stats = _function_stats[func.__name__]
                        stats['count'] += 1
                        stats['total_time'] += execution_time
                        stats['min_time'] = min(stats['min_time'], execution_time)
                        stats['max_time'] = max(stats['max_time'], execution_time)
                
                # Log slow operations
                if execution_time > threshold:
                    args_str = str(args)[:100] if log_args else "..."
                    logger.warning(
                        f"⚠️ SLOW OPERATION: {func.__name__} took {execution_time:.3f}s "
                        f"(threshold: {threshold}s) | args: {args_str}"
                    )
                elif execution_time > threshold / 2:
                    logger.debug(f"Operation {func.__name__} took {execution_time:.3f}s")
                
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                logger.error(f"Error in {func.__name__} after {execution_time:.3f}s: {e}")
                raise
        
        return wrapper
    return decorator


def get_function_stats() -> dict:
    """Get statistics for all profiled functions"""
    if _stats_lock:
        with _stats_lock:
            return dict(_function_stats)
    return dict(_function_stats)


def get_slowest_functions(limit: int = 10) -> list:
    """Get list of slowest functions by average time"""
    stats = get_function_stats()
    functions = []
    
    for func_name, stat in stats.items():
        if stat['count'] > 0:
            avg_time = stat['total_time'] / stat['count']
            functions.append({
                'function': func_name,
                'count': stat['count'],
                'total_time': stat['total_time'],
                'avg_time': avg_time,
                'min_time': stat['min_time'],
                'max_time': stat['max_time']
            })
    
    return sorted(functions, key=lambda x: x['avg_time'], reverse=True)[:limit]


def reset_stats():
    """Reset all function statistics"""
    global _function_stats
    if _stats_lock:
        with _stats_lock:
            _function_stats.clear()
    else:
        _function_stats.clear()


class PerformanceContext:
    """Context manager for profiling code blocks"""
    
    def __init__(self, operation_name: str, threshold: float = 1.0):
        self.operation_name = operation_name
        self.threshold = threshold
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        execution_time = time.time() - self.start_time
        
        if execution_time > self.threshold:
            logger.warning(
                f"⚠️ SLOW OPERATION: {self.operation_name} took {execution_time:.3f}s "
                f"(threshold: {self.threshold}s)"
            )
        elif execution_time > self.threshold / 2:
            logger.debug(f"Operation {self.operation_name} took {execution_time:.3f}s")
        
        return False  # Don't suppress exceptions

