"""
Audio file reader - Extract metadata from audio files
Aligned with database design principles - all functions within class
Supports: MP3, WAV, FLAC, OGG, M4A, WMA, AAC, AIFF
"""

import os
import logging
from typing import Dict, Any, Optional, Set
from pathlib import Path

from .base_reader import BaseReader
from .read_video import NO_SPEECH_NOTE, probe_container_metadata

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

logger = logging.getLogger(__name__)


class AudioFileReader(BaseReader):
    """
    Reader for audio files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported audio extensions"""
        return {
            '.mp3', '.wav', '.flac', '.ogg', '.m4a', 
            '.wma', '.aac', '.aiff', '.opus', '.webm',
            '.ape', '.wv', '.tta', '.tak', '.mpc'
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read audio file and extract metadata with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with audio metadata or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        
        try:
            return self.read_audio_file(file_path)
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def read_audio_file(self, filepath: str) -> Dict[str, Any]:
        """
        Extract metadata from audio file using mutagen with improved error handling
        
        Args:
            filepath: Path to audio file
        
        Returns:
            Dictionary with audio metadata including duration, bitrate, codec, tags
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
                context={'operation': 'read_audio_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_audio_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            result["file_size"] = os.path.getsize(filepath)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_audio_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        # Try mutagen (most comprehensive)
        try:
            from mutagen import File as MutagenFile
            
            audio = MutagenFile(filepath)
            
            if audio is None:
                result["error"] = "Could not read audio file"
                return result
            
            # Basic info
            if hasattr(audio, 'info'):
                info = audio.info
                result["duration_seconds"] = getattr(info, 'length', 0)
                result["duration_formatted"] = self._format_duration(result["duration_seconds"])
                result["bitrate"] = getattr(info, 'bitrate', 0)
                result["bitrate_formatted"] = f"{result['bitrate'] // 1000} kbps" if result["bitrate"] > 0 else "N/A"
                result["sample_rate"] = getattr(info, 'sample_rate', 0)
                result["channels"] = getattr(info, 'channels', 0)
                result["codec"] = getattr(info, 'codec', 'unknown')
                
                # Calculate approximate quality
                if result["bitrate"] > 320000:
                    result["quality"] = "Very High (>320kbps)"
                elif result["bitrate"] > 192000:
                    result["quality"] = "High (192-320kbps)"
                elif result["bitrate"] > 128000:
                    result["quality"] = "Medium (128-192kbps)"
                else:
                    result["quality"] = "Low (<128kbps)"
            
            # Tags/Metadata
            if audio.tags:
                result["tags"] = {}
                for key, value in audio.tags.items():
                    # Convert mutagen tag objects to strings
                    if isinstance(value, list):
                        result["tags"][str(key)] = [str(v) for v in value]
                    else:
                        result["tags"][str(key)] = str(value)
                
                # Extract common metadata
                result["title"] = self._get_tag(audio, ['TIT2', 'title', '©nam', 'TITLE'])
                result["artist"] = self._get_tag(audio, ['TPE1', 'artist', '©ART', 'ARTIST'])
                result["album"] = self._get_tag(audio, ['TALB', 'album', '©alb', 'ALBUM'])
                result["year"] = self._get_tag(audio, ['TDRC', 'date', '©day', 'DATE', 'YEAR'])
                result["genre"] = self._get_tag(audio, ['TCON', 'genre', '©gen', 'GENRE'])
                result["track_number"] = self._get_tag(audio, ['TRCK', 'tracknumber', 'trkn', 'TRACKNUMBER'])
                result["album_artist"] = self._get_tag(audio, ['TPE2', 'albumartist', 'aART', 'ALBUMARTIST'])
                result["composer"] = self._get_tag(audio, ['TCOM', 'composer', '©wrt', 'COMPOSER'])
                result["comment"] = self._get_tag(audio, ['COMM', 'comment', '©cmt', 'COMMENT'])
            
            result["extraction_scope"] = "container_metadata"
            result["speech_extracted"] = False
            result["extraction_note"] = NO_SPEECH_NOTE
            return result

        except ImportError as e:
            if self._container_metadata_fallback(
                    filepath, result, reason="mutagen not installed"):
                return result
            error_msg = "mutagen not installed. Install with: pip install mutagen"
            handle_error(
                e,
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_audio_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        except Exception as e:
            # mutagen refuses truncated and DRM-protected containers - five
            # corpus files returned "sync not found", "truncated data" or an
            # empty message - and each of those reads ended as an error with
            # nothing stored, though ffmpeg still reports the duration, the
            # codec and the tags. The container is probed before the read is
            # called a failure, and the tag reader's complaint is kept.
            if self._container_metadata_fallback(
                    filepath, result, reason=f"tag reader failed: {str(e)[:160]}"):
                result["decoder_error"] = str(e)[:500]
                return result
            error_msg = f"Error reading audio file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_audio_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result

    def _container_metadata_fallback(self, filepath: str, result: Dict[str, Any],
                                     reason: str) -> bool:
        """Fill ``result`` from ffmpeg's view of the container; False if it cannot.

        The video reader owns the probe and the wording of "no speech was
        extracted"; importing it keeps one implementation of both.
        """
        probed = probe_container_metadata(filepath)
        if not probed:
            return False
        result.update(probed)
        if result.get("duration_seconds"):
            result["duration_formatted"] = self._format_duration(
                result["duration_seconds"])
        result["metadata_only"] = True
        result["extraction_scope"] = "container_metadata"
        result["speech_extracted"] = False
        result["extraction_note"] = (
            f"Container metadata recovered with ffmpeg ({reason}); the tag reader "
            "did not accept this file. " + NO_SPEECH_NOTE
        )
        result["missing_dependency"] = f"{reason}: tag-level extraction unavailable"
        return True
    
    def _get_tag(self, audio, possible_keys):
        """Get tag value from audio file, trying multiple possible keys"""
        for key in possible_keys:
            try:
                if key in audio:
                    value = audio[key]
                    if isinstance(value, list) and value:
                        return str(value[0])
                    return str(value)
            except:
                continue
        return None
    
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


