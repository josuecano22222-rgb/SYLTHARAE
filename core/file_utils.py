"""
File utilities - Independent functions for file operations
No dependencies on other project modules.
"""

import os
import re
import stat as stat_module
import hashlib
from pathlib import Path
from datetime import datetime, date
from typing import Any, Dict, Iterator, Optional, Tuple


#: Files at or above this size are not hashed during discovery. Discovery
#: stays cheap; ``pipeline.storage_pipeline`` computes the real streamed
#: SHA-256 exactly once when the file is stored.
HASH_INLINE_MAX_BYTES = 100 * 1024 * 1024

#: Value placed in ``Metadata['hash']`` when hashing was deliberately deferred.
#: It is a sentinel, not a digest, and must never be persisted as an identity.
HASH_DEFERRED_SENTINEL = "SKIPPED_LARGE_FILE"


def format_file_size(size_bytes: Optional[int]) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: File size in bytes
        
    Returns:
        Formatted string (e.g., "1.23 MB")
    """
    if size_bytes is None:
        return "N/A"
    
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def get_file_creation_and_modification_date(
    file_path: Optional[str],
    content_dict: Optional[Dict[str, Any]] = None
) -> Tuple[date, date]:
    """
    Extract the file's actual creation date and modification date.
    Tied to the file itself (embedded metadata or file system timestamps),
    never reflecting the current system execution date.

    Returns:
        Tuple[date, date]: (creation_date, modification_date)
    """
    created_dt = None
    modified_dt = None

    # 1. Inspect embedded metadata if content_dict is provided
    if content_dict and isinstance(content_dict, dict):
        meta = content_dict.get('metadata') if isinstance(content_dict.get('metadata'), dict) else {}

        # Keys for creation date
        for key in ('creationDate', 'created', 'creation_date', 'date', 'DateTimeOriginal', 'CreateDate', 'dc:date'):
            val = meta.get(key) or content_dict.get(key)
            if val:
                parsed = parse_date_string(val)
                if parsed:
                    created_dt = parsed
                    break

        # Keys for modification date
        for key in ('modDate', 'modified', 'modification_date', 'last_modified', 'ModifyDate'):
            val = meta.get(key) or content_dict.get(key)
            if val:
                parsed = parse_date_string(val)
                if parsed:
                    modified_dt = parsed
                    break

    # 2. Inspect file system attributes if file exists
    if file_path and os.path.exists(file_path):
        try:
            stat_res = os.stat(file_path)
            fs_mtime = datetime.fromtimestamp(stat_res.st_mtime)
            if not modified_dt:
                modified_dt = fs_mtime

            birthtime = getattr(stat_res, 'st_birthtime', None)
            if birthtime and birthtime > 0:
                fs_ctime = datetime.fromtimestamp(birthtime)
            elif os.name == 'nt':
                fs_ctime = datetime.fromtimestamp(stat_res.st_ctime)
            else:
                # On Unix/Linux, st_ctime is inode metadata change time (often current system copy/upload time).
                # The file's actual modification time on disk (st_mtime) is preserved across copies.
                # Use st_mtime as creation time fallback rather than current system st_ctime.
                fs_ctime = fs_mtime

            if not created_dt:
                created_dt = fs_ctime
        except Exception:
            pass

    today_dt = datetime.now()
    if not created_dt and modified_dt:
        created_dt = modified_dt
    if not modified_dt and created_dt:
        modified_dt = created_dt
    if not created_dt:
        created_dt = today_dt
    if not modified_dt:
        modified_dt = today_dt

    return created_dt.date(), modified_dt.date()


def parse_date_string(val: Any) -> Optional[datetime]:
    """Parse a date string, object, or timestamp into a datetime object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val)
        except Exception:
            return None
    val_str = str(val).strip()
    if not val_str:
        return None

    # PDF date format: D:YYYYMMDDHHMMSS...
    if val_str.startswith("D:"):
        s = val_str[2:].replace("'", "").replace("Z", "")
        digits = "".join(c for c in s if c.isdigit())
        if len(digits) >= 8:
            try:
                year = int(digits[0:4])
                month = int(digits[4:6])
                day = int(digits[6:8])
                hour = int(digits[8:10]) if len(digits) >= 10 else 0
                minute = int(digits[10:12]) if len(digits) >= 12 else 0
                second = int(digits[12:14]) if len(digits) >= 14 else 0
                return datetime(year, month, day, hour, minute, second)
            except Exception:
                pass

    # EXIF date format: YYYY:MM:DD HH:MM:SS
    if ":" in val_str[:10] and len(val_str) >= 10 and val_str[4] == ":" and val_str[7] == ":":
        try:
            parts = val_str.split()
            date_parts = [int(p) for p in parts[0].split(":")]
            time_parts = [int(p) for p in parts[1].split(":")] if len(parts) > 1 else [0, 0, 0]
            return datetime(date_parts[0], date_parts[1], date_parts[2], time_parts[0], time_parts[1], time_parts[2])
        except Exception:
            pass

    try:
        from dateutil import parser
        return parser.parse(val_str)
    except Exception:
        pass

    try:
        return datetime.fromisoformat(val_str.replace("Z", "+00:00"))
    except Exception:
        pass

    return None


def calculate_file_hash(file_path: str, algorithm: str = 'sha256', chunk_size: int = 8192) -> str:
    """
    Calculate file hash.
    
    Args:
        file_path: Path to file
        algorithm: Hash algorithm (default: 'sha256')
        chunk_size: Size of chunks to read (default: 8192)
        
    Returns:
        Hexadecimal hash string
    """
    hash_obj = hashlib.new(algorithm)
    
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            hash_obj.update(chunk)
    
    return hash_obj.hexdigest()



#: Suffixes openpyxl accepts when it is handed a *path*. It validates the name
#: before it looks at the bytes and refuses anything else with
#: "openpyxl does not support .docx file format ...". A file-like object skips
#: that check, so a spreadsheet whose declared name is something else (an
#: attachment inherited from its container, for example) must be opened as a
#: stream. Both the ingestion reader and the preview service used to carry their
#: own copy of this rule; it lives here so they cannot drift apart.
OPENPYXL_PATH_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm")


def load_spreadsheet_workbook(file_path: str, *, read_only: bool = False,
                              data_only: bool = False, keep_links: bool = True):
    """Open a workbook by its bytes when its name is not a supported suffix.

    ``read_only`` and ``data_only`` are passed through unchanged, so the caller
    keeps deciding whether formulas or cached values are wanted. ``keep_links``
    is passed through too: ``False`` skips external-link parts. A DDE or
    external link whose ``_rels`` part is missing from the package otherwise
    makes openpyxl raise ``There is no item named 'xl/externalLinks/_rels/...'``
    and the whole workbook is lost, even though its cell values are intact.
    Raises whatever openpyxl raises for content that is not a workbook.

    The returned workbook owns the stream it was opened from and closes it in
    its own ``close()``. openpyxl reads from a stream lazily - a ``read_only``
    worksheet fetches rows on demand - so closing the handle when this function
    returns (what a ``with`` block does) makes the *first* row read fail with
    ``ValueError: seek of closed file``. Callers that use ``read_only`` must
    still call ``close()`` when they are done.
    """
    import openpyxl

    suffix = os.path.splitext(str(file_path))[1].lower()
    if suffix in OPENPYXL_PATH_SUFFIXES:
        return openpyxl.load_workbook(file_path, read_only=read_only,
                                      data_only=data_only, keep_links=keep_links)

    handle = open(file_path, "rb")
    try:
        workbook = openpyxl.load_workbook(handle, read_only=read_only,
                                          data_only=data_only, keep_links=keep_links)
    except Exception:
        handle.close()
        raise

    original_close = workbook.close

    def _close() -> None:
        try:
            original_close()
        finally:
            try:
                handle.close()
            except Exception:  # pragma: no cover - defensive
                pass

    workbook.close = _close          # type: ignore[method-assign]
    #: Keeps the stream reachable (and closed by ``close()``) for as long as the
    #: workbook is alive, instead of depending on garbage collection order.
    workbook._inforaxis_source_stream = handle
    return workbook


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename by removing invalid characters.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    if not filename:
        return "unnamed_attachment"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    filename = filename.strip('. ')
    return filename if filename else "unnamed_attachment"


def get_standardized_metadata(file_path: str, compute_hash: bool = True,
                             dir_entry=None) -> Optional[Dict[str, Any]]:
    """Get standardized metadata for a file or directory.

    Args:
        file_path: Path to file or directory
        compute_hash: Hash readable files below :data:`HASH_INLINE_MAX_BYTES`
            while inspecting them.  Kept True by default (the historical
            behaviour); the ingestion pipeline passes False so that hashing
            happens once, in the parallel store stage, instead of a serialized
            full read of the corpus during enumeration.  Either way a file that
            is not hashed here carries :data:`HASH_DEFERRED_SENTINEL`, never a
            fabricated identity.
        dir_entry: Optional ``os.DirEntry`` for ``file_path``.  When supplied
            its cached stat results are used, which removes several syscalls
            per file (``read_tree`` walks millions of entries at a time).

    Returns:
        Dictionary with metadata or None if error
    """
    try:
        if dir_entry is not None:
            try:
                stats = dir_entry.stat(follow_symlinks=False)
            except OSError:
                stats = os.stat(file_path)
            path = Path(file_path)
        else:
            path = Path(file_path)
            try:
                stats = path.stat()
            except OSError:
                stats = None

        if stats is None:
            return {
                "name": path.name,
                "path": str(path),
                "type": "UNKNOWN",
                "extension": path.suffix.lower() if path.suffix else "none",
                "size": "N/A",
                "size_bytes": None,
                "hash": "N/A",
                "created": "N/A",
                "modified": "N/A",
                "accessed": "N/A",
                "readable": False,
                "writable": False,
                "executable": False,
                "processing_time": "N/A"
            }

        # Determine file type from the stat result we already have.
        mode = stats.st_mode
        if stat_module.S_ISREG(mode):
            file_type = "FILE"
        elif stat_module.S_ISDIR(mode):
            file_type = "DIRECTORY"
        elif stat_module.S_ISLNK(mode):
            file_type = "SYMLINK"
        else:
            file_type = "OTHER"

        is_readable = os.access(file_path, os.R_OK)
        is_writable = os.access(file_path, os.W_OK)
        is_executable = os.access(file_path, os.X_OK)

        file_size = format_file_size(stats.st_size)

        file_hash = "N/A"
        if file_type == "FILE" and is_readable:
            try:
                if not compute_hash:
                    file_hash = HASH_DEFERRED_SENTINEL
                elif stats.st_size < HASH_INLINE_MAX_BYTES:
                    file_hash = calculate_file_hash(file_path)
                else:
                    # HASH-01: never fabricate an identity. This used to be
                    # sha256(f"{path}|{size}|{mtime}"), which is not a content
                    # hash: two byte-identical large files at different paths
                    # received different values, so deduplication failed and
                    # both were stored. The sentinel below is what
                    # pipeline.storage_pipeline already recognises as "compute
                    # the real streamed hash yourself".
                    file_hash = HASH_DEFERRED_SENTINEL
            except Exception:
                file_hash = "ERROR"

        created_d, modified_d = get_file_creation_and_modification_date(file_path)
        metadata = {
            "name": path.name,
            "path": os.path.abspath(file_path),
            "type": file_type,
            "extension": path.suffix.lower() if path.suffix else "none",
            "size": file_size,
            "size_bytes": stats.st_size,
            "hash": file_hash,
            "created": created_d.isoformat(),
            "modified": modified_d.isoformat(),
            "accessed": datetime.fromtimestamp(stats.st_atime).isoformat(),
            "readable": is_readable,
            "writable": is_writable,
            "executable": is_executable,
            "processing_time": "N/A"
        }

        return metadata

    except Exception:
        return {
            "name": Path(file_path).name if file_path else "Unknown",
            "path": str(file_path) if file_path else "Unknown",
            "type": "ERROR",
            "extension": "none",
            "size": "N/A",
            "size_bytes": None,
            "hash": "N/A",
            "created": "N/A",
            "modified": "N/A",
            "accessed": "N/A",
            "readable": False,
            "writable": False,
            "executable": False,
            "processing_time": "N/A",
            "error": "Failed to get metadata"
        }


def nested_extract_skip_reason(relative_path: str) -> Optional[str]:
    """Return a skip reason for archive/email members that should not be ingested.

    Mac ZIP exports ship AppleDouble ``__MACOSX/._*`` resource forks (not images).
    iWork bundles expose ``Index/*.iwa`` shards that only make sense on the parent
    ``.pages`` / ``.numbers`` / ``.key`` package row.
    """
    norm = str(relative_path or "").replace("\\", "/").strip("/")
    if not norm:
        return None
    parts = [p for p in norm.lower().split("/") if p]
    if any(p == "__macosx" for p in parts):
        return "macosx_metadata"
    base = parts[-1] if parts else norm.lower()
    if base.startswith("._"):
        return "appledouble_resource_fork"
    if base.endswith(".iwa"):
        return "iwork_internal_shard"
    return None


def _error_metadata(name: str, path: str, extension: str, message: str) -> Dict[str, Any]:
    """Metadata for an entry that could not be inspected.

    The pipeline stores these records too ("files must not be lost because
    they could not be read"), so the shape is the same as a successful
    ``get_standardized_metadata`` result.
    """
    return {
        "name": name,
        "path": path,
        "type": "ERROR",
        "extension": extension,
        "size": "N/A",
        "size_bytes": None,
        "hash": "N/A",
        "created": "N/A",
        "modified": "N/A",
        "accessed": "N/A",
        "readable": False,
        "writable": False,
        "executable": False,
        "processing_time": "N/A",
        "error": message,
    }


def iter_tree(path: str, compute_hashes: bool = True) -> Iterator[Dict[str, Any]]:
    """Yield file/directory metadata for everything under ``path``.

    This is the streaming form of :func:`read_tree`.  It exists because the
    list form does not scale: each entry is a metadata dictionary of roughly
    1.7 KB (measured), so a million-file tree needs ~1.7 GB of resident memory
    just to be enumerated and ten million needs ~17 GB - before a single byte
    of file content is read.  A generator lets the pipeline hold one batch at a
    time instead.

    Directory walking uses ``os.scandir`` with ``followlinks=False``: symlink
    cycles cannot recurse, ``DirEntry.stat`` avoids a second ``stat`` syscall
    per entry, and no ``Path.resolve()`` (a chain of syscalls) is needed to
    stay loop-safe.  Errors on individual entries are yielded as ``ERROR``
    records rather than aborting the walk, which is the behaviour the pipeline
    depends on for locked, unreadable or vanishing files.

    Args:
        path: Root directory (a file path yields a single record).
        compute_hashes: When True (the default, and the historical behaviour of
            :func:`read_tree`) each readable file below
            :data:`HASH_INLINE_MAX_BYTES` is hashed while being enumerated.
            The ingestion pipeline passes ``False``: hashing there is done by
            the parallel store stage, which computes the same streamed SHA-256
            exactly once and would otherwise duplicate a full serialized read
            of the whole corpus before processing began.  Metadata produced
            with ``False`` carries :data:`HASH_DEFERRED_SENTINEL`, which
            storage already recognises as "compute the real hash yourself" -
            the same contract that has always applied to files above the
            threshold.
    """
    root_path = Path(path)

    # A single stat tells us whether the root exists / is a file / is a dir.
    try:
        root_stat = os.stat(path)
    except FileNotFoundError:
        yield _error_metadata(root_path.name, str(root_path), "none",
                              "Root path does not exist")
        return
    except PermissionError as perm_err:
        yield _error_metadata(root_path.name, str(root_path), "none",
                              f"Permission denied accessing root path: {perm_err}")
        return
    except OSError as os_err:
        yield _error_metadata(root_path.name, str(root_path), "none",
                              f"Error accessing root path: {os_err}")
        return
    except Exception as exc:  # pragma: no cover - defensive
        yield _error_metadata(Path(path).name if path else "Unknown",
                              str(path) if path else "Unknown", "none",
                              f"Critical error in read_tree: {exc}")
        return

    if stat_module.S_ISREG(root_stat.st_mode):
        # A file was passed: enumerate exactly it (with the same error handling
        # used for every other entry).
        try:
            info = get_standardized_metadata(path, compute_hash=compute_hashes)
        except Exception as exc:
            info = _error_metadata(root_path.name, str(root_path),
                                   root_path.suffix.lower() or "none",
                                   f"Unexpected error: {exc}")
        if info:
            yield info
        return

    def _directory_identity(stat_result) -> Optional[tuple]:
        """Identity of a directory for cycle detection, or None when unusable.

        ``(st_dev, st_ino)`` is only a real identity when the filesystem reports
        a usable inode.  Windows volumes - and some network, FAT and virtual
        mounts - report ``st_ino == 0`` for every entry.  Treating those zeros as
        inode numbers gives every directory the *same* key, so the walk visits
        the first directory it meets and silently skips every other directory
        and all the files inside them: a nested tree collapses to a handful of
        entries, and the files that were never enumerated are files the pipeline
        can never process.  Zero (or a missing value) therefore means "identity
        unavailable", and the caller falls back to the resolved real path, which
        still catches symlink, bind-mount and hardlinked-root cycles.
        """
        try:
            if not stat_result or not stat_result.st_ino:
                return None
            return (stat_result.st_dev, stat_result.st_ino)
        except Exception:
            return None

    def _real_directory_path(directory: str) -> str:
        """Canonical path of a directory, used when no inode identity exists."""
        try:
            return os.path.realpath(directory)
        except Exception:
            return os.path.abspath(directory)

    #: Directories already visited, keyed by (device, inode) where the
    #: filesystem reports one, otherwise by canonical path.  Bounding the guard
    #: to directories keeps loop safety without holding one entry per file for
    #: the whole walk.
    visited_dirs = set()
    visited_paths = set()
    try:
        root_key = _directory_identity(root_stat)
        if root_key is not None:
            visited_dirs.add(root_key)
        else:
            visited_paths.add(_real_directory_path(str(root_path)))
    except Exception:
        pass

    def _walk(current: str) -> Iterator[Dict[str, Any]]:
        try:
            with os.scandir(current) as entries:
                items = list(entries)
        except PermissionError as perm_err:
            yield _error_metadata(os.path.basename(current) or current, current,
                                  "none", f"Permission denied: {perm_err}")
            return
        except OSError as os_err:
            yield _error_metadata(os.path.basename(current) or current, current,
                                  "none", f"OS error: {os_err}")
            return
        except Exception as exc:  # pragma: no cover - defensive
            yield _error_metadata(os.path.basename(current) or current, current,
                                  "none", f"Unexpected error: {exc}")
            return

        subdirectories = []
        for entry in items:
            try:
                entry_path = entry.path
                try:
                    info = get_standardized_metadata(entry_path,
                                                     compute_hash=compute_hashes,
                                                     dir_entry=entry)
                except Exception as exc:
                    info = _error_metadata(entry.name, entry_path,
                                           os.path.splitext(entry.name)[1].lower() or "none",
                                           f"Unexpected error: {exc}")
                if not info:
                    continue
                if not info.get("readable", True):
                    info["error"] = "File is not readable (permission denied)"
                yield info

                if info.get("type") == "DIRECTORY" and not entry.is_symlink():
                    # Track the real identity of the directory, not its path:
                    # two names for the same directory (bind mount, hardlinked
                    # tree root) must not be walked twice.  When the filesystem
                    # cannot supply an inode, identity falls back to the
                    # canonical path - never to a shared zero value, which would
                    # make every directory look like the same one.
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        st = None
                    key = _directory_identity(st)
                    if key is not None:
                        if key not in visited_dirs:
                            visited_dirs.add(key)
                            subdirectories.append(entry_path)
                    else:
                        real = _real_directory_path(entry_path)
                        if real not in visited_paths:
                            visited_paths.add(real)
                            subdirectories.append(entry_path)
            except PermissionError as perm_err:
                yield _error_metadata(getattr(entry, "name", str(entry)),
                                      getattr(entry, "path", str(entry)), "none",
                                      f"Permission denied: {perm_err}")
            except OSError as os_err:
                yield _error_metadata(getattr(entry, "name", str(entry)),
                                      getattr(entry, "path", str(entry)), "none",
                                      f"OS error: {os_err}")
            except Exception as exc:
                yield _error_metadata(getattr(entry, "name", str(entry)),
                                      getattr(entry, "path", str(entry)), "none",
                                      f"Unexpected error: {exc}")

        for subdirectory in subdirectories:
            yield from _walk(subdirectory)

    yield from _walk(str(root_path))


def read_tree(path: str, compute_hashes: bool = True) -> list:
    """
    Read directory tree and return list of file metadata.
    PRODUCTION-READY: Handles all edge cases including permission errors, symlinks, and very long paths.
    Ensures NO files are lost, even if they can't be accessed.

    Args:
        path: Directory path
        compute_hashes: Hash readable files while listing (see :func:`iter_tree`).
            :func:`iter_tree` is the streaming form and is what large
            ingestions should use.

    Returns:
        List of file metadata dictionaries (includes files with errors)
    """
    try:
        return list(iter_tree(path, compute_hashes=compute_hashes))
    except Exception as e:
        return [_error_metadata(Path(path).name if path else "Unknown",
                                str(path) if path else "Unknown", "none",
                                f"Critical error in read_tree: {e}")]

def create_standardized_result(file_path: str, content_data: Any, 
                               processing_time: Optional[float] = None) -> Dict[str, Any]:
    """
    Create standardized result structure.
    
    Args:
        file_path: Path to processed file
        content_data: Content data from processing
        processing_time: Processing time in seconds
        
    Returns:
        Standardized result dictionary
    """
    metadata = get_standardized_metadata(file_path)
    
    # Update processing time
    if processing_time is not None:
        metadata["processing_time"] = f"{processing_time:.4f} seconds"
    
    # Create the standardized format
    result = {
        "Metadata": metadata,
        "Content": content_data if content_data is not None else {}
    }
    
    return result


def normalize_text(text: str) -> str:
    """Normalize text for tokenization: NFKC, lowercasing, and whitespace collapse."""
    if text is None:
        return ""
    try:
        import unicodedata
        s = unicodedata.normalize('NFKC', text)
    except Exception:
        s = text
    s = s.replace('\r', ' ').replace('\n', ' ')
    s = re.sub(r"\s+", ' ', s).strip()
    return s


def tokenize_text(text: str) -> list:
    """Return an ordered list of word tokens from input text.

    Rules:
    - Normalize using `normalize_text`
    - Split on non-word characters, preserve only tokens with letters/numbers
    - Lowercase tokens
    """
    if not text:
        return []
    s = normalize_text(text)
    # Split on anything that's not a letter/number/apostrophe
    raw_tokens = re.split(r"[^\w']+", s)
    tokens = [t.lower() for t in raw_tokens if t and re.search(r"\w", t)]
    return tokens


def tokens_with_positions(text: str) -> list:
    """Return list of (token, index, char_start, char_end) preserving order.

    Useful for building inverted index or storing ordered tokens with positions.
    """
    s = normalize_text(text)
    tokens = []
    if not s:
        return tokens

    # Walk the string and match tokens using the same pattern
    pattern = re.compile(r"[\w']+")
    for idx, m in enumerate(pattern.finditer(s)):
        token = m.group(0).lower()
        tokens.append((token, idx, m.start(), m.end()))
    return tokens


def text_to_word_array_and_ordered_text(text: str) -> dict:
    """Produce a dictionary with words array and ordered full text.

    Returns:
        {"words": [...], "ordered_text": "..."}
    """
    toks = tokenize_text(text)
    ordered = ' '.join(toks)
    return {"words": toks, "ordered_text": ordered}

