"""
Entry point for the CLI application.

DEPRECATED as an operational interface: the web frontend is the primary
control surface (Input / Ingestion page). This script is kept as a thin
compatibility adapter over the same IngestionService the frontend uses -
no business logic lives here anymore.
"""

import sys
import multiprocessing
from pathlib import Path

# Windows-native console safety (redirected output uses legacy code pages)
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

# Set multiprocessing start method (Windows uses 'spawn' by default)
if __name__ == '__main__':
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        pass  # Already set, ignore

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

if __name__ == '__main__':
    # Compatibility adapter: with arguments -> non-interactive service run;
    # with no arguments -> the legacy interactive flow (deprecated).
    #
    # ``--compute-mode`` is a global processing policy, accepted by both
    # front-ends: on its own it preselects the mode and still opens the
    # interactive flow, which is how the operator switches policy for a
    # manual run without editing data/settings.json.
    from apps.cli.main import cli_main, extract_compute_mode_flag, main

    try:
        requested_mode, remaining = extract_compute_mode_flag(sys.argv[1:])
    except ValueError as mode_error:
        print(f"[ERROR] {mode_error}")
        sys.exit(1)

    if any(arg.startswith('-') for arg in remaining):
        # The non-interactive adapter parses and applies the flag itself.
        sys.exit(cli_main())

    if requested_mode is not None:
        # Pin the policy without printing: the interactive flow reports the
        # effective mode (and refuses an impossible one) once, at startup.
        try:
            from core.compute.policy import set_mode_override

            set_mode_override(requested_mode,
                              source=f"command line (--compute-mode {requested_mode})")
        except ValueError as mode_error:
            print(f"[ERROR] {mode_error}")
            sys.exit(1)
    sys.exit(main() or 0)
else:
    # When imported by multiprocessing child process, ensure path is set up
    setup_project_path()
