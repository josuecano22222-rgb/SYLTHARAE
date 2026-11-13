"""
Archive file reader - Safe extraction (SEC-05)

Supports: ZIP, TAR (.tar/.tar.gz/.tar.bz2/.tar.xz), GZ, BZ2, RAR, 7Z.

All extraction goes through :mod:`core.archive_safety`, which enforces
path-traversal rejection, symlink/hardlink refusal, depth / file-count /
byte / ratio limits and timeouts. The legacy implementation used
``extractall`` (zip-slip / tar-slip vulnerable) and is replaced entirely.
"""

from typing import Dict, Any, Optional, Set
from pathlib import Path

from core.path_utils import get_extraction_name_file, reset_extraction_dir
from core import archive_safety
from core.archive_safety import ArchiveEncrypted, ArchiveSafetyError

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

import logging
logger = logging.getLogger(__name__)


class ArchiveFileReader(BaseReader):
    """
    Reader for archive files.

    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """

    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported archive extensions"""
        return {
            '.zip',
            '.tar',
            '.gz',
            '.bz2',
            '.xz',
            '.rar',
            '.7z',
            # Compound spellings: the tar layer inside a compressed stream.
            '.tgz',    # tar + gzip
            '.tbz2',   # tar + bzip2
            '.txz',    # tar + xz
        }

    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read archive file and extract contents safely.

        Args:
            file_info: Dictionary containing file information with 'path' key

        Returns:
            Dictionary with extraction path or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))

        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)

        try:
            extraction_path = None

            if ext == '.zip':
                extraction_path = self.extract_zip(file_path)
            elif ext in ('.tar', '.tar.gz', '.tar.bz2', '.tar.xz'):
                extraction_path = self.extract_tar(file_path, ext)
            elif ext == '.tgz':
                extraction_path = self.extract_tar(file_path, '.tar.gz')
            elif ext == '.tbz2':
                extraction_path = self.extract_tar(file_path, '.tar.bz2')
            elif ext == '.txz':
                extraction_path = self.extract_tar(file_path, '.tar.xz')
            elif ext == '.gz':
                extraction_path = self.extract_gz(file_path)
            elif ext == '.bz2':
                extraction_path = self.extract_bz2(file_path)
            elif ext == '.xz':
                extraction_path = self.extract_xz(file_path)
            elif ext == '.rar':
                extraction_path = self.extract_rar(file_path)
            elif ext == '.7z':
                extraction_path = self.extract_7z(file_path)
            else:
                error_msg = f"Unsupported archive type: {ext or Path(file_path).suffix}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")

            # STANDARDIZED: Always return dict
            if extraction_path:
                result, extraction_path = extraction_path
                files = getattr(result, "files_extracted", 0) if result else 0
                payload = {
                    "extraction_path": extraction_path,
                    "status": "success",
                    "archive_type": ext.lstrip('.'),
                    # archive_safety measures these; without surfacing them a
                    # 1000-member archive and one that yielded nothing are
                    # indistinguishable downstream.
                    "files_extracted": files,
                    "bytes_extracted": getattr(result, "bytes_extracted", 0) if result else 0,
                    "skipped_members": list(getattr(result, "skipped", []) or []),
                }
                if getattr(result, "decoder_missing", False):
                    # Part of this archive needed a decoder this machine does
                    # not have. Members stored uncompressed were still read, so
                    # this is a real result with a bounded gap: it is reported
                    # as content (the container's own manifest) rather than
                    # discarded as "Extraction failed", and the unreadable
                    # members are named with their reason. Only RAR needs an
                    # external decoder, so no other format is affected.
                    result_notes = list(getattr(result, "notes", []) or [])
                    payload["decoder_missing"] = True
                    payload["members_total"] = getattr(result, "members_total", None)
                    payload["members_unreadable"] = dict(
                        getattr(result, "members_unreadable", None) or {}
                    )
                    payload["text"] = self._undecoded_container_text(
                        file_path, ext, result, result_notes
                    )
                    hint = getattr(result, "decoder_hint", None)
                    payload["extraction_info"] = {
                        "extracted": files > 0,
                        "stored": True,
                        "warning": "archive_needs_external_decoder",
                        "decoder_missing": True,
                        "members_total": getattr(result, "members_total", None),
                        "members_read": files,
                        "members_unreadable": payload["members_unreadable"],
                        "notes": result_notes,
                        "detail": (
                            "Part of this archive uses a RAR compression method "
                            "that requires an external decoder, which is not "
                            "installed. Members stored uncompressed were read and "
                            "are listed below. " + (hint or "")
                        ).strip(),
                    }
                    return payload
                if files == 0:
                    # An empty archive is legitimate; an archive the backend
                    # could not actually parse is not. rarfile, for instance,
                    # opens a corrupt RAR5 and reports an empty namelist rather
                    # than raising, so this is the only place the distinction
                    # can be recorded rather than lost.
                    payload["extraction_info"] = {
                        "warning": "archive_opened_but_no_members_extracted",
                        "detail": (
                            "The archive was opened without error but yielded no "
                            "members. It may be genuinely empty, corrupt, or in a "
                            "format the installed backend cannot decode."
                        ),
                    }
                return payload
            else:
                error_msg = "Extraction failed"
                return self.handle_read_error(Exception(error_msg), file_path, "read_file")

        except archive_safety.ArchiveDecoderUnavailable as e:
            # The container was identified, but nothing on this machine can
            # decode it - the RAR front end reads stored members only, and
            # without a decoder a compressed archive yields nothing. Report the
            # environment gap and what to install; "Extraction failed" told the
            # operator to re-read a file that is perfectly intact.
            logger.warning(
                "Archive needs an external decoder that is not installed: %s (%s)",
                file_path, e,
            )
            result = self.handle_read_error(e, file_path, "read_file")
            # A missing dependency, not a broken artifact: installing the
            # decoder and re-running reads this file. Classified like the
            # missing-OCR-engine case so the run's accounting says "worth
            # another attempt" instead of "failed".
            result["retryable"] = True
            result["decoder_missing"] = True
            result["extraction_info"] = {
                "extracted": False,
                "warning": "archive_needs_external_decoder",
                "decoder_missing": True,
                "decoder_hint": archive_safety.RAR_DECODER_HINT,
                "detail": f"{e}. {archive_safety.RAR_DECODER_HINT}",
            }
            return result
        except ArchiveEncrypted as e:
            # A locked archive is actionable (supply a password) and must not be
            # recorded as though the file were corrupt.
            logger.info("Archive is password-protected: %s", file_path)
            result = self.handle_read_error(e, file_path, "read_file")
            result["archive_locked"] = True
            result["extraction_info"] = {
                "warning": "archive_password_protected",
                "detail": (
                    "The archive is encrypted. No password was supplied, so no "
                    "members could be read. This is not corruption: the "
                    "container was identified and opened."
                ),
            }
            return result
        except ArchiveSafetyError as e:
            # Safety violations are reported as rejected archives (client-safe).
            logger.warning("Archive rejected by safety policy: %s (%s)", file_path, e)
            return self.handle_read_error(e, file_path, "read_file")
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")

    # ------------------------------------------------------------------
    # Extraction backends - all via core.archive_safety
    # ------------------------------------------------------------------
    def extract_zip(self, file_path):
        """Extract ZIP files safely (zip-slip protected)."""
        extract_to = get_extraction_name_file(file_path, '.zip')
        reset_extraction_dir(extract_to)
        result = archive_safety.extract_zip(file_path, extract_to)
        logger.info("Extracted %d files from %s", result.files_extracted, file_path)
        return result, str(extract_to)

    def extract_tar(self, file_path, extension=None):
        """Extract TAR files safely (.tar, .tar.gz, .tar.bz2, .tar.xz).

        Args:
            file_path: Path to the tarball.
            extension: Effective (content-verified) extension. When omitted it
                is derived from the filename, which is wrong for a tarball
                whose declared extension disagrees with its contents.
        """
        if extension not in ('.tar.gz', '.tar.bz2', '.tar.xz'):
            extension = '.tar'

        extract_to = get_extraction_name_file(file_path, extension)
        reset_extraction_dir(extract_to)
        result = archive_safety.extract_tar(file_path, extract_to)
        logger.info("Extracted %d files from %s", result.files_extracted, file_path)
        return result, str(extract_to)

    def extract_gz(self, file_path):
        """Extract GZ files (single file compression) safely."""
        extract_to = get_extraction_name_file(file_path, '.gz')
        reset_extraction_dir(extract_to)
        result = archive_safety.extract_single_file(file_path, extract_to, codec="gzip")
        return result, str(extract_to)

    def extract_bz2(self, file_path):
        """Extract BZ2 files (single file compression) safely."""
        extract_to = get_extraction_name_file(file_path, '.bz2')
        reset_extraction_dir(extract_to)
        result = archive_safety.extract_single_file(file_path, extract_to, codec="bzip2")
        return result, str(extract_to)

    def extract_xz(self, file_path):
        """Extract an XZ stream (single file compression) safely."""
        extract_to = get_extraction_name_file(file_path, '.xz')
        reset_extraction_dir(extract_to)
        result = archive_safety.extract_single_file(file_path, extract_to, codec="xz")
        logger.info("Decompressed %s (%d bytes)", file_path, result.bytes_extracted)
        return result, str(extract_to)

    def extract_rar(self, file_path):
        """Extract RAR files safely.

        Decoding is delegated to whichever RAR decoder is installed (unrar,
        unar, 7-Zip or bsdtar). When none is - the normal state of a Windows
        machine without WinRAR or 7-Zip - core.archive_safety reads the
        container itself: members stored uncompressed are extracted and
        CRC32-verified, the member inventory is reported, and members that need
        a decoder are named instead of failing the whole file.

        ArchiveSafetyError and ArchiveEncrypted propagate: read_file reports
        the specific condition (rejected by policy, corrupt, password-protected)
        rather than collapsing everything into "Extraction failed".
        """
        extract_to = get_extraction_name_file(file_path, '.rar')
        reset_extraction_dir(extract_to)
        result = archive_safety.extract_rar(file_path, extract_to)
        if getattr(result, "decoder_missing", False):
            logger.warning(
                "RAR read without an external decoder for %s: %d of %s member(s) "
                "read (%s); compressed members require 7-Zip or WinRAR/UnRAR",
                file_path,
                result.files_extracted,
                result.members_total,
                archive_safety.rar_decoder_status(),
            )
        else:
            logger.info("Extracted %d files from %s", result.files_extracted, file_path)
        return result, str(extract_to)

    # ------------------------------------------------------------------
    # Reporting for containers read without their decoder
    # ------------------------------------------------------------------
    #: Name listing cap. A 10 000-member archive must not turn into a 10 000-line
    #: content blob in the word index; the count is always exact, the listing is
    #: representative and says so.
    MAX_LISTED_MEMBERS = 200

    #: Hard cap on the manifest text handed to the index.
    MAX_MANIFEST_CHARS = 20_000

    def _undecoded_container_text(self, file_path, ext, result, notes):
        """Describe a container that was read without its external decoder.

        The manifest is built from what the container declares - names, sizes
        and why each unreadable member is unreadable - so the archive's own
        content is searchable evidence even when a member could not be
        decoded, and the gap is never silent.
        """
        family = ext.lstrip('.').upper() or 'ARCHIVE'
        lines = [
            f"{family} archive: {Path(file_path).name}",
            f"Members: {getattr(result, 'members_total', None)}",
            f"Members read: {getattr(result, 'files_extracted', 0)}",
        ]
        unreadable = getattr(result, "members_unreadable", None) or {}
        if unreadable:
            described = ", ".join(
                f"{count} {archive_safety.MEMBER_REASON_TEXT.get(reason, reason)}"
                for reason, count in sorted(unreadable.items())
            )
            lines.append(f"Members that could not be read: {described}")
        for note in notes or []:
            lines.append(f"Note: {note}")

        skipped = list(getattr(result, "skipped", []) or [])
        if skipped:
            lines.append("Members not extracted:")
            lines.extend(f"  {name}" for name in skipped[:self.MAX_LISTED_MEMBERS])
            if len(skipped) > self.MAX_LISTED_MEMBERS:
                lines.append(
                    f"  ... and {len(skipped) - self.MAX_LISTED_MEMBERS} more"
                )
        text = "\n".join(lines)
        if len(text) > self.MAX_MANIFEST_CHARS:
            text = text[:self.MAX_MANIFEST_CHARS] + "\n[manifest truncated]"
        return text

    def extract_7z(self, file_path):
        """Extract 7Z files safely (requires py7zr package)."""
        try:
            import py7zr  # noqa: F401
        except ImportError:
            logger.warning("py7zr not installed. Install with: pip install py7zr")
            return None

        extract_to = get_extraction_name_file(file_path, '.7z')
        reset_extraction_dir(extract_to)
        try:
            result = archive_safety.extract_7z(file_path, extract_to)
            logger.info("Extracted %d files from %s", result.files_extracted, file_path)
            return result, str(extract_to)
        except ArchiveSafetyError:
            raise  # see extract_rar: read_file reports the specific reason
        except Exception as e:
            logger.error("Error extracting 7z file %s: %s", file_path, e.__class__.__name__)
            return None
