"""
Performance Monitor
Tracks system performance metrics and provides monitoring capabilities
"""

import time
import logging
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import psutil

logger = logging.getLogger(__name__)

# Global singleton instance
_monitor_instance: Optional['PerformanceMonitor'] = None
_monitor_lock = threading.Lock()


@dataclass
class PerformanceMetrics:
    """Performance metrics data class"""
    timestamp: datetime = field(default_factory=datetime.now)
    files_processed: int = 0
    files_per_second: float = 0.0
    avg_processing_time: float = 0.0
    db_queries: int = 0
    db_query_time: float = 0.0
    cache_hit_rate: float = 0.0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    error_count: int = 0
    error_rate: float = 0.0


class PerformanceMonitor:
    """Performance monitoring singleton"""
    
    def __init__(self):
        self.start_time = time.time()
        self.files_processed = 0
        self.processing_times = []
        self.db_queries = 0
        self.db_query_time = 0.0
        self.cache_hits = 0
        self.cache_misses = 0
        self.error_count = 0
        self._lock = threading.Lock()
        self._running = True
        
    def record_file_processed(self, processing_time: float = 0.0):
        """Record a file processing event"""
        with self._lock:
            self.files_processed += 1
            if processing_time > 0:
                self.processing_times.append(processing_time)
                # Keep only last 1000 processing times
                if len(self.processing_times) > 1000:
                    self.processing_times = self.processing_times[-1000:]
    
    def record_db_query(self, query_time: float):
        """Record a database query"""
        with self._lock:
            self.db_queries += 1
            self.db_query_time += query_time
    
    def record_cache_hit(self):
        """Record a cache hit"""
        with self._lock:
            self.cache_hits += 1
    
    def record_cache_miss(self):
        """Record a cache miss"""
        with self._lock:
            self.cache_misses += 1
    
    def record_error(self):
        """Record an error"""
        with self._lock:
            self.error_count += 1
    
    def get_current_metrics(self) -> PerformanceMetrics:
        """Get current performance metrics"""
        with self._lock:
            elapsed_time = time.time() - self.start_time
            
            # Calculate files per second
            files_per_second = self.files_processed / elapsed_time if elapsed_time > 0 else 0.0
            
            # Calculate average processing time
            avg_processing_time = (
                sum(self.processing_times) / len(self.processing_times)
                if self.processing_times else 0.0
            )
            
            # Calculate cache hit rate
            total_cache_requests = self.cache_hits + self.cache_misses
            cache_hit_rate = (
                self.cache_hits / total_cache_requests
                if total_cache_requests > 0 else 0.0
            )
            
            # Calculate error rate
            error_rate = (
                self.error_count / self.files_processed
                if self.files_processed > 0 else 0.0
            )
            
            # Get system metrics
            try:
                cpu_percent = psutil.cpu_percent(interval=0.1)
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                memory_mb = memory.used / (1024 * 1024)
            except Exception:
                cpu_percent = 0.0
                memory_percent = 0.0
                memory_mb = 0.0
            
            return PerformanceMetrics(
                timestamp=datetime.now(),
                files_processed=self.files_processed,
                files_per_second=files_per_second,
                avg_processing_time=avg_processing_time,
                db_queries=self.db_queries,
                db_query_time=self.db_query_time,
                cache_hit_rate=cache_hit_rate,
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                memory_mb=memory_mb,
                error_count=self.error_count,
                error_rate=error_rate
            )
    
    def get_summary_report(self) -> Dict[str, Any]:
        """Get summary performance report"""
        metrics = self.get_current_metrics()
        elapsed_time = time.time() - self.start_time
        
        return {
            'uptime_seconds': elapsed_time,
            'uptime_formatted': f"{int(elapsed_time // 3600)}h {int((elapsed_time % 3600) // 60)}m {int(elapsed_time % 60)}s",
            'total_files_processed': self.files_processed,
            'total_errors': self.error_count,
            'total_db_queries': self.db_queries,
            'current_metrics': {
                'files_per_second': metrics.files_per_second,
                'avg_processing_time': metrics.avg_processing_time,
                'cache_hit_rate': metrics.cache_hit_rate,
                'error_rate': metrics.error_rate
            }
        }
    
    def stop(self):
        """Stop the monitor"""
        with self._lock:
            self._running = False

def get_monitor() -> Optional[PerformanceMonitor]:
    """Get the global monitor instance"""
    return _monitor_instance

def start_monitoring() -> PerformanceMonitor:
    """Start monitoring and return the global monitor instance"""
    global _monitor_instance
    
    with _monitor_lock:
        if _monitor_instance is None:
            _monitor_instance = PerformanceMonitor()
            logger.info("Performance monitoring started")
        return _monitor_instance

def stop_monitoring():
    """Stop monitoring"""
    global _monitor_instance
    
    with _monitor_lock:
        if _monitor_instance:
            _monitor_instance.stop()
            _monitor_instance = None
            logger.info("Performance monitoring stopped")

def print_dashboard():
    """Print performance dashboard to console"""
    monitor = get_monitor()
    if not monitor:
        print("Performance monitor not available")
        return
    
    metrics = monitor.get_current_metrics()
    summary = monitor.get_summary_report()
    
    print("\n" + "="*60)
    print("PERFORMANCE DASHBOARD")
    print("="*60)
    print(f"Uptime: {summary['uptime_formatted']}")
    print(f"Files Processed: {metrics.files_processed}")
    print(f"Files/Second: {metrics.files_per_second:.2f}")
    print(f"Avg Processing Time: {metrics.avg_processing_time:.3f}s")
    print(f"DB Queries: {metrics.db_queries}")
    print(f"Cache Hit Rate: {metrics.cache_hit_rate*100:.1f}%")
    print(f"Errors: {metrics.error_count} ({metrics.error_rate*100:.1f}%)")
    print(f"CPU: {metrics.cpu_percent:.1f}%")
    print(f"Memory: {metrics.memory_percent:.1f}% ({metrics.memory_mb:.0f} MB)")
    print("="*60 + "\n")