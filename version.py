"""
Centralized Version Management
Single source of truth for application versioning
"""

__version__ = "2.2.0"
__version_info__ = (2, 2, 0)

# Build metadata
__build_date__ = "2026-09-26"
__python_requires__ = ">=3.10"  # pyproject.toml requires-python

# Component versions (for reference)
COMPONENT_VERSIONS = {
    "application": __version__,
    "python_min": "3.10",
    "python_max": "3.12",
}

def get_version():
    """Get the application version string"""
    return __version__

def get_version_info():
    """Get the version as a tuple"""
    return __version_info__

def get_full_version():
    """Get full version string with metadata"""
    return f"{__version__} (Python {__python_requires__})"

