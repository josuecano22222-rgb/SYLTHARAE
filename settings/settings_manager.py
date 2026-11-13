"""
Settings Manager - Core business logic
File: settings/settings_manager.py

Handles all settings operations: load, save, validate, backup
Enhanced with migration support and file path detection
"""

import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import threading
import logging

from .settings_models import (
    AllSettings, ValidationError, SettingDefinition,
    get_setting_definition, InterfaceConfig
)
# OPS-03 / OPS-04: imported at module scope so that both ``import_settings``
# and ``restore_backup`` can re-raise it. A local import inside
# ``import_settings`` would leave ``restore_backup``'s ``except`` clause
# raising NameError at the exact moment it needs to propagate the rejection.
from .database_validation import DatabaseConfigRejected

logger = logging.getLogger(__name__)


def detect_settings_file() -> Path:
    """
    Detect existing settings.json file location.
    Checks common locations in order of preference.
    """
    # Get project root (parent of settings directory)
    project_root = Path(__file__).parent.parent
    
    # Possible locations
    possible_locations = [
        project_root / "data" / "settings.json",  # Data folder
        Path.home() / '.file_analysis' / 'settings.json',  # Home directory
    ]
    
    # Check if any exist
    for loc in possible_locations:
        if loc.exists():
            logger.info(f"📁 Found existing settings file at: {loc}")
            return loc
    
    # Default to project root
    logger.info(f"📁 Using default settings file location: {possible_locations[0]}")
    return possible_locations[0]


def migrate_from_old_format(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate old settings format to new format.
    
    Handles:
    - Old structure without version
    - Interface format conversion (bool to dict)
    - Missing categories
    - Paths section preservation
    """
    logger.info("🔄 Migrating settings from old format to new format...")
    
    new_data = {
        "version": "3.0.0",
        "system": {},
        "display": {},
        "search": {},
        "processing": {},
        "notifications": {},
        "theme": {},
        "database": {},
        "storage": {},
        "interfaces": {},
    }
    
    # Migrate system settings
    if "system" in data:
        system_old = data["system"]
        new_data["system"] = {
            "app_name": system_old.get("app_name", "SYLTHARAE System"),
            "app_icon": system_old.get("app_icon", "bi-file-earmark-text"),
            "app_logo": system_old.get("app_logo", ""),
            "language": system_old.get("language", "en"),
            "timezone": system_old.get("timezone", "UTC"),
            "date_format": system_old.get("date_format", "YYYY-MM-DD"),
            "time_format": system_old.get("time_format", "24h"),
            "theme": system_old.get("theme", "light"),
            "animations_enabled": system_old.get("animations_enabled", True),
            "notifications_enabled": system_old.get("notifications_enabled", True),
            "show_breadcrumbs": system_old.get("show_breadcrumbs", True),
            "compact_mode": system_old.get("compact_mode", False),
            "auto_save": system_old.get("auto_save", True),
            "confirm_actions": system_old.get("confirm_actions", True),
            "items_per_page": system_old.get("items_per_page", 50),
            "default_sort": system_old.get("default_sort", "date_creation"),
            "sort_direction": system_old.get("sort_direction", "desc"),
            "debug_mode": system_old.get("debug_mode", False),
            "log_level": system_old.get("log_level", "INFO"),
            "log_file": system_old.get("log_file"),
            "action_logging_enabled": system_old.get("action_logging_enabled", True),
        }
    
    # Migrate display settings
    if "display" in data:
        display_old = data["display"]
        new_data["display"] = {
            "compact_view": display_old.get("compact_view", False),
            "show_file_preview": display_old.get("show_file_preview", True),
            "show_metadata": display_old.get("show_metadata", True),
            "results_per_page": display_old.get("results_per_page", 50),
            "default_sort": display_old.get("default_sort", "date_creation"),
            "sort_direction": display_old.get("sort_direction", "desc"),
            "theme": display_old.get("theme", "light"),
            "language": display_old.get("language", "en"),
        }
    
    # Migrate search settings
    if "search" in data:
        search_old = data["search"]
        new_data["search"] = {
            "default_search_type": search_old.get("default_search_type", "basic"),
            "case_sensitive": search_old.get("case_sensitive", False),
            "highlight_results": search_old.get("highlight_results", True),
            "search_in_content": search_old.get("search_in_content", True),
            "search_in_filename": search_old.get("search_in_filename", True),
            "search_in_metadata": search_old.get("search_in_metadata", False),
            "max_results": search_old.get("max_results", 1000),
            "enable_history": search_old.get("enable_history", True),
            "enable_saved_searches": search_old.get("enable_saved_searches", True),
        }
    
    # Migrate processing settings
    if "processing" in data:
        processing_old = data["processing"]
        new_data["processing"] = {
            "use_threading": processing_old.get("use_threading", True),
            "max_workers": processing_old.get("max_workers", 4),
            "enable_monitoring": processing_old.get("enable_monitoring", True),
            "monitor_interval": processing_old.get("monitor_interval", 5.0),
            "use_priority": processing_old.get("use_priority", False),
            "chunk_size": processing_old.get("chunk_size", 1048576),
            "parallel_processing": processing_old.get("parallel_processing", True),
            "auto_process_uploads": processing_old.get("auto_process_uploads", True),
            "extract_archives": processing_old.get("extract_archives", True),
            "extract_attachments": processing_old.get("extract_attachments", True),
            "process_nested": processing_old.get("process_nested", True),
            "calculate_hashes": processing_old.get("calculate_hashes", True),
            "extract_text": processing_old.get("extract_text", True),
            "extract_metadata": processing_old.get("extract_metadata", True),
            # max_file_size_mb removed - no file size limitations
            "max_depth": processing_old.get("max_depth", 10),
            "file_processing_timeout": processing_old.get("file_processing_timeout", 1200),
        }
    
    # Migrate notifications
    if "notifications" in data:
        notifications_old = data["notifications"]
        new_data["notifications"] = {
            "enabled": notifications_old.get("enabled", True),
            "email_notifications": notifications_old.get("email_notifications", False),
            "browser_notifications": notifications_old.get("browser_notifications", True),
            "processing_complete": notifications_old.get("processing_complete", True),
            "batch_complete": notifications_old.get("batch_complete", True),
            "errors_only": notifications_old.get("errors_only", False),
            "similar_files_enabled": notifications_old.get("similar_files_enabled", True),
            "future_dates_enabled": notifications_old.get("future_dates_enabled", True),
            "future_events_enabled": notifications_old.get("future_events_enabled", True),
            "auto_analyze_files": notifications_old.get("auto_analyze_files", True),
            "upcoming_events_days": notifications_old.get("upcoming_events_days", 30),
        }
    
    # Migrate database settings
    if "database" in data:
        database_old = data["database"]
        new_data["database"] = {
            "host": database_old.get("host", "localhost"),
            "port": database_old.get("port", 5432),
            "database": database_old.get("database", "analysis"),
            "user": database_old.get("user", "postgres"),
            "password": database_old.get("password", ""),
            "pool_min_conn": database_old.get("pool_min_conn", 5),
            "pool_max_conn": database_old.get("pool_max_conn", 30),
            "pool_timeout": database_old.get("pool_timeout", 60),
            "query_timeout": database_old.get("query_timeout", 60),
            "batch_size": database_old.get("batch_size", 10000),
            "chunk_size": database_old.get("chunk_size", 10000),
        }
    
    # Migrate storage settings
    if "storage" in data:
        storage_old = data["storage"]
        new_data["storage"] = {
            "enable_storage": storage_old.get("enable_storage", True),
            "default_source": storage_old.get("default_source", "default"),
            "default_side": storage_old.get("default_side", "default"),
            "db_name": storage_old.get("db_name", "analysis"),
        }
    
    # Migrate theme (preserve existing theme if present)
    if "theme" in data:
        new_data["theme"] = data["theme"]
    else:
        # Initialize with defaults
        new_data["theme"] = {}
    
    # Migrate interfaces (convert bool to dict format)
    interfaces_new = {}
    
    # Migrate show_page_tips from system to interfaces.page_tips
    if "system" in data and "show_page_tips" in data.get("system", {}):
        page_tips_enabled = data["system"].get("show_page_tips", True)
        interfaces_new["page_tips"] = {
            "enabled": page_tips_enabled,
            "category": "user"
        }
        logger.info(f"🔄 Migrated show_page_tips from system to interfaces.page_tips (enabled={page_tips_enabled})")
    
    # Migrate existing interfaces
    if "interfaces" in data:
        interfaces_old = data["interfaces"]
        raw_interfaces = interfaces_old.get("interfaces", interfaces_old) if isinstance(interfaces_old, dict) else interfaces_old
        
        for interface_id, value in raw_interfaces.items():
            # Skip if already migrated from system.show_page_tips
            if interface_id == "page_tips" and "page_tips" in interfaces_new:
                continue
                
            if isinstance(value, bool):
                # Convert bool to dict with category
                interfaces_new[interface_id] = {
                    "enabled": value,
                    "category": "user"  # Default category
                }
            elif isinstance(value, dict):
                # Already in dict format, preserve it
                interfaces_new[interface_id] = {
                    "enabled": value.get("enabled", True),
                    "category": value.get("category", "user")
                }
            else:
                # Fallback
                interfaces_new[interface_id] = {
                    "enabled": True,
                    "category": "user"
                }
    
    new_data["interfaces"] = {"interfaces": interfaces_new}
    
    # Preserve paths section if it exists (for backward compatibility)
    if "paths" in data:
        new_data["paths"] = data["paths"]
    
    logger.info("✅ Migration completed successfully")
    return new_data


class SettingsManager:
    """
    Thread-safe settings manager with automatic backups and validation.
    
    This is the ONLY class that reads/writes settings.json.
    All other code must go through this manager.
    """
    
    def __init__(self, settings_file: Path):
        self.settings_file = settings_file
        self.backups_dir = settings_file.parent / "settings_backups"
        self.lock = threading.RLock()
        self._settings: Optional[AllSettings] = None
        self._file_mtime: Optional[float] = None

        # OPS-07: counter (not a boolean) so nested/overlapping commits cannot
        # clear the flag belonging to an outer ``apply_database_config()``.
        self._database_mutation_depth = 0
        
        # Ensure directories exist
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        
        # Load on initialization
        self.load()
    
    @property
    def _database_mutation_allowed(self) -> bool:
        """True only while :meth:`apply_database_config` is committing."""
        return self._database_mutation_depth > 0
    
    @property
    def settings(self) -> AllSettings:
        """Get current settings (cached)"""
        with self.lock:
            if self._settings is None:
                self.load()
            return self._settings
    
    def load(self) -> AllSettings:
        """Load settings from JSON file with migration support"""
        with self.lock:
            try:
                if self.settings_file.exists():
                    with open(self.settings_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # Check if migration is needed
                    version = data.get("version", "1.0")
                    if version != "3.0.0":
                        logger.info(f"🔄 Migrating settings from version {version} to 3.0.0")
                        # Create backup before migration
                        self._create_backup()
                        # Migrate
                        data = migrate_from_old_format(data)
                        # Save migrated version
                        with open(self.settings_file, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        logger.info("✅ Settings migrated and saved")
                    
                    self._settings = AllSettings.from_dict(data)
                    
                    # ARCH-02: environment variables take precedence over the
                    # persisted configuration file (defaults < file < env <
                    # secret provider). Re-apply DB_* env overrides after the
                    # file load so a stale settings.json can never override a
                    # deployment's environment.
                    self._settings.database.apply_env_overrides()
                    
                    # Restore missing interfaces after loading
                    self._restore_missing_interfaces()
                    
                    self._file_mtime = self.settings_file.stat().st_mtime
                    logger.info(f"✅ Settings loaded from {self.settings_file}")
                else:
                    # Create default settings
                    self._settings = AllSettings()
                    # Initialize default interfaces
                    self._restore_missing_interfaces()
                    self.save()
                    logger.info(f"📝 Created default settings at {self.settings_file}")
                
                return self._settings
            
            except Exception as e:
                logger.error(f"❌ Error loading settings: {e}", exc_info=True)
                # Return defaults on error
                self._settings = AllSettings()
                self._restore_missing_interfaces()
                return self._settings
    
    def save(self, create_backup: bool = True) -> bool:
        """
        Save settings to JSON file.
        
        Args:
            create_backup: Whether to create a backup before saving
            
        Returns:
            True if successful
        """
        with self.lock:
            try:
                # Create backup first
                if create_backup and self.settings_file.exists():
                    self._create_backup()
                
                # Write to temp file first (atomic write)
                temp_file = self.settings_file.with_suffix('.tmp')
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(
                        self._settings.to_dict(),
                        f,
                        indent=2,
                        ensure_ascii=False
                    )
                
                # Atomic replace
                temp_file.replace(self.settings_file)
                
                # Update mtime
                self._file_mtime = self.settings_file.stat().st_mtime
                
                logger.info(f"💾 Settings saved to {self.settings_file}")
                return True
            
            except Exception as e:
                logger.error(f"❌ Error saving settings: {e}")
                return False
    
    def _create_backup(self):
        """Create a timestamped backup"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = self.backups_dir / f"settings_{timestamp}.json"
        
        try:
            shutil.copy2(self.settings_file, backup_file)
            logger.info(f"📦 Backup created: {backup_file.name}")
            
            # Clean old backups (keep last 10)
            self._cleanup_old_backups(keep=10)
        
        except Exception as e:
            logger.warning(f"⚠️ Could not create backup: {e}")
    
    def _cleanup_old_backups(self, keep: int = 10):
        """Remove old backup files, keeping only the most recent"""
        try:
            backups = sorted(
                self.backups_dir.glob("settings_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            
            for old_backup in backups[keep:]:
                old_backup.unlink()
                logger.debug(f"🗑️ Removed old backup: {old_backup.name}")
        
        except Exception as e:
            logger.warning(f"⚠️ Error cleaning backups: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value by dot-notation key.
        
        Examples:
            get("system.language") -> "en"
            get("theme.primary_color") -> "#4f46e5"
            get("interfaces.dashboard.enabled") -> True
        """
        with self.lock:
            parts = key.split('.')
            if len(parts) < 2:
                return default
            
            category = parts[0]
            setting_key = '.'.join(parts[1:])
            
            # Get category object
            category_obj = getattr(self._settings, category, None)
            if category_obj is None:
                return default
            
            # Special handling for interfaces
            if category == "interfaces" and len(parts) >= 3:
                interface_id = parts[1]
                attr = parts[2]
                if interface_id in category_obj.interfaces:
                    interface_config = category_obj.interfaces[interface_id]
                    if attr == "enabled":
                        return interface_config.enabled
                    elif attr == "category":
                        return interface_config.category
                return default
            
            # Handle nested keys
            value = category_obj
            for part in parts[1:]:
                if hasattr(value, part):
                    value = getattr(value, part)
                elif isinstance(value, dict):
                    value = value.get(part, default)
                else:
                    return default
            
            return value
    
    # Fields :meth:`apply_database_config` commits, in order.
    _DATABASE_FIELDS = (
        "host", "port", "database", "user", "password",
        "pool_min_conn", "pool_max_conn", "pool_timeout",
        "query_timeout", "batch_size", "chunk_size",
    )

    def apply_database_config(
        self,
        proposed: Optional[Dict[str, Any]],
        *,
        require_test: bool = True,
        persist: bool = True,
        activate: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """Validate, prove and commit a database configuration change.

        OPS-06 / OPS-07: this is the **only** supported way to change
        ``database.*``. The invariant "an invalid database configuration can
        never become persistent or active" used to be enforced by a hand-written
        guard in each route, which is why ``core/initialization.py`` - which
        seeds ``database.*`` from ``config.json`` on *every* startup - was able
        to overwrite a validated, working configuration with an unvalidated and
        untested one.

        Moving the boundary here means a future caller that reaches for
        ``manager.set("database.host", ...)`` is refused by the manager itself
        rather than depending on that caller remembering to validate.

        Args:
            proposed: Proposed database block. Fields it omits fall back to the
                **current** values, never to dataclass defaults, so a partial
                payload cannot silently repoint the database at
                ``localhost:5432``.
            require_test: Run the real connectivity probe. Structural
                validation always runs.
            persist: Write to ``settings.json``.
            activate: Drop cached connections built against the old target.
                Callers that need to report activation failure in their own
                response (the settings route) pass ``False`` and do it
                themselves, so the side effect is not performed twice.

        Returns:
            ``(True, None)`` on success, ``(False, reason)`` on rejection.
            Nothing is mutated when this returns ``False``.
        """
        from .database_validation import (
            apply_candidate_to_environment,
            guard_database_config_change,
        )

        with self.lock:
            current = self._settings.database
            try:
                candidate = guard_database_config_change(
                    proposed, current, require_test=require_test
                )
            except DatabaseConfigRejected as exc:
                return False, str(exc)

            # Commit through the normal path, with the boundary explicitly
            # opened for the duration. ``self.lock`` is an RLock, so re-entry
            # is safe; the counter is only ever touched while holding it.
            # Snapshot so a failed commit can be rolled back completely - a
            # half-applied database target is precisely the state this gate
            # exists to prevent.
            snapshot = {
                field: getattr(current, field)
                for field in self._DATABASE_FIELDS
                if hasattr(current, field)
            }
            env_snapshot = {
                key: os.environ.get(key)
                for key in ("DB_HOST", "DB_PORT", "DB_USER", "DB_NAME", "DB_PASSWORD")
            }
            failure: Optional[str] = None

            self._database_mutation_depth += 1
            try:
                for field in self._DATABASE_FIELDS:
                    if field not in candidate:
                        continue
                    success, error = self.set(
                        f"database.{field}", candidate[field], validate=False
                    )
                    if not success:
                        failure = f"database.{field}: {error}"
                        break

                if failure is None:
                    # Disk, memory and environment must agree on one config.
                    apply_candidate_to_environment(candidate)

                    if persist and not self.save():
                        failure = "The configuration was accepted but could not be saved."
            finally:
                if failure is not None:
                    # Roll back the in-memory configuration AND the environment,
                    # so the running process keeps using the last-known-good
                    # database rather than a target that was never persisted.
                    for field, value in snapshot.items():
                        self.set(f"database.{field}", value, validate=False)
                    for key, value in env_snapshot.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
                self._database_mutation_depth -= 1

            if failure is not None:
                return False, failure

            # Activate: drop cached connections built against the old target.
            if activate:
                self._activate_database_config()

            return True, None

    def _activate_database_config(self) -> None:
        """Drop cached connections built against the previous target."""
        try:
            from .config import invalidate_database_connections

            invalidate_database_connections()
        except Exception:
            # Failure here cannot diverge state - activation only drops
            # caches - so it is a warning, not a rollback.
            logger.warning(
                "Accepted database configuration but could not invalidate "
                "cached connections.",
                exc_info=True,
            )

    def set(self, key: str, value: Any, validate: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Set a setting value by dot-notation key.
        
        Args:
            key: Dot-notation key (e.g., "system.language")
            value: New value
            validate: Whether to validate the value
            
        Returns:
            Tuple of (success, error_message)
        """
        with self.lock:
            parts = key.split('.')
            if len(parts) < 2:
                return False, "Invalid key format"
            
            category = parts[0]
            setting_key = parts[-1]
            
            # OPS-06 / OPS-07: the mutation boundary lives in the manager, not
            # in each caller. ``database.*`` has no entry in
            # SETTING_DEFINITIONS, so ``validate=True`` below is a no-op for it
            # and a bare ``manager.set(...) + manager.save()`` would persist an
            # unvalidated, untested database target - the original OPS-02
            # outage. Refuse it here and point the caller at the one supported
            # mutator; apply_database_config() opens the boundary deliberately.
            if category == "database" and not self._database_mutation_allowed:
                return False, (
                    "Database configuration cannot be changed with set(). Use "
                    "SettingsManager.apply_database_config(), which validates the "
                    "whole configuration and tests the connection before saving."
                )
            
            # Validate if requested
            if validate:
                definition = get_setting_definition(key)
                if definition:
                    try:
                        value = definition.validate(value)
                    except ValidationError as e:
                        return False, str(e)
            
            # Special handling for interfaces
            if category == "interfaces" and len(parts) >= 3:
                interface_id = parts[1]
                attr = parts[2]
                
                # The registry decides what exists. Writing a state for an id
                # nothing declares would put a switch in the settings file that
                # cannot do anything (and that an older version of this code
                # would have read as "on"). Legacy ids are still writable so a
                # migration can carry their value across.
                from core.interfaces import LEGACY_INTERFACE_IDS, is_registered
                if not is_registered(interface_id) and interface_id not in LEGACY_INTERFACE_IDS:
                    return False, f"There is no interface called '{interface_id}'."

                if interface_id not in self._settings.interfaces.interfaces:
                    # Create new interface config
                    self._settings.interfaces.interfaces[interface_id] = InterfaceConfig()
                
                interface_config = self._settings.interfaces.interfaces[interface_id]
                
                if attr == "enabled":
                    interface_config.enabled = bool(value)
                elif attr == "category":
                    interface_config.category = str(value)
                else:
                    return False, f"Unknown interface attribute: {attr}"
                
                logger.debug(f"✏️ Interface setting updated: {key} = {value}")
                return True, None
            
            # Get category object
            category_obj = getattr(self._settings, category, None)
            if category_obj is None:
                return False, f"Unknown category: {category}"
            
            # Handle nested keys
            if len(parts) == 2:
                # Direct attribute
                if hasattr(category_obj, setting_key):
                    setattr(category_obj, setting_key, value)
                elif isinstance(category_obj, dict):
                    category_obj[setting_key] = value
                else:
                    return False, f"Unknown setting: {key}"
            else:
                # Nested attribute
                current = category_obj
                for part in parts[1:-1]:
                    if hasattr(current, part):
                        current = getattr(current, part)
                    elif isinstance(current, dict):
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    else:
                        return False, f"Cannot navigate to: {key}"
                
                if isinstance(current, dict):
                    current[setting_key] = value
                else:
                    setattr(current, setting_key, value)
            
            logger.debug(f"✏️ Setting updated: {key} = {value}")
            return True, None
    
    def update_many(self, updates: Dict[str, Any], validate: bool = True) -> Tuple[bool, List[str]]:
        """
        Update multiple settings at once.
        
        Args:
            updates: Dictionary of key -> value updates
            validate: Whether to validate values
            
        Returns:
            Tuple of (all_successful, list_of_errors)
        """
        with self.lock:
            errors = []
            
            for key, value in updates.items():
                success, error = self.set(key, value, validate=validate)
                if not success:
                    errors.append(f"{key}: {error}")
            
            return len(errors) == 0, errors
    
    def reload(self) -> bool:
        """Reload settings from file"""
        with self.lock:
            try:
                self.load()
                return True
            except Exception as e:
                logger.error(f"❌ Error reloading: {e}")
                return False
    
    def has_changed(self) -> bool:
        """Check if settings file has been modified externally"""
        if not self.settings_file.exists():
            return False
        
        current_mtime = self.settings_file.stat().st_mtime
        return current_mtime != self._file_mtime
    
    def export(self) -> Dict[str, Any]:
        """Export all settings as dictionary"""
        with self.lock:
            return self._settings.to_dict()
    
    def import_settings(self, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Import settings from dictionary.
        
        Args:
            data: Settings dictionary
            
        Returns:
            Tuple of (success, error_message)
        """
        with self.lock:
            try:
                # Check if migration is needed
                version = data.get("version", "1.0")
                if version != "3.0.0":
                    logger.info(f"🔄 Migrating imported settings from version {version}")
                    data = migrate_from_old_format(data)

                # OPS-03 / OPS-04: gate the database configuration BEFORE
                # anything is replaced or persisted.
                #
                # Two distinct hazards existed here:
                #   1. ``AllSettings.from_dict`` builds the database block with
                #      ``DatabaseConfig.from_dict(data.get("database", {}))``,
                #      so a payload that OMITS "database" silently reset the
                #      target to the dataclass defaults (localhost:5432). No
                #      hostile input required - importing a theme export was
                #      enough to brick the application on the next restart.
                #   2. A payload supplying an unreachable host was persisted
                #      verbatim. Nothing tested it, and nothing synced the
                #      environment, so disk and environment silently diverged.
                #
                # The gate falls back to the CURRENT value for every field the
                # payload omits (never to defaults), validates the result and
                # proves it connects. It raises before any mutation.
                from .database_validation import (
                    DatabaseConfigRejected,
                    apply_candidate_to_environment,
                    guard_database_config_change,
                )

                current_db = self._settings.database
                candidate = guard_database_config_change(data.get("database"), current_db)

                # Validate by loading into model
                new_settings = AllSettings.from_dict(data)

                # ``DatabaseConfig.__post_init__`` applies DB_* environment
                # overrides, so the freshly built object may not carry the
                # imported values. Re-assert the accepted candidate so that
                # disk, memory and environment all agree on one configuration.
                for field, value in candidate.items():
                    if hasattr(new_settings.database, field):
                        setattr(new_settings.database, field, value)

                # Create backup before importing
                if self.settings_file.exists():
                    self._create_backup()

                # Replace current settings
                self._settings = new_settings

                # Restore missing interfaces
                self._restore_missing_interfaces()

                # Keep the environment in step with what we just accepted, so
                # the running process and the persisted file cannot disagree.
                apply_candidate_to_environment(candidate)

                # Save
                self.save(create_backup=False)

                # Activate: drop cached connections built against the old target.
                try:
                    from .config import invalidate_database_connections

                    invalidate_database_connections()
                except Exception:
                    logger.debug("Database connection invalidation skipped", exc_info=True)

                return True, None

            except DatabaseConfigRejected:
                # Deliberately not swallowed: the caller must surface this as
                # 422 rather than a generic 400, and no state has changed.
                raise
            except Exception as e:
                logger.error(f"❌ Import error: {e}")
                return False, str(e)
    
    def reset_to_defaults(self) -> bool:
        """Reset all settings to default values.

        OPS-05: the database block is deliberately EXCLUDED from the reset.

        ``AllSettings()`` builds ``DatabaseConfig()``, whose defaults are
        ``localhost:5432``. Resetting to that and persisting it would point the
        application at a database that in most deployments does not exist, and
        because the reset persists, the application would fail to start on the
        next restart - the same outage class as OPS-02/03/04.

        A "reset to defaults" is about presentation and behaviour. Repointing
        the database is a destructive side effect no administrator would expect
        from it, so the working database configuration is carried across.
        """
        with self.lock:
            try:
                # Create backup
                if self.settings_file.exists():
                    self._create_backup()

                previous_db = self._settings.database

                # Reset to defaults
                self._settings = AllSettings()

                # Restore the working database configuration field by field
                # (a shared reference would let later mutation leak both ways).
                from dataclasses import fields as _dc_fields
                for _f in _dc_fields(self._settings.database):
                    if hasattr(previous_db, _f.name):
                        setattr(self._settings.database, _f.name,
                                getattr(previous_db, _f.name))

                # Keep the environment in step with what we are persisting.
                from .database_validation import apply_candidate_to_environment
                apply_candidate_to_environment({
                    'host': self._settings.database.host,
                    'port': self._settings.database.port,
                    'database': self._settings.database.database,
                    'user': self._settings.database.user,
                    'password': self._settings.database.password,
                })

                # OPS-05b: a reset must leave a usable interface set.
                #
                # ``AllSettings()`` starts with an EMPTY interface registry.
                # Api/routes/common.py gates every page on
                # ``is_interface_enabled_by_endpoint()`` and maps ``index`` ->
                # ``dashboard``; with no interfaces defined, the dashboard
                # reads as disabled and the home page redirect-loops on
                # itself. ``load()`` already guards against this with
                # ``_restore_missing_interfaces()``; the reset path did not.
                self._restore_missing_interfaces()

                # Save
                self.save(create_backup=False)

                logger.info(
                    "🔄 Settings reset to defaults (database configuration preserved: %s:%s)",
                    self._settings.database.host, self._settings.database.port,
                )
                return True
            
            except Exception as e:
                logger.error(f"❌ Reset error: {e}")
                return False
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups"""
        backups = []
        
        for backup_file in sorted(
            self.backups_dir.glob("settings_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        ):
            stat = backup_file.stat()
            backups.append({
                "filename": backup_file.name,
                "path": str(backup_file),
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        
        return backups
    
    def restore_backup(self, backup_filename: str) -> Tuple[bool, Optional[str]]:
        """
        Restore settings from a backup file.
        
        Args:
            backup_filename: Name of backup file
            
        Returns:
            Tuple of (success, error_message)
        """
        with self.lock:
            backup_file = self.backups_dir / backup_filename
            
            if not backup_file.exists():
                return False, "Backup file not found"
            
            try:
                # Load backup data
                with open(backup_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Create backup of current state
                if self.settings_file.exists():
                    self._create_backup()
                
                # Import backup. OPS-04: a backup may carry a database block
                # pointing at an unreachable server; the same validate -> test
                # gate applies. The rejection must reach the caller as 422, so
                # it is re-raised rather than flattened into (False, str(e)).
                success, error = self.import_settings(data)
                if success:
                    # Restore missing interfaces after restore
                    self._restore_missing_interfaces()
                    self.save()
                return success, error

            except DatabaseConfigRejected:
                raise
            except Exception as e:
                logger.error(f"❌ Restore error: {e}")
                return False, str(e)
    
    def _restore_missing_interfaces(self):
        """Give every interface the product declares a stored state.

        The default for an interface is defined once, in the registry
        (``core.interfaces.defaults()``). This method used to carry its own
        hand-written dictionary of defaults, which meant the product model lived
        in two places and drifted: the registry listed interfaces the defaults
        dictionary had never heard of, and a reset enabled everything that
        happened to be in the settings file instead of what the registry says.

        Legacy ids are folded in first, so a settings file written by an earlier
        version keeps its choices: the value stored under an old key
        (``file_analysis``, ``upload_files``, ``file_browser`` ...) reaches the
        interface that replaced it. The old key itself is left in the file - it
        is the operator's file, and deleting it would lose information a
        rollback would need.
        """
        from core.interfaces import LEGACY_INTERFACE_IDS, defaults

        current = self._settings.interfaces.interfaces

        # 1. Carry legacy choices onto the interface that replaced them.
        migrated = []
        for legacy_id, canonical_id in LEGACY_INTERFACE_IDS.items():
            legacy = current.get(legacy_id)
            if legacy is None:
                continue
            canonical = current.get(canonical_id)
            legacy_enabled = bool(getattr(legacy, "enabled", False))
            if canonical is None:
                current[canonical_id] = InterfaceConfig(
                    enabled=legacy_enabled,
                    category=getattr(legacy, "category", "user"),
                )
                migrated.append(f"{legacy_id}->{canonical_id}")
            elif legacy_enabled and not canonical.enabled:
                # A merged twin only ever turns something on: an explicit "off"
                # on one of two switches for the same capability would otherwise
                # hide it even though the other said yes.
                canonical.enabled = True
                migrated.append(f"{legacy_id}+->{canonical_id}")

        # 2. Fill in what the registry declares and the file has never seen.
        default_interfaces = {
            interface_id: {"enabled": enabled, "category": "user"}
            for interface_id, enabled in defaults().items()
        }
        restored = self._settings.interfaces.restore_missing_interfaces(default_interfaces)

        if migrated:
            logger.info("Folded legacy interface settings: %s", ", ".join(migrated))
        if restored:
            logger.info(f"✅ Restored {len(restored)} missing interfaces: {', '.join(restored)}")
        if restored or migrated:
            self.save(create_backup=False)


# Global singleton instance
_settings_manager: Optional[SettingsManager] = None
_manager_lock = threading.Lock()


def get_settings_manager(settings_file: Optional[Path] = None) -> SettingsManager:
    """Get or create global settings manager singleton with automatic file detection"""
    global _settings_manager
    
    if _settings_manager is None:
        with _manager_lock:
            if _settings_manager is None:
                if settings_file is None:
                    settings_file = detect_settings_file()
                _settings_manager = SettingsManager(settings_file)
    
    return _settings_manager

