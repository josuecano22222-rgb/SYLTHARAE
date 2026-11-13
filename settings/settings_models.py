"""
Settings Data Models - Core dataclasses and validation
File: settings/settings_models.py

Complete rebuild - no legacy dependencies
Enhanced with all existing system features
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Union
from enum import Enum
import re
import os


class SettingType(Enum):
    """Supported setting types"""
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    FLOAT = "float"
    COLOR = "color"
    ENUM = "enum"


class ValidationError(Exception):
    """Raised when setting validation fails"""
    pass


@dataclass
class SettingDefinition:
    """Definition of a single setting"""
    key: str
    type: SettingType
    default: Any
    label: str
    description: str
    category: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[List[Any]] = None
    pattern: Optional[str] = None
    requires_restart: bool = False
    
    def validate(self, value: Any) -> Any:
        """Validate and coerce value"""
        # Type validation
        if self.type == SettingType.BOOLEAN:
            if not isinstance(value, bool):
                if isinstance(value, str):
                    value = value.lower() in ('true', '1', 'yes', 'on')
                else:
                    value = bool(value)
        
        elif self.type == SettingType.INTEGER:
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValidationError(f"{self.key}: must be an integer")
            
            if self.min_value is not None and value < self.min_value:
                raise ValidationError(f"{self.key}: must be >= {self.min_value}")
            if self.max_value is not None and value > self.max_value:
                raise ValidationError(f"{self.key}: must be <= {self.max_value}")
        
        elif self.type == SettingType.FLOAT:
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise ValidationError(f"{self.key}: must be a number")
            
            if self.min_value is not None and value < self.min_value:
                raise ValidationError(f"{self.key}: must be >= {self.min_value}")
            if self.max_value is not None and value > self.max_value:
                raise ValidationError(f"{self.key}: must be <= {self.max_value}")
        
        elif self.type == SettingType.STRING:
            value = str(value)
            if self.pattern and not re.match(self.pattern, value):
                raise ValidationError(f"{self.key}: invalid format")
        
        elif self.type == SettingType.COLOR:
            value = str(value)
            # Allow hex, rgb, rgba
            if not (re.match(r'^#[0-9A-Fa-f]{6}$', value) or 
                    value.startswith('rgb(') or 
                    value.startswith('rgba(')):
                raise ValidationError(f"{self.key}: must be hex color (#RRGGBB), rgb(), or rgba()")
        
        elif self.type == SettingType.ENUM:
            if self.allowed_values and value not in self.allowed_values:
                raise ValidationError(
                    f"{self.key}: must be one of {self.allowed_values}"
                )
        
        return value


@dataclass
class SystemSettings:
    """System-wide settings"""
    app_name: str = "SYLTHARAE System"
    app_icon: str = "bi-file-earmark-text"
    app_logo: str = ""
    language: str = "en"
    timezone: str = "UTC"
    date_format: str = "YYYY-MM-DD"
    time_format: str = "24h"
    theme: str = "light"
    animations_enabled: bool = True
    notifications_enabled: bool = True
    show_breadcrumbs: bool = True
    compact_mode: bool = False
    auto_save: bool = True
    confirm_actions: bool = True
    items_per_page: int = 50
    default_sort: str = "date_creation"
    sort_direction: str = "desc"
    debug_mode: bool = False
    #: The Screen Inspector: a read-only panel that says which registry entry,
    #: component, action and binding owns an element. Off by default, because a
    #: diagnostic tool is a decision an operator makes, not a default a reader
    #: meets. Administrators only, and the endpoint behind it is guarded on its
    #: own.
    screen_inspector: bool = False
    log_level: str = "INFO"
    log_file: Optional[str] = None
    action_logging_enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SystemSettings':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class DisplaySettings:
    """Display and UI settings"""
    compact_view: bool = False
    show_file_preview: bool = True
    show_metadata: bool = True
    results_per_page: int = 50
    default_sort: str = "date_creation"
    sort_direction: str = "desc"
    theme: str = "light"
    language: str = "en"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DisplaySettings':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class SearchSettings:
    """Search configuration"""
    default_search_type: str = "basic"
    case_sensitive: bool = False
    highlight_results: bool = True
    search_in_content: bool = True
    search_in_filename: bool = True
    search_in_metadata: bool = False
    max_results: int = 1000
    enable_history: bool = True
    enable_saved_searches: bool = True
    ripgrep_enabled: bool = True
    ripgrep_path: Optional[str] = None
    ripgrep_config_path: Optional[str] = None
    ripgrep_min_content_bytes: int = 8192
    ripgrep_max_matches_per_file: int = 10
    ripgrep_context_lines: int = 0
    ripgrep_use_pcre2: bool = False
    fast_search_enabled: bool = True
    defer_snippets: bool = True
    sql_rank_pool_max: int = 500
    snippet_context_chars: int = 250
    max_snippets_per_document: int = 5
    max_snippet_bytes: int = 4096
    max_total_snippet_bytes: int = 65536
    snippet_load_max_bytes: int = 262144
    snippet_worker_concurrency: int = 4
    search_count_mode: str = "timed"
    search_count_timeout_ms: int = 250
    search_max_candidates: int = 500
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SearchSettings':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class ProcessingSettings:
    """File processing settings"""
    use_threading: bool = True
    max_workers: int = 8  # Increased for faster processing
    enable_monitoring: bool = True
    monitor_interval: float = 5.0
    use_priority: bool = True  # Enabled by default for prioritization
    chunk_size: int = 1048576  # 1MB
    parallel_processing: bool = True
    auto_process_uploads: bool = True
    extract_archives: bool = True
    extract_attachments: bool = True
    process_nested: bool = True
    calculate_hashes: bool = True
    extract_text: bool = True
    extract_metadata: bool = True
    # max_file_size_mb removed - no file size limitations
    max_depth: int = 10
    file_processing_timeout: int = 1200  # 20 minutes
    #: Hard ceiling on one file's time budget, in seconds (default 24 h). The
    #: budget itself is base + bytes/observed-rate; this only bounds a single
    #: pathological file. Raise it to process a file that needs longer, rather
    #: than raising file_processing_timeout for every file.
    max_file_timeout_s: int = 86400
    # Compute-control layer (see core/compute). Defaults preserve the previous
    # behaviour exactly: automatic device routing, with the concurrency and
    # limits derived from the machine rather than configured. 0 / -1 / empty
    # mean "derive from this host"; setting a value caps it.
    compute_mode: str = "auto"             # cpu | gpu | cpu+gpu | auto
    compute_max_concurrency: int = 0       # 0 = derived from host cores
    compute_reserved_gateway_cores: int = -1  # -1 = derived (never starves the gateway)
    compute_queue_depth: int = 0           # 0 = derived
    compute_memory_budget_mb: int = 0      # 0 = a share of free memory
    compute_latency_budget_s: float = 10.0  # admission back-pressure threshold
    compute_isolation: bool = True         # keep compute off gateway/network cores
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProcessingSettings':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class NotificationSettings:
    """Notification preferences"""
    enabled: bool = True
    email_notifications: bool = False
    browser_notifications: bool = True
    processing_complete: bool = True
    batch_complete: bool = True
    errors_only: bool = False
    similar_files_enabled: bool = True
    future_dates_enabled: bool = True
    # Deprecated, ignored: the verb-tense analyzer it toggled was replaced by
    # stored temporal signals (core/detection). Kept so older settings files load.
    future_events_enabled: bool = True
    auto_analyze_files: bool = True
    upcoming_events_days: int = 30
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NotificationSettings':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class DatabaseConfig:
    """Database connection configuration.

    Configuration precedence (ARCH-02): dataclass defaults < persisted
    settings file < environment variables.  Credentials are never hardcoded
    (SEC-07): the password must come from the ``DB_PASSWORD`` environment
    variable or an explicitly saved settings file.
    """
    host: str = "localhost"
    port: int = 5432
    database: str = "analysis"
    user: str = "postgres"
    password: str = ""
    pool_min_conn: int = 2
    pool_max_conn: int = 25  # Increased default for concurrent operations (resource coordinator will adjust)
    pool_timeout: int = 30
    query_timeout: int = 60
    batch_size: int = 10000
    chunk_size: int = 10000

    def __post_init__(self):
        # Environment variables override persisted/default values (12-factor).
        self.apply_env_overrides()

    def apply_env_overrides(self) -> None:
        """Apply DB_* environment variables on top of current values."""
        self.host = os.environ.get("DB_HOST", self.host)
        self.port = int(os.environ.get("DB_PORT", self.port))
        self.database = os.environ.get("DB_NAME", self.database)
        self.user = os.environ.get("DB_USER", self.user)
        if os.environ.get("DB_PASSWORD") is not None:
            self.password = os.environ["DB_PASSWORD"]

    
    def to_dict(self) -> Dict[str, Any]:
        # Don't expose password in dict
        d = asdict(self)
        d['password_set'] = bool(self.password)
        d.pop('password', None)
        return d
    
    def to_dict_full(self) -> Dict[str, Any]:
        """Full dict including password (for internal use)"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DatabaseConfig':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class StorageConfig:
    """Storage configuration"""
    enable_storage: bool = True
    default_source: str = "default"
    default_side: str = "default"
    db_name: str = "analysis"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StorageConfig':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class ThemeSettings:
    """Theme and color customization"""
    # Brand colors
    primary_color: str = "#4f46e5"
    secondary_color: str = "#06b6d4"
    success_color: str = "#10b981"
    danger_color: str = "#ef4444"
    warning_color: str = "#f59e0b"
    info_color: str = "#3b82f6"
    
    # Background colors
    bg_light: str = "#f8fafc"
    bg_white: str = "#ffffff"
    bg_section: str = "#f8fafc"
    
    # Text colors
    text_dark: str = "#1e293b"
    text_light: str = "#64748b"
    text_muted: str = "#94a3b8"
    
    # Border colors
    border_color: str = "#e2e8f0"
    border_light: str = "#f1f5f9"
    border_dark: str = "#cbd5e1"
    
    # Sidebar
    sidebar_bg: str = "#1e293b"
    sidebar_text: str = "#e2e8f0"
    sidebar_active: str = "#4f46e5"
    sidebar_hover: str = "#334155"
    
    # Buttons
    btn_primary: str = "#4f46e5"
    btn_primary_hover: str = "#4338ca"
    btn_secondary: str = "#06b6d4"
    btn_secondary_hover: str = "#0891b2"
    
    # Gradient
    gradient_start: str = "#4f46e5"
    gradient_end: str = "#06b6d4"
    gradient_direction: str = "135deg"
    
    # Spacing
    spacing_xs: str = "0.25rem"
    spacing_sm: str = "0.5rem"
    spacing_md: str = "1rem"
    spacing_lg: str = "1.5rem"
    spacing_xl: str = "2rem"
    spacing_2xl: str = "3rem"
    
    # Font sizes
    font_size_xs: str = "0.75rem"
    font_size_sm: str = "0.875rem"
    font_size_base: str = "1rem"
    font_size_lg: str = "1.125rem"
    font_size_xl: str = "1.25rem"
    font_size_2xl: str = "1.5rem"
    font_size_3xl: str = "1.875rem"
    
    # Font weights
    font_weight_normal: str = "400"
    font_weight_medium: str = "500"
    font_weight_semibold: str = "600"
    font_weight_bold: str = "700"
    
    # Border radius
    border_radius_sm: str = "0.25rem"
    border_radius_md: str = "0.375rem"
    border_radius_lg: str = "0.5rem"
    border_radius_xl: str = "0.75rem"
    
    # Shadows
    shadow_sm: str = "0 1px 2px 0 rgba(0, 0, 0, 0.05)"
    shadow_md: str = "0 4px 6px -1px rgba(0, 0, 0, 0.1)"
    shadow_lg: str = "0 10px 15px -3px rgba(0, 0, 0, 0.1)"
    shadow_xl: str = "0 20px 25px -5px rgba(0, 0, 0, 0.1)"
    
    # Icon sizes
    icon_size_sm: str = "1rem"
    icon_size_md: str = "1.5rem"
    icon_size_lg: str = "2rem"
    icon_size_xl: str = "2.5rem"
    
    # Sidebar width
    sidebar_width: str = "260px"
    
    # Custom CSS
    custom_css: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ThemeSettings':
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class InterfaceConfig:
    """Configuration for a single interface"""
    enabled: bool = True
    category: str = "user"
    
    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "category": self.category}
    
    @classmethod
    def from_dict(cls, data: Union[Dict[str, Any], bool]) -> 'InterfaceConfig':
        """Handle both dict and bool formats for backward compatibility"""
        if isinstance(data, bool):
            return cls(enabled=data, category="user")
        return cls(
            enabled=data.get("enabled", True),
            category=data.get("category", "user")
        )


@dataclass
class InterfaceVisibility:
    """Interface visibility settings with category support"""
    interfaces: Dict[str, InterfaceConfig] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interfaces": {
                k: v.to_dict() if isinstance(v, InterfaceConfig) else v
                for k, v in self.interfaces.items()
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InterfaceVisibility':
        interfaces = {}
        raw_interfaces = data.get("interfaces", {})
        
        for key, value in raw_interfaces.items():
            interfaces[key] = InterfaceConfig.from_dict(value)
        
        return cls(interfaces=interfaces)
    
    def get_interface_enabled(self, interface_id: str) -> bool:
        """Get enabled status for an interface"""
        if interface_id in self.interfaces:
            return self.interfaces[interface_id].enabled
        # Default to False (disabled) for missing interfaces - they must be explicitly enabled
        # This ensures that if an interface is not configured, it's hidden by default
        return False
    
    def set_interface_enabled(self, interface_id: str, enabled: bool, category: str = "user"):
        """Set enabled status for an interface"""
        if interface_id not in self.interfaces:
            self.interfaces[interface_id] = InterfaceConfig(enabled=enabled, category=category)
        else:
            self.interfaces[interface_id].enabled = enabled
            if category:
                self.interfaces[interface_id].category = category
    
    def restore_missing_interfaces(self, default_interfaces: Dict[str, Dict[str, Any]]):
        """
        Restore missing interfaces with default values.
        
        Args:
            default_interfaces: Dictionary mapping interface_id to default config
                Format: {
                    "interface_id": {
                        "enabled": bool,
                        "category": str,
                        "name": str (optional),
                        "description": str (optional),
                        "icon": str (optional),
                        "endpoint": str (optional)
                    }
                }
        """
        restored = []
        for interface_id, default_config in default_interfaces.items():
            if interface_id not in self.interfaces:
                enabled = default_config.get("enabled", True)
                category = default_config.get("category", "user")
                self.interfaces[interface_id] = InterfaceConfig(enabled=enabled, category=category)
                restored.append(interface_id)
        return restored


@dataclass
class RetentionSettings:
    """Step 21: days to keep each growing area; ``0`` keeps it forever.

    The defaults and the authority are ``services/retention/model.py``
    (``POLICIES``) - these fields are only the persisted storage shape; the
    retention service re-validates whatever is stored here.
    """
    jobs_days: int = 90
    scenario_outcomes_days: int = 0
    notifications_days: int = 0
    path_revisions_days: int = 0
    audit_log_days: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {f: getattr(self, f) for f in (
            "jobs_days", "scenario_outcomes_days", "notifications_days",
            "path_revisions_days", "audit_log_days")}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RetentionSettings':
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class AllSettings:
    """Complete settings container"""
    system: SystemSettings = field(default_factory=SystemSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    theme: ThemeSettings = field(default_factory=ThemeSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    interfaces: InterfaceVisibility = field(default_factory=InterfaceVisibility)
    version: str = "3.0.0"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "system": self.system.to_dict(),
            "display": self.display.to_dict(),
            "search": self.search.to_dict(),
            "processing": self.processing.to_dict(),
            "notifications": self.notifications.to_dict(),
            "theme": self.theme.to_dict(),
            "retention": self.retention.to_dict(),
            "database": self.database.to_dict(),
            "storage": self.storage.to_dict(),
            "interfaces": self.interfaces.to_dict(),
            "version": self.version
        }
    
    def to_dict_full(self) -> Dict[str, Any]:
        """Full dict including sensitive data (for internal use)"""
        d = self.to_dict()
        d["database"] = self.database.to_dict_full()
        return d
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AllSettings':
        return cls(
            system=SystemSettings.from_dict(data.get("system", {})),
            display=DisplaySettings.from_dict(data.get("display", {})),
            search=SearchSettings.from_dict(data.get("search", {})),
            processing=ProcessingSettings.from_dict(data.get("processing", {})),
            notifications=NotificationSettings.from_dict(data.get("notifications", {})),
            theme=ThemeSettings.from_dict(data.get("theme", {})),
            retention=RetentionSettings.from_dict(data.get("retention", {})),
            database=DatabaseConfig.from_dict(data.get("database", {})),
            storage=StorageConfig.from_dict(data.get("storage", {})),
            interfaces=InterfaceVisibility.from_dict(data.get("interfaces", {})),
            version=data.get("version", "3.0.0")
        )


try:
    from settings.languages import SUPPORTED_LANGUAGES
except ImportError:  # pragma: no cover - fallback keeps validation functional
    SUPPORTED_LANGUAGES = {"en": "English", "ar": "العربية", "he": "עברית", "fa": "فارسی"}

# Setting definitions for validation
SETTING_DEFINITIONS = {
    # System settings
    "system.app_name": SettingDefinition(
        key="app_name",
        type=SettingType.STRING,
        default="SYLTHARAE System",
        label="Application Name",
        description="Name displayed in the interface",
        category="system"
    ),
    "system.language": SettingDefinition(
        key="language",
        type=SettingType.ENUM,
        default="en",
        label="Language",
        description="Interface language",
        category="system",
        allowed_values=list(SUPPORTED_LANGUAGES.keys()),
        requires_restart=True
    ),
    "system.items_per_page": SettingDefinition(
        key="items_per_page",
        type=SettingType.INTEGER,
        default=50,
        label="Items Per Page",
        description="Number of items to display per page",
        category="system",
        min_value=10,
        max_value=1000
    ),
    "interfaces.page_tips": SettingDefinition(
        key="page_tips",
        type=SettingType.BOOLEAN,
        default=True,
        label="Show Page Tips",
        description="Display helpful tips and documentation on pages",
        category="interfaces"
    ),
    
    "system.screen_inspector": SettingDefinition(
        key="screen_inspector",
        type=SettingType.BOOLEAN,
        default=False,
        label="Screen Inspector",
        description="Show the Screen Inspector: a read-only panel that reports "
                    "which interface, component, action and binding owns an "
                    "element on the page (administrators only)",
        category="system"
    ),

    # Retention (step 21): days to keep each growing area; 0 = keep forever.
    # Bounds match services/retention/model.py, which re-validates.
    "retention.jobs_days": SettingDefinition(
        key="jobs_days", type=SettingType.INTEGER, default=90,
        label="Retention: finished jobs (days)",
        description="Delete finished JobManager jobs and their events after "
                    "this many days. 0 keeps them forever.",
        category="retention", min_value=0, max_value=3650),
    "retention.scenario_outcomes_days": SettingDefinition(
        key="scenario_outcomes_days", type=SettingType.INTEGER, default=0,
        label="Retention: scenario outcomes (days)",
        description="Delete scenario outcome history after this many days. "
                    "0 (default) keeps the append-only evidence forever.",
        category="retention", min_value=0, max_value=3650),
    "retention.notifications_days": SettingDefinition(
        key="notifications_days", type=SettingType.INTEGER, default=0,
        label="Retention: notifications (days)",
        description="Delete notifications for every recipient after this "
                    "many days, read or unread. 0 keeps them forever.",
        category="retention", min_value=0, max_value=3650),
    "retention.path_revisions_days": SettingDefinition(
        key="path_revisions_days", type=SettingType.INTEGER, default=0,
        label="Retention: revision log (days)",
        description="Delete the append-only revision log behind the Change "
                    "report after this many days. 0 keeps it forever.",
        category="retention", min_value=0, max_value=3650),
    "retention.audit_log_days": SettingDefinition(
        key="audit_log_days", type=SettingType.INTEGER, default=0,
        label="Retention: audit log (days)",
        description="Delete audit history (export disclosures included) "
                    "after this many days. 0 (default) keeps it forever; "
                    "deleting it weakens tamper evidence and is audited.",
        category="retention", min_value=0, max_value=3650),

    # Theme settings
    "theme.primary_color": SettingDefinition(
        key="primary_color",
        type=SettingType.COLOR,
        default="#4f46e5",
        label="Primary Color",
        description="Main brand color",
        category="theme"
    ),
    
    # Add more definitions as needed...
}


def get_setting_definition(key: str) -> Optional[SettingDefinition]:
    """Get definition for a setting by its dot-notation key"""
    return SETTING_DEFINITIONS.get(key)

