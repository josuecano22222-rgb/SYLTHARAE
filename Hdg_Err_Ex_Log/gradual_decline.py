"""
Gradual Decline and Backup Mechanisms
Provides fallback strategies and service degradation
"""

import logging
import time
from typing import Callable, Optional, List, Dict, Any, TypeVar
from dataclasses import dataclass
from enum import Enum

from .error_handling import handle_error, ErrorCategory, ErrorSeverity

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ServiceHealth(Enum):
    """Service health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DOWN = "down"


@dataclass
class BackupService:
    """Backup service configuration"""
    name: str
    service_func: Callable
    priority: int = 1  # Lower number = higher priority
    health_check: Optional[Callable] = None
    enabled: bool = True


class GradualDeclineManager:
    """
    Manages gradual service decline and backup mechanisms
    
    Provides:
    - Automatic fallback to backup services
    - Service health monitoring
    - Graceful degradation
    """
    
    def __init__(self, primary_service: Callable, service_name: str = "service"):
        """
        Initialize gradual decline manager
        
        Args:
            primary_service: Primary service function
            service_name: Name of the service
        """
        self.primary_service = primary_service
        self.service_name = service_name
        self.backup_services: List[BackupService] = []
        self.health_status = ServiceHealth.HEALTHY
        self.last_health_check: Optional[float] = None
        self.health_check_interval = 60.0  # Check health every 60 seconds
        self.failure_count = 0
        self.failure_threshold = 3  # Switch to backup after 3 failures
        self.stats = {
            'primary_calls': 0,
            'backup_calls': 0,
            'failures': 0,
            'recoveries': 0
        }
    
    def add_backup(self, backup: BackupService):
        """Add backup service"""
        self.backup_services.append(backup)
        # Sort by priority
        self.backup_services.sort(key=lambda x: x.priority)
        logger.info(f"Added backup service '{backup.name}' for '{self.service_name}'")
    
    def call(self, *args, **kwargs) -> Any:
        """
        Call service with automatic fallback
        
        Args:
            *args: Positional arguments for service
            **kwargs: Keyword arguments for service
            
        Returns:
            Service result
            
        Raises:
            Original exception if all services fail
        """
        # Try primary service first if healthy
        if self.health_status in (ServiceHealth.HEALTHY, ServiceHealth.DEGRADED):
            try:
                result = self.primary_service(*args, **kwargs)
                self._on_success()
                self.stats['primary_calls'] += 1
                return result
            except Exception as e:
                self._on_failure()
                logger.warning(f"Primary service '{self.service_name}' failed: {e}")
        
        # Try backup services
        for backup in self.backup_services:
            if not backup.enabled:
                continue
            
            # Check backup health if health check available
            if backup.health_check:
                try:
                    if not backup.health_check():
                        logger.debug(f"Backup service '{backup.name}' is unhealthy, skipping")
                        continue
                except Exception as health_error:
                    logger.warning(f"Health check failed for '{backup.name}': {health_error}")
                    continue
            
            try:
                logger.info(f"Using backup service '{backup.name}' for '{self.service_name}'")
                result = backup.service_func(*args, **kwargs)
                self.stats['backup_calls'] += 1
                return result
            except Exception as e:
                logger.warning(f"Backup service '{backup.name}' failed: {e}")
                continue
        
        # All services failed
        self.stats['failures'] += 1
        raise RuntimeError(f"All services failed for '{self.service_name}'")
    
    def _on_success(self):
        """Handle successful call"""
        if self.failure_count > 0:
            self.failure_count = 0
            if self.health_status != ServiceHealth.HEALTHY:
                self.health_status = ServiceHealth.HEALTHY
                self.stats['recoveries'] += 1
                logger.info(f"Service '{self.service_name}' recovered")
    
    def _on_failure(self):
        """Handle failed call"""
        self.failure_count += 1
        self.stats['failures'] += 1
        
        if self.failure_count >= self.failure_threshold:
            if self.health_status == ServiceHealth.HEALTHY:
                self.health_status = ServiceHealth.DEGRADED
                logger.warning(f"Service '{self.service_name}' degraded, using backups")
            elif self.failure_count >= self.failure_threshold * 2:
                self.health_status = ServiceHealth.UNHEALTHY
                logger.error(f"Service '{self.service_name}' unhealthy")
    
    def check_health(self) -> ServiceHealth:
        """
        Check service health
        
        Returns:
            Current health status
        """
        current_time = time.time()
        
        # Throttle health checks
        if self.last_health_check and \
           (current_time - self.last_health_check) < self.health_check_interval:
            return self.health_status
        
        self.last_health_check = current_time
        
        # Check primary service
        try:
            # Simple health check - try a minimal operation
            # This should be overridden by specific health checks
            if hasattr(self.primary_service, '__self__'):
                # It's a method, try to call a health check method if available
                if hasattr(self.primary_service.__self__, 'health_check'):
                    if not self.primary_service.__self__.health_check():
                        self.health_status = ServiceHealth.UNHEALTHY
                        return self.health_status
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            self.health_status = ServiceHealth.UNHEALTHY
            return self.health_status
        
        # If we got here and had failures, mark as degraded
        if self.failure_count > 0:
            self.health_status = ServiceHealth.DEGRADED
        else:
            self.health_status = ServiceHealth.HEALTHY
        
        return self.health_status
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics"""
        return {
            'service_name': self.service_name,
            'health_status': self.health_status.value,
            'failure_count': self.failure_count,
            'backup_count': len(self.backup_services),
            'stats': self.stats.copy()
        }


def with_backup(primary: Callable, backups: List[BackupService], service_name: str = "service"):
    """
    Decorator for automatic backup service fallback
    
    Args:
        primary: Primary service function
        backups: List of backup services
        service_name: Service name
        
    Returns:
        Decorated function
    """
    manager = GradualDeclineManager(primary, service_name)
    for backup in backups:
        manager.add_backup(backup)
    
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            return manager.call(*args, **kwargs)
        wrapper.gradual_decline_manager = manager
        return wrapper
    return decorator

