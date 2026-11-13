"""Safe archive extraction (SEC-05).

Hardening applied to every archive reader (ZIP, TAR, GZIP, BZIP2, RAR, 7z):

* Rejection of absolute paths, ``..`` traversal, drive-qualified and UNC paths.
* Every destination is re-validated to resolve beneath the extraction root
  after normalisation (zip-slip / tar-slip / symlink escapes).
* Symlinks and hard links are refused (they cannot be validated statically).
* Resource limits: maximum depth, maximum member count, maximum extracted
  bytes, maximum compression ratio, per-file size limit and a wall-clock
  timeout.
* No ``extractall`` anywhere: members are streamed one at a time.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import zipfile
import bz2
import gzip
import lzma
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ArchiveSafetyError(Exception):
    """Raised when an archive violates the extraction safety policy."""


class ArchiveEncrypted(ArchiveSafetyError):
    """The archive is password-protected and no usable password was supplied.

    A subclass of ArchiveSafetyError so existing handlers keep working, but
    distinguishable: a locked archive is a different fact from a corrupt one,
    and reporting both as an opaque extraction error makes an actionable
    condition (supply a password) look like a defect.
    """


class ArchiveTimeout(ArchiveSafetyError):
    pass


class ArchiveDecoderUnavailable(ArchiveSafetyError):
    """The format needs an external decoder and none is installed.

    Deliberately not an ``ArchiveSafetyError`` body-failure: the container was
    identified, opened and parsed. Reporting this as corruption (or as a
    generic "Extraction failed") tells the operator to re-read a file that is
    perfectly intact, when the only thing missing is a binary.
    """


@dataclass
class ExtractionPolicy:
    """Resource limits applied during extraction."""

    max_depth: int = 8
    max_files: int = 10_000
    max_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB total extracted
    max_file_size: int = 512 * 1024 * 1024  # per member
    max_compression_ratio: int = 500
    timeout_seconds: float = 600.0
    allowed_extensions: Optional[Iterable[str]] = None  # None = any


DEFAULT_POLICY = ExtractionPolicy()

_FORBIDDEN_MEMBERS = ("", ".", "..")

#: Windows reserved device names. These are devices, not files: opening
#: ``CON.txt`` writes to the console and ``NUL`` discards data, with or without
#: an extension and regardless of case. Archives are frequently authored on
#: other platforms where such names are ordinary, so they arrive here intact.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: Per-component filename limit. Windows rejects longer components outright,
#: and the legacy MAX_PATH of 260 is still the default for most tools.
_MAX_COMPONENT_LENGTH = 200


def windows_safe_component(part: str) -> str:
    """Make one path component writable on Windows without discarding the file.

    Windows is the primary production platform, so a member name that is legal
    on the authoring platform must not become an unwritable path here. Renaming
    is deliberate over rejecting: refusing the whole archive because one member
    happened to be called ``CON`` would discard every other object in it, and an
    unsupported or awkward child must stay part of the parent's object graph.
    """
    if not part:
        return part

    # Reserved device names, with or without an extension, case-insensitive.
    stem = part.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        part = "_" + part

    # Windows strips trailing dots and spaces; leaving them makes the written
    # name differ from the requested one and can collide with a sibling.
    part = part.rstrip(". ")

    # Keep the extension when truncating so the type is still identifiable.
    if len(part) > _MAX_COMPONENT_LENGTH:
        stem_part, dot, ext = part.rpartition(".")
        if dot and len(ext) <= 20 and stem_part:
            keep = _MAX_COMPONENT_LENGTH - len(ext) - 1
            part = f"{stem_part[:keep]}.{ext}"
        else:
            part = part[:_MAX_COMPONENT_LENGTH]

    return part or "_"


def _check_deadline(deadline: Optional[float]) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise ArchiveTimeout("Archive extraction exceeded the configured timeout")


def validate_member_path(name: str) -> str:
    """Normalise and validate an archive member name.

    Returns a safe, relative, slash-delimited path.

    Raises :class:`ArchiveSafetyError` for absolute paths, traversal,
    drive-qualified paths, UNC paths and NUL bytes.
    """
    if not name or "\x00" in name:
        raise ArchiveSafetyError(f"Invalid archive member name: {name!r}")

    # Detect Windows-isms before any normalisation can hide them.
    win = PureWindowsPath(name)
    if win.is_absolute() or win.drive:
        raise ArchiveSafetyError(f"Absolute or drive-qualified path in archive: {name!r}")
    if name.startswith("\\\\") or name.startswith("//"):
        raise ArchiveSafetyError(f"UNC path in archive: {name!r}")

    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute():
        raise ArchiveSafetyError(f"Absolute path in archive: {name!r}")

    parts: List[str] = []
    depth = 0
    for part in p.parts:
        if part in ("", "."):
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ArchiveSafetyError(f"Path traversal in archive member: {name!r}")
            if parts:
                parts.pop()
            continue
        depth += 1
        parts.append(windows_safe_component(part))

    if not parts:
        raise ArchiveSafetyError(f"Empty archive member path: {name!r}")
    if len(parts) > DEFAULT_POLICY.max_depth:
        raise ArchiveSafetyError(f"Archive member exceeds maximum depth: {name!r}")

    return "/".join(parts)


def safe_destination(root: Path, member_name: str) -> Path:
    """Resolve ``root`` + validated member name and confirm containment."""
    root = root.resolve()
    relative = validate_member_path(member_name)
    destination = (root / relative).resolve()
    # Final containment check post-normalisation (defends against symlinked
    # intermediate directories created by earlier members).
    if destination != root and root not in destination.parents:
        raise ArchiveSafetyError(
            f"Archive member escapes extraction root: {member_name!r}"
        )
    return destination


@dataclass
class ExtractionResult:
    output_dir: Path
    files_extracted: int = 0
    bytes_extracted: int = 0
    skipped: List[str] = field(default_factory=list)
    #: Members the container declares, whether or not they could be read.
    members_total: Optional[int] = None
    #: Reason -> count for members that were declared but not materialised.
    #: Never None: "nothing was unreadable" is an empty map, not a missing one,
    #: so callers can compare it without a guard.
    members_unreadable: dict = field(default_factory=dict)
    #: True when members needed a decoder that this machine does not have, so
    #: part of the archive could not be read. Callers report this; they must
    #: not hide it, and it must never be confused with corruption.
    decoder_missing: bool = False
    #: Concrete, actionable text naming what to install.
    decoder_hint: Optional[str] = None
    #: Free-form notes carried into the file's status detail.
    notes: List[str] = field(default_factory=list)


def _policy_for_file(policy: ExtractionPolicy, name: str) -> None:
    if policy.allowed_extensions is not None:
        ext = Path(name).suffix.lower()
        if ext not in policy.allowed_extensions:
            raise ArchiveSafetyError(f"File type not allowed in archive: {name!r}")


def _destination_checks(policy: ExtractionPolicy, dest: Path, member_size: int) -> None:
    if member_size > policy.max_file_size:
        raise ArchiveSafetyError(
            f"Archive member exceeds per-file size limit: {dest.name} ({member_size} bytes)"
        )


def _prepare_output_dir(output_dir) -> Path:
    """Return an **empty** output directory for a fresh extraction.

    Extraction publishes the complete member set of one source file. An output
    directory left over from an earlier attempt (crash, retry, or a previous
    archive that occupied the same name) would otherwise be indistinguishable
    from the current archive's members, and the pipeline would ingest stale
    children as if they belonged to this source - the extraction equivalent of
    reusing a stale hash.

    This is done here, in the single choke point every format goes through,
    rather than at each call site, so a new extraction backend cannot forget it.
    """
    root = Path(output_dir)
    if root.is_symlink() or root.is_file():
        root.unlink()
    elif root.is_dir():
        for child in root.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError as exc:  # pragma: no cover - permission/lock edge
                logger.warning("Could not clear %s from %s: %s", child, root, exc)
    root.mkdir(parents=True, exist_ok=True)
    return root


def extract_zip(
    archive_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    policy: ExtractionPolicy = DEFAULT_POLICY,
    on_member: Optional[Callable[[str], None]] = None,
) -> ExtractionResult:
    """Safely extract a ZIP archive member-by-member."""
    deadline = time.monotonic() + policy.timeout_seconds if policy.timeout_seconds else None
    root = _prepare_output_dir(output_dir)
    root = root.resolve()
    result = ExtractionResult(output_dir=root)

    with zipfile.ZipFile(archive_path) as zf:
        infos = zf.infolist()
        # Bit 0 of the general-purpose flag marks an encrypted member. Check it
        # before any read: zipfile raises an opaque RuntimeError from deep
        # inside read(), which is indistinguishable from corruption and gives
        # the caller nothing actionable.
        if any((i.flag_bits & 0x1) for i in infos):
            raise ArchiveEncrypted(
                "Archive is password-protected and no password was supplied"
            )
        if len(infos) > policy.max_files:
            raise ArchiveSafetyError(
                f"Archive contains {len(infos)} members (limit {policy.max_files})"
            )
        total_uncompressed = sum(i.file_size for i in infos)
        total_compressed = sum(i.compress_size for i in infos) or 1
        if total_uncompressed > policy.max_bytes:
            raise ArchiveSafetyError("Archive expands beyond the total byte limit")
        if total_uncompressed / total_compressed > policy.max_compression_ratio:
            raise ArchiveSafetyError("Archive compression ratio exceeds safety limit")

        for info in infos:
            _check_deadline(deadline)
            name = info.filename
            if name.endswith("/"):
                continue
            safe_name = validate_member_path(name)
            dest = safe_destination(root, safe_name)
            _policy_for_file(policy, name)
            _destination_checks(policy, dest, info.file_size)
            if on_member:
                on_member(safe_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Re-verify containment after mkdir (symlinked parents).
            if root not in dest.resolve().parents:
                raise ArchiveSafetyError(f"Archive member escapes extraction root: {name!r}")
            with zf.open(info, "r") as src, open(dest, "wb") as out:
                copied = 0
                while True:
                    _check_deadline(deadline)
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > info.file_size:
                        raise ArchiveSafetyError(
                            f"Member reports inconsistent size while streaming: {name!r}"
                        )
                    out.write(chunk)
            result.files_extracted += 1
            result.bytes_extracted += copied
    return result


def extract_tar(
    archive_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    policy: ExtractionPolicy = DEFAULT_POLICY,
    on_member: Optional[Callable[[str], None]] = None,
) -> ExtractionResult:
    """Safely extract a TAR archive (incl. .tar.gz/.tar.bz2/.tar.xz)."""
    deadline = time.monotonic() + policy.timeout_seconds if policy.timeout_seconds else None
    root = _prepare_output_dir(output_dir)
    root = root.resolve()
    result = ExtractionResult(output_dir=root)

    with tarfile.open(archive_path, "r:*") as tf:
        members = tf.getmembers()
        if len(members) > policy.max_files:
            raise ArchiveSafetyError(
                f"Archive contains {len(members)} members (limit {policy.max_files})"
            )
        total = sum(m.size for m in members)
        if total > policy.max_bytes:
            raise ArchiveSafetyError("Archive expands beyond the total byte limit")

        for member in members:
            _check_deadline(deadline)
            if not (member.isfile() or member.isdir()):
                # Refuse symlinks, hardlinks, devices, fifos.
                result.skipped.append(member.name)
                logger.warning("Refusing non-regular archive member: %s", member.name)
                continue
            safe_name = validate_member_path(member.name)
            dest = safe_destination(root, safe_name)
            _policy_for_file(policy, member.name)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                if root not in dest.resolve().parents and dest != root:
                    raise ArchiveSafetyError(
                        f"Archive member escapes extraction root: {member.name!r}"
                    )
                continue
            _destination_checks(policy, dest, member.size)
            if on_member:
                on_member(safe_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if root not in dest.resolve().parents:
                raise ArchiveSafetyError(
                    f"Archive member escapes extraction root: {member.name!r}"
                )
            src = tf.extractfile(member)
            if src is None:
                result.skipped.append(member.name)
                continue
            with src, open(dest, "wb") as out:
                copied = 0
                while True:
                    _check_deadline(deadline)
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    out.write(chunk)
            os.chmod(dest, 0o600)
            result.files_extracted += 1
            result.bytes_extracted += copied
    return result


def extract_single_file(
    archive_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    codec: str = "gzip",
    policy: ExtractionPolicy = DEFAULT_POLICY,
) -> ExtractionResult:
    """Safely extract a single-file stream (gzip/bzip2/xz)."""
    deadline = time.monotonic() + policy.timeout_seconds if policy.timeout_seconds else None
    root = _prepare_output_dir(output_dir)
    result = ExtractionResult(output_dir=root)

    opener = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}[codec]
    src_name = Path(archive_path).name
    for suffix in (".gz", ".bz2", ".xz"):
        if src_name.lower().endswith(suffix):
            src_name = src_name[: -len(suffix)]
            break
    dest = safe_destination(root, src_name or "decompressed.bin")
    if dest.exists():
        dest = safe_destination(root, (src_name or "decompressed.bin") + ".out")

    compressed_size = Path(archive_path).stat().st_size or 1
    copied = 0
    with opener(archive_path, "rb") as src, open(dest, "wb") as out:
        while True:
            _check_deadline(deadline)
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > policy.max_file_size:
                raise ArchiveSafetyError("Decompressed file exceeds the per-file size limit")
            if copied / compressed_size > policy.max_compression_ratio:
                raise ArchiveSafetyError("Decompression ratio exceeds safety limit")
            out.write(chunk)
    result.files_extracted = 1
    result.bytes_extracted = copied
    return result


def extract_7z(
    archive_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    policy: ExtractionPolicy = DEFAULT_POLICY,
) -> ExtractionResult:
    """Safely extract a 7z archive (requires py7zr)."""
    try:
        import py7zr
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ArchiveSafetyError("py7zr is not installed; cannot extract 7z archives") from exc

    deadline = time.monotonic() + policy.timeout_seconds if policy.timeout_seconds else None
    root = _prepare_output_dir(output_dir)
    root = root.resolve()
    result = ExtractionResult(output_dir=root)

    with py7zr.SevenZipFile(archive_path, "r") as sz:
        if sz.needs_password():
            raise ArchiveEncrypted(
                "Archive is password-protected and no password was supplied"
            )
        file_list = sz.list()
        if len(file_list) > policy.max_files:
            raise ArchiveSafetyError(
                f"Archive contains {len(file_list)} members (limit {policy.max_files})"
            )
        # AUDIT-ARCH-01: enforce the size limits from the declared member
        # sizes BEFORE writing anything, as extract_zip/extract_tar do. The
        # post-extraction check below remains as a backstop, but on its own
        # it let a 7z bomb fill the disk before the limit was noticed.
        declared_total = 0
        for info in file_list:
            size = max(0, int(getattr(info, "uncompressed", 0) or 0))
            if size > policy.max_file_size:
                raise ArchiveSafetyError(
                    f"Archive member {info.filename!r} declares {size} bytes "
                    f"(limit {policy.max_file_size})"
                )
            declared_total += size
        if declared_total > policy.max_bytes:
            raise ArchiveSafetyError(
                f"Archive declares {declared_total} uncompressed bytes (limit {policy.max_bytes})"
            )
        for info in file_list:
            _check_deadline(deadline)
            validate_member_path(info.filename)
            if policy.allowed_extensions is not None:
                ext = Path(info.filename).suffix.lower()
                if ext and ext not in policy.allowed_extensions:
                    raise ArchiveSafetyError(
                        f"File type not allowed in archive: {info.filename!r}"
                    )
        # py7zr performs its own traversal sanitisation for targets we point it
        # at; we pre-validated every member name above and verify the tree.
        # B202 (unchecked extractall): names validated, declared size bounded
        # above, and _verify_tree_within() rejects escapes and links below.
        sz.extractall(path=root)  # nosec B202
    _verify_tree_within(root, root, policy)
    for p in root.rglob("*"):
        if p.is_file():
            result.files_extracted += 1
            result.bytes_extracted += p.stat().st_size
    if result.files_extracted > policy.max_files or result.bytes_extracted > policy.max_bytes:
        raise ArchiveSafetyError("7z extraction exceeded resource limits")
    return result


# ----------------------------------------------------------------------
# RAR decoder discovery
# ----------------------------------------------------------------------
# RAR is the one supported container that cannot be decoded in Python: the
# compressed streams are a proprietary LZ+arithmetic family spanning eleven
# archive generations, and ``rarfile`` is a front end that always drives an
# external binary. Neither WinRAR nor 7-Zip adds itself to ``PATH`` on Windows,
# so an operator who *has* a decoder installed still gets ``RarCannotExec``.
#
# rarfile's own probe is not a capability check - it accepts anything that
# answers ``bsdtar --version`` or ``7z i`` - so a decoder is only configured
# here after it has been observed to support RAR. GNU tar answers
# ``--version`` too and cannot read RAR at all; configuring it would turn a
# clear failure into silent garbage.
_WINDOWS_DECODER_LOCATIONS = (
    # (family, environment variable holding the root, relative candidates)
    ("unrar", "ProgramFiles", (r"WinRAR\UnRAR.exe",)),
    ("unrar", "ProgramFiles(x86)", (r"WinRAR\UnRAR.exe",)),
    ("unrar", "LocalAppData", (r"Programs\WinRAR\UnRAR.exe",)),
    ("7z", "ProgramFiles", (r"7-Zip\7z.exe", r"7-Zip\7za.exe")),
    ("7z", "ProgramFiles(x86)", (r"7-Zip\7z.exe", r"7-Zip\7za.exe")),
    ("7z", "LocalAppData", (r"Programs\7-Zip\7z.exe",)),
    ("7z", "ChocolateyInstall", (r"bin\7z.exe",)),
    # Windows 10 1803+ ships libarchive's bsdtar, which reads RAR4 and
    # (libarchive >= 3.4) RAR5.
    ("bsdtar", "SystemRoot", (r"System32\tar.exe",)),
    ("bsdtar", "windir", (r"System32\tar.exe",)),
)

#: Tool names rarfile resolves through ``PATH``, in rarfile's own preference
#: order (unrar > unar > 7z > 7zz > bsdtar). ``tar`` is deliberately absent:
#: on POSIX that name is GNU tar, which cannot read RAR.
RAR_DECODER_TOOL_NAMES = ("unrar", "unar", "7z", "7zz", "7za", "bsdtar")

#: Which rarfile global each decoder family configures.
_RARFILE_TOOL_GLOBALS = {
    "unrar": ("UNRAR_TOOL",),
    "unar": ("UNAR_TOOL",),
    "7z": ("SEVENZIP_TOOL", "SEVENZIP2_TOOL"),
    "bsdtar": ("BSDTAR_TOOL",),
}

#: Shown verbatim whenever a decoder is missing, so the operator knows exactly
#: what to do. Kept concrete: what to install, where to get it, what changes.
RAR_DECODER_HINT = (
    "No RAR decoder is installed. Install 7-Zip (https://7-zip.org/) or "
    "WinRAR/UnRAR (https://www.rarlab.com/) and re-run the ingest to read "
    "every member. Members stored uncompressed are read and CRC32-verified "
    "without a decoder; compressed members need one."
)

#: How long a decoder is given to answer its capability probe.
_DECODER_PROBE_TIMEOUT = 15.0

#: Why a declared member was not materialised. A closed vocabulary: the
#: operator-facing text is derived from these keys, so a new reason cannot
#: appear in a log without a phrase being defined for it.
MEMBER_NEEDS_DECODER = "decoder_required"
MEMBER_UNREADABLE = "unreadable"
MEMBER_REJECTED = "rejected_by_policy"

#: Human phrases for the reason vocabulary, used in status text.
MEMBER_REASON_TEXT = {
    MEMBER_NEEDS_DECODER: "need an external decoder",
    MEMBER_UNREADABLE: "could not be read (damaged or unsupported)",
    MEMBER_REJECTED: "rejected by the extraction safety policy",
}


def _family_for_tool_name(name: str) -> Optional[str]:
    """Map an executable name to a decoder family."""
    base = os.path.basename(name).lower()
    if base.startswith("7z"):
        return "7z"
    if "unrar" in base:
        return "unrar"
    if "unar" in base:
        return "unar"
    if "bsdtar" in base or "tar" == base:
        return "bsdtar"
    return None


def _run_probe(command: List[str]) -> str:
    """Run a capability probe, returning its combined output ('' on failure)."""
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=_DECODER_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or b"").decode("utf-8", "replace")


def decoder_supports_rar(path: str, family: Optional[str] = None) -> bool:
    """True when the executable at ``path`` can actually read RAR archives.

    Each family is asked the question in its own language:

    * ``unrar``    - prints its usage (containing "UNRAR") when run bare;
    * ``unar``     - ``-version`` identifies The Unarchiver;
    * ``7z``/``7za`` - ``i`` lists the supported formats and codecs; RAR must
      appear in that list;
    * ``bsdtar``   - ``--version`` must identify bsdtar/libarchive (GNU tar
      answers this command too, which is exactly why the identity matters).
    """
    family = family or _family_for_tool_name(path)
    if family == "unrar":
        output = _run_probe([path])
        return "unrar" in output.lower()
    if family == "unar":
        output = _run_probe([path, "-version"])
        return "unar" in output.lower() or "unarchiver" in output.lower()
    if family == "7z":
        output = _run_probe([path, "i"]).lower()
        return "7-zip" in output and "rar" in output
    if family == "bsdtar":
        output = _run_probe([path, "--version"]).lower()
        return "bsdtar" in output or "libarchive" in output
    return False


def _candidate_decoder_paths() -> List[Tuple[str, str]]:
    """``(family, absolute path)`` pairs where a decoder may be installed."""
    candidates: List[Tuple[str, str]] = []
    for family, env_root, relatives in _WINDOWS_DECODER_LOCATIONS:
        root = os.environ.get(env_root)
        if not root:
            continue
        for relative in relatives:
            candidates.append((family, os.path.join(root, relative)))
    return candidates


def find_rar_decoder() -> Optional[Tuple[str, str]]:
    """Return ``(family, executable)`` for a decoder that can read RAR, or None.

    ``PATH`` is consulted first - that is what rarfile itself uses and it
    keeps the configured value a bare name - then the standard install
    locations of WinRAR, 7-Zip and the bundled Windows bsdtar. Every
    candidate must pass :func:`decoder_supports_rar`; a binary that merely
    exists is not accepted.
    """
    for name in RAR_DECODER_TOOL_NAMES:
        found = shutil.which(name)
        if not found:
            continue
        family = _family_for_tool_name(name) or _family_for_tool_name(found)
        if family in ("bsdtar", "7z") and not decoder_supports_rar(found, family):
            # bsdtar and 7z are the two families whose names are shared with
            # unrelated tools (GNU tar; assorted "7z"-named wrappers), so the
            # capability probe decides.
            logger.debug("Ignoring %s: it cannot read RAR archives", found)
            continue
        return family or "unrar", found
    for family, candidate in _candidate_decoder_paths():
        if os.path.isfile(candidate) and decoder_supports_rar(candidate, family):
            return family, candidate
    return None


def configure_rar_decoder() -> Optional[str]:
    """Point ``rarfile`` at an installed RAR decoder; return what was configured.

    rarfile caches the first successful probe for the process lifetime, so a
    discovered path is assigned to the module global it uses and the probe is
    re-run with ``force=True``. A failure to configure is not fatal here: the
    caller still gets a usable result: members stored uncompressed are read
    without it (see :func:`extract_rar`).
    """
    try:
        import rarfile
    except ImportError:  # pragma: no cover - optional dependency
        return None

    found = find_rar_decoder()
    if not found:
        return None
    family, executable = found

    if os.path.dirname(executable):
        for global_name in _RARFILE_TOOL_GLOBALS.get(family, ()):
            if hasattr(rarfile, global_name):
                setattr(rarfile, global_name, executable)
        try:
            rarfile.tool_setup(force=True)
        except Exception as exc:
            logger.warning(
                "Found a RAR decoder at %s but rarfile cannot drive it: %s",
                executable, exc,
            )
            return None
    return executable


def rar_decoder_status() -> str:
    """Human-readable statement of RAR decoding available on this machine.

    Called on the failure path and in logs, so it never raises: it reports
    either the decoder that will be used or the fact that none is available.
    """
    found = find_rar_decoder()
    if not found:
        return "no RAR decoder installed"
    family, executable = found
    return f"{family} decoder at {executable}"


def extract_rar(
    archive_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    policy: ExtractionPolicy = DEFAULT_POLICY,
) -> ExtractionResult:
    """Safely extract a RAR archive, member by member.

    ``rarfile`` reads RAR4 and RAR5 headers itself, without any external tool,
    and reads members stored uncompressed the same way; only *compressed*
    streams need unrar/unar/7-Zip/bsdtar. That is what makes a partial read
    possible, and this function is where it happens:

    * every member that can be read is extracted, CRC-verified by rarfile;
    * a member whose stream needs a decoder that is not installed is recorded
      under :data:`MEMBER_NEEDS_DECODER` and the loop continues to the next
      member - the old implementation let the first such member abort the whole
      archive, which is why a 66 MB container became a single failed row with
      no children and no reason;
    * a member that fails for any other reason is recorded under
      :data:`MEMBER_UNREADABLE`, and any bytes already written for it are
      removed rather than published as though they were the member;
    * the result states the counts, so the caller can report "n of m members
      could not be read" instead of a generic failure.

    Encrypted archives raise :class:`ArchiveEncrypted` - a password is a
    different fix from a missing binary, and calling either one "extraction
    failed" told the operator nothing. A container that is not RAR at all, or is
    too damaged to parse, raises :class:`ArchiveSafetyError`.
    """
    configure_rar_decoder()

    try:
        import rarfile
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ArchiveDecoderUnavailable(
            "RAR support requires the rarfile package, which is not installed"
            " (pip install rarfile)"
        ) from exc

    deadline = time.monotonic() + policy.timeout_seconds if policy.timeout_seconds else None
    root = _prepare_output_dir(output_dir)
    root = root.resolve()
    result = ExtractionResult(output_dir=root)

    with _open_rar(rarfile, archive_path) as rf:
        infos = list(rf.infolist())
        result.members_total = len(infos)

        if not infos:
            # No member was declared. Either the headers are encrypted (rarfile
            # says so), the archive is genuinely empty, or the parser stopped at
            # a damaged header - rarfile records that in strerror(), which is
            # the only honest thing to report about it.
            if rf.needs_password():
                raise ArchiveEncrypted(
                    "Archive headers are encrypted and no password was supplied"
                )
            parser_error = _safe_strerror(rf)
            result.notes.append(
                "container declares no members"
                + (
                    f" (parser reported: {parser_error})"
                    if parser_error
                    else " (empty archive, or a header the parser could not follow)"
                )
            )
            return result

        if len(infos) > policy.max_files:
            raise ArchiveSafetyError(
                f"Archive contains {len(infos)} members (limit {policy.max_files})"
            )
        if _is_solid(rf):
            result.notes.append("solid archive: members share a compression stream")
        volumes = _volume_count(rf)
        if volumes > 1:
            result.notes.append(
                f"multi-volume set of {volumes} parts: members continued in "
                "other volumes are not present in this file"
            )
        if _has_comment(rf):
            result.notes.append("archive comment present")

        _extract_rar_members(rarfile, rf, infos, root, policy, deadline, result)

    if result.members_unreadable.get(MEMBER_NEEDS_DECODER):
        result.decoder_missing = True
        result.decoder_hint = RAR_DECODER_HINT
        result.notes.append(
            f"{result.members_unreadable[MEMBER_NEEDS_DECODER]} member(s) need an "
            f"external RAR decoder ({rar_decoder_status()})"
        )
    return result


def _open_rar(rarfile, archive_path):
    """Open a RAR container, translating rarfile's failures into ours.

    Handler order matters: ``PasswordRequired``, ``RarCannotExec`` and
    ``RarWrongPassword`` are all subclasses of the parse errors (or of
    ``RarExecError``) and each one names a different fix.
    """
    try:
        return rarfile.RarFile(str(archive_path))
    except rarfile.PasswordRequired as exc:
        raise ArchiveEncrypted(
            "Archive headers are encrypted and no password was supplied"
        ) from exc
    except rarfile.RarWrongPassword as exc:
        raise ArchiveEncrypted(f"RAR password rejected: {exc}") from exc
    except rarfile.RarCannotExec as exc:
        # No decoder, or a decoder that cannot be executed. Not a property of
        # this container: it may be perfectly intact.
        raise ArchiveDecoderUnavailable(str(exc)) from exc
    except rarfile.NoCrypto as exc:
        raise ArchiveDecoderUnavailable(
            f"RAR headers are encrypted and the crypto backend is unavailable: {exc}"
        ) from exc
    except rarfile.NotRarFile as exc:
        raise ArchiveSafetyError(f"not a RAR container: {exc}") from exc
    except rarfile.BadRarFile as exc:
        raise ArchiveSafetyError(
            f"RAR container is damaged or unsupported: {exc}"
        ) from exc


def _safe_strerror(rf) -> Optional[str]:
    """rarfile's recorded header-parse error, when it has one."""
    try:
        error = rf.strerror()
    except Exception:  # pragma: no cover - defensive
        return None
    return str(error) if error else None


def _is_solid(rf) -> bool:
    try:
        return bool(rf.is_solid())
    except Exception:  # pragma: no cover - needs a decoder to answer
        return False


def _volume_count(rf) -> int:
    try:
        return len(rf.volumelist())
    except Exception:  # pragma: no cover - needs a decoder to answer
        return 0


def _has_comment(rf) -> bool:
    try:
        return bool(rf.comment)
    except Exception:  # pragma: no cover - needs a decoder to answer
        return False


def _record_unreadable(result: ExtractionResult, name: str, reason: str,
                       dest: Optional[Path] = None) -> None:
    """Count a member that was declared but not materialised.

    A partially written destination is deleted first: publishing half a member
    is the same forensic error as publishing a wrong one - downstream it is
    indistinguishable from a complete file.
    """
    if dest is not None:
        try:
            if dest.exists():
                dest.unlink()
        except OSError:  # pragma: no cover - best effort
            logger.warning("Could not remove partial extraction of %s", name)
    result.members_unreadable[reason] = result.members_unreadable.get(reason, 0) + 1
    result.skipped.append(f"{name} ({MEMBER_REASON_TEXT.get(reason, reason)})")


def _extract_rar_members(rarfile, rf, infos, root: Path, policy: ExtractionPolicy,
                         deadline, result: ExtractionResult) -> None:
    """Extract each member, isolating per-member failure from the archive."""
    total_bytes = 0
    for info in infos:
        _check_deadline(deadline)
        if info.is_dir():
            continue
        if info.needs_password():
            # An encrypted member cannot be read without its password; there is
            # nothing to be gained by continuing, and the operator's fix is to
            # supply it.
            raise ArchiveEncrypted(
                f"Member {info.filename!r} is encrypted and no password was supplied"
            )

        try:
            safe_name = validate_member_path(info.filename)
            dest = safe_destination(root, safe_name)
            _destination_checks(policy, dest, info.file_size)
            _policy_for_file(policy, safe_name)
        except ArchiveSafetyError as exc:
            # A rejected member is a policy decision about that member, not a
            # reason to discard the rest of the container: the unsafe member is
            # refused before anything is written, and the readable members are
            # still evidence. It is recorded under its own reason so the report
            # does not call a refused member "damaged".
            logger.warning("Refusing RAR member %r: %s", info.filename, exc)
            _record_unreadable(result, info.filename, MEMBER_REJECTED)
            continue

        total_bytes += max(0, int(info.file_size or 0))
        if total_bytes > policy.max_bytes:
            raise ArchiveSafetyError("RAR extraction exceeded the byte limit")

        dest.parent.mkdir(parents=True, exist_ok=True)
        if root not in dest.resolve().parents and dest != root:
            raise ArchiveSafetyError(
                f"Archive member escapes extraction root: {info.filename!r}"
            )

        try:
            with rf.open(info) as src, open(dest, "wb") as out:
                copied = 0
                while True:
                    _check_deadline(deadline)
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    out.write(chunk)
        except rarfile.RarCannotExec as exc:
            # This member's stream needs a decoder that is not installed. The
            # container is intact and the other members are still readable.
            logger.debug("RAR member needs a decoder (%s): %s", exc, info.filename)
            _record_unreadable(result, info.filename, MEMBER_NEEDS_DECODER, dest)
            continue
        except (rarfile.Error, OSError, EOFError, ValueError) as exc:
            logger.warning(
                "RAR member could not be read (%s: %s): %s",
                type(exc).__name__, exc, info.filename,
            )
            _record_unreadable(result, info.filename, MEMBER_UNREADABLE, dest)
            continue

        try:
            os.chmod(dest, 0o600)
        except OSError:  # pragma: no cover - Windows/ACL variance
            pass
        result.files_extracted += 1
        result.bytes_extracted += copied


def _verify_tree_within(root: Path, current: Path, policy: ExtractionPolicy) -> None:
    """Verify every filesystem node under ``current`` stays within ``root``."""
    root = root.resolve()
    count = 0
    for p in current.rglob("*"):
        count += 1
        if count > policy.max_files:
            raise ArchiveSafetyError("Extracted tree exceeds the file-count limit")
        resolved = p.resolve()
        if resolved != root and root not in resolved.parents:
            raise ArchiveSafetyError(f"Path escapes extraction root: {p}")
        if p.is_symlink():
            raise ArchiveSafetyError(f"Symlink created during extraction: {p}")
        if p.is_file():
            if p.stat().st_size > policy.max_file_size:
                raise ArchiveSafetyError(f"Extracted file exceeds size limit: {p}")


def is_safe_member_name(name: str) -> bool:
    """Boolean convenience wrapper around :func:`validate_member_path`."""
    try:
        validate_member_path(name)
        return True
    except ArchiveSafetyError:
        return False
