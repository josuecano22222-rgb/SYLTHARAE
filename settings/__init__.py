"""
Unified Settings Management System
Centralized interface for all settings and configuration.

This module provides:
- SettingsManager: Core settings management with file I/O
- SettingsAdapter: Backward compatibility layer
- All settings models: System, Display, Search, Processing, etc.
- Database configuration: Connection and pool settings
- Import settings: Import-specific configuration
- API routes: Flask routes for settings management

Usage:
    from settings import get_settings, get_database_config, get_settings_manager
    
    # Get settings
    settings = get_settings()
    language = settings.system.language
    
    # Get database config
    db_config = get_database_config()
    connection_string = db_config.get_connection_string()
    
    # Direct manager access
    manager = get_settings_manager()
    manager.set('system.language', 'fr')
    manager.save()
"""

# ==================== CORE SETTINGS MANAGER ====================

from .settings_manager import (
    SettingsManager,
    get_settings_manager,
    detect_settings_file
)

# ==================== SETTINGS ADAPTER (Backward Compatibility) ====================

from .settings_adapter import (
    SettingsAdapter,
    get_interface_manager,
    get_settings,
    get_user_settings,
    get_settings_integration
)

# ==================== SETTINGS MODELS ====================

from .settings_models import (
    AllSettings,
    SystemSettings,
    DisplaySettings,
    SearchSettings,
    ProcessingSettings,
    NotificationSettings,
    StorageConfig,
    DatabaseConfig,
    ThemeSettings,
    InterfaceConfig,
    InterfaceVisibility,
    SettingDefinition,
    SettingType,
    ValidationError,
    get_setting_definition
)

# ==================== SETTINGS API (Main Entry Point) ====================

from .settings import (
    reload_settings_from_file,
    ensure_settings_file_exists,
    get_processing_config,
    get_storage_config,
    get_database_config,
    reset_settings,
    UnifiedSettingsManager,
    ProcessingConfig,
    DisplayConfig,
    SearchConfig,
    NotificationConfig,
    SystemConfig
)

# ==================== DATABASE CONFIGURATION ====================

from .config import (
    get_db_config,
    get_connection_string,
    AppConfig,
    get_config,
    set_config,
    reset_config,
    load_database_config_from_file,
    invalidate_database_connections,
    POOL_MIN_CONN,
    POOL_MAX_CONN,
    POOL_TIMEOUT,
    QUERY_TIMEOUT,
    BATCH_SIZE,
    CHUNK_SIZE
)

# ==================== EXPORTS ====================

__all__ = [
    # Core manager
    'SettingsManager',
    'get_settings_manager',
    'detect_settings_file',
    
    # Settings adapter
    'SettingsAdapter',
    'get_interface_manager',
    'get_settings',
    'get_user_settings',
    'get_settings_integration',
    
    # Settings models
    'AllSettings',
    'SystemSettings',
    'DisplaySettings',
    'SearchSettings',
    'ProcessingSettings',
    'NotificationSettings',
    'StorageConfig',
    'DatabaseConfig',
    'ThemeSettings',
    'InterfaceConfig',
    'InterfaceVisibility',
    'SettingDefinition',
    'SettingType',
    'ValidationError',
    'get_setting_definition',
    
    # Main settings API
    'reload_settings_from_file',
    'ensure_settings_file_exists',
    'get_processing_config',
    'get_storage_config',
    'get_database_config',
    'reset_settings',
    'UnifiedSettingsManager',
    'ProcessingConfig',
    'DisplayConfig',
    'SearchConfig',
    'NotificationConfig',
    'SystemConfig',
    
    # Database configuration
    'get_db_config',
    'get_connection_string',
    'AppConfig',
    'get_config',
    'set_config',
    'reset_config',
    'load_database_config_from_file',
    'invalidate_database_connections',
    'POOL_MIN_CONN',
    'POOL_MAX_CONN',
    'POOL_TIMEOUT',
    'QUERY_TIMEOUT',
    'BATCH_SIZE',
    'CHUNK_SIZE',
]
