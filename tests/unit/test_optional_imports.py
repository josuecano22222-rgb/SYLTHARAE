"""A failed ``import cv2`` must not leave the interpreter changed.

Round 22 found the shape of this: OpenCV inserts its own package directory into
``sys.path`` **before** it loads its binaries, and restores the path only when
the load succeeds. In the locked environment the load fails (``libGL.so.1`` is
absent), so the directory stays on ``sys.path`` for the rest of the process -
and a later ``from version import get_version`` then imports ``cv2/version.py``.
That is how 222 tests began failing in two seeded orders: every page that
rendered the release line answered 500.

``core.app_version`` removed the symptom by not resolving the version through
``sys.path`` at all. This module removes the cause: the product asks for OpenCV
through ``core.optional_imports.import_opencv()``, which either returns a working
module or returns ``None`` having put ``sys.path``, ``sys.modules`` and OpenCV's
own loader attribute back exactly as they were.

The fake package below copies OpenCV's sequence (insert the directory, import a
submodule, then fail) so the test does not depend on this machine's OpenCV
being broken - or on it being installed at all.
"""

from __future__ import annotations

import pathlib
import re
import re
import sys
import textwrap

import pytest

from core.optional_imports import import_opencv

PRODUCT_ROOTS = ("core", "reader_file", "pipeline", "services", "apps", "Api",
                 "settings", "database", "tools")

#: Copies the shape of OpenCV's loader: the directory goes on ``sys.path`` and
#: ``sys.OpenCV_LOADER`` is set before the binary is loaded, and the failure
#: happens after both.
FAKE_OPENCV_FAILS = textwrap.dedent('''
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.OpenCV_LOADER = True
    import cv2.version  # noqa: F401  - a real submodule, really imported

    raise ImportError("libGL.so.1: cannot open shared object file: No such file or directory")
''')

FAKE_OPENCV_WORKS = textwrap.dedent('''
    __version__ = "5.0.0-fake"
    from . import version  # noqa: F401
''')

FAKE_VERSION_MODULE = '__version__ = "5.0.0-fake"\n'


def _install_fake_cv2(tmp_path: pathlib.Path, source: str) -> pathlib.Path:
    """A ``cv2`` package on ``sys.path`` whose ``__init__`` does ``source``."""
    package = tmp_path / "cv2"
    package.mkdir()
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (package / "version.py").write_text(FAKE_VERSION_MODULE, encoding="utf-8")
    return package


def _forget_cv2() -> None:
    for name in [m for m in sys.modules if m == "cv2" or m.startswith("cv2.")]:
        del sys.modules[name]
    if hasattr(sys, "OpenCV_LOADER"):
        delattr(sys, "OpenCV_LOADER")


@pytest.fixture(autouse=True)
def _clean_after():
    before_path = list(sys.path)
    yield
    sys.path[:] = before_path
    _forget_cv2()


def test_a_failing_import_really_does_leave_the_directory_on_the_path(tmp_path, monkeypatch):
    """The hazard, reproduced - without this the guard below proves nothing."""
    package = _install_fake_cv2(tmp_path, FAKE_OPENCV_FAILS)
    _forget_cv2()
    # Ahead of the real site-packages cv2, which this machine may well have.
    monkeypatch.syspath_prepend(str(tmp_path))
    before_path = list(sys.path)
    with pytest.raises(ImportError):
        import cv2  # noqa: F401

    assert str(package) in sys.path, "the leak did not reproduce; the guard is untested"
    # The half-imported package is visible by its submodules: ``cv2`` itself
    # never lands in ``sys.modules`` because its __init__ raised.
    assert "cv2.version" in sys.modules
    assert hasattr(sys, "OpenCV_LOADER")

    # Put the interpreter back for the rest of the session.
    sys.path[:] = before_path
    _forget_cv2()


def test_the_guarded_import_leaves_the_interpreter_as_it_found_it(tmp_path, monkeypatch):
    package = _install_fake_cv2(tmp_path, FAKE_OPENCV_FAILS)
    _forget_cv2()
    monkeypatch.syspath_prepend(str(tmp_path))
    before_path = list(sys.path)

    assert import_opencv() is None

    assert sys.path == before_path, "a failed import must not change sys.path"
    assert str(package) not in sys.path
    assert [m for m in sys.modules if m == "cv2" or m.startswith("cv2.")] == []
    assert not hasattr(sys, "OpenCV_LOADER")


def test_the_guarded_import_returns_a_working_module(tmp_path, monkeypatch):
    _install_fake_cv2(tmp_path, FAKE_OPENCV_WORKS)
    _forget_cv2()
    monkeypatch.syspath_prepend(str(tmp_path))
    before_path = list(sys.path)

    module = import_opencv()

    assert module is not None
    assert module.__version__ == "5.0.0-fake"
    assert sys.path == before_path


def test_a_working_opencv_is_reported_as_working():
    """Against whatever this machine has: usable, or honestly absent."""
    module = import_opencv()
    if module is None:
        pytest.skip("cv2 cannot load in this environment; the failure path is covered above")
    assert hasattr(module, "__version__")


def test_no_product_module_imports_cv2_directly():
    """The guard is worthless if the call sites do not use it."""
    pattern = re.compile(r"^\s*(?:import\s+cv2\b|from\s+cv2[\s.])", re.MULTILINE)
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for directory in PRODUCT_ROOTS:
        for path in sorted((root / directory).rglob("*.py")):
            if path.name == "optional_imports.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in pattern.finditer(text):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(root)}:{line}")
    assert offenders == [], (
        "these import cv2 directly, so a failed load leaves sys.path changed: "
        + ", ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# The same hazard, for every other bare import in the product
# --------------------------------------------------------------------------- #
#: Modules the product imports by bare name, resolved through ``sys.path``: the
#: application's own packages (the repository root is on the path at runtime)
#: plus the error module. Each one is shadowable by a stray directory in the
#: same way ``version`` was - ``cv2`` used to be the stray directory.
SHADOWABLE = ("core", "database", "pipeline", "reader_file", "services",
              "settings", "config", "concurrency", "Hdg_Err_Ex_Log")


def test_the_application_packages_resolve_inside_the_repository():
    import importlib

    root = pathlib.Path(__file__).resolve().parents[2]
    resolved, outside = {}, []
    for name in SHADOWABLE:
        module = importlib.import_module(name)
        path = getattr(module, "__file__", None)
        resolved[name] = path
        if path is None or not str(pathlib.Path(path).resolve()).startswith(str(root)):
            outside.append(f"{name} -> {path}")
    assert outside == [], "a bare import resolved outside the repository: " + ", ".join(outside)


def test_no_product_import_leaves_a_package_directory_on_the_path():
    """The generic signature of the cv2 leak: an entry *inside* site-packages.

    Measured as a before/after around the product's own imports, not as a
    statement about the whole process: third-party packages do this too - the
    ``odf`` wheel inserts its own directory every time it is imported, which is
    the same hazard and is outside this repository's control (ticketed in the
    round's report). What can be checked here is that the product adds none.
    """
    import sysconfig

    before = list(sys.path)
    import core.ocr.engines  # noqa: F401
    import reader_file.readers.read_img_fast  # noqa: F401
    import reader_file.readers.read_video  # noqa: F401

    site_packages = pathlib.Path(sysconfig.get_paths()["purelib"]).resolve()
    added = [entry for entry in sys.path if entry not in before]
    offenders = [entry for entry in added
                 if entry and pathlib.Path(entry).resolve().is_relative_to(site_packages)
                 and pathlib.Path(entry).resolve() != site_packages]
    assert offenders == [], (
        "importing the product added these sys.path entries inside a package "
        "directory, where they can shadow a later import: " + ", ".join(offenders))


def test_the_session_wide_path_is_reported_not_asserted():
    """Informational: a third-party leak is a ticket, not a red test here."""
    import sysconfig

    site_packages = pathlib.Path(sysconfig.get_paths()["purelib"]).resolve()
    inside = sorted({entry for entry in sys.path
                     if entry and pathlib.Path(entry).resolve().is_relative_to(site_packages)
                     and pathlib.Path(entry).resolve() != site_packages})
    if inside:
        print("sys.path entries inside site-packages in this process: "
              + ", ".join(inside))


def test_the_version_module_is_still_not_resolved_through_the_path():
    """The round-22 fix, guarded from this side as well as its own."""
    import core.app_version

    resolved = pathlib.Path(core.app_version.version_module().__file__).resolve()
    root = pathlib.Path(__file__).resolve().parents[2]
    assert resolved == (root / "version.py").resolve()


# --------------------------------------------------------------------------- #
# The same pattern for a package that appends its own directory on import
# --------------------------------------------------------------------------- #
def test_odf_does_append_its_own_directory_to_the_path():
    """The hazard, from the third-party side - it is not this repository's bug."""
    import importlib

    for name in [m for m in sys.modules if m == "odf" or m.startswith("odf.")]:
        del sys.modules[name]
    before = list(sys.path)
    try:
        importlib.import_module("odf.table")
        leaked = [entry for entry in sys.path if entry not in before]
        assert any(entry.endswith("/odf") or entry.endswith("\\odf") for entry in leaked), (
            "odf no longer appends its directory; the guard is now unnecessary "
            "(remove it, do not leave dead code)")
    finally:
        sys.path[:] = before
        for name in [m for m in sys.modules if m == "odf" or m.startswith("odf.")]:
            del sys.modules[name]


def test_the_guarded_import_of_odf_leaves_the_path_alone():
    from core.optional_imports import import_clean

    before = list(sys.path)
    module = import_clean("odf.table")
    assert hasattr(module, "Table")
    assert sys.path == before, "the guarded import left a directory on sys.path"


def test_the_guarded_import_restores_the_path_when_the_import_fails():
    from core.optional_imports import import_clean

    before = list(sys.path)
    with pytest.raises(ImportError):
        import_clean("odf.this_submodule_does_not_exist")
    assert sys.path == before


def test_the_office_reader_uses_the_guard_rather_than_a_bare_odf_import():
    source = (pathlib.Path(__file__).resolve().parents[2]
              / "reader_file" / "readers" / "read_office.py").read_text(encoding="utf-8")
    bare = [line for line in source.splitlines()
            if re.match(r"^\s*from odf[.\s]|^\s*import odf\b", line)]
    assert bare == [], (
        "these import odf directly, which appends site-packages/odf to sys.path: "
        + "; ".join(bare))
    assert '_odf("' in source


def test_reading_a_spreadsheet_leaves_no_package_directory_on_the_path():
    """End to end, through the reader's own helper."""
    import reader_file.readers.read_office as read_office

    import sysconfig

    site_packages = pathlib.Path(sysconfig.get_paths()["purelib"]).resolve()
    before = list(sys.path)
    read_office._odf("odf.table")
    added = [entry for entry in sys.path if entry not in before]
    assert [entry for entry in added
            if pathlib.Path(entry).resolve().is_relative_to(site_packages)] == []
