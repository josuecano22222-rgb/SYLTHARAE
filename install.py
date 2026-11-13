#!/usr/bin/env python3
"""
SYLTHARAE — CLI Installer (headless / CI / recovery)

Thin CLI wrapper around the shared installer services in core.installer.
For normal installation, use the web wizard at http://127.0.0.1:5000/setup.

Usage:
    python install.py                    # Full interactive installation
    python install.py --check            # Prerequisites check only
    python install.py --configure        # Reconfigure existing installation
    python install.py --verify           # Post-install verification only
    python install.py --env production   # Set environment non-interactively
    python install.py --non-interactive  # Use defaults/env (CI/unattended)
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "config.json"
CONFIG_EXAMPLE = PROJECT_ROOT / "config.example.json"
INSTALL_STATE_FILE = PROJECT_ROOT / ".install_state.json"

# ANSI helpers
_NO_COLOR = (
    not sys.stdout.isatty()
    or os.environ.get("NO_COLOR") == "1"
    or (os.name == "nt" and not os.environ.get("ANSICON"))
)
def _c(code: str, text: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"
GREEN  = lambda t: _c("32", t)
RED    = lambda t: _c("31", t)
YELLOW = lambda t: _c("33", t)
CYAN   = lambda t: _c("36", t)
BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)

def section(title: str):
    print(f"\n{BOLD(CYAN(f'── {title} '))}{'─' * max(0, 64 - len(title) - 4)}")


# ── Prompt helpers ──
def prompt(label: str, default: str = "", secret: bool = False,
           validator: Optional[Callable] = None,
           non_interactive: bool = False) -> str:
    if non_interactive:
        return default
    suffix = f" [{DIM(default)}]" if default else ""
    while True:
        if secret:
            import getpass
            raw = getpass.getpass(f"  {label}{suffix}: ").strip()
        else:
            raw = input(f"  {label}{suffix}: ").strip()
        value = raw if raw else default
        if validator:
            ok, msg = validator(value)
            if not ok:
                print(f"    {RED('↳')} {msg}")
                continue
        return value

def yes_no(label: str, default: bool = True, non_interactive: bool = False) -> bool:
    if non_interactive:
        return default
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {label} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(f"    {RED('↳')} Please enter y or n.")


# ── Validators ──
def _validate_port(value: str) -> Tuple[bool, str]:
    try:
        p = int(value)
        return (True, "") if 1 <= p <= 65535 else (False, "Port must be 1–65535")
    except ValueError:
        return False, "Must be an integer"

def _validate_positive_int(value: str) -> Tuple[bool, str]:
    try:
        return (True, "") if int(value) >= 1 else (False, "Must be >= 1")
    except ValueError:
        return False, "Must be an integer"

def _validate_log_level(value: str) -> Tuple[bool, str]:
    valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    return (True, "") if value.upper() in valid else (False, f"Must be one of: {', '.join(sorted(valid))}")


# ── Stage state ──
def _save_state(stages: List[str]):
    try:
        INSTALL_STATE_FILE.write_text(json.dumps({"stages": stages}, indent=2))
    except OSError:
        pass

def _load_state() -> List[str]:
    if INSTALL_STATE_FILE.exists():
        try:
            return json.loads(INSTALL_STATE_FILE.read_text()).get("stages", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []

def _clear_state():
    try:
        INSTALL_STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── Phase: System check ──
def run_system_checks(ni: bool) -> bool:
    from core.installer import check_system
    section("System Prerequisites")
    checks = check_system()
    all_ok = True
    for name, c in checks.items():
        ok = c.get("ok", False)
        detail = c.get("version") or c.get("name", "") or c.get("message", "")
        if "free_mb" in c and c["free_mb"] > 0:
            detail = f"{c['free_mb']} MB free"
        mark = GREEN("✓") if ok else RED("✗")
        print(f"  {mark} {name:16s} {detail}")
        if not ok:
            all_ok = False
            msg = c.get("message", "")
            if msg:
                print(f"    {DIM('↳')} {CYAN('Fix:')} {msg}")
    return all_ok


# ── Phase: Dependency check ──
def _requirement_names(req_file: Path) -> Dict[str, str]:
    """``{distribution or module name: original requirement line}``.

    The manifest is keyed by *distribution* name, which is not always the
    importable module name (``python-docx`` imports as ``docx``).
    """
    import re

    required: Dict[str, str] = {}
    for line in Path(req_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[><=!~\[; ]", line, maxsplit=1)[0].strip()
        if name:
            required[name.lower()] = line
    return required


def _missing_requirements(req_file: Path) -> List[str]:
    """Requirement lines from *req_file* that are not installed.

    Checks the *distribution* metadata first, so entries whose import name
    differs from the distribution name (python-docx, python-pptx, odfpy,
    RTFDE, compressed-rtf, PyMuPDF...) are not reported as missing on a
    complete environment. A module-import fallback catches anything that is
    importable but has no metadata (vendored modules, editable checkouts).
    """
    import_aliases = {
        "python-docx": "docx", "python-pptx": "pptx", "pyyaml": "yaml",
        "psycopg2-binary": "psycopg2", "python-dateutil": "dateutil",
        "python-dotenv": "dotenv", "flask-babel": "flask_babel",
        "flask-limiter": "flask_limiter", "flask-wtf": "flask_wtf",
        "flask-compress": "flask_compress", "extract-msg": "extract_msg",
        "rtfde": "RTFDE", "compressed-rtf": "compressed_rtf",
        "pymupdf": "fitz", "pillow": "PIL", "beautifulsoup4": "bs4",
        "msoffcrypto-tool": "msoffcrypto", "opencv-python": "cv2",
        "opencv-python-headless": "cv2", "pymupdf4llm": "pymupdf4llm",
        "pymupdf-layout": "pymupdf_layout", "pycryptodomex": "Cryptodome",
        "rapidocr-onnxruntime": "rapidocr_onnxruntime", "mobi": "mobi",
        "eBookLib": "ebooklib", "ordered-set": "ordered_set",
        "backports.zstd": "backports.zstd",
    }
    from importlib.metadata import PackageNotFoundError, distribution

    missing: List[str] = []
    for name, req_spec in _requirement_names(req_file).items():
        try:
            distribution(name)
            continue
        except (PackageNotFoundError, ValueError):
            pass
        module = import_aliases.get(name, name.replace("-", "_"))
        try:
            importlib.import_module(module)
        except Exception:
            missing.append(req_spec)
    return missing


def run_dependency_check(ni: bool) -> bool:
    section("Dependencies")
    req_file = PROJECT_ROOT / "requirements.txt"
    if not req_file.exists():
        print(f"  {RED('✗')} requirements.txt not found")
        return False

    required = _requirement_names(req_file)
    missing = _missing_requirements(req_file)

    if missing:
        print(f"  {RED('✗')} {len(missing)} missing: {', '.join(m[:30] for m in missing[:5])}")
        if yes_no("  Install missing packages now?", default=True, non_interactive=ni):
            print(f"\n  Installing {len(missing)} package(s)...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                # pip's exit code says "pip succeeded", not "the environment is
                # now complete", so ask the same question again.
                still_missing = _missing_requirements(req_file)
                if still_missing:
                    print(f"  {RED('✗')} still missing after install: "
                          f"{', '.join(m[:30] for m in still_missing[:5])}")
                    return False
                print(f"  {GREEN('✓')} All packages installed")
                return True
            print(f"  {RED('✗')} pip returned code {result.returncode}")
            return False
        return False
    print(f"  {GREEN('✓')} {len(required)} packages importable")
    return True


# ── Phase: Gather configuration ──
#: The settings the installer prompts for; these keys are also accepted from
#: the process environment (see gather_config).
_CONFIG_KEYS = frozenset({
    "FLASK_ENV", "FLASK_HOST", "FLASK_PORT", "FLASK_SECRET_KEY",
    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "APP_ADMIN_USERNAME", "APP_ADMIN_PASSWORD", "PASSWORD_MIN_LENGTH",
    "SECURITY_MAX_FAILED_LOGINS", "SECURITY_LOCKOUT_MINUTES",
    "SECURITY_SESSION_HOURS", "SECURITY_SESSION_IDLE_HOURS",
    "RATE_LIMIT_PER_MINUTE", "RATE_LIMIT_PER_HOUR",
    "MAX_WORKERS", "FILE_PROCESSING_TIMEOUT", "INGESTION_ROOTS", "LOG_LEVEL",
})


def gather_config(ni: bool) -> Dict[str, str]:
    section("Configuration")
    print("  Press Enter to accept the default shown in brackets.\n")

    # Defaults, lowest precedence first: the .env file, then the process
    # environment. Non-interactive mode returns these defaults unchanged, so
    # without the environment half an unattended install could never set the
    # administrator password (main() reports "Password must be at least 12
    # characters" however long APP_ADMIN_PASSWORD was) - measured on a clean
    # clone in the round-10 install drill. The precedence (environment over
    # file) is the one the application itself uses.
    existing: Dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing.update({k: v for k, v in os.environ.items() if k in _CONFIG_KEYS})

    config: Dict[str, str] = {}

    # Environment
    if ni:
        config["FLASK_ENV"] = existing.get("FLASK_ENV", "production")
    else:
        print(f"  {BOLD('Deployment Environment')}")
        print("    1) development  2) staging  3) production")
        choice = input("  Environment [3]: ").strip()
        env_map = {"1": "development", "2": "staging", "3": "production"}
        config["FLASK_ENV"] = env_map.get(choice, existing.get("FLASK_ENV", "production"))
    print(f"  {GREEN('✓')} Environment: {config['FLASK_ENV']}")

    # Database
    print(f"\n  {BOLD('Database')}")
    config["DB_HOST"] = prompt("Host", existing.get("DB_HOST", "localhost"), non_interactive=ni)
    config["DB_PORT"] = prompt("Port", existing.get("DB_PORT", "5432"), validator=_validate_port, non_interactive=ni)
    config["DB_USER"] = prompt("User", existing.get("DB_USER", "postgres"), non_interactive=ni)
    config["DB_PASSWORD"] = prompt("Password", existing.get("DB_PASSWORD", ""), secret=True, non_interactive=ni)
    config["DB_NAME"] = prompt("Database name", existing.get("DB_NAME", "analysis"), non_interactive=ni)

    # Admin
    print(f"\n  {BOLD('Administrator')}")
    config["APP_ADMIN_USERNAME"] = prompt("Admin username", existing.get("APP_ADMIN_USERNAME", "admin"), non_interactive=ni)
    config["APP_ADMIN_PASSWORD"] = prompt("Admin password", existing.get("APP_ADMIN_PASSWORD", ""), secret=True, non_interactive=ni)
    config["PASSWORD_MIN_LENGTH"] = prompt("Min password length", existing.get("PASSWORD_MIN_LENGTH", "12"), validator=_validate_positive_int, non_interactive=ni)

    # Security
    print(f"\n  {BOLD('Security')}")
    config["SECURITY_MAX_FAILED_LOGINS"] = prompt("Max failed logins", existing.get("SECURITY_MAX_FAILED_LOGINS", "5"), validator=_validate_positive_int, non_interactive=ni)
    config["SECURITY_LOCKOUT_MINUTES"] = prompt("Lockout (min)", existing.get("SECURITY_LOCKOUT_MINUTES", "15"), validator=_validate_positive_int, non_interactive=ni)
    config["SECURITY_SESSION_HOURS"] = prompt("Session (hours)", existing.get("SECURITY_SESSION_HOURS", "12"), validator=_validate_positive_int, non_interactive=ni)
    config["SECURITY_SESSION_IDLE_HOURS"] = prompt("Idle timeout (hours)", existing.get("SECURITY_SESSION_IDLE_HOURS", "6"), validator=_validate_positive_int, non_interactive=ni)
    config["RATE_LIMIT_PER_MINUTE"] = prompt("Rate limit/min", existing.get("RATE_LIMIT_PER_MINUTE", "60"), validator=_validate_positive_int, non_interactive=ni)
    config["RATE_LIMIT_PER_HOUR"] = prompt("Rate limit/hour", existing.get("RATE_LIMIT_PER_HOUR", "600"), validator=_validate_positive_int, non_interactive=ni)

    # Web
    print(f"\n  {BOLD('Web Server')}")
    config["FLASK_SECRET_KEY"] = existing.get("FLASK_SECRET_KEY", "")
    config["FLASK_HOST"] = prompt("Bind address", existing.get("FLASK_HOST", "0.0.0.0"), non_interactive=ni)
    config["FLASK_PORT"] = prompt("Port", existing.get("FLASK_PORT", "5000"), validator=_validate_port, non_interactive=ni)

    # Processing
    print(f"\n  {BOLD('Processing')}")
    config["MAX_WORKERS"] = prompt("Workers", existing.get("MAX_WORKERS", "8"), validator=_validate_positive_int, non_interactive=ni)
    config["FILE_PROCESSING_TIMEOUT"] = prompt("Timeout (sec)", existing.get("FILE_PROCESSING_TIMEOUT", "1200"), validator=_validate_positive_int, non_interactive=ni)
    config["INGESTION_ROOTS"] = prompt("Ingestion roots (;-separated)", existing.get("INGESTION_ROOTS", ""), non_interactive=ni)
    config["LOG_LEVEL"] = prompt("Log level", existing.get("LOG_LEVEL", "INFO"), validator=_validate_log_level, non_interactive=ni)

    return config


# ── Phase: Write config.json ──
#: The sections every config.json must have. ``config.example.json`` is not in
#: the repository (only ``.env.example`` is tracked), so on a clean checkout the
#: example file does not exist and the skeleton below is the only definition of
#: the document - the install used to die with KeyError: 'app' here.
_CONFIG_SKELETON: Dict[str, Any] = {
    "environment": "production",
    "app": {},
    "database": {},
    "security": {"rate_limit": {}},
    "admin": {},
    "processing": {},
    "ingestion": {"roots": []},
}


def _config_skeleton() -> Dict[str, Any]:
    import copy

    return copy.deepcopy(_CONFIG_SKELETON)


def write_config_json(config: Dict[str, str]):
    import copy

    data = _config_skeleton()
    if CONFIG_EXAMPLE.exists():
        with open(CONFIG_EXAMPLE) as f:
            example = json.load(f)
        if isinstance(example, dict):
            data.update(example)
            for section, value in _CONFIG_SKELETON.items():
                if isinstance(value, dict) and not isinstance(data.get(section), dict):
                    data[section] = copy.deepcopy(value)

    data["environment"] = config.get("FLASK_ENV", "production")
    data["app"]["debug_mode"] = data["environment"] == "development"
    data["app"]["log_level"] = config.get("LOG_LEVEL", "INFO")
    data["database"]["host"] = config.get("DB_HOST", "localhost")
    data["database"]["port"] = int(config.get("DB_PORT", "5432"))
    data["database"]["user"] = config.get("DB_USER", "postgres")
    data["database"]["password"] = config.get("DB_PASSWORD", "")
    data["database"]["database"] = config.get("DB_NAME", "analysis")
    data["security"]["max_failed_logins"] = int(config.get("SECURITY_MAX_FAILED_LOGINS", "5"))
    data["security"]["lockout_minutes"] = int(config.get("SECURITY_LOCKOUT_MINUTES", "15"))
    data["security"]["session_hours"] = int(config.get("SECURITY_SESSION_HOURS", "12"))
    data["security"]["password_min_length"] = int(config.get("PASSWORD_MIN_LENGTH", "12"))
    data["security"]["rate_limit"]["per_minute"] = int(config.get("RATE_LIMIT_PER_MINUTE", "60"))
    data["security"]["rate_limit"]["per_hour"] = int(config.get("RATE_LIMIT_PER_HOUR", "600"))
    data["admin"]["username"] = config.get("APP_ADMIN_USERNAME", "admin")
    data["admin"]["password"] = config.get("APP_ADMIN_PASSWORD", "")
    data["processing"]["max_workers"] = int(config.get("MAX_WORKERS", "8"))
    data["processing"]["file_processing_timeout"] = int(config.get("FILE_PROCESSING_TIMEOUT", "1200"))
    roots = config.get("INGESTION_ROOTS", "")
    if roots:
        data["ingestion"]["roots"] = [r.strip() for r in roots.split(";") if r.strip()]

    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass
    print(f"  {GREEN('✓')} config.json written")


# ── Phase: Install ──
def run_install(config: Dict[str, str]) -> bool:
    from core.installer import run_installation
    section("Installing")
    result = run_installation(config)
    for step in result.get("steps", []):
        ok = step.get("ok", False)
        mark = GREEN("✓") if ok else RED("✗")
        detail = step.get("detail", "")
        print(f"  {mark} {step['name']:28s} {detail}")
    if not result["ok"]:
        print(f"\n  {RED('✗')} {result.get('error', 'Installation failed')}")
    return result["ok"]


# ── Phase: Verify ──
def run_verify(config: Dict[str, str]) -> bool:
    from core.installer import verify_installation
    section("Verification")
    result = verify_installation(
        host=config.get("DB_HOST", "localhost"),
        port=int(config.get("DB_PORT", "5432")),
        user=config.get("DB_USER", "postgres"),
        password=config.get("DB_PASSWORD", ""),
        database=config.get("DB_NAME", "analysis"),
    )
    for check in result.get("checks", []):
        ok = check.get("ok", False)
        mark = GREEN("✓") if ok else RED("✗")
        print(f"  {mark} {check['check']:20s} {check.get('detail', '')}")
    return result["ok"]


# ── Summary ──
def print_summary(install_ok: bool, verify_ok: bool, config: Dict[str, str]):
    section("Summary")
    if install_ok and verify_ok:
        print(f"\n  {GREEN(BOLD('✅ Installation completed successfully!'))}")
        print(f"\n  Start: {CYAN('start.bat')} (Windows) or {CYAN('bash start.sh')} (Unix)")
        port = config.get("FLASK_PORT", "5000")
        print(f"  Open:  {CYAN(f'http://127.0.0.1:{port}')}")
    else:
        print(f"\n  {RED(BOLD('❌ Installation completed with errors.'))}")
        print(f"  Fix the issues above, then re-run: {CYAN('python install.py')}")
        print(f"  Or use the web wizard: {CYAN('http://127.0.0.1:5000/setup')}")


# ── Main ──
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="SYLTHARAE — CLI Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            For normal installation, use the web wizard at http://127.0.0.1:5000/setup.
            This CLI is for headless/CI environments, automation, and recovery.

            Examples:
              python install.py                    Full interactive install
              python install.py --check            Prerequisites only
              python install.py --configure        Reconfigure
              python install.py --verify           Verification only
              python install.py --non-interactive  CI/unattended
        """))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--env", choices=["development", "staging", "production"])
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        if ENV_FILE.exists():
            load_dotenv(ENV_FILE, override=False, interpolate=False)
    except ImportError:
        pass

    ni = args.non_interactive
    if args.env:
        os.environ["FLASK_ENV"] = args.env

    print(f"\n{BOLD(CYAN('╔══════════════════════════════════════════════════════════╗'))}")
    print(f"{BOLD(CYAN('║       SYLTHARAE — CLI Installation                  ║'))}")
    print(f"{BOLD(CYAN('╚══════════════════════════════════════════════════════════╝'))}")

    if args.check:
        ok = run_system_checks(ni) and run_dependency_check(ni)
        return 0 if ok else 1

    if args.configure:
        config = gather_config(ni)
        from core.installer import write_env_file
        write_env_file(config)
        write_config_json(config)
        print(f"\n  {GREEN('✓')} Configuration saved. Restart the server to apply.")
        return 0

    if args.verify:
        config = gather_config(ni)
        ok = run_verify(config)
        return 0 if ok else 1

    # Full install
    if not run_system_checks(ni):
        print(f"\n  {RED('Fix prerequisite issues above before continuing.')}")
        return 1

    if not run_dependency_check(ni):
        return 1

    _save_state(["prerequisites"])

    config = gather_config(ni)
    from core.installer import write_env_file, apply_config_to_environ
    write_env_file(config)
    apply_config_to_environ(config)
    write_config_json(config)
    _save_state(["prerequisites", "configuration"])

    ok = run_install(config)
    _save_state(["prerequisites", "configuration", "install"])

    verify_ok = run_verify(config) if ok else False

    print_summary(ok, verify_ok, config)
    if ok and verify_ok:
        _clear_state()
    return 0 if (ok and verify_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
