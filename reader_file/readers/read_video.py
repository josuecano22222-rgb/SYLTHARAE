"""
Video file reader - Extract metadata from video files
Aligned with database design principles - all functions within class
Supports: MP4, AVI, MKV, MOV, WMV, FLV, WEBM
"""

import os
import logging
from typing import Dict, Any, Optional, Set
from pathlib import Path

from .base_reader import BaseReader
from core.optional_imports import import_opencv

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

logger = logging.getLogger(__name__)


#: What a video read can and cannot tell you.
#:
#: This reader decodes container and stream metadata. It does not transcribe
#: speech: no ASR engine ships with the bundle, and even with one installed the
#: honest claim is about the stream, not about what is said in it. Every result
#: therefore carries ``speech_extracted: False`` so a consumer - and the audit -
#: cannot mistake "we read the video" for "we understood the video".
EXTRACTION_SCOPE = "container_metadata"
NO_SPEECH_NOTE = (
    "No speech was extracted: this read covers container and stream metadata "
    "only, so dialogue, narration and on-screen speech are not searchable. "
    "Transcribe separately if the audio content matters."
)

#: Seconds to wait for ``ffmpeg -version``. A binary that hangs must cost one
#: timeout, not a hung read: the probe is on the read path of every media file a
#: machine without a working ffmpeg tries to open.
_FFMPEG_PROBE_TIMEOUT = 10


#: ``{path: bool}`` - whether this binary actually ran. A path is probed once,
#: so the subprocess cost is paid on the first media read and never again; the
#: read path itself calls only :func:`ffmpeg_path`, which hits this cache.
_FFMPEG_USABLE: Dict[str, bool] = {}


def _runs(binary: str, timeout: float = None) -> bool:
    """Does this program run and identify itself as ffmpeg?

    ``shutil.which`` answers "is a file with this name here and executable",
    which is not the same question. A broken symlink into a deleted virtualenv,
    a stub left on ``PATH``, or a build missing a shared library all pass
    ``which`` and then fail every invocation - and the caller sees a reader
    that stored nothing rather than an environment that cannot read. Round 22
    reproduced exactly that shape.
    """
    import subprocess

    if binary in _FFMPEG_USABLE:
        return _FFMPEG_USABLE[binary]
    usable = False
    try:
        completed = subprocess.run([binary, "-version"], capture_output=True,
                                   text=True,
                                   timeout=_FFMPEG_PROBE_TIMEOUT if timeout is None else timeout)
        usable = completed.returncode == 0 and "ffmpeg version" in (
            completed.stdout + completed.stderr).lower()
    except Exception:                                    # noqa: BLE001
        usable = False
    _FFMPEG_USABLE[binary] = usable
    return usable


def ffmpeg_path():
    """An ffmpeg binary that runs, or None.

    ``imageio-ffmpeg`` ships a static ffmpeg in its wheel, so a machine with no
    system ffmpeg still has one. Checked in that order because the system build
    may carry codecs the bundled one does not. A candidate is only returned if
    it actually runs (see :func:`_runs`); a program that exists but cannot read
    anything is reported as absent, so the caller skips instead of failing.
    """
    import shutil

    for candidate in ("ffmpeg", "ffmpeg.exe"):
        found = shutil.which(candidate)
        if found and _runs(found):
            return found
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and _runs(bundled):
            return bundled
    except Exception:                                    # noqa: BLE001
        pass
    return None


def _parse_ffmpeg_stderr(text: str) -> Dict[str, Any]:
    """Metadata out of ``ffmpeg -i``'s stderr banner.

    ffprobe would be neater, but the wheel that makes ffmpeg available at all
    (imageio-ffmpeg) does not ship ffprobe. The banner carries the same facts:
    duration, each stream's codec and geometry, and the container tags.
    """
    import re

    found: Dict[str, Any] = {}
    match = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)", text)
    if match:
        hours, minutes, seconds, fraction = match.groups()
        found["duration_seconds"] = (int(hours) * 3600 + int(minutes) * 60
                                     + int(seconds) + float("0." + fraction))
    match = re.search(r"Video:\s*([^,\s]+)", text)
    if match:
        found["codec"] = match.group(1)
    match = re.search(r"(\d{2,5})x(\d{2,5})", text)
    if match:
        found["width"], found["height"] = int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    if match:
        found["fps"] = float(match.group(1))
    match = re.search(r"Audio:\s*([^,\s]+)", text)
    if match:
        found["audio_codec"] = match.group(1)
    match = re.search(r"(\d{4,6}) Hz", text)
    if match:
        found["audio_sample_rate"] = int(match.group(1))
    if "Audio:" in text:
        found["has_audio"] = True
    elif "Video:" in text:
        found["has_audio"] = False
    tags: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        tag = re.match(r"^(title|artist|album|comment|creation_time|encoder|"
                       r"location|date)\s*:\s*(.+)$", stripped)
        if tag:
            tags[tag.group(1)] = tag.group(2).strip()
    if tags:
        found["tags"] = tags
    return found


def probe_container_metadata(filepath: str) -> Dict[str, Any]:
    """Container metadata from ffmpeg, or ``{}`` when no ffmpeg is available."""
    import subprocess

    binary = ffmpeg_path()
    if not binary:
        return {}
    try:
        completed = subprocess.run(
            [binary, "-hide_banner", "-nostdin", "-i", filepath],
            capture_output=True, text=True, timeout=_FFMPEG_PROBE_TIMEOUT,
        )
    except Exception as exc:  # timeout, missing binary, permission
        logger.debug("ffmpeg probe failed for %s: %s", filepath, exc)
        return {}
    # ffmpeg exits non-zero for "-i without an output" by design; the banner is
    # on stderr either way and is the whole point of the call.
    parsed = _parse_ffmpeg_stderr(completed.stderr or "")
    if parsed:
        parsed["metadata_source"] = "ffmpeg"
        parsed["ffmpeg"] = binary
    return parsed


def _add_quality(result: Dict[str, Any]) -> None:
    height = result.get("height") or 0
    if height >= 2160:
        result["quality"] = "4K (2160p)"
    elif height >= 1440:
        result["quality"] = "2K (1440p)"
    elif height >= 1080:
        result["quality"] = "Full HD (1080p)"
    elif height >= 720:
        result["quality"] = "HD (720p)"
    elif height >= 480:
        result["quality"] = "SD (480p)"
    else:
        result["quality"] = "Low Resolution"


class VideoFileReader(BaseReader):
    """
    Reader for video files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported video extensions"""
        return {
            '.mp4', '.avi', '.mkv', '.mov', '.wmv', 
            '.flv', '.webm', '.m4v', '.mpg', '.mpeg',
            '.3gp', '.ogv', '.ts', '.mts', '.m2ts',
            '.vob', '.rm', '.rmvb', '.asf', '.divx'
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read video file and extract metadata with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with video metadata or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        
        try:
            return self.read_video_file(file_path)
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def read_video_file(self, filepath: str) -> Dict[str, Any]:
        """
        Extract metadata from video file with improved error handling
        
        Args:
            filepath: Path to video file
        
        Returns:
            Dictionary with video metadata including duration, resolution, codec, bitrate
        """
        result: Dict[str, Any] = {
            "filepath": filepath,
            "format": Path(filepath).suffix.lower()
        }
        
        # Validate filepath
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            file_size = os.path.getsize(filepath)
            result["file_size"] = file_size
            result["file_size_formatted"] = self._format_file_size(file_size)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        # Try opencv first (most reliable and fastest). Imported through the
        # guard: a failed load leaves OpenCV's own directory on sys.path, where
        # it can shadow a later import in the same process (round 22).
        try:
            cv2 = import_opencv()
            if cv2 is None:
                raise ImportError("cv2 is not usable in this environment")

            cap = cv2.VideoCapture(filepath)
            
            if not cap.isOpened():
                raise Exception("Could not open video file")
            
            result["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            result["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            result["resolution"] = f"{result['width']}x{result['height']}"
            result["fps"] = cap.get(cv2.CAP_PROP_FPS)
            result["frame_count"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            if result["fps"] > 0:
                result["duration_seconds"] = result["frame_count"] / result["fps"]
                result["duration_formatted"] = self._format_duration(result["duration_seconds"])
            
            # Determine video quality based on resolution
            if result["height"] >= 2160:
                result["quality"] = "4K (2160p)"
            elif result["height"] >= 1440:
                result["quality"] = "2K (1440p)"
            elif result["height"] >= 1080:
                result["quality"] = "Full HD (1080p)"
            elif result["height"] >= 720:
                result["quality"] = "HD (720p)"
            elif result["height"] >= 480:
                result["quality"] = "SD (480p)"
            else:
                result["quality"] = "Low Resolution"
            
            # Try to get codec information
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            if fourcc > 0:
                codec_bytes = fourcc.to_bytes(4, byteorder='little')
                try:
                    result["codec"] = codec_bytes.decode('ascii').strip('\x00')
                except:
                    result["codec"] = "Unknown"
            
            cap.release()
            # OpenCV decodes frames but says nothing about the container's own
            # tags (title, creation_time, encoder) or about the audio stream.
            # One ffmpeg probe fills those in and never overrides what the
            # decoder measured.
            for key, value in probe_container_metadata(filepath).items():
                result.setdefault(key, value)
            result["extraction_scope"] = EXTRACTION_SCOPE
            result["speech_extracted"] = False
            result["extraction_note"] = NO_SPEECH_NOTE
            return result
            
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"opencv failed: {e}")
        
        # Try moviepy as an OPTIONAL fallback (not shipped: its 1.x line has
        # no wheel for the offline bundle and its 2.x line pins pillow<11,
        # conflicting with the Pillow security floor - step 27 packaging
        # audit). Environments that install it themselves get the richer
        # metadata; the OpenCV path above is the shipped behaviour.
        try:
            # AUDIT-DEP-02: moviepy 2.x removed ``moviepy.editor``; support both.
            try:
                from moviepy import VideoFileClip
            except ImportError:
                from moviepy.editor import VideoFileClip
            
            with VideoFileClip(filepath) as clip:
                result["duration_seconds"] = clip.duration
                result["duration_formatted"] = self._format_duration(clip.duration)
                result["fps"] = clip.fps
                result["width"] = clip.w
                result["height"] = clip.h
                result["resolution"] = f"{clip.w}x{clip.h}"
                result["has_audio"] = clip.audio is not None
                
                # Determine quality
                if result["height"] >= 2160:
                    result["quality"] = "4K (2160p)"
                elif result["height"] >= 1440:
                    result["quality"] = "2K (1440p)"
                elif result["height"] >= 1080:
                    result["quality"] = "Full HD (1080p)"
                elif result["height"] >= 720:
                    result["quality"] = "HD (720p)"
                elif result["height"] >= 480:
                    result["quality"] = "SD (480p)"
                else:
                    result["quality"] = "Low Resolution"
                
                if clip.audio:
                    result["audio_fps"] = clip.audio.fps
                    result["audio_channels"] = clip.audio.nchannels

            result["extraction_scope"] = EXTRACTION_SCOPE
            result["speech_extracted"] = False
            result["extraction_note"] = NO_SPEECH_NOTE
            return result
            
        except ImportError as e:
            # Neither OpenCV nor moviepy is installed. That used to be the end
            # of the read - "No video library installed" - even though the
            # container could still be inspected. A video whose duration,
            # resolution, codec and creation time are stored is worth more than
            # an error record, and the result says plainly which part was
            # skipped, so it is not mistaken for a full read.
            recovered = self._container_metadata_fallback(
                filepath, result, reason="no video decoder installed")
            if recovered:
                return result
            error_msg = (
                "No video library installed and no ffmpeg available: container "
                "metadata could not be read. Install with: pip install "
                "opencv-python, pip install moviepy, or install imageio-ffmpeg."
            )
            handle_error(
                e,
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            result["speech_extracted"] = False
            return result
        except Exception as e:
            err_text = str(e)
            if "video_fps" in err_text or (
                isinstance(e, KeyError) and str(e).strip("'\"") == "video_fps"
            ):
                try:
                    from .read_audio import AudioFileReader

                    audio = AudioFileReader().read_audio_file(filepath)
                    if isinstance(audio, dict) and not audio.get("error"):
                        audio["container_note"] = "audio_only_in_video_container"
                        return audio
                except Exception:
                    pass
            # A decoder that refuses a file is not the same as a file with no
            # metadata. moviepy raises on an audio-only MP4 and on several
            # QuickTime containers, and every one of those reads used to end as
            # an error with no duration, no resolution and no codec - though
            # ffmpeg describes all of them. The container is probed before the
            # read is declared a failure, and the decoder's complaint is kept
            # so the partial read is not mistaken for a successful decode.
            if self._container_metadata_fallback(
                    filepath, result, reason=f"decoder failed: {err_text[:160]}"):
                result["decoder_error"] = err_text[:500]
                return result
            error_msg = f"Error reading video file: {err_text}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result

    def _container_metadata_fallback(self, filepath: str, result: Dict[str, Any],
                                     reason: str) -> bool:
        """Fill ``result`` from ffmpeg's view of the container; False if impossible."""
        probed = probe_container_metadata(filepath)
        if not probed:
            return False
        # A decoder may have got as far as the stream geometry before failing;
        # ffmpeg's reading is the container's own and replaces it.
        result.update(probed)
        if result.get("width") and result.get("height"):
            result["resolution"] = f"{result['width']}x{result['height']}"
            _add_quality(result)
        if result.get("duration_seconds"):
            result["duration_formatted"] = self._format_duration(
                result["duration_seconds"])
        result["metadata_only"] = True
        result["extraction_scope"] = EXTRACTION_SCOPE
        result["speech_extracted"] = False
        result["extraction_note"] = (
            f"Container metadata recovered with ffmpeg ({reason}); frames were "
            "not decoded. " + NO_SPEECH_NOTE
        )
        result["missing_dependency"] = (
            f"{reason}: frame-level extraction unavailable")
        return True
    
    def _format_duration(self, seconds):
        """Format duration in HH:MM:SS"""
        if not seconds or seconds <= 0:
            return "00:00"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
    
    def _format_file_size(self, size_bytes):
        """Format file size in human-readable format"""
        if size_bytes is None:
            return "N/A"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"



