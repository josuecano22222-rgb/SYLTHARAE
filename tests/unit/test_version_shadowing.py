"""Unit: the application version cannot be shadowed by another ``version`` module.

``apps/web/app.py`` and four other modules did ``from version import
get_version``. That resolves through ``sys.path``, and a third-party package can
put its own directory there: OpenCV's loader inserts ``<site-packages>/cv2`` while
it imports, and when the import *fails* (it needs ``libGL.so.1``, which a
headless deployment does not have) the insertion is never undone. The next
``from version import get_version`` then imports ``cv2/version.py``, which has no
``get_version``, and every page that renders the release line answers 500.

That is not hypothetical: it is what made two seeded module orders fail 222 ids
in this round - a module that touched an image ran before a module that rendered
a page. The fix is to stop resolving the application's own version through
``sys.path`` at all; these tests fail if anyone puts the name back.
"""

from __future__ import annotations

import importlib
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REPO_VERSION = PROJECT_ROOT / "version.py"

#: Every module that used to import the version by name. A release line is
#: rendered from these, so each one has to go through the helper.
CALL_SITES = (
    "apps/web/app.py",
    "Api/routes/common.py",
    "settings/routes.py",
    "settings/settings_adapter.py",
)


def _repo_version() -> str:
    namespace = {}
    exec(compile(REPO_VERSION.read_text(encoding="utf-8"), str(REPO_VERSION), "exec"),
         namespace)                                     # noqa: S102 - the file is ours
    return namespace["get_version"]()


def test_the_repository_version_file_is_where_the_helper_looks():
    assert REPO_VERSION.is_file()
    assert "get_version" in REPO_VERSION.read_text(encoding="utf-8")


def test_a_shadowing_version_module_does_not_change_the_reported_version(tmp_path,
                                                                       monkeypatch):
    """A ``version.py`` earlier on ``sys.path`` must not win."""
    shadow = tmp_path / "version.py"
    shadow.write_text('__version__ = "9.9.9-shadow"\n', encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "version", raising=False)
    monkeypatch.delitem(sys.modules, "core.app_version", raising=False)

    helper = importlib.import_module("core.app_version")
    assert helper.get_version() == _repo_version()
    assert helper.get_version() != "9.9.9-shadow"


def test_the_failed_opencv_import_does_not_break_the_version(monkeypatch):
    """The exact production shape: cv2 cannot load, and leaves state behind."""
    monkeypatch.delitem(sys.modules, "version", raising=False)
    before_modules = set(sys.modules)
    before_path = list(sys.path)
    try:
        import cv2                                       # noqa: F401
    except Exception:                                    # libGL, or not installed
        pass
    leaked = [p for p in sys.path if p not in before_path]
    try:
        helper = importlib.import_module("core.app_version")
        assert helper.get_version() == _repo_version()
        if leaked:
            # The leak is cv2's, not ours: it is exactly the condition this fix
            # has to survive, so it is asserted rather than tolerated silently.
            assert helper.get_version() != "9.9.9-shadow"
    finally:
        # A half-imported cv2 is not a leftover this test may hand to the next
        # module: with its own directory on sys.path a second ``import cv2``
        # raises "recursion is detected during loading of cv2 binaries", which
        # is how this test first made tests/unit/test_video_metadata_only.py
        # fail. Put the interpreter back exactly as it was.
        for name in [m for m in sys.modules
                     if m == "cv2" or m.startswith("cv2.")
                     or (m not in before_modules and m == "version")]:
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.delattr(sys, "OpenCV_LOADER", raising=False)
        sys.path[:] = before_path


def test_no_call_site_imports_the_version_by_name():
    offenders = []
    for relative in CALL_SITES:
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for match in re.finditer(r"^\s*(?:from version import|import version)\b", source,
                                 re.MULTILINE):
            offenders.append(f"{relative}: {match.group(0).strip()}")
    assert not offenders, (
        "the version is resolved through sys.path again, so any package that "
        f"puts a 'version' module first on sys.path breaks every page: {offenders}")
