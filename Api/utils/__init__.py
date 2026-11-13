"""
Utility functions for the Api application

This package re-exports functions from Api.utils.py and submodules
for backward compatibility with package-style imports.

When importing from Api.utils, Python will use this package.
This file properly re-exports all functions from the parent Api/utils.py module.
"""

# Direct imports from submodules
from .title_similarity import (
    group_similar_titles,
    find_similar_titles
)

from .title import (
    filter_titles_by_search,
    display_titles_sorted
)

# Import from the parent utils.py module file directly
# Since Api/utils is both a package (this file) and a module (utils.py),
# we need to import the module file directly
import sys
from pathlib import Path
import importlib.util

# Get the utils.py file in the same directory as this __init__.py
# __file__ is Api/utils/__init__.py, so parent is Api/utils/
_utils_dir = Path(__file__).parent
_utils_py_path = _utils_dir / "utils.py"

# Import the utils.py module directly and re-export all functions
if _utils_py_path.exists():
    # Load the module from the file
    spec = importlib.util.spec_from_file_location("Api.utils_module", _utils_py_path)
    if spec and spec.loader:
        _utils_module = importlib.util.module_from_spec(spec)
        # Add to sys.modules to prevent re-importing
        sys.modules['Api.utils_module'] = _utils_module
        spec.loader.exec_module(_utils_module)
        
        # Re-export all public functions from utils.py (those not starting with _)
        # This ensures all functions are available through Api.utils
        for name in dir(_utils_module):
            if not name.startswith('_'):
                globals()[name] = getattr(_utils_module, name)
else:
    # Fallback: raise error if utils.py doesn't exist
    raise ImportError(f"Api/utils.py not found at {_utils_py_path}")
