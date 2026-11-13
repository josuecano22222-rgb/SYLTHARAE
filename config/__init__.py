"""
Configuration Validator & Environment Manager
File: config/__init__.py

Validates, merges, and applies environment-specific configuration overlays.
Supports development, staging, and production environments.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CONFIG_DIR.parent


def get_environment() -> str:
    """Get the current deployment environment."""
    return os.environ.get("FLASK_ENV", "production").lower().strip()


def get_environment_config_path(environment: Optional[str] = None) -> Optional[Path]:
    """Get the path to the environment-specific config file."""
    env = environment or get_environment()
    path = _CONFIG_DIR / f"{env}.json"
    return path if path.exists() else None


def load_environment_overlay(environment: Optional[str] = None) -> Dict[str, Any]:
    """Load the environment-specific configuration overlay."""
    path = get_environment_config_path(environment)
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not load environment config %s: %s", path, e)
        return {}


def deep_merge(base: Dict, overlay: Dict) -> Dict:
    """Deep-merge overlay into base. Overlay values win on conflict."""
    result = base.copy()
    for key, value in overlay.items():
        if key.startswith("_"):
            continue  # skip metadata keys
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_effective_config() -> Dict[str, Any]:
    """Load and merge configuration files for validation/diagnostics only.

    Merges: config.example.json → config.json → config/{env}.json.

    This function is used for:
    - Startup validation (warnings on invalid config)
    - Diagnostic display (print_config_summary)
    - Utility lookups (get_config_value)

    It is NOT the authoritative runtime configuration source.  The
    authoritative source is the SettingsManager (settings/settings_manager.py)
    combined with environment variables.  This function reads raw JSON files
    and returns a merged dict; it does not update the running system.
    """
    # 1. Project defaults
    defaults_path = _PROJECT_ROOT / "config.example.json"
    config: Dict[str, Any] = {}
    if defaults_path.exists():
        try:
            with open(defaults_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 2. User config
    user_path = _PROJECT_ROOT / "config.json"
    if user_path.exists():
        try:
            with open(user_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            config = deep_merge(config, user_config)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Environment overlay
    overlay = load_environment_overlay()
    if overlay:
        config = deep_merge(config, overlay)

    return config


def validate_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate a configuration dictionary comprehensively.

    Returns (is_valid, list_of_errors).
    """
    errors: List[str] = []

    # Environment
    env = config.get("environment", "production")
    valid_envs = {"development", "staging", "production", "testing"}
    if env not in valid_envs:
        errors.append(f"environment must be one of: {', '.join(sorted(valid_envs))}")

    # Database
    db = config.get("database", {})
    port = db.get("port", 5432)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        errors.append("database.port must be an integer 1–65535")

    pool = db.get("pool", {})
    if pool:
        min_conn = pool.get("min_connections", 2)
        max_conn = pool.get("max_connections", 25)
        if not isinstance(min_conn, int) or min_conn < 1:
            errors.append("database.pool.min_connections must be >= 1")
        if not isinstance(max_conn, int) or max_conn < 1:
            errors.append("database.pool.max_connections must be >= 1")
        if (isinstance(min_conn, int) and isinstance(max_conn, int)
                and min_conn > max_conn):
            errors.append("database.pool.min_connections cannot exceed max_connections")

    query_timeout = db.get("query_timeout", 60)
    if isinstance(query_timeout, (int, float)) and query_timeout < 1:
        errors.append("database.query_timeout must be >= 1 second")

    # Security
    sec = config.get("security", {})
    if sec:
        pw_len = sec.get("password_min_length", 12)
        if isinstance(pw_len, int) and pw_len < 8:
            errors.append("security.password_min_length must be >= 8")
        if isinstance(pw_len, int) and pw_len > 128:
            errors.append("security.password_min_length must be <= 128")

        max_failed = sec.get("max_failed_logins", 5)
        if isinstance(max_failed, int) and max_failed < 1:
            errors.append("security.max_failed_logins must be >= 1")

        session_hours = sec.get("session_hours", 12)
        if isinstance(session_hours, (int, float)) and session_hours < 0.5:
            errors.append("security.session_hours must be >= 0.5")

        lockout = sec.get("lockout_minutes", 15)
        if isinstance(lockout, (int, float)) and lockout < 1:
            errors.append("security.lockout_minutes must be >= 1")

        rl = sec.get("rate_limit", {})
        if rl:
            rpm = rl.get("per_minute", 60)
            rph = rl.get("per_hour", 600)
            if isinstance(rpm, int) and rpm < 1:
                errors.append("security.rate_limit.per_minute must be >= 1")
            if isinstance(rph, int) and rph < 1:
                errors.append("security.rate_limit.per_hour must be >= 1")
            if (isinstance(rpm, int) and isinstance(rph, int) and rpm > rph):
                errors.append("security.rate_limit.per_minute cannot exceed per_hour")

    # Processing
    proc = config.get("processing", {})
    if proc:
        workers = proc.get("max_workers", 8)
        if isinstance(workers, int) and workers < 1:
            errors.append("processing.max_workers must be >= 1")
        if isinstance(workers, int) and workers > 64:
            errors.append("processing.max_workers must be <= 64")

        timeout = proc.get("file_processing_timeout", 1200)
        if isinstance(timeout, (int, float)) and timeout < 10:
            errors.append("processing.file_processing_timeout must be >= 10 seconds")

    # Ingestion
    ingestion = config.get("ingestion", {})
    roots = ingestion.get("roots", [])
    if isinstance(roots, list):
        for root in roots:
            if not isinstance(root, str) or not root.strip():
                errors.append(f"ingestion.roots contains invalid entry: {root!r}")

    # Admin
    admin = config.get("admin", {})
    if admin:
        username = admin.get("username", "")
        if username and (len(username) < 1 or len(username) > 64):
            errors.append("admin.username must be 1–64 characters")

    return len(errors) == 0, errors


def get_config_value(key_path: str, default: Any = None,
                     config: Optional[Dict] = None) -> Any:
    """
    Get a nested config value by dot-notation path.

    Examples:
        get_config_value("database.host") -> "localhost"
        get_config_value("security.rate_limit.per_minute") -> 60
    """
    cfg = config if config is not None else load_effective_config()
    parts = key_path.split(".")
    current = cfg
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def mask_secrets(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of config with sensitive values masked.
    Safe for display in logs or web UIs.
    """
    import copy
    masked = copy.deepcopy(config)
    secret_keys = {"password", "secret_key", "api_key", "token", "credential"}

    def _mask_dict(d: Dict):
        for key, value in d.items():
            if isinstance(value, dict):
                _mask_dict(value)
            elif any(s in key.lower() for s in secret_keys):
                if isinstance(value, str) and value:
                    d[key] = "****" + value[-4:] if len(value) > 4 else "****"
            elif key == "DB_PASSWORD" or key == "APP_ADMIN_PASSWORD":
                if isinstance(value, str) and value:
                    d[key] = "****"

    _mask_dict(masked)
    return masked


def print_config_summary(config: Optional[Dict] = None):
    """Print a human-readable summary of the effective configuration."""
    cfg = config or load_effective_config()
    env = cfg.get("environment", "unknown")
    db = cfg.get("database", {})
    sec = cfg.get("security", {})
    proc = cfg.get("processing", {})

    print(f"  Environment:    {env}")
    print(f"  Database:       {db.get('host', '?')}:{db.get('port', '?')}"
          f"/{db.get('database', '?')}")
    print(f"  DB Pool:        {db.get('pool', {}).get('min_connections', '?')}-"
          f"{db.get('pool', {}).get('max_connections', '?')} connections")
    print(f"  Max Workers:    {proc.get('max_workers', '?')}")
    print(f"  Session:        {sec.get('session_hours', '?')}h "
          f"(idle: {sec.get('session_idle_hours', '?')}h)")
    print(f"  Lockout:        {sec.get('max_failed_logins', '?')} attempts / "
          f"{sec.get('lockout_minutes', '?')}min")
    print(f"  Rate Limit:     "
          f"{sec.get('rate_limit', {}).get('per_minute', '?')}/min, "
          f"{sec.get('rate_limit', {}).get('per_hour', '?')}/hour")


__all__ = [
    "get_environment",
    "get_environment_config_path",
    "load_environment_overlay",
    "deep_merge",
    "load_effective_config",
    "validate_config",
    "get_config_value",
    "mask_secrets",
    "print_config_summary",
]
