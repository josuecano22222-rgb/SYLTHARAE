"""
Unified Central Settings System - BACKWARD COMPATIBILITY LAYER
File: settings/settings.py

This module now provides backward compatibility by wrapping the new settings system.
All functionality is provided by settings.settings_manager and settings.settings_adapter.

The old UnifiedSettingsManager API is preserved for existing code.
"""

# Import the new system
from .settings_adapter import SettingsAdapter, get_interface_manager
from .settings_models import (
    ProcessingSettings, StorageConfig, DatabaseConfig,
    DisplaySettings, SearchSettings, NotificationSettings, SystemSettings
)
from pathlib import Path
from typing import Optional, Dict, Any

# Create aliases for backward compatibility (old names -> new names)
ProcessingConfig = ProcessingSettings
DisplayConfig = DisplaySettings
SearchConfig = SearchSettings
NotificationConfig = NotificationSettings
SystemConfig = SystemSettings

# Re-export for backward compatibility
UnifiedSettingsManager = SettingsAdapter

# Re-export functions from settings_adapter (no duplicates)
from .settings_adapter import get_settings, get_user_settings, get_settings_integration


def reload_settings_from_file() -> bool:
    """Reload settings from file (backward compatibility)"""
    settings = get_settings()
    if hasattr(settings, 'reload_from_file'):
        return settings.reload_from_file()
    # If reload_from_file doesn't exist, reload via manager
    settings._manager.load()
    return True


def ensure_settings_file_exists() -> Optional[Path]:
    """
    Ensure settings file exists (backward compatibility).
    
    Returns:
        Path to settings file
    """
    settings = get_settings()
    # The SettingsManager automatically creates the file if it doesn't exist
    # Access the manager's settings file to trigger creation
    settings_file = settings._manager.settings_file
    return settings_file


# Additional convenience functions for backward compatibility
def get_processing_config():
    """Get processing configuration (backward compatibility)"""
    return get_settings().processing


def get_storage_config():
    """Get storage configuration (backward compatibility)"""
    return get_settings().storage


# Import from config module to avoid duplication
from .config import get_database_config


def reset_settings():
    """Reset settings to defaults (useful for testing)"""
    from .settings_adapter import reset_adapter
    reset_adapter()


# Re-export config classes for backward compatibility
__all__ = [
    'UnifiedSettingsManager',
    'SettingsAdapter',
    'get_settings',
    'get_user_settings',
    'get_settings_integration',
    'reload_settings_from_file',
    'ensure_settings_file_exists',
    'get_processing_config',
    'get_storage_config',
    'get_database_config',
    'reset_settings',
    'ProcessingConfig',
    'StorageConfig',
    'DatabaseConfig',
    'DisplayConfig',
    'SearchConfig',
    'NotificationConfig',
    'SystemConfig',
    # Constants for backward compatibility
    'DEFAULT_SETTINGS_FILE',
    'SETTINGS_FILE',
    'INTERFACES',
]

# Default settings file location (for backward compatibility)
DEFAULT_SETTINGS_FILE = Path.home() / '.file_analysis' / 'settings.json'
SETTINGS_FILE = None  # Will be determined at runtime

# INTERFACES constant for backward compatibility (empty dict - interfaces are managed via settings)
INTERFACES = {}
