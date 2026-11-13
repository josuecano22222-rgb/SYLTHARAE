"""
Simplified initialization module for project entry points.
Consolidates common initialization logic.
"""

import sys
from pathlib import Path
from typing import Optional

# Ensure UTF-8 encoding for stdout on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        # Python < 3.7 or reconfigure not available
        pass

def safe_print(message: str) -> None:
    """Safely print message, handling encoding issues"""
    try:
        print(message)
    except UnicodeEncodeError:
        # Fallback: replace problematic characters
        safe_message = message.encode('ascii', 'replace').decode('ascii')
        print(safe_message)


def setup_project_path(file_path: Optional[str] = None) -> Path:
    """
    Set up Python path by adding project root to sys.path.
    Simplified version that handles common cases.

    Also loads the project's ``.env`` file (if present) into the process
    environment so that DB_*, FLASK_* and APP_* settings work out of the
    box. Real OS environment variables always win over ``.env`` values.

    Args:
        file_path: Path to the file calling this function (usually __file__)

    Returns:
        Path object to project root
    """
    if file_path is None:
        file_path = __file__
    
    current_file = Path(file_path).resolve()
    project_root = current_file.parent
    
    # For entry points at project root, project_root is already correct
    # For files in subdirectories, go up to project root
    # Check if we're in a subdirectory that indicates we need to go up
    if project_root.name in ['apps', 'core', 'database', 'Api']:
        project_root = project_root.parent
    
    project_root_str = str(project_root)
    
    # Remove if exists elsewhere to avoid conflicts
    if project_root_str in sys.path:
        sys.path.remove(project_root_str)
    
    # Add to beginning of path
    sys.path.insert(0, project_root_str)
    
    # Clear any cached wrong imports for core.path_utils
    if 'core.path_utils' in sys.modules:
        module = sys.modules['core.path_utils']
        if hasattr(module, '__file__'):
            module_path = Path(module.__file__).resolve()
            if 'managers' in str(module_path):
                del sys.modules['core.path_utils']
                if 'core' in sys.modules and hasattr(sys.modules['core'], 'path_utils'):
                    delattr(sys.modules['core'], 'path_utils')

    # Load .env from the project root (beginner-friendly configuration).
    # override=False preserves ARCH-02 precedence: real environment
    # variables always beat values from the file.
    _load_dotenv_file(project_root)

    # Apply the environment-specific config overlay (config/{FLASK_ENV}.json).
    _apply_environment_overlay(project_root)

    return project_root


def _load_dotenv_file(project_root: Path) -> None:
    """
    Load environment variables from ``<project_root>/.env`` if it exists.

    Silently does nothing when python-dotenv is not installed or when no
    ``.env`` file exists, so minimal/offline installs keep working.
    """
    try:
        env_file = Path(project_root) / '.env'
        if not env_file.exists():
            return
        from dotenv import load_dotenv  # python-dotenv is a core dependency
        # Generated credentials are literal values; interpolation would turn
        # a password such as ``pa${HOME}ss`` into a different secret.
        loaded = load_dotenv(env_file, override=False, interpolate=False)
        if loaded:
            import logging
            logging.getLogger(__name__).info(
                "Loaded configuration from %s", env_file
            )
    except ImportError:
        pass  # python-dotenv not installed yet (first-time setup)
    except Exception as exc:  # never block startup on a config file issue
        import logging
        logging.getLogger(__name__).warning(
            "Could not load .env file (%s); continuing with environment defaults",
            exc,
        )


def _apply_environment_overlay(project_root: Path) -> None:
    """
    Load and apply the environment-specific config overlay.

    The overlay lives in ``config/{FLASK_ENV}.json`` and is merged on top
    of the project-level ``config.json`` and ``.env`` values.  This gives
    per-environment tuning (e.g. stricter rate limits in production) without
    touching the user's ``.env`` or ``config.json``.
    """
    try:
        from config import load_effective_config, validate_config, get_environment
        effective = load_effective_config()
        valid, errors = validate_config(effective)
        if not valid:
            import logging
            logging.getLogger(__name__).warning(
                "Configuration validation warnings: %s", "; ".join(errors)
            )
    except ImportError:
        pass  # config module not available (very early in setup)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug(
            "Environment overlay not applied: %s", exc
        )


def initialize_settings(project_root: Path) -> None:
    """
    Initialize unified settings with project root.
    
    Args:
        project_root: Path to project root
    """
    try:
        from settings import get_settings, ensure_settings_file_exists
        settings = get_settings()
        if settings.project_root is None:
            settings.set_project_root(project_root)
        
        ensure_settings_file_exists()
        settings.reload_from_file()
        
        # Initialize resource coordinator
        try:
            from core.resource_coordinator import (
                get_resource_coordinator,
                get_safe_worker_count,
                get_safe_db_pool_size
            )
            coordinator = get_resource_coordinator()
            
            # Apply safe resource limits
            requested_workers = settings.processing.max_workers
            safe_workers = get_safe_worker_count(requested_workers)
            if safe_workers != requested_workers:
                settings.processing.max_workers = safe_workers
                safe_print(f"[WARNING] Worker count adjusted from {requested_workers} to {safe_workers}")
            
            requested_db_pool = settings.database.pool_max_conn
            safe_db_pool = get_safe_db_pool_size(requested_db_pool)
            if safe_db_pool != requested_db_pool:
                settings.database.pool_max_conn = safe_db_pool
                safe_print(f"[WARNING] DB pool size adjusted from {requested_db_pool} to {safe_db_pool}")
            
            status = coordinator.get_resource_status()
            safe_print(f"[OK] Resource Coordinator active - {status['running_instances']['total']} instance(s)")
        except Exception as e:
            safe_print(f"[WARNING] Resource coordinator warning: {e}")
        
        processing_timeout = getattr(settings.processing, 'file_processing_timeout', 1200)
        safe_print(f"[OK] Settings initialized - File processing timeout: {processing_timeout}s")
    except Exception as e:
        safe_print(f"[WARNING] Settings initialization warning: {e}")


def initialize_database_config() -> None:
    """Load saved database configuration if available."""
    try:
        from settings.config import load_database_config_from_file
        load_database_config_from_file()
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Database config not found (using defaults): {e}")


def initialize_system() -> None:
    """Initialize system on startup."""
    try:
        from core.initialization import ensure_system_initialized
        ensure_system_initialized()
        safe_print("[OK] System initialization completed")
    except Exception as e:
        safe_print(f"[WARNING] System initialization warning: {e}")

