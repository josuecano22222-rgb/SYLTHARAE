"""
Settings Adapter - Backward Compatibility Layer
File: settings/settings_adapter.py

Provides backward compatibility with existing code that uses:
- settings.get_interface_manager()
- settings.get_settings()
- Old interface patterns
"""

import logging
from typing import Dict, Any, Optional
from pathlib import Path

from core.interfaces import (
    RETIRED_INTERFACE_IDS,
    InterfaceKind,
    get_all_interfaces as registry_interfaces,
    get_interface,
    get_interface_for_endpoint,
    is_registered,
)

from .interface_state import InterfaceState
from .settings_manager import get_settings_manager
from .settings_models import AllSettings, InterfaceConfig

logger = logging.getLogger(__name__)


#: Ids that no longer name anything. Sourced from the registry so there is one
#: list: ``file_upload`` was the "core" twin of the ingestion page and gated no
#: route. Kept under its original name because callers outside this module use
#: it; the registry is where it is defined.
RETIRED_INTERFACES = frozenset(RETIRED_INTERFACE_IDS)


class SettingsAdapter:
    """
    Adapter class that provides backward compatibility with old settings API.
    
    This allows existing code to work with the new settings system without
    requiring immediate changes.
    """
    
    def __init__(self):
        self._manager = get_settings_manager()
    
    @property
    def settings(self) -> AllSettings:
        """Get settings object (for backward compatibility)"""
        return self._manager.settings

    @property
    def version(self) -> str:
        """Application settings version.

        AUDIT (OPS-01): ``Api/routes/health.py`` probes ``settings.version`` on
        this adapter during its settings health check. The adapter only
        forwarded ``.settings``, so the probe raised AttributeError on every
        boot and /health permanently returned
        ``{"status":"degraded","checks":{"settings":"error: AttributeError"}}``
        - precisely the signal a container orchestrator uses to restart or
        remove an instance from a load-balancer pool.
        """
        try:
            return self.settings.version
        except AttributeError:
            try:
                from core.app_version import get_version as _get_version
            except Exception:  # pragma: no cover - defensive
                return "unknown"
            return _get_version()

    def get(self, category: str = None, key: str = None, default: Any = None) -> Any:
        """
        Get a setting value (old API style).
        
        Supports both old API (single key) and new API (category.key):
        - get('theme') -> tries to find 'theme' in any category
        - get('theme', 'custom_css') -> gets theme.custom_css
        - get('system', 'language') -> gets system.language
        
        Args:
            category: Settings category (e.g., 'system', 'display') or single key
            key: Setting key (optional if category is a single key)
            default: Default value if not found
            
        Returns:
            Setting value
        """
        # Backward compatibility: if only one argument, treat as single key
        if key is None:
            # Single key lookup - try to find in common categories
            single_key = category
            if single_key is None:
                return default
            
            # Try direct access to settings attributes
            if hasattr(self.settings, single_key):
                attr = getattr(self.settings, single_key)
                if hasattr(attr, 'to_dict'):
                    return attr.to_dict()
                return attr
            
            # Try common category.key patterns
            common_categories = ['system', 'display', 'search', 'processing', 
                               'notifications', 'theme', 'database', 'storage']
            for cat in common_categories:
                if hasattr(self.settings, cat):
                    cat_obj = getattr(self.settings, cat)
                    if hasattr(cat_obj, single_key):
                        return getattr(cat_obj, single_key)
            
            return default
        
        # Two-argument call: category.key
        # Special handling for interfaces
        if category == "interfaces":
            # For interfaces, check if it's a nested key like interfaces.page_tips.enabled
            if key in self.settings.interfaces.interfaces:
                interface_config = self.settings.interfaces.interfaces[key]
                return interface_config.to_dict()
            # Try to get via manager for nested keys
            full_key = f"{category}.{key}"
            return self._manager.get(full_key, default)
        
        full_key = f"{category}.{key}"
        return self._manager.get(full_key, default)
    
    def set(self, category: str, key: str, value: Any, save_to_file: bool = True) -> bool:
        """
        Set a setting value (old API style).
        
        Args:
            category: Settings category
            key: Setting key
            value: New value
            save_to_file: Whether to save immediately
            
        Returns:
            True if successful
        """
        full_key = f"{category}.{key}"
        success, error = self._manager.set(full_key, value, validate=True)
        
        if success and save_to_file:
            self._manager.save()
        
        return success
    
    def get_all(self) -> Dict[str, Any]:
        """Get all settings as dictionary (old API style)"""
        return self._manager.export()
    
    def get_system_config(self) -> Dict[str, Any]:
        """Get system configuration (old API style)"""
        return self.settings.system.to_dict()
    
    def get_display_config(self) -> Dict[str, Any]:
        """Get display configuration (old API style)"""
        return self.settings.display.to_dict()
    
    def get_search_config(self) -> Dict[str, Any]:
        """Get search configuration (old API style)"""
        return self.settings.search.to_dict()
    
    def get_processing_config(self) -> Dict[str, Any]:
        """Get processing configuration (old API style)"""
        return self.settings.processing.to_dict()
    
    def get_storage_config(self) -> Dict[str, Any]:
        """Get storage configuration (old API style)"""
        return self.settings.storage.to_dict()
    
    # -- registry-backed interface API -----------------------------------
    def get_state(self) -> InterfaceState:
        """The registry applied to the stored settings.

        Everything that asks a question about interfaces goes through this, so
        there is one place where "enabled", "visible" and "may be switched off"
        are decided - and it is not a dictionary copied beside the settings.
        """
        return InterfaceState(self.settings.interfaces)

    def get_interface(self, interface_id: str):
        """The registry entry for an id, or ``None``."""
        return get_interface(interface_id)

    def get_all_interfaces(self) -> Dict[str, Dict[str, Any]]:
        """Every interface the product declares, with its current state.

        Legacy shape (``{id: {...}}``) preserved for callers that still expect
        a dictionary; the contents now come from the registry, so an entry can
        no longer describe a page that does not exist - the previous version
        answered from a hand-written metadata table and a stored settings dict
        that could disagree with each other and with the application.
        """
        state = self.get_state()
        interfaces: Dict[str, Dict[str, Any]] = {}
        for row in state.interfaces_with_state():
            entry = dict(row)
            # ``endpoint`` is what the settings template has always read.
            entry["endpoint"] = row.get("route") or ""
            entry["category"] = row.get("domain")
            interfaces[row["interface_id"]] = entry
        return interfaces

    def get_interfaces_by_domain(self, user=None) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Interfaces grouped by the domain that owns them.

        Domains replace the old free-text ``category`` values ('user', 'core',
        'analysis') that were stored per interface and could drift from what
        the interface actually was. A section or a feature has no page, so it is
        listed under its domain too - an operator can switch it, which is the
        point of the screen.
        """
        state = self.get_state()
        grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in state.interfaces_with_state(user):
            if row["kind"] == str(InterfaceKind.INTERNAL):
                # Diagnostics surfaces are not offered as switches.
                continue
            domain = row["domain"]
            entry = dict(row)
            entry["endpoint"] = row.get("route") or ""
            entry["category"] = domain
            grouped.setdefault(domain, {})[row["interface_id"]] = entry
        return grouped

    def get_interfaces_by_category(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Legacy name for :meth:`get_interfaces_by_domain`.

        The old grouping came from a stored ``category`` string; it is now the
        interface's domain, which the registry owns.
        """
        return self.get_interfaces_by_domain()

    def get_dependents(self, interface_id: str):
        from core.interfaces import get_dependents

        return get_dependents(interface_id)

    def missing_dependencies(self, interface_id: str):
        return self.get_state().missing_dependencies(interface_id)

    def can_disable(self, interface_id: str):
        return self.get_state().can_disable(interface_id)

    def can_enable(self, interface_id: str):
        return self.get_state().can_enable(interface_id)

    def state_report(self):
        return self.get_state().state_report()

    def set_interface_enabled(self, interface_id: str, enabled: bool):
        """Switch an interface on or off, refusing configurations that cannot work.

        Returns ``(ok, message)``. The dependency rules live in
        ``InterfaceState``; this method's job is to persist an accepted change.
        """
        state = self.get_state()
        allowed, message = state.can_enable(interface_id) if enabled else state.can_disable(interface_id)
        if not allowed:
            return False, message

        full_key = f"interfaces.{interface_id}.enabled"
        success, error = self._manager.set(full_key, enabled, validate=True)
        if not success:
            return False, error or "The setting could not be saved."
        self._manager.save()
        return True, None

    def reset_interfaces_to_defaults(self) -> Dict[str, bool]:
        """Reset to the registry's defaults - and to nothing else.

        The previous version enabled every interface it found in the settings
        file, which made the *settings file* authoritative over the product
        model: a switch that exists but should default to off was turned on by
        resetting. Defaults now come from the registry (one definition), and an
        interface whose default is off stays off after a reset.
        """
        state = self.get_state()
        defaults = state.defaults()
        for interface_id, default in defaults.items():
            self._manager.set(f"interfaces.{interface_id}.enabled", bool(default))
        self._manager.save()
        return defaults

    def is_interface_enabled(self, interface_id: str) -> bool:
        """Is this interface switched on and usable?

        False for anything the registry does not declare. That is the point:
        the old settings file could contain an id nothing had heard of, and the
        application would happily treat it as on.
        """
        return self.get_state().is_enabled(interface_id)

    def is_interface_enabled_by_endpoint(self, endpoint: str) -> bool:
        """May the interface that owns this endpoint be served?

        Replaces a hand-written endpoint -> interface map whose fallback was
        ``return True``: an endpoint nobody had registered was treated as
        enabled, which is indistinguishable from a page somebody forgot to add
        to the product model. Unknown now means unregistered, and unregistered
        means it is not served - the coverage test fails the build long before
        an operator meets it.
        """
        return self.get_state().endpoint_enabled(endpoint)

    def get_interface_for_endpoint(self, endpoint: str):
        """Which interface owns this endpoint (``None`` when none does)."""
        return get_interface_for_endpoint(endpoint)

    def reload_from_file(self) -> bool:
        """Reload settings from file (old API style)"""
        return self._manager.reload()
    
    def verify_settings_completeness(self) -> Dict[str, Any]:
        """Verify settings completeness (old API style)"""
        # Check if all required categories exist
        required_categories = ['system', 'display', 'search', 'processing', 
                              'notifications', 'theme', 'database', 'storage', 'interfaces']
        
        missing = []
        for category in required_categories:
            if not hasattr(self.settings, category):
                missing.append(category)
        
        # AUDIT (OPS-01): ``SettingsManager`` has no ``.version`` attribute -
        # the version lives on ``AllSettings`` / the ``version`` module. This
        # AttributeError made /health report
        # {"status":"degraded","checks":{"settings":"error: AttributeError"}}
        # on every boot, which is exactly the signal an orchestrator watches.
        try:
            from core.app_version import get_version as _get_version
        except Exception:  # pragma: no cover - defensive
            _get_version = None

        version = getattr(self.settings, 'version', None)
        if version is None:
            version = _get_version() if _get_version else None

        return {
            'complete': len(missing) == 0,
            'missing_categories': missing,
            'version': version
        }
    
    def check_file_changes(self) -> bool:
        """Check if settings file has changed (old API style)"""
        return self._manager.has_changed()
    
    @property
    def settings_file(self) -> Path:
        """Get settings file path (old API style)"""
        return self._manager.settings_file
    
    # Expose settings objects for direct access (backward compatibility)
    @property
    def system(self):
        """Direct access to system settings"""
        return self.settings.system
    
    @property
    def display(self):
        """Direct access to display settings"""
        return self.settings.display
    
    @property
    def processing(self):
        """Direct access to processing settings"""
        return self.settings.processing
    
    @property
    def storage(self):
        """Direct access to storage settings"""
        return self.settings.storage
    
    @property
    def database(self):
        """Direct access to database settings"""
        return self.settings.database
    
    # Project root management (for backward compatibility)
    @property
    def project_root(self) -> Optional[Path]:
        """
        Get project root path.
        
        The project root is inferred from the settings file location:
        - If settings.json is in data/ folder, project root is parent of data
        - If settings.json is directly in project root, that's the project root
        - Also checks PROJECT_ROOT environment variable
        """
        import os
        # First check environment variable (set by set_project_root)
        env_root = os.getenv('PROJECT_ROOT')
        if env_root:
            return Path(env_root)
        
        # Try to infer from settings file location
        settings_file = self._manager.settings_file
        if settings_file:
            # If settings.json is in data/ folder, project root is parent of data
            if settings_file.parent.name == 'data':
                return settings_file.parent.parent
            # If settings.json is directly in project root
            if settings_file.name == 'settings.json':
                return settings_file.parent
        return None
    
    def set_project_root(self, root_path: Path) -> None:
        """
        Set project root path (backward compatibility).
        
        Note: The project root is primarily inferred from the settings file location.
        This method is provided for backward compatibility but the actual project root
        is determined by where settings.json is located (typically in data/ folder).
        """
        # Store project root in a module-level variable for backward compatibility
        # The actual project root is inferred from settings file location
        import os
        if root_path:
            os.environ['PROJECT_ROOT'] = str(root_path.resolve())
    
    @property
    def uploads_dir(self) -> Optional[Path]:
        """Get uploads directory path"""
        if self.project_root:
            return self.project_root / 'uploads'
        return None
    
    @property
    def logs_dir(self) -> Optional[Path]:
        """Get logs directory path"""
        if self.project_root:
            return self.project_root / 'logs'
        return None
    
    def set_setting(self, key: str, value: Any, save_to_file: bool = True) -> None:
        """Set a setting using dot notation (backward compatibility)"""
        success, error = self._manager.set(key, value, validate=True)
        if not success:
            raise ValueError(f"Failed to set {key}: {error}")
        if save_to_file:
            self._manager.save()
    
    def _save_to_file(self) -> None:
        """Save settings to file (backward compatibility)"""
        self._manager.save()
    
    def _create_complete_settings_file(self) -> None:
        """Create complete settings file (backward compatibility)"""
        # Settings file is created automatically by SettingsManager
        self._manager.save()


# Global singleton instance
_interface_manager: Optional[SettingsAdapter] = None
_adapter_lock = __import__('threading').Lock()


def get_interface_manager() -> SettingsAdapter:
    """
    Get interface manager (backward compatibility function).
    
    This function provides the same interface as the old system,
    allowing existing code to work without changes.
    """
    global _interface_manager
    
    if _interface_manager is None:
        with _adapter_lock:
            if _interface_manager is None:
                _interface_manager = SettingsAdapter()
    
    return _interface_manager


# Additional backward compatibility functions
def get_settings() -> SettingsAdapter:
    """Get settings (backward compatibility)"""
    return get_interface_manager()


def get_user_settings() -> SettingsAdapter:
    """Get user settings (backward compatibility)"""
    return get_interface_manager()


def get_settings_integration() -> SettingsAdapter:
    """Get settings integration (backward compatibility)"""
    return get_interface_manager()


def reset_adapter():
    """Reset the adapter singleton instance (useful for testing)"""
    global _interface_manager
    with _adapter_lock:
        _interface_manager = None

