"""
Entry point for the domain import utility.

DEPRECATED as an operational interface: the web Import Center
(/operations/import) is the primary surface for domain data imports.
This script is a thin compatibility adapter over the same
DomainImportService the frontend uses.

Usage:
    python run_import.py [--data-file NAME]
"""

import sys
from pathlib import Path

# Windows-native console safety (redirected output uses legacy code pages)
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

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


def main():
    """Thin adapter: parse args, construct DomainImportRequest, run service."""
    import argparse

    parser = argparse.ArgumentParser(description='Import domain classification data')
    parser.add_argument('--data-file', type=str,
                        help='Data file name located in the import data directory')
    args = parser.parse_args()

    from services.importing.domain_import_service import (
        DomainImportRequest, DomainImportService,
    )

    service = DomainImportService()
    request = DomainImportRequest(data_file=args.data_file)
    try:
        request = service.validate(request)
    except Exception as exc:
        print(f"Invalid import request: {exc}")
        sys.exit(1)

    def progress(snapshot):
        phase = snapshot.get("current_phase") or ""
        pct = snapshot.get("percent")
        if pct is not None:
            print(f"[{pct:>3}%] {phase}")

    result = service.run(request, progress_cb=progress)
    if result.success:
        print(f"[OK] Domain import complete: {result.stats}")
        sys.exit(0)
    print("[ERROR] Domain import failed - see server logs for details")
    sys.exit(1)


if __name__ == '__main__':
    main()
