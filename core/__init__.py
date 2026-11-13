"""Core utilities module at project root
Provides path setup and other core functionality
"""

# Export configuration functions — authoritative source is settings/
import sys as _sys
_settings = _sys.modules.get("settings")
if _settings is None:
    try:
        import settings as _settings
    except ImportError:
        _settings = None

if _settings is not None:
    # Read the names defensively. This module is a compatibility shim, and it
    # is imported by anything that touches ``core.*`` - including modules that
    # ``settings/__init__`` itself imports (the interface registry is one).
    # Reaching for an attribute that a partially initialised ``settings``
    # module does not have yet raised AttributeError and made ``import core``
    # fail depending purely on which package was imported first.
    for _name in (
        "get_config", "set_config", "reset_config", "get_processing_config",
        "get_storage_config", "get_database_config",
        "AppConfig", "ProcessingConfig", "StorageConfig", "DatabaseConfig",
    ):
        _value = getattr(_settings, _name, None)
        if _value is not None:
            globals()[_name] = _value
    del _name, _value

"""
Core utilities module - Common toolkit for shared functions
All functions are independent, reusable, and have no hidden dependencies.
"""

# Safe imports with error handling
try:
    from .file_utils import (
        get_standardized_metadata,
        read_tree,
        create_standardized_result,
        format_file_size,
        calculate_file_hash,
        sanitize_filename
    )
except ImportError as e:
    raise ImportError(f"Failed to import from file_utils: {e}")

try:
    from .path_utils import (
        get_extraction_base_folder,
        get_extraction_name_file
    )
except ImportError as e:
    raise ImportError(f"Failed to import from path_utils: {e}")

try:
    from .time_utils import (
        print_execution_time,
        calculate_processing_statistics,
        calculate_file_processing_metrics
    )
except ImportError as e:
    raise ImportError(f"Failed to import from time_utils: {e}")


try:
    from .detect_binanry_utils import (
        detect_file_type,
        detect_file_type_with_confidence,
        get_filename_with_correct_extension,
        read_file_header,
        sniff_file_type,
    )
except ImportError as e:
    raise ImportError(f"Failed to import from detect_binanry_utils: {e}")
__all__ = [
    'get_config',
    'set_config',
    'reset_config',
    'get_processing_config',
    'get_storage_config',
    'get_database_config',
    'AppConfig',
    'ProcessingConfig',
    'StorageConfig',
    'DatabaseConfig',
        # File utilities
    'get_standardized_metadata',
    'read_tree',
    'create_standardized_result',
    'format_file_size',
    'calculate_file_hash',
    'sanitize_filename',
    # Path utilities
    'get_extraction_base_folder',
    'get_extraction_name_file',
    # Time utilities
    'print_execution_time',
    'calculate_processing_statistics',
    'calculate_file_processing_metrics',
    # Logging utilities
    'record_command_line_action',
    'start_action_recording',
    'stop_action_recording',
    'is_recording_enabled',
    'get_log_file_path',
    # detect_file utilities
    'detect_file_type',
    'detect_file_type_with_confidence',
    'get_filename_with_correct_extension',
    'read_file_header',
    'sniff_file_type'
]

