"""
Unified Error Management System
Complete error handling, monitoring, and recovery system.

This module provides:
- Centralized error handling with categories and severity levels
- Structured error logging with automatic monitoring
- Error context management
- Error monitoring and alerting (Sentry/Rollbar integration)
- Automatic error recovery mechanisms
- Error pattern detection
- Error metrics and telemetry

Usage:
    from Hdg_Err_Ex_Log import handle_error, ErrorCategory, ErrorSeverity, get_error_monitor
    
    try:
        # some operation
    except Exception as e:
        handle_error(e, category=ErrorCategory.DATABASE, severity=ErrorSeverity.HIGH)
        # Error is automatically recorded to monitoring service
"""

from .error_handling import (
    ErrorCategory,
    ErrorSeverity,
    ErrorContext,
    handle_error,
    is_connection_error,
    is_retryable_error,
    get_error_category,
    get_error_severity,
    format_validation_error
)

from .retry_utils import (
    retry_on_error,
    retry_with_reconnect,
    safe_execute
)

from .logging_utils import (
    ActionRecorder,
    recorder,
    record_command_line_action,
    start_action_recording,
    stop_action_recording,
    is_recording_enabled,
    get_log_file_path
)

from .enhanced_error_handler import (
    UserFriendlyError,
    get_user_friendly_message,
    format_error_for_user
)

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    circuit_breaker
)

from .retry_policies import (
    RetryPolicy,
    RetryPolicyType,
    retry_with_policy,
    QUICK_RETRY,
    STANDARD_RETRY,
    AGGRESSIVE_RETRY,
    DATABASE_RETRY,
    NETWORK_RETRY
)

from .gradual_decline import (
    GradualDeclineManager,
    BackupService,
    ServiceHealth,
    with_backup
)

from .service_integrity import (
    ServiceIntegrityMonitor,
    IntegrityCheck,
    IntegrityCheckResult,
    database_health_check
)

from .error_monitoring import (
    ErrorMonitor,
    ErrorEvent,
    ErrorPattern,
    AlertLevel,
    get_error_monitor,
    record_error as record_error_to_monitor
)

from .error_recovery import (
    ErrorRecoveryManager,
    RecoveryStrategy,
    RecoveryResult,
    get_recovery_manager,
    with_recovery
)

__all__ = [
    'ErrorCategory',
    'ErrorSeverity',
    'ErrorContext',
    'handle_error',
    'is_connection_error',
    'is_retryable_error',
    'get_error_category',
    'get_error_severity',
    'format_validation_error',
    'retry_on_error',
    'retry_with_reconnect',
    'safe_execute',
    'ActionRecorder',
    'recorder',
    'record_command_line_action',
    'start_action_recording',
    'stop_action_recording',
    'is_recording_enabled',
    'get_log_file_path',
    'UserFriendlyError',
    'get_user_friendly_message',
    'format_error_for_user',
    'CircuitBreaker',
    'CircuitBreakerConfig',
    'CircuitBreakerOpenError',
    'CircuitState',
    'circuit_breaker',
    'RetryPolicy',
    'RetryPolicyType',
    'retry_with_policy',
    'QUICK_RETRY',
    'STANDARD_RETRY',
    'AGGRESSIVE_RETRY',
    'DATABASE_RETRY',
    'NETWORK_RETRY',
    'GradualDeclineManager',
    'BackupService',
    'ServiceHealth',
    'with_backup',
    'ServiceIntegrityMonitor',
    'IntegrityCheck',
    'IntegrityCheckResult',
    'database_health_check',
    'ErrorMonitor',
    'ErrorEvent',
    'ErrorPattern',
    'AlertLevel',
    'get_error_monitor',
    'record_error_to_monitor',
    'ErrorRecoveryManager',
    'RecoveryStrategy',
    'RecoveryResult',
    'get_recovery_manager',
    'with_recovery',
]

