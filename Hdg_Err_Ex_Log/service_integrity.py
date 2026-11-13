"""
Service Integrity Checks
Monitors service health and provides automatic recovery
"""

import time
import logging
import threading
from typing import Callable, Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from .error_handling import handle_error, ErrorCategory, ErrorSeverity

logger = logging.getLogger(__name__)


class IntegrityCheckResult(Enum):
    """Result of integrity check"""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


@dataclass
class IntegrityCheck:
    """Service integrity check configuration"""
    name: str
    check_func: Callable[[], bool]
    interval: float = 60.0  # Check interval in seconds
    timeout: float = 5.0  # Check timeout in seconds
    failure_threshold: int = 3  # Failures before marking service down
    recovery_func: Optional[Callable[[], bool]] = None
    enabled: bool = True


class ServiceIntegrityMonitor:
    """
    Monitors service integrity and provides automatic recovery
    """
    
    def __init__(self, service_name: str):
        """
        Initialize integrity monitor
        
        Args:
            service_name: Name of the service being monitored
        """
        self.service_name = service_name
        self.checks: List[IntegrityCheck] = []
        self.check_results: Dict[str, IntegrityCheckResult] = {}
        self.failure_counts: Dict[str, int] = {}
        self.last_check_times: Dict[str, float] = {}
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.stats = {
            'total_checks': 0,
            'passed_checks': 0,
            'failed_checks': 0,
            'recoveries': 0
        }
    
    def add_check(self, check: IntegrityCheck):
        """Add integrity check"""
        with self.lock:
            self.checks.append(check)
            self.check_results[check.name] = IntegrityCheckResult.PASS
            self.failure_counts[check.name] = 0
            logger.info(f"Added integrity check '{check.name}' for '{self.service_name}'")
    
    def run_check(self, check: IntegrityCheck) -> IntegrityCheckResult:
        """
        Run a single integrity check
        
        Args:
            check: Integrity check to run
            
        Returns:
            Check result
        """
        if not check.enabled:
            return IntegrityCheckResult.PASS
        
        try:
            # Run check with timeout
            import signal
            
            def timeout_handler(signum, frame):
                raise TimeoutError(f"Integrity check '{check.name}' timed out")
            
            # Set timeout (Unix only)
            try:
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(int(check.timeout))
            except (AttributeError, ValueError):
                # Windows doesn't support SIGALRM, use threading.Timer instead
                pass
            
            try:
                result = check.check_func()
                if result:
                    return IntegrityCheckResult.PASS
                else:
                    return IntegrityCheckResult.FAIL
            finally:
                try:
                    signal.alarm(0)  # Cancel alarm
                except (AttributeError, ValueError):
                    pass
        
        except TimeoutError as e:
            logger.warning(f"Integrity check '{check.name}' timed out: {e}")
            return IntegrityCheckResult.FAIL
        except Exception as e:
            logger.error(f"Integrity check '{check.name}' raised exception: {e}")
            return IntegrityCheckResult.FAIL
    
    def check_all(self) -> Dict[str, IntegrityCheckResult]:
        """
        Run all integrity checks
        
        Returns:
            Dictionary of check results
        """
        results = {}
        
        with self.lock:
            for check in self.checks:
                if not check.enabled:
                    continue
                
                # Check if enough time has passed since last check
                last_check = self.last_check_times.get(check.name, 0)
                if time.time() - last_check < check.interval:
                    # Use cached result
                    results[check.name] = self.check_results.get(check.name, IntegrityCheckResult.PASS)
                    continue
                
                # Run check
                self.stats['total_checks'] += 1
                result = self.run_check(check)
                results[check.name] = result
                self.last_check_times[check.name] = time.time()
                
                # Update failure count
                if result == IntegrityCheckResult.FAIL:
                    self.failure_counts[check.name] = self.failure_counts.get(check.name, 0) + 1
                    self.stats['failed_checks'] += 1
                    
                    # Try recovery if threshold reached
                    if self.failure_counts[check.name] >= check.failure_threshold:
                        if check.recovery_func:
                            logger.warning(
                                f"Integrity check '{check.name}' failed {self.failure_counts[check.name]} times. "
                                f"Attempting recovery..."
                            )
                            try:
                                if check.recovery_func():
                                    logger.info(f"Recovery successful for '{check.name}'")
                                    self.failure_counts[check.name] = 0
                                    self.stats['recoveries'] += 1
                                    results[check.name] = IntegrityCheckResult.PASS
                                else:
                                    logger.error(f"Recovery failed for '{check.name}'")
                            except Exception as recovery_error:
                                logger.error(f"Recovery raised exception: {recovery_error}")
                else:
                    # Reset failure count on success
                    if self.failure_counts.get(check.name, 0) > 0:
                        self.failure_counts[check.name] = 0
                    self.stats['passed_checks'] += 1
                
                self.check_results[check.name] = result
        
        return results
    
    def start_monitoring(self):
        """Start background monitoring"""
        if self.monitoring_active:
            return
        
        self.monitoring_active = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"Started integrity monitoring for '{self.service_name}'")
    
    def stop_monitoring(self):
        """Stop background monitoring"""
        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5.0)
        logger.info(f"Stopped integrity monitoring for '{self.service_name}'")
    
    def _monitor_loop(self):
        """Background monitoring loop"""
        while self.monitoring_active:
            try:
                self.check_all()
                # Sleep for minimum interval
                min_interval = min([c.interval for c in self.checks], default=60.0)
                time.sleep(min_interval)
            except Exception as e:
                logger.error(f"Error in integrity monitoring loop: {e}")
                time.sleep(10.0)  # Wait before retrying
    
    def get_status(self) -> Dict[str, Any]:
        """Get service integrity status"""
        with self.lock:
            all_passing = all(
                result == IntegrityCheckResult.PASS
                for result in self.check_results.values()
            )
            
            return {
                'service_name': self.service_name,
                'healthy': all_passing,
                'check_results': {k: v.value for k, v in self.check_results.items()},
                'failure_counts': self.failure_counts.copy(),
                'stats': self.stats.copy()
            }


def database_health_check(connection) -> Callable[[], bool]:
    """
    Create database health check function
    
    Args:
        connection: Database connection
        
    Returns:
        Health check function
    """
    def check() -> bool:
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except Exception:
            return False
    
    return check

