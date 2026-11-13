"""Where the "this system is installed" marker lives.

The marker decides whether the operator sees the application or the setup
wizard. It used to be ``Path('.system_initialized')`` - a path relative to the
current working directory - which contradicts the invariant stated in
``core/app_paths.py``:

    "The application must behave identically regardless of the directory from
     which it is launched; no module may rely on the current working
     directory."

Measured consequence: a fully migrated database with an administrator, started
with ``run_web.py`` from a directory that has no marker, serves the Installation
Wizard instead of the application. Every deployment that starts the server with
a ``WorkingDirectory=`` other than the one the installer used sees this.

The tests below pin the location to the data root, prove the answer does not
change when the process changes directory, and keep a marker written by an
existing installation (in the checkout, by the old code) working.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """An isolated application data root for this test."""
    import core.app_paths as app_paths
    import core.initialization as init

    root = tmp_path / "appdata"
    monkeypatch.setenv("APP_DATA_DIR", str(root))
    app_paths.set_data_root(root)
    monkeypatch.setattr(init, "INIT_MARKER_FILE", None)
    yield root
    app_paths.reset_cache()


def test_the_default_marker_is_absolute_and_under_the_data_root(data_root):
    import core.initialization as init

    marker = init.marker_path()
    assert marker.is_absolute(), marker
    assert marker.parent == data_root.resolve(), marker


def test_the_marker_does_not_depend_on_the_working_directory(data_root, tmp_path, monkeypatch):
    """Mark from one directory, ask from another: the answer must not change."""
    import core.initialization as init

    first = tmp_path / "install-cwd"
    first.mkdir()
    monkeypatch.chdir(first)
    assert init.is_system_initialized() is False

    init.mark_system_initialized()
    assert init.is_system_initialized() is True
    assert not (first / ".system_initialized").exists(), (
        "the marker was written into the working directory")

    second = tmp_path / "service-cwd"
    second.mkdir()
    monkeypatch.chdir(second)
    assert init.is_system_initialized() is True, (
        "an installed system looked uninstalled from another directory - the "
        "server would serve the setup wizard to a working deployment")


def test_a_marker_from_an_existing_installation_still_counts(data_root, tmp_path, monkeypatch):
    """Back-compat: the old code wrote the marker next to the checkout."""
    import core.initialization as init

    legacy = tmp_path / "legacy" / ".system_initialized"
    legacy.parent.mkdir()
    legacy.touch()
    monkeypatch.setattr(init, "_LEGACY_MARKER_DIRS", (legacy.parent,))

    assert init.is_system_initialized() is True, (
        "an installation upgraded from the previous release was told to run "
        "setup again")
    # ... and the next mark moves it to the data root, so the legacy copy is
    # no longer needed
    init.mark_system_initialized()
    assert init.marker_path().exists()


def test_no_marker_anywhere_means_not_initialized(data_root, monkeypatch):
    import core.initialization as init

    monkeypatch.setattr(init, "_LEGACY_MARKER_DIRS", ())
    assert init.is_system_initialized() is False


def test_the_marker_is_not_written_into_the_checkout_by_default(data_root, monkeypatch):
    """The suite must not leave ``.system_initialized`` in the working tree."""
    import core.initialization as init

    monkeypatch.chdir(PROJECT_ROOT)
    init.mark_system_initialized()
    assert not (PROJECT_ROOT / ".system_initialized").exists()
    assert os.environ["APP_DATA_DIR"] in str(init.marker_path())
