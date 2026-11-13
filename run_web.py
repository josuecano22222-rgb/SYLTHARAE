"""
Entry point for the web application.

This script validates the deployment settings (apps/web/deployment.py), then
serves the Flask application: with Flask's built-in server by default
Background jobs run as threads inside this one process, so run exactly one
process per database; multi-process servers such as Gunicorn or uWSGI with
several workers are not supported.

pybabel extract -F babel.cfg -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations -l ar
# translate new fuzzy entries, then:
pybabel compile -d translations
pybabel extract -F babel.cfg -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations -l ar
# translate new fuzzy entries, then:
pybabel compile -d translations

Usage:
    python run_web.py
"""

import sys
import os
from pathlib import Path

# Windows-native console safety: when stdout/stderr are redirected (service
# install, pipe, scheduled task) Python falls back to the legacy ANSI code
# page and the engine's progress output (emoji) would raise
# UnicodeEncodeError inside worker threads. Force UTF-8 with replacement.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass

# Line-buffer stdout on every platform so print() lines flush atomically;
# merged stdout/stderr logs then interleave only at line boundaries, never
# mid-line ("Progress: 2/11INFO:werkzeug...").
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except (AttributeError, ValueError, OSError):
        pass

# Auto-install check: Only run if AUTO_INSTALL is not disabled
# The job scheduler (step 20) runs in this same single process; tests and
# import-only processes keep it off by not going through run_web.py.
os.environ.setdefault('SYLTHARAE_SCHEDULER', '1')
# point the database is reconciled with it at startup, so an edit made while
# the service was down reaches the database without anybody running a command.
# Set SYLTHARAE_TAXONOMY_SYNC=check to only be told, or =0 to turn it off.
os.environ.setdefault('SYLTHARAE_TAXONOMY_SYNC', 'apply')
# The gazetteer seed (data/gazetteer/places_seed.json) is loaded into the empty
# tables the baseline migration creates, so the place detector works on a fresh
# install. Idempotent; set SYLTHARAE_GAZETTEER_SYNC=check to only report, or =0
# to turn it off. Logic lives in services/geo/gazetteer_startup.py.
os.environ.setdefault('SYLTHARAE_GAZETTEER_SYNC', 'apply')

if os.environ.get('AUTO_INSTALL', '1') == '1':
    try:
        try:
            import flask
            import psycopg2
        except ImportError:
            print("=" * 60)
            print("Dependencies not found. Attempting auto-install...")
            print("=" * 60)
            
            in_venv = hasattr(sys, 'real_prefix') or (
                hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
            )
            
            if not in_venv:
                print("WARNING: Not in virtual environment.")
                print("Please run: setup.bat or start.bat")
                print("=" * 60)
            else:
                wheels_dir = Path("wheels")
                if wheels_dir.exists():
                    import subprocess
                    print("Installing missing dependencies from local wheels...")
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--no-index", 
                         "--find-links=wheels", "flask", "psycopg2-binary", "sqlalchemy"],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        print("Dependencies installed successfully!")
                        print("=" * 60)
                        import importlib
                        importlib.invalidate_caches()
                    else:
                        print("WARNING: Auto-install failed. Please run setup.bat manually")
                        print("=" * 60)
                else:
                    print("ERROR: wheels\\ directory not found!")
                    print("Please run download_dependencies.bat first (with internet)")
                    print("=" * 60)
    except Exception as e:
        print(f"Auto-install check failed: {e}")
        print("Continuing with startup...")

# Simplified initialization
from core.init import (
    setup_project_path,
    initialize_settings,
    initialize_database_config,
    initialize_system
)

# Set up project path
project_root = setup_project_path(__file__)

# Initialize settings
initialize_settings(project_root)

# Load database configuration
initialize_database_config()

# Initialize system
initialize_system()

# Import and run the web app
from apps.web.app import app

#: Exit status when start-up validation refuses the settings. The systemd unit
#: (deploy/systemd/syltharae.service) does not restart on it.
EXIT_REFUSED = 2


def main():
    """Console entry point (pyproject: file-analysis-web = run_web:main)."""
    from apps.web.deployment import DeploymentError

    try:
        globals()['_run_main']()
    except DeploymentError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(EXIT_REFUSED)


def _run_main():
    # SEC-10: debug mode is environment-controlled and never hardcoded.
    # Start-up validation refuses invalid or dangerous settings (debug mode in
    # production among them) and warns about unsafe production ones.
    from apps.web.deployment import enforce

    enforce(printer=print)  # raises DeploymentError (a RuntimeError) on errors
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')
    if debug_mode:
        print("[WARNING] Flask debug mode is ENABLED - development use only")

    # Compute policy: report the effective mode (and the layer that set it)
    # before serving. The web application still starts when the configured mode
    # cannot be honoured - an operator must be able to reach the UI to fix the
    # setting - but the problem is stated here and every workload that requires
    # the missing device is refused by the gateway rather than silently run on
    # the CPU.
    try:
        from core.compute import policy as compute_policy

        selection = compute_policy.mode_selection()
        support = compute_policy.mode_support(selection)
        compute_policy.record(selection, support)
        compute_policy.emit(selection, support, printer=print)
        if not support.ok:
            print(f"[WARNING] {support.problem}")
            if support.action:
                print(f"[ACTION] {support.action}")
    except Exception as policy_exc:  # never block startup over the banner
        print(f"[WARNING] Compute policy could not be resolved: {policy_exc}")

    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', '5000'))
    print(f"[OK] Starting web server on http://127.0.0.1:{port} (press CTRL+C to stop)")
    from apps.web.serve import serve

    serve(app, host, port, debug=debug_mode)


if __name__ == '__main__':
    # `python run_web.py` must start the server (previously it imported the
    # app and exited silently, leaving beginners at an empty prompt).
    main()
