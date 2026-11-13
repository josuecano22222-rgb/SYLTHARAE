"""
Path utilities for project setup
Path utilities - Independent functions for path operations

Provides setup_path function for configuring Python path
No dependencies on other project modules.
"""

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def setup_path(file_path: Optional[str] = None, levels_up: int = 0) -> Path:
    """
    Set up Python path by adding project root to sys.path
    
    Args:
        file_path: Path to the file calling this function (usually __file__)
        levels_up: Number of directory levels to go up from file_path to reach project root
    
    Returns:
        Path object to project root
    """
    import sys
    
    try:
        if file_path is None:
            file_path = __file__
        
        current_file = Path(file_path).resolve()
        project_root = current_file.parent
        
        # Go up the specified number of levels
        for _ in range(levels_up):
            project_root = project_root.parent
        
        project_root_str = str(project_root)
        
        # Add to sys.path if not already there
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)
        
        return project_root
    except Exception:
        # Fallback to current working directory
        return Path.cwd()



def get_extraction_base_folder(base_path: Optional[Path] = None) -> Path:
    """
    Get base folder for file extractions.
    Creates the folder if it doesn't exist.
    
    Args:
        base_path: Optional base path for extraction folder.
                   If None, uses EXTRACTION_FOLDER environment variable or current directory.
    
    Returns:
        Path object to extraction base folder
    """
    if base_path:
        extraction_base = Path(base_path)
    else:
        import os
        extraction_folder_env = os.getenv('EXTRACTION_FOLDER')
        if extraction_folder_env:
            extraction_base = Path(extraction_folder_env)
        else:
            # Phase 19: derive from the application data root, never the CWD,
            # so behaviour is identical regardless of launch directory.
            from core.app_paths import get_extracted_dir
            extraction_base = get_extracted_dir()
    
    extraction_base.mkdir(parents=True, exist_ok=True)
    return extraction_base


def get_extraction_name_file(
    file_path: str,
    extension: Optional[str] = None,
    base_path: Optional[Path] = None
) -> str:
    """Folder where the children of ``file_path`` are materialised.

    The name is derived from the source file's *absolute* path, so it is:

    * **stable** - the same source file always maps to the same folder, which
      makes extraction idempotent and lets a retry reuse that location;
    * **O(1)** - the previous implementation searched for a free numeric suffix
      with ``while extract_to.exists()``, so with N folders already sharing a
      name it performed N ``stat`` calls to mint folder N+1. Corpora routinely
      contain thousands of archives with the same basename (``data.zip``,
      ``report.zip``), which made this quadratic in the number of archives -
      billions of syscalls over a multi-million-file run;
    * **unique per source file** - a digest of the absolute path keeps two
      same-named archives in different directories apart, which is the property
      the numeric suffix was protecting.

    Args:
        file_path: Path to the source file.
        extension: Optional extension to strip from the name. An empty string
            means "no extension to strip" (``Path.stem``), which is what
            embedded-object extraction passes.
        base_path: Optional extraction root; defaults to the application data
            root's ``extracted`` directory.

    Returns:
        Path of the folder (as a string). The caller creates it.
    """
    path = Path(file_path)
    name = path.name

    if extension:
        folder_name = name[:-len(extension)] if name.endswith(extension) else name
    else:
        folder_name = path.stem

    try:
        source_key = str(path.resolve())
    except Exception:  # pragma: no cover - resolve can fail on unusual mounts
        source_key = str(path)
    digest = hashlib.sha1(source_key.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:12]

    extraction_base = get_extraction_base_folder(base_path)
    return str(extraction_base / f"{folder_name}__{digest}")


def reset_extraction_dir(extraction_path: str) -> None:
    """Make ``extraction_path`` an empty directory, creating it if needed.

    Extraction must publish the *complete* member set of a source file. If the
    folder already holds members from an earlier attempt (a crash mid-way
    through extraction, or a different archive that once occupied the same
    name), leaving them in place would ingest stale children that do not belong
    to the current source - the same class of error as reusing a stale hash.
    Callers therefore reset the folder immediately before extracting into it.
    """
    target = Path(extraction_path)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        for child in target.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError as exc:  # pragma: no cover - permission/lock edge
                logger.warning("Could not clear %s from extraction folder: %s",
                               child, exc)
    target.mkdir(parents=True, exist_ok=True)

