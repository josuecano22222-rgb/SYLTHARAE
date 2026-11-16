"""The Windows directory flush, tested off Windows.

``os.open(dir, os.O_RDONLY)`` fails on Windows, so the POSIX branch of
``_fsync_dir`` did nothing there: a journal's contents were fsync'd but its
name was not, and a power cut could lose the whole file.

This tests the ctypes replacement with an injected stand-in for ctypes, so the
call sequence, the flags and every failure path are pinned down on any
platform. It does NOT prove durability against a real power cut - that is
stated in the module docstring and cannot be done from a Linux sandbox.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

import pytest

from core.checkpoint_manager import _fsync_dir, _fsync_dir_windows

FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = -1
#: Distinct from None, which is itself a handle value we want to test.
_UNSET = object()


class _FakeWintypes(types.ModuleType):
    """Just enough of ctypes.wintypes to declare the argtypes."""

    def __init__(self):
        super().__init__("wintypes")
        self.LPCWSTR = "LPCWSTR"
        self.DWORD = "DWORD"
        self.LPVOID = "LPVOID"
        self.HANDLE = "HANDLE"
        self.BOOL = "BOOL"


class _FakeCtypes(types.ModuleType):
    """A stand-in for ctypes that records what was called."""

    def __init__(self, *, flush_result=1, handle=_UNSET, create_raises=None):
        super().__init__("ctypes")
        self.wintypes = _FakeWintypes()
        self.calls = []
        self.flush_result = flush_result
        # Default to a plausible non-NULL, non-invalid handle.
        self.handle = 0x1234 if handle is _UNSET else handle
        self.create_raises = create_raises

        self._kernel32 = self._build_kernel32()
        self.WinDLL = lambda name, use_last_error=False: self._kernel32

    def c_void_p(self, value):
        box = types.SimpleNamespace(value=value)
        return box

    def _build_kernel32(self):
        outer = self

        class _FuncPtr:
            """Stands in for a ctypes function pointer.

            Real ctypes function pointers accept .argtypes/.restype, but bound
            methods do not, so this must be an instance of a callable class.
            """

            def __init__(self, fn):
                self.fn = fn

            def __call__(self, *args, **kwargs):
                return self.fn(*args, **kwargs)

        def create_file(path, access, share, sec, disp, flags, tmpl):
            outer.calls.append(("CreateFileW", path, access, share, disp, flags))
            if outer.create_raises:
                raise outer.create_raises
            return outer.handle

        def flush(handle):
            outer.calls.append(("FlushFileBuffers", handle))
            return outer.flush_result

        def close(handle):
            outer.calls.append(("CloseHandle", handle))
            return 1

        return types.SimpleNamespace(
            CreateFileW=_FuncPtr(create_file),
            FlushFileBuffers=_FuncPtr(flush),
            CloseHandle=_FuncPtr(close),
        )


def test_the_directory_is_opened_with_backup_semantics_and_flushed():
    folder = tempfile.mkdtemp()
    fake = _FakeCtypes()

    assert _fsync_dir_windows(folder, fake) is True

    names = [c[0] for c in fake.calls]
    assert names == ["CreateFileW", "FlushFileBuffers", "CloseHandle"], names

    _, path, access, share, disp, flags = fake.calls[0]
    assert path == str(folder)
    # The whole point: without this flag Windows refuses a directory handle.
    assert flags & FILE_FLAG_BACKUP_SEMANTICS, (
        "FILE_FLAG_BACKUP_SEMANTICS is required to open a directory on Windows"
    )
    assert disp == 3, "OPEN_EXISTING"

    # The handle is flushed and closed even though FlushFileBuffers succeeded.
    _, flushed = fake.calls[1]
    assert flushed == fake.handle
    _, closed = fake.calls[2]
    assert closed == fake.handle


def test_the_handle_is_closed_even_when_the_flush_fails():
    folder = tempfile.mkdtemp()
    fake = _FakeCtypes(flush_result=0)

    assert _fsync_dir_windows(folder, fake) is False
    names = [c[0] for c in fake.calls]
    assert "CloseHandle" in names, "a failed flush must still release the handle"


@pytest.mark.parametrize("handle", [0, None, INVALID_HANDLE_VALUE])
def test_an_invalid_handle_returns_false_without_flushing(handle):
    folder = tempfile.mkdtemp()
    fake = _FakeCtypes(handle=handle)

    assert _fsync_dir_windows(folder, fake) is False
    names = [c[0] for c in fake.calls]
    assert "FlushFileBuffers" not in names, (
        "flushing an invalid handle would be a no-op at best"
    )


def test_a_raising_createfile_returns_false_instead_of_propagating():
    """A durability nicety must never break ingestion."""
    folder = tempfile.mkdtemp()
    fake = _FakeCtypes(create_raises=OSError("access denied"))

    assert _fsync_dir_windows(folder, fake) is False


def test_a_missing_wintypes_returns_false():
    """Off Windows ctypes has no wintypes, so this must degrade quietly."""
    folder = tempfile.mkdtemp()
    fake = _FakeCtypes()
    del fake.wintypes

    assert _fsync_dir_windows(folder, fake) is False


def test_fsync_dir_uses_the_windows_path_only_on_windows(monkeypatch):
    """On POSIX the Windows call must not be attempted at all."""
    folder = tempfile.mkdtemp()
    called = []

    def fake_windows(path, ctypes_module=None):
        called.append(path)
        return True

    import core.checkpoint_manager as checkpoint_manager

    monkeypatch.setattr(checkpoint_manager, "_fsync_dir_windows", fake_windows)

    monkeypatch.setattr(os, "name", "posix")
    _fsync_dir(folder)
    assert called == [], "POSIX must use os.open, not CreateFileW"

    monkeypatch.setattr(os, "name", "nt")
    _fsync_dir(folder)
    assert called == [folder], "Windows must attempt the directory flush"


def test_fsync_dir_falls_back_when_the_windows_flush_fails(monkeypatch):
    """A failed Windows flush must fall through, not stop the run."""
    folder = tempfile.mkdtemp()

    import core.checkpoint_manager as checkpoint_manager

    monkeypatch.setattr(checkpoint_manager, "_fsync_dir_windows", lambda *a, **k: False)
    monkeypatch.setattr(os, "name", "nt")

    opened = {}
    real_open = os.open

    def spy_open(path, flags, *a, **k):
        opened["path"] = path
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", spy_open)
    _fsync_dir(folder)

    assert opened.get("path") == str(folder), (
        "after a failed Windows flush the POSIX attempt must still be tried"
    )
