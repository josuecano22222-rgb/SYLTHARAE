"""Round 18, task 5 (F-29): a video read is not all-or-nothing.

The reader tried OpenCV, then moviepy, then gave up with "No video library
installed". Two things were wrong with that:

* the container is readable without either decoder - duration, resolution,
  codec, sample rate and the container's own tags say a great deal about a
  video, and ffmpeg is available offline (the ``imageio-ffmpeg`` wheel ships a
  static binary), so the failure was a missing fallback, not missing data;
* nothing in the result ever said that *speech* was not transcribed. A caller
  reading "duration 30s, 1920x1080" could reasonably assume the video had been
  understood. Every path now states ``speech_extracted: False`` and names the
  scope of the read.

Fixtures are the corpus videos (read, never written) plus one generated clip,
so the tests exercise real containers rather than header stubs.
"""
import os
import subprocess
import sys

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")
VIDEO_DIR = os.path.join(CORPUS, "Videos")
SAMPLE = os.path.join(VIDEO_DIR, "testMP4VideoAudio.mp4")

from reader_file.readers import read_video  # noqa: E402
from reader_file.readers.read_video import (  # noqa: E402
    VideoFileReader,
    _parse_ffmpeg_stderr,
    ffmpeg_path,
    probe_container_metadata,
)


@pytest.fixture
def no_decoders(monkeypatch):
    """Make `import cv2` and `import moviepy` fail the way a bare machine does."""
    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setitem(sys.modules, "moviepy", None)
    monkeypatch.setitem(sys.modules, "moviepy.editor", None)


def _require(path):
    if not os.path.exists(path):
        pytest.skip(f"corpus file missing: {path}")


def _require_opencv():
    """Skip when OpenCV cannot actually be imported.

    ``pytest.importorskip("cv2")`` is not enough: the module *exists* in the
    locked environment, it just fails to load its binaries (``libGL.so.1`` is
    absent for the GUI wheel). pytest re-raises an ``ImportError`` from an
    existing module, so the test failed instead of skipping - and the failed
    load leaves OpenCV's own directory on ``sys.path``, which makes a later
    ``import cv2`` raise "recursion is detected". Both are undone here so a
    skip leaves the interpreter as it found it.
    """
    import sys

    before_path = list(sys.path)
    try:
        import cv2                                       # noqa: F401
    except Exception as exc:                             # noqa: BLE001 - the reason
        for name in [m for m in sys.modules if m == "cv2" or m.startswith("cv2.")]:
            sys.modules.pop(name, None)
        sys.path[:] = before_path
        if hasattr(sys, "OpenCV_LOADER"):
            delattr(sys, "OpenCV_LOADER")
        pytest.skip(f"cv2 is installed but cannot load in this environment: {exc}")
    return True


def _require_ffmpeg():
    """Skip when no ffmpeg binary is installed.

    The container-metadata path needs the binary, which is not a Python
    package and so is not carried by any lock file. Every test that reads a
    real container through ffmpeg uses this, so a machine without one reports
    a skip instead of a failure that says nothing about the reader.
    """
    if ffmpeg_path() is None:
        pytest.skip("no ffmpeg binary available")


# --------------------------------------------------------------------------- #
# ffmpeg banner parsing
# --------------------------------------------------------------------------- #
BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Metadata:
    major_brand     : isom
    creation_time   : 2021-04-05T09:08:07.000000Z
    title           : Quarterly review
  Duration: 00:01:23.45, start: 0.000000, bitrate: 812 kb/s
  Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1280x720, 812 kb/s, 25 fps, 25 tbr
  Stream #0:1(und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 96 kb/s
"""


def test_the_banner_yields_duration_geometry_and_streams():
    parsed = _parse_ffmpeg_stderr(BANNER)
    assert parsed["duration_seconds"] == pytest.approx(83.45, abs=0.01)
    assert (parsed["width"], parsed["height"]) == (1280, 720)
    assert parsed["fps"] == 25.0
    assert parsed["codec"].startswith("h264")
    assert parsed["audio_codec"].startswith("aac")
    assert parsed["audio_sample_rate"] == 44100
    assert parsed["has_audio"] is True


def test_container_tags_are_read_not_discarded():
    parsed = _parse_ffmpeg_stderr(BANNER)
    assert parsed["tags"]["title"] == "Quarterly review"
    assert parsed["tags"]["creation_time"].startswith("2021-04-05")


def test_a_silent_stream_is_reported_as_having_no_audio():
    parsed = _parse_ffmpeg_stderr(
        "  Duration: 00:00:05.00, start: 0.0, bitrate: 100 kb/s\n"
        "  Stream #0:0: Video: h264, yuv420p, 320x240, 30 fps\n")
    assert parsed["has_audio"] is False
    assert parsed["width"] == 320


# --------------------------------------------------------------------------- #
# No decoder installed: metadata only, and it says so
# --------------------------------------------------------------------------- #
def test_no_decoder_still_returns_metadata(no_decoders):
    _require(SAMPLE)
    if ffmpeg_path() is None:
        pytest.skip("no ffmpeg binary available")
    result = VideoFileReader().read_video_file(SAMPLE)
    assert "error" not in result, result.get("error")
    assert result.get("metadata_only") is True
    assert result.get("metadata_source") == "ffmpeg"
    assert result["width"] > 0 and result["height"] > 0
    assert result["resolution"] == f"{result['width']}x{result['height']}"
    assert result["duration_seconds"] > 0
    assert result["duration_formatted"]
    assert result.get("codec")


def test_no_decoder_states_that_no_speech_was_extracted(no_decoders):
    _require(SAMPLE)
    if ffmpeg_path() is None:
        pytest.skip("no ffmpeg binary available")
    result = VideoFileReader().read_video_file(SAMPLE)
    assert result["speech_extracted"] is False
    assert result["extraction_scope"] == "container_metadata"
    assert "speech" in result["extraction_note"].lower()
    assert result.get("missing_dependency")


def test_without_ffmpeg_either_the_read_fails_honestly(no_decoders, monkeypatch):
    _require(SAMPLE)
    monkeypatch.setattr(read_video, "ffmpeg_path", lambda: None)
    result = VideoFileReader().read_video_file(SAMPLE)
    assert result.get("error")
    assert "ffmpeg" in result["error"]
    assert result["speech_extracted"] is False


def test_a_file_that_is_not_a_video_does_not_crash_the_reader(no_decoders, tmp_path):
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"this is not a container")
    result = VideoFileReader().read_video_file(str(bogus))
    assert isinstance(result, dict)
    assert result.get("error") or result.get("speech_extracted") is False


# --------------------------------------------------------------------------- #
# The decoder path makes the same claim
# --------------------------------------------------------------------------- #
def test_the_opencv_path_also_says_no_speech_was_extracted():
    _require(SAMPLE)
    _require_opencv()
    result = VideoFileReader().read_video_file(SAMPLE)
    assert "error" not in result, result.get("error")
    assert result["speech_extracted"] is False
    assert result["extraction_scope"] == "container_metadata"
    assert "speech" in result["extraction_note"].lower()


def test_probing_a_missing_file_returns_nothing_rather_than_raising(tmp_path):
    assert probe_container_metadata(str(tmp_path / "absent.mp4")) == {}


# --------------------------------------------------------------------------- #
# 3. The metadata has to survive the storage boundary
# --------------------------------------------------------------------------- #
def _stored_text(reader_result):
    from pipeline.storage_pipeline import StoragePipeline

    content = reader_result.get("content", reader_result)
    return StoragePipeline._extract_text_from_content.__get__(
        object.__new__(StoragePipeline))(content) or ""


def test_video_metadata_reaches_the_stored_text():
    """The readers reported duration and codec; the store kept 0 characters."""
    _require(SAMPLE)
    _require_ffmpeg()
    result = VideoFileReader().read_video_file(SAMPLE)
    stored = _stored_text(result)
    assert stored, "a media read that yields metadata stored nothing"
    assert f"Resolution: {result['width']}x{result['height']}" in stored
    assert "Duration:" in stored
    assert "Codec" in stored or "codec" in stored.lower()


def test_the_stored_text_says_that_speech_was_not_extracted():
    _require(SAMPLE)
    _require_ffmpeg()
    stored = _stored_text(VideoFileReader().read_video_file(SAMPLE))
    assert "Speech: not extracted" in stored


def test_container_tags_are_stored_when_present(tmp_path):
    # Only the binary matters to the test. `ffmpeg_path()` already resolves one
    # (system build first, then the `imageio-ffmpeg` wheel), so importing the
    # wheel here made the test need a package the lock file does not carry -
    # and failed outright when a system ffmpeg was present but the wheel was
    # not installed.
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        pytest.skip("no ffmpeg binary available")

    clip = tmp_path / "tagged.mp4"
    completed = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5", "-t", "1",
         "-pix_fmt", "yuv420p", "-metadata", "title=Evidence clip", str(clip)],
        capture_output=True, text=True)
    if completed.returncode != 0 or not clip.exists():
        pytest.skip(f"could not build a fixture clip: {completed.stderr[:200]}")
    stored = _stored_text(VideoFileReader().read_video_file(str(clip)))
    assert "Tag title: Evidence clip" in stored, stored


def test_media_metadata_is_in_provenance_not_only_in_text():
    from pipeline.storage_pipeline import StoragePipeline

    _require(SAMPLE)
    _require_ffmpeg()
    result = VideoFileReader().read_video_file(SAMPLE)
    provenance = StoragePipeline._build_extraction_provenance.__get__(
        object.__new__(StoragePipeline))(result)
    media = (provenance or {}).get("media") or {}
    assert media.get("speech_extracted") is False
    assert media.get("resolution")
    assert media.get("duration_seconds")


def test_a_decoder_failure_falls_back_to_the_container():
    """moviepy refuses several containers; the read is not a failure because of it.

    Called on the fallback itself, because whether moviepy is installed decides
    which path a corpus file takes and the corpus contains both kinds: an
    audio-only MP4 and several QuickTime containers raise inside moviepy today.
    """
    _require(SAMPLE)
    if ffmpeg_path() is None:
        pytest.skip("no ffmpeg binary available")
    reader = VideoFileReader()
    result = {"filepath": SAMPLE, "format": ".mp4"}
    recovered = reader._container_metadata_fallback(
        SAMPLE, result, reason="decoder failed: first frame unreadable")
    assert recovered is True
    assert result["metadata_only"] is True
    assert result["speech_extracted"] is False
    assert "decoder failed" in result["extraction_note"]
    assert result["resolution"]


# --------------------------------------------------------------------------- #
# A binary on PATH is not the same as a usable binary
# --------------------------------------------------------------------------- #
def test_a_binary_that_cannot_run_is_not_reported_as_usable(tmp_path, monkeypatch):
    """`shutil.which` answers "is a file here", not "does it work".

    Round 22 could not reproduce three failures recorded in an earlier run of
    this module and found the condition that produces them: a program named
    `ffmpeg` sitting on `PATH` that exits non-zero. The guard passed (a binary
    existed), the reader then produced nothing, and three tests failed with
    "a media read that yields metadata stored nothing" - a message about the
    reader, caused by the environment. A guard has to test what it claims.
    """
    stub = tmp_path / "ffmpeg"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    # A machine may also have the bundled wheel; the question here is only
    # whether the *stub* is accepted, so the wheel is made unavailable.
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    import importlib

    import reader_file.readers.read_video as read_video

    read_video = importlib.reload(read_video)
    try:
        assert read_video.ffmpeg_path() is None, (
            "a binary that exits non-zero was accepted as usable ffmpeg")
    finally:
        importlib.reload(read_video)


def test_a_working_binary_is_still_found(tmp_path, monkeypatch):
    """The check must not reject the real one."""
    stub = tmp_path / "ffmpeg"
    stub.write_text("#!/bin/sh\necho 'ffmpeg version 7.0.2-static'\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    import importlib

    import reader_file.readers.read_video as read_video

    read_video = importlib.reload(read_video)
    try:
        assert read_video.ffmpeg_path() == str(stub)
    finally:
        importlib.reload(read_video)
