"""Round 18: audio reads failed on files ffmpeg describes perfectly.

Five corpus audio files returned ERROR and stored nothing: two AAC files
("sync not found", "can't sync to MPEG frame"), a truncated MP3 (empty message),
and two MP4 audio containers ("truncated data"). mutagen is a tag parser, not a
container parser, so a file whose tags it cannot reach is a file it refuses
whole - even when the duration, codec and tags are still readable.

They are read through the same ffmpeg probe the video reader uses now, the
failure is recorded as ``decoder_error``, and the result says that speech was
not transcribed. A file ffmpeg cannot describe either remains an honest error:
``testMP3truncated.mp3`` is one, and the last test pins that it stays that way
rather than being quietly reported as a successful read.
"""
import os

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "..", "test-doc")
AUDIO = os.path.join(CORPUS, "Audio")


def _require_ffmpeg():
    """Skip when no runnable ffmpeg binary is installed.

    The recovery these tests describe *is* the ffmpeg probe: without a binary
    there is nothing to recover with, and a failure would say "the reader is
    broken" about a machine that cannot read the container at all. The guard is
    the reader's own ``ffmpeg_path()``, so it cannot disagree with the code
    under test - including about a binary that exists but does not run.
    """
    from reader_file.readers.read_video import ffmpeg_path

    if ffmpeg_path() is None:
        pytest.skip("no ffmpeg binary available")


def _reader():
    from reader_file.readers.read_audio import AudioFileReader

    return AudioFileReader()


def _stored_text(result):
    from pipeline.storage_pipeline import StoragePipeline

    content = result.get("content", result)
    return StoragePipeline._extract_text_from_content.__get__(
        object.__new__(StoragePipeline))(content) or ""


@pytest.mark.parametrize("name", ["testAAC.aac", "testAACid3.aac",
                                  "testMP4_drm.m4a", "testMP4_truncated.m4a"])
def test_a_refused_tag_parse_still_yields_container_metadata(name):
    _require_ffmpeg()
    path = os.path.join(AUDIO, name)
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    result = _reader().read_audio_file(path)
    assert not result.get("error"), result.get("error")
    assert result.get("metadata_only") is True
    assert result.get("metadata_source") == "ffmpeg"
    assert result.get("speech_extracted") is False
    assert "speech" in result["extraction_note"].lower()


def test_the_tag_reader_failure_is_kept_on_the_record():
    _require_ffmpeg()
    path = os.path.join(AUDIO, "testAAC.aac")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    result = _reader().read_audio_file(path)
    assert result.get("decoder_error")
    assert "mutagen" in result["extraction_note"] or "tag reader" in result["extraction_note"]


def test_the_recovered_metadata_is_stored():
    _require_ffmpeg()
    path = os.path.join(AUDIO, "testAAC.aac")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    stored = _stored_text(_reader().read_audio_file(path))
    assert "Duration:" in stored
    assert "Speech: not extracted" in stored


def test_a_successful_tag_read_also_states_no_speech():
    _require_ffmpeg()
    path = os.path.join(AUDIO, "testFLAC.flac")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    result = _reader().read_audio_file(path)
    assert not result.get("error")
    assert result["speech_extracted"] is False
    assert result["extraction_scope"] == "container_metadata"


def test_a_file_ffmpeg_cannot_describe_remains_an_error():
    path = os.path.join(AUDIO, "testMP3truncated.mp3")
    if not os.path.exists(path):
        pytest.skip("corpus file missing")
    result = _reader().read_audio_file(path)
    if not result.get("error"):
        pytest.skip("ffmpeg described this container after all")
    assert "audio" in result["error"].lower()
