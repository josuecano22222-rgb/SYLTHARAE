"""
Error Monitoring and Alerting Service
Provides centralized error monitoring with Sentry/Rollbar integration,
error metrics, pattern detection, and alerting.
"""

import logging
import time
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from enum import Enum
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ErrorEvent:
    """Represents a single error event"""
    timestamp: datetime
    error_type: str
    error_message: str
    category: str
    severity: str
    context: Dict[str, Any] = field(default_factory=dict)
    stack_trace: Optional[str] = None
    user_id: Optional[str] = None
    request_id: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return data


@dataclass
class ErrorPattern:
    """Represents a detected error pattern"""
    pattern_id: str
    error_type: str
    error_message_pattern: str
    category: str
    count: int
    first_seen: datetime
    last_seen: datetime
    frequency: float  # errors per hour
    affected_files: List[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        data['first_seen'] = self.first_seen.isoformat()
        data['last_seen'] = self.last_seen.isoformat()
        return data


class ErrorMonitor:
    """
    Centralized error monitoring service with:
    - Error tracking and metrics
    - Pattern detection
    - Alerting
    - Integration with external services (Sentry, Rollbar)
    """
    
    def __init__(
        self,
        enable_sentry: bool = False,
        sentry_dsn: Optional[str] = None,
        enable_rollbar: bool = False,
        rollbar_token: Optional[str] = None,
        max_events: int = 10000,
        pattern_detection_window: int = 3600,  # 1 hour
        alert_threshold: int = 10  # errors per hour
    ):
        self.enable_sentry = enable_sentry
        self.enable_rollbar = enable_rollbar
        self.max_events = max_events
        self.pattern_detection_window = pattern_detection_window
        self.alert_threshold = alert_threshold
        
        # Error storage
        self._events: deque = deque(maxlen=max_events)
        self._patterns: Dict[str, ErrorPattern] = {}
        self._metrics: Dict[str, Any] = defaultdict(int)
        # AUDIT (CONC-01): this must be re-entrant. ``get_metrics()`` acquires
        # the lock and then calls ``_get_recent_errors()``, which acquires the
        # same lock. With a plain ``threading.Lock`` that is a guaranteed
        # self-deadlock: every request to /api/errors/{metrics,recent,patterns,
        # stats} hung forever and permanently leaked a worker thread, so four
        # unauthenticated-safe GETs were enough to starve the server.
        self._lock = threading.RLock()
        
        # Alert callbacks
        self._alert_callbacks: List[Callable[[ErrorEvent, AlertLevel], None]] = []
        
        # Initialize external services
        self._sentry_client = None
        self._rollbar_client = None
        
        if enable_sentry and sentry_dsn:
            self._init_sentry(sentry_dsn)
        
        if enable_rollbar and rollbar_token:
            self._init_rollbar(rollbar_token)
        
        # Start background tasks
        self._running = True
        self._pattern_detection_thread = threading.Thread(
            target=self._pattern_detection_worker,
            daemon=True
        )
        self._pattern_detection_thread.start()
    
    def _init_sentry(self, dsn: str):
        """Initialize Sentry client"""
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
            
            sentry_sdk.init(
                dsn=dsn,
                integrations=[
                    LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
                ],
                traces_sample_rate=0.1,
                environment="production"
            )
            self._sentry_client = sentry_sdk
            logger.info("Sentry error monitoring initialized")
        except ImportError:
            logger.warning("sentry-sdk not installed. Install with: pip install sentry-sdk")
        except Exception as e:
            logger.error(f"Failed to initialize Sentry: {e}")
    
    def _init_rollbar(self, token: str):
        """Initialize Rollbar client"""
        try:
            import rollbar
            
            rollbar.init(
                token,
                environment='production',
                handler='blocking',
                timeout=5
            )
            self._rollbar_client = rollbar
            logger.info("Rollbar error monitoring initialized")
        except ImportError:
            logger.warning("rollbar not installed. Install with: pip install rollbar")
        except Exception as e:
            logger.error(f"Failed to initialize Rollbar: {e}")
    
    def record_error(
        self,
        error: Exception,
        category: str = "unknown",
        severity: str = "medium",
        context: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ):
        """
        Record an error event
        
        Args:
            error: The exception that occurred
            category: Error category
            severity: Error severity level
            context: Additional context
            user_id: Optional user ID
            request_id: Optional request ID
        """
        import traceback
        
        error_type = type(error).__name__
        error_message = str(error)
        stack_trace = traceback.format_exc()
        
        # Extract file path and line number from stack trace
        file_path = None
        line_number = None
        if stack_trace:
            lines = stack_trace.split('\n')
            for line in lines:
                if 'File "' in line and ', line ' in line:
                    try:
                        parts = line.split('File "')[1].split('", line ')
                        if len(parts) == 2:
                            file_path = parts[0]
                            line_number = int(parts[1].split(',')[0])
                            break
                    except (ValueError, IndexError):
                        pass
        
        event = ErrorEvent(
            timestamp=datetime.now(),
            error_type=error_type,
            error_message=error_message,
            category=category,
            severity=severity,
            context=context or {},
            stack_trace=stack_trace,
            user_id=user_id,
            request_id=request_id,
            file_path=file_path,
            line_number=line_number
        )
        
        with self._lock:
            self._events.append(event)
            self._metrics[f"errors_{category}"] += 1
            self._metrics[f"errors_{severity}"] += 1
            self._metrics["total_errors"] += 1
        
        # Send to external services
        self._send_to_external_services(event, error)
        
        # Check for alerting
        self._check_alerts(event)
    
    def _send_to_external_services(self, event: ErrorEvent, error: Exception):
        """Send error to external monitoring services"""
        # Send to Sentry
        if self._sentry_client:
            try:
                with self._sentry_client.push_scope() as scope:
                    scope.set_tag("category", event.category)
                    scope.set_tag("severity", event.severity)
                    if event.user_id:
                        scope.user = {"id": event.user_id}
                    if event.context:
                        scope.set_context("error_context", event.context)
                    self._sentry_client.capture_exception(error)
            except Exception as e:
                logger.warning(f"Failed to send error to Sentry: {e}")
        
        # Send to Rollbar
        if self._rollbar_client:
            try:
                extra_data = {
                    "category": event.category,
                    "severity": event.severity,
                    "context": event.context
                }
                if event.user_id:
                    extra_data["user_id"] = event.user_id
                
                level = "error" if event.severity in ["high", "critical"] else "warning"
                self._rollbar_client.report_exc_info(
                    exc_info=(type(error), error, error.__traceback__),
                    level=level,
                    extra_data=extra_data
                )
            except Exception as e:
                logger.warning(f"Failed to send error to Rollbar: {e}")
    
    def _check_alerts(self, event: ErrorEvent):
        """Check if error should trigger an alert"""
        if event.severity == "critical":
            self._trigger_alert(event, AlertLevel.CRITICAL)
        elif event.severity == "high":
            # Check frequency for high severity errors
            recent_errors = self._get_recent_errors(
                category=event.category,
                minutes=60
            )
            if len(recent_errors) >= self.alert_threshold:
                self._trigger_alert(event, AlertLevel.ERROR)
    
    def _trigger_alert(self, event: ErrorEvent, level: AlertLevel):
        """Trigger alert callbacks"""
        for callback in self._alert_callbacks:
            try:
                callback(event, level)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")
    
    def register_alert_callback(self, callback: Callable[[ErrorEvent, AlertLevel], None]):
        """Register a callback for alerts"""
        self._alert_callbacks.append(callback)
    
    def _pattern_detection_worker(self):
        """Background worker for pattern detection"""
        while self._running:
            try:
                time.sleep(300)  # Run every 5 minutes
                self._detect_patterns()
            except Exception as e:
                logger.error(f"Pattern detection worker error: {e}")
    
    def _detect_patterns(self):
        """Detect error patterns"""
        with self._lock:
            now = datetime.now()
            window_start = now - timedelta(seconds=self.pattern_detection_window)
            
            # Group errors by type and message pattern
            error_groups: Dict[str, List[ErrorEvent]] = defaultdict(list)
            
            for event in self._events:
                if event.timestamp >= window_start:
                    # Create pattern key from error type and message
                    pattern_key = f"{event.error_type}:{event.error_message[:100]}"
                    error_groups[pattern_key].append(event)
            
            # Detect patterns
            for pattern_key, events in error_groups.items():
                if len(events) >= 3:  # At least 3 occurrences
                    hours = self.pattern_detection_window / 3600
                    frequency = len(events) / hours
                    
                    if pattern_key not in self._patterns:
                        # New pattern
                        self._patterns[pattern_key] = ErrorPattern(
                            pattern_id=pattern_key,
                            error_type=events[0].error_type,
                            error_message_pattern=events[0].error_message[:100],
                            category=events[0].category,
                            count=len(events),
                            first_seen=events[0].timestamp,
                            last_seen=events[-1].timestamp,
                            frequency=frequency,
                            affected_files=list(set(e.file_path for e in events if e.file_path))
                        )
                    else:
                        # Update existing pattern
                        pattern = self._patterns[pattern_key]
                        pattern.count = len(events)
                        pattern.last_seen = events[-1].timestamp
                        pattern.frequency = frequency
                        pattern.affected_files = list(set(
                            pattern.affected_files + 
                            [e.file_path for e in events if e.file_path]
                        ))
    
    def _get_recent_errors(
        self,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        minutes: int = 60
    ) -> List[ErrorEvent]:
        """Get recent errors matching criteria"""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        
        with self._lock:
            return [
                event for event in self._events
                if event.timestamp >= cutoff
                and (category is None or event.category == category)
                and (severity is None or event.severity == severity)
            ]
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get error metrics"""
        with self._lock:
            recent_errors = self._get_recent_errors(minutes=60)
            
            return {
                "total_errors": self._metrics["total_errors"],
                "errors_last_hour": len(recent_errors),
                "errors_by_category": {
                    cat: self._metrics[f"errors_{cat}"]
                    for cat in ["file_processing", "database", "database_connection", 
                               "validation", "network", "configuration", "unknown"]
                },
                "errors_by_severity": {
                    sev: self._metrics[f"errors_{sev}"]
                    for sev in ["low", "medium", "high", "critical"]
                },
                "patterns_detected": len(self._patterns),
                "top_patterns": [
                    pattern.to_dict()
                    for pattern in sorted(
                        self._patterns.values(),
                        key=lambda p: p.frequency,
                        reverse=True
                    )[:10]
                ]
            }
    
    def get_patterns(self) -> List[Dict[str, Any]]:
        """Get detected error patterns"""
        with self._lock:
            return [pattern.to_dict() for pattern in self._patterns.values()]
    
    def get_recent_errors(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent error events"""
        with self._lock:
            return [
                event.to_dict()
                for event in list(self._events)[-limit:]
            ]
    
    def shutdown(self):
        """Shutdown the monitor"""
        self._running = False
        if self._pattern_detection_thread.is_alive():
            self._pattern_detection_thread.join(timeout=5)


# Global monitor instance
_monitor_instance: Optional[ErrorMonitor] = None
_monitor_lock = threading.Lock()


def get_error_monitor() -> ErrorMonitor:
    """Get or create the global error monitor instance"""
    global _monitor_instance
    
    if _monitor_instance is None:
        with _monitor_lock:
            if _monitor_instance is None:
                # Initialize with defaults (can be configured via environment)
                import os
                _monitor_instance = ErrorMonitor(
                    enable_sentry=os.getenv("SENTRY_ENABLED", "false").lower() == "true",
                    sentry_dsn=os.getenv("SENTRY_DSN"),
                    enable_rollbar=os.getenv("ROLLBAR_ENABLED", "false").lower() == "true",
                    rollbar_token=os.getenv("ROLLBAR_TOKEN")
                )
    
    return _monitor_instance


def record_error(
    error: Exception,
    category: str = "unknown",
    severity: str = "medium",
    context: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
):
    """
    Convenience function to record an error
    
    Args:
        error: The exception
        category: Error category
        severity: Error severity
        context: Additional context
        user_id: Optional user ID
        request_id: Optional request ID
    """
    try:
        monitor = get_error_monitor()
        monitor.record_error(
            error=error,
            category=category,
            severity=severity,
            context=context,
            user_id=user_id,
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"Failed to record error in monitor: {e}")

