"""The application version, loaded from the repository's own ``version.py``.

Why not ``from version import get_version``? Because that resolves through
``sys.path``, and a third-party package can put a directory there that contains
a module called ``version``. OpenCV does exactly that: its loader inserts
``<site-packages>/cv2`` while importing, and when the import fails - it needs
``libGL.so.1``, absent from a headless deployment - the insertion is never
undone. The next ``from version import get_version`` then imports
``cv2/version.py``, which has no ``get_version``, and every page that renders the
release line answers 500.

Measured in this round: two seeded module orders failed 222 ids from one module
that touched an image running before a module that rendered a page.

The file is loaded once, by path, under a private module name, so nothing that
appears on ``sys.path`` can change which code answers "what version is this?".
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from types import ModuleType
from typing import Optional

from core.app_paths import get_project_root

_LOCK = threading.Lock()
_MODULE: Optional[ModuleType] = None


def version_module() -> ModuleType:
    """The repository's ``version.py``, loaded by path (once per process)."""
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    with _LOCK:
        if _MODULE is not None:
            return _MODULE
        path = Path(get_project_root()) / "version.py"
        spec = importlib.util.spec_from_file_location("syltharae_version", path)
        if spec is None or spec.loader is None:      # pragma: no cover - defensive
            raise ImportError(f"cannot load the application version from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULE = module
        return module


def get_version() -> str:
    """``"SYLTHARAE 2.2.0 (2026-09-26)"`` - the string every page footer shows."""
    return version_module().get_version()
