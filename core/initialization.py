"""
System Initialization Module
Handles first-time setup and system initialization on startup
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from settings.config import get_config
from settings import get_settings, get_settings_manager

logger = logging.getLogger(__name__)

# Marker file to track if system has been initialized.
#
# ``None`` means "derive it from the application data root" - see
# :func:`marker_path`. Tests and tools may bind an absolute path here to
# redirect the marker; the default must never be a bare relative path, because
# this flag decides whether an operator sees the application or the setup
# wizard, and a relative path makes that decision depend on the directory the
# process was started from (``core/app_paths.py`` states the rule: every
# runtime path derives from the data root, never from the CWD).
INIT_MARKER_FILE: Optional[Path] = None

#: ``None`` = the defaults below (the working directory and the checkout).
#: Previous releases wrote ``.system_initialized`` in whichever directory the
#: installer or the server was started from, so both places are still read -
#: otherwise upgrading would ask a working installation to install itself.
_LEGACY_MARKER_DIRS: Optional[tuple] = None

_MARKER_NAME = ".system_initialized"


def _legacy_marker_dirs() -> tuple:
    if _LEGACY_MARKER_DIRS is not None:
        return tuple(Path(d) for d in _LEGACY_MARKER_DIRS)
    from core.app_paths import get_project_root

    return (Path.cwd(), get_project_root())


def marker_path() -> Path:
    """The marker's location: explicit override, else the data root.

    Resolved on every call rather than at import: the data root is
    configuration (``APP_DATA_DIR``), and a module imported before the
    configuration is applied would otherwise freeze the wrong path for the
    lifetime of the process.
    """
    if INIT_MARKER_FILE is not None:
        return Path(INIT_MARKER_FILE)
    from core.app_paths import get_data_root

    return get_data_root() / _MARKER_NAME


def is_system_initialized() -> bool:
    """Check if system has been initialized.

    True when the marker is where this release puts it, or where a previous
    release put it. Never relative to the current working directory unless
    that is where a previous release left the file.
    """
    try:
        if marker_path().exists():
            return True
    except Exception as e:  # a broken data root must not claim "installed"
        logger.warning(f"Could not resolve the initialization marker: {e}")
        return False
    return any((directory / _MARKER_NAME).is_file()
               for directory in _legacy_marker_dirs())


def mark_system_initialized():
    """Mark system as initialized"""
    try:
        marker = marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        logger.info("System initialization marker created at %s", marker)
    except Exception as e:
        logger.warning(f"Could not create initialization marker: {e}")


def load_config_from_json(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from config.json file"""
    if config_path is None:
        # Try to find config.json in project root
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent
        config_path = project_root / 'config.json'
    
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"Configuration loaded from {config_path}")
        return config
    except Exception as e:
        logger.error(f"Error loading config from {config_path}: {e}")
        return {}


def initialize_from_config(config: Dict[str, Any]):
    """Initialize system settings from config.json"""
    try:
        # Initialize settings manager
        settings_manager = get_settings()
        
        # Update processing settings
        if 'processing' in config:
            processing_config = config['processing']
            settings_manager.set_setting('processing.max_workers', processing_config.get('max_workers', 4))
            settings_manager.set_setting('processing.chunk_size', processing_config.get('chunk_size', 1048576))
            settings_manager.set_setting('processing.parallel_processing', processing_config.get('parallel_processing', True))
            settings_manager.set_setting('processing.extract_archives', processing_config.get('extract_archives', True))
            settings_manager.set_setting('processing.extract_attachments', processing_config.get('extract_attachments', True))
        
        # Update display settings
        if 'display' in config:
            display_config = config['display']
            settings_manager.set_setting('display.results_per_page', display_config.get('results_per_page', 10))
            settings_manager.set_setting('display.theme', display_config.get('theme', 'light'))
            settings_manager.set_setting('display.language', display_config.get('language', 'en'))
        
        # Update search settings
        if 'search' in config:
            search_config = config['search']
            settings_manager.set_setting('search.max_results', search_config.get('max_results', 1000))
            settings_manager.set_setting('search.enable_history', search_config.get('enable_history', True))
            settings_manager.set_setting('search.enable_saved_searches', search_config.get('enable_saved_searches', True))
        
        # Update notification settings
        if 'notifications' in config:
            notification_config = config['notifications']
            settings_manager.set_setting('notifications.enabled', notification_config.get('enabled', True))
            settings_manager.set_setting('notifications.email_notifications', notification_config.get('email_notifications', False))
            settings_manager.set_setting('notifications.browser_notifications', notification_config.get('browser_notifications', True))
        
        # Update system settings
        if 'system' in config:
            system_config = config['system']
            settings_manager.set_setting('system.language', system_config.get('language', 'en'))
            settings_manager.set_setting('system.timezone', system_config.get('timezone', 'UTC'))
            settings_manager.set_setting('system.debug_mode', system_config.get('debug_mode', False))
            if 'app_logo' in system_config:
                settings_manager.set_setting('system.app_logo', system_config.get('app_logo', ''))
            if 'app_icon' in system_config:
                settings_manager.set_setting('system.app_icon', system_config.get('app_icon', 'bi-file-earmark-text'))
        
        # Update theme settings
        if 'theme' in config:
            theme_config = config['theme']
            for key, value in theme_config.items():
                settings_manager.set_setting(f'theme.{key}', value)
        
        # Update environment variables
        if 'environment_variables' in config:
            env_vars = config['environment_variables']
            for key, value in env_vars.items():
                if value:  # Only set if value is not empty
                    os.environ[key] = str(value)
        
        logger.info("System settings initialized from config.json")
        
    except Exception as e:
        logger.error(f"Error initializing from config: {e}")


def initialize_paths(config: Dict[str, Any]):
    """Initialize directory paths from config"""
    try:
        app_config = get_config()
        settings = get_settings()
        
        if 'paths' in config:
            config['paths']  # validate presence
            
            # Set project root
            if app_config.project_root is None:
                current_file = Path(__file__).resolve()
                project_root = current_file.parent.parent
                app_config.set_project_root(project_root)
                # Also set in unified settings
                settings.set_project_root(project_root)
            elif settings.project_root is None:
                # Sync from app_config to settings
                settings.set_project_root(app_config.project_root)
            
            # Create necessary directories
            if app_config.uploads_dir:
                app_config.uploads_dir.mkdir(parents=True, exist_ok=True)
            if app_config.logs_dir:
                app_config.logs_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info("Directory paths initialized")
        
    except Exception as e:
        logger.error(f"Error initializing paths: {e}")


def initialize_database_config(config: Dict[str, Any], skip_connection_test: bool = True):
    """Apply the database configuration from config.json.

    OPS-07: this used to seed ``database.*`` one key at a time with
    ``set_setting()``. ``database.*`` has no entry in ``SETTING_DEFINITIONS``,
    so ``SettingsManager.set(..., validate=True)`` validated nothing, and
    ``settings.json`` was rewritten on *every* startup with an unvalidated and
    untested target. An operator ``config.json`` pointing at a host that no
    longer exists therefore silently destroyed the working configuration the
    administrator had saved through the validated settings endpoint.

    It now goes through ``SettingsManager.apply_database_config()`` - the same
    boundary every other database mutation uses:

    * fields config.json omits fall back to the current values (never to
      ``localhost:5432`` defaults);
    * the result is structurally validated;
    * unless this is the very first startup, the connection is proven before
      anything is written;
    * a rejected configuration is **not** persisted. The application keeps the
      last-known-good configuration it loaded from ``settings.json``.

    Args:
        config: Configuration dictionary
        skip_connection_test: If True, don't test database connection (for first startup)
    """
    try:
        if 'database' not in config:
            return

        db_config_data = config['database']
        if not isinstance(db_config_data, dict):
            logger.error(
                "Ignoring config.json database block: expected an object, got %s.",
                type(db_config_data).__name__,
            )
            return

        manager = get_settings_manager()

        # Drop nulls so an explicitly-null field falls back to the current
        # value instead of being written as the string "None".
        proposed = {k: v for k, v in db_config_data.items() if v is not None}

        applied, error = manager.apply_database_config(
            proposed, require_test=not skip_connection_test
        )

        if applied:
            logger.info("Database configuration applied from config.json")
            return

        # Rejected. Nothing was written and the environment is untouched, so the
        # process keeps using the last-known-good configuration it loaded from
        # settings.json.
        logger.error(
            "Database configuration in config.json was rejected and has NOT been "
            "applied: %s The application continues with its existing configuration.",
            error,
        )

    except Exception as e:
        logger.error(f"Error initializing database config: {e}")


def _startup_print(message: str) -> None:
    """Print a start-up line that is visible whatever the logging setup.

    The upgrade runs before logging is configured, so INFO records are
    dropped: a successful upgrade used to be invisible and a failed one
    was only visible through Python's last-resort handler.
    """
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"), flush=True)


def _schema_db_config() -> Dict[str, Any]:
    from settings.config import get_database_config

    db = get_database_config()
    return {
        "host": db.host,
        "port": int(db.port),
        "user": db.user,
        "password": db.password,
        "database": db.database,
    }


def _describe_target(cfg: Dict[str, Any]) -> str:
    """host:port/database - never the password."""
    return f"{cfg.get('host')}:{cfg.get('port')}/{cfg.get('database')}"


#: Tables whose presence means the application database has been installed
#: (the authoritative half of the initialisation check, core/installer.py).
CRITICAL_TABLES = ("users", "words", "paths", "contents")


def installed_schema_present(cfg: Dict[str, Any]) -> Optional[bool]:
    """Whether the application database exists and holds the critical tables.

    ``True``: installed; ``False``: the database is missing or has not been
    installed (setup pending); ``None``: no connection could be made, so the
    question cannot be answered. Never creates anything.
    """
    return _probe_installed_schema(cfg)[0]


def _connection_failure_reason(exc: Exception) -> str:
    """The server's reason from a libpq connection error, without the preamble.

    ``connection to server at "localhost" (::1), port 5432 failed: fe_sendauth:
    no password supplied`` -> ``fe_sendauth: no password supplied``. libpq
    repeats the message per address tried; the first line suffices.
    """
    first = (str(exc).strip().splitlines() or [""])[0]
    return first.rsplit(" failed: ", 1)[-1].strip() or exc.__class__.__name__


def _probe_installed_schema(cfg: Dict[str, Any]):
    """``(installed_schema_present, reason)``; ``reason`` is set when ``None``."""
    import psycopg2

    try:
        conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                                password=cfg["password"], dbname=cfg["database"],
                                connect_timeout=5)
    except psycopg2.OperationalError as exc:
        if "does not exist" in str(exc):
            return False, None
        return None, _connection_failure_reason(exc)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables"
                        " WHERE table_schema = 'public' AND table_name = ANY(%s)",
                        (list(CRITICAL_TABLES),))
            return cur.fetchone()[0] == len(CRITICAL_TABLES), None
    finally:
        conn.close()


def upgrade_database_schema():
    """Apply pending schema migrations to the configured database.

    Upgrade path for EXISTING installations. The setup wizard runs
    migrations only at first install, so a deployment that pulls new code
    containing a new migration would otherwise start against a stale
    schema - exactly what happened with PR #11: the analyst tables were
    missing, every categorization call failed with
    ``relation "analyst_categories" does not exist`` and the assign
    endpoint surfaced it as an opaque 400.

    Uses the single authoritative migration runner: idempotent,
    pending-only, each migration in its own transaction with rollback on
    failure. Startup continues on failure (the setup gate and health checks
    report the problem), but the outcome is printed with the target
    database and the real cause: an unreachable server and a failing
    migration are different problems and are reported as such.

    Returns the list of migration versions applied (empty when current or
    on failure).
    """
    try:
        cfg = _schema_db_config()
    except Exception as e:
        _startup_print(f"[WARNING] Schema upgrade skipped - no database configuration: {e}")
        return []
    target = _describe_target(cfg)
    try:
        from database.bootstrap import bootstrap_database

        report = bootstrap_database(cfg)
    except Exception as e:
        cause = e.__cause__
        detail = f"{e}" + (f" - {cause}" if cause is not None else "")
        if "Migration " in str(e):
            logger.error("Schema upgrade failed on %s: %s", target, detail)
            _startup_print(
                f"[ERROR] Schema upgrade FAILED on {target}: {detail}. The failing "
                "migration was rolled back and later migrations were not applied; "
                "features that need them will fail until this is fixed.")
        else:
            logger.warning("Schema upgrade skipped on %s: %s", target, detail)
            _startup_print(f"[WARNING] Schema upgrade skipped - database {target} "
                           f"unreachable or not configured yet: {detail}")
        return []
    applied = report.get("applied_migrations") or []
    if applied:
        logger.info("Applied pending schema migrations on %s: %s (now at %s)",
                    target, ", ".join(applied), report.get("current_version"))
        _startup_print(f"[OK] Applied schema migrations {', '.join(applied)} on {target} "
                       f"(schema now at version {report.get('current_version')})")
    else:
        logger.info("Database schema on %s is up to date (version %s)",
                    target, report.get("current_version"))
        _startup_print(f"[OK] Database schema is up to date (version "
                       f"{report.get('current_version')}) on {target}")
    return applied


def upgrade_installed_schema():
    """Apply pending migrations when the application database is installed.

    Used on the start-up path that has no initialisation marker. The marker
    (``.system_initialized``) is written only when ``config.json`` exists,
    so an installation configured through ``.env`` - what the installer
    writes - never gets one, and its schema used to be left stale forever
    (field report: ``column "recipient_user_id" does not exist`` after
    pulling m0020). The critical tables are the authoritative installed
    check; before the setup wizard has created them nothing is created or
    migrated here.
    """
    try:
        cfg = _schema_db_config()
    except Exception as e:
        _startup_print(f"[WARNING] Schema upgrade skipped - no database configuration: {e}")
        return []
    present, reason = _probe_installed_schema(cfg)
    if present is None:
        # "unreachable" was printed for every failure, including a server that
        # answered and refused the credentials (field report: fe_sendauth: no
        # password supplied, before the setup wizard had written .env).
        _startup_print(f"[WARNING] Schema upgrade skipped - cannot connect to "
                       f"{_describe_target(cfg)}: {reason}")
        return []
    if not present:
        logger.info("No installed schema in %s yet; setup pending, nothing migrated",
                    _describe_target(cfg))
        return []
    return upgrade_database_schema()


def initialize_system(first_startup: bool = False):
    """
    Initialize the entire system
    
    Args:
        first_startup: If True, perform first-time initialization
    """
    try:
        logger.info("Starting system initialization...")
        
        # Load configuration from config.json
        config = load_config_from_json()
        
        if not config:
            logger.warning("No configuration found, using defaults")
            return
        
        # Initialize paths first
        initialize_paths(config)
        
        # Initialize settings from config
        initialize_from_config(config)
        
        # Initialize database config (skip connection test on first startup)
        # Connection will be tested during setup wizard
        initialize_database_config(config, skip_connection_test=first_startup)
        
        # Mark as initialized if first startup
        if first_startup:
            mark_system_initialized()
            logger.info("✅ System initialization completed (first startup)")
        else:
            logger.info("✅ System initialization completed")
        
    except Exception as e:
        logger.error(f"Error during system initialization: {e}")
        raise


def ensure_system_initialized():
    """Ensure system is initialized, run initialization if needed"""
    if not is_system_initialized():
        logger.info("First startup detected, initializing system...")
        initialize_system(first_startup=True)
        # No marker does not mean no installation: the marker is only written
        # when config.json exists. Upgrade an installed database either way.
        upgrade_installed_schema()
    else:
        # Still load config on every startup to apply any changes
        logger.info("System already initialized, loading configuration...")
        initialize_system(first_startup=False)
        # Upgrade path for existing installations: the setup wizard runs
        # migrations only at first install, so apply anything pending now.
        # Deliberately outside initialize_system(): that function returns
        # early when config.json is absent, but database configuration can
        # equally come from .env (what the installer writes) - pending
        # migrations must be applied either way.
        upgrade_database_schema()

