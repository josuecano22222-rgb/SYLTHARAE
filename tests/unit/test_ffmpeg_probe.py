"""The ffmpeg probe: it must test what it claims, cheaply, and never hang.

The reader asks one question before it reads a container - "is there an ffmpeg
here that works?" - and the answer decides whether a media test passes, skips or
fails. Round 22 found the old answer wrong in the expensive direction
(``shutil.which`` accepted a binary that exits non-zero, and three tests failed
with a message about the reader), so the probe now runs the binary. That opens
two new ways to be wrong, and both are pinned here:

* a binary that **hangs** must cost one timeout, not a hung read;
* the probe must not pay a subprocess on every read - the answer is cached.

The measured overhead is asserted, not described: the cached call is timed below.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from reader_file.readers import read_video  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """A clean cache per test; a leaked one would answer for the wrong binary."""
    monkeypatch.setattr(read_video, "_FFMPEG_USABLE", {})
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    yield


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_a_missing_binary_is_reported_as_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read_video.ffmpeg_path() is None


def test_a_binary_that_exits_non_zero_is_not_accepted(tmp_path, monkeypatch):
    _script(tmp_path, "ffmpeg", "exit 1")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read_video.ffmpeg_path() is None


def test_a_binary_that_hangs_costs_one_timeout_and_is_rejected(tmp_path, monkeypatch):
    """A hung probe must not hang the read, and must be remembered as unusable."""
    hanging = _script(tmp_path, "ffmpeg", "sleep 30")
    monkeypatch.setattr(read_video, "_FFMPEG_PROBE_TIMEOUT", 0.3)
    started = time.monotonic()
    assert read_video._runs(str(hanging)) is False
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"the probe waited {elapsed:.1f}s; the timeout did not apply"
    assert read_video._FFMPEG_USABLE[str(hanging)] is False, (
        "a hung binary has to be remembered, or every read pays the timeout again")


def test_a_hanging_binary_on_the_path_does_not_hang_ffmpeg_path(tmp_path, monkeypatch):
    _script(tmp_path, "ffmpeg", "sleep 30")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(read_video, "_FFMPEG_PROBE_TIMEOUT", 0.3)
    started = time.monotonic()
    assert read_video.ffmpeg_path() is None
    assert time.monotonic() - started < 5


def test_a_working_binary_is_accepted(tmp_path, monkeypatch):
    working = _script(tmp_path, "ffmpeg", 'echo "ffmpeg version 7.0.2-static"')
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read_video.ffmpeg_path() == str(working)


def test_the_probe_is_cached_and_the_read_path_pays_no_subprocess(tmp_path, monkeypatch):
    """The second call must not run anything: it is on every media read."""
    working = _script(tmp_path, "ffmpeg", 'echo "ffmpeg version 7.0.2-static"')
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read_video.ffmpeg_path() == str(working)

    calls = []
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)
    for _ in range(100):
        assert read_video.ffmpeg_path() == str(working)
    assert calls == [], f"the read path ran a subprocess {len(calls)} times"


def test_the_cached_call_is_cheap():
    """Measured, with the number asserted so a regression is visible.

    The threshold is generous on purpose (a shared runner is slow); the point is
    that the call is a dict lookup and not a process spawn, which costs
    milliseconds even for ``/bin/true``.
    """
    read_video.ffmpeg_path()  # warm: whatever this machine has, the answer is cached
    started = time.monotonic()
    for _ in range(1000):
        read_video.ffmpeg_path()
    per_call_ms = (time.monotonic() - started)
    assert per_call_ms < 1.0, (
        f"1,000 cached probes took {per_call_ms:.3f}s ({per_call_ms:.3f} ms/call); "
        "the answer is not cached")


def test_an_unusable_binary_is_probed_once_per_process(tmp_path, monkeypatch):
    """Cost of the wrong answer is bounded: one probe, then the cache."""
    broken = _script(tmp_path, "ffmpeg", "exit 1")
    monkeypatch.setenv("PATH", str(tmp_path))
    calls = []
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        calls.append(args[0] if args else None)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)
    for _ in range(5):
        assert read_video.ffmpeg_path() is None
    assert len(calls) == 1, f"the unusable binary was probed {len(calls)} times"
