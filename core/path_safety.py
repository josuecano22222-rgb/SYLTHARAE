"""Filesystem path safety for ingestion (SEC-06).

The application must never allow a client to point ingestion at arbitrary
server filesystem locations. All server-side paths entering the ingestion
pipeline are validated against an explicit allowlist of configured ingestion
roots (``INGESTION_ROOTS`` environment variable, semicolon-separated).

Rules:
* The security boundary is the explicit root allowlist: a candidate is
  accepted only when it resolves inside one of the configured roots.
* Native Windows paths are first-class citizens: drive-qualified
  (``C:\\data\\inbox``) and UNC (``\\\\server\\share\\cases``) candidates are
  accepted when (and only when) they resolve under a configured root. There
  is deliberately no blanket rejection of drive letters - on Windows every
  absolute path is drive-qualified, so such a rejection would disable the
  feature entirely. Operators decide which drives/shares are roots.
* Containment matching is case-insensitive and separator-tolerant on Windows.
* Relative candidates are resolved against each configured root - never
  against the process working directory (which would make behavior depend
  on the launch directory).
* The resolved (symlink-followed) path must remain inside the root; ``..``
  traversal that escapes the root is rejected.
* Staged uploads (``APP_DATA_DIR/uploads``) are an application-managed input
  location and are always ingestible, independent of INGESTION_ROOTS.
* With no roots configured, server-path ingestion is disabled entirely
  (fail-closed); upload-based ingestion remains available.
"""

from __future__ import annotations

import logging
import ntpath
import os
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

# Module-level so unit tests can exercise Windows semantics on any OS
# (patch core.path_safety._WINDOWS). Never patch os.name itself - pathlib
# derives its concrete Path class from it.
_WINDOWS = os.name == "nt"


class PathSafetyError(ValueError):
    """Raised when a path is outside all approved ingestion roots."""


def configured_ingestion_roots() -> List[Path]:
    """Return the explicit list of approved ingestion roots.

    ``INGESTION_ROOTS`` is a semicolon-separated list. Windows examples::

        set INGESTION_ROOTS=C:\\data\\evidence;D:\\inbox
        setx INGESTION_ROOTS "C:\\data\\evidence;D:\\inbox"

    POSIX example::

        export INGESTION_ROOTS="/data/evidence;/srv/inbox"
    """
    raw = os.environ.get("INGESTION_ROOTS", "")
    roots: List[Path] = []
    for part in raw.split(";"):
        part = part.strip().strip('"')
        if part:
            roots.append(Path(part).expanduser().resolve())
    return roots


def _contained(candidate: Path, root: Path) -> bool:
    """True when *candidate* equals or lies beneath *root*.

    Windows correctness: paths are case-insensitive and ``/`` and ``\\`` are
    interchangeable, so the comparison normalizes via :mod:`ntpath` pure
    string operations (deterministic and unit-testable on every OS). On
    POSIX, pathlib containment is exact.
    """
    if _WINDOWS:
        a = ntpath.normpath(str(candidate)).casefold().replace("/", "\\")
        b = ntpath.normpath(str(root)).casefold().replace("/", "\\")
        if not b.endswith("\\"):
            b += "\\"
        # The root itself is contained; everything beneath it via the prefix.
        return a == b.rstrip("\\") or a.startswith(b)
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def validate_ingestion_path(
    candidate: str | os.PathLike,
    roots: Optional[Iterable[Path | str]] = None,
) -> Path:
    """Validate that *candidate* resolves inside an approved ingestion root.

    Returns the resolved :class:`~pathlib.Path` on success.
    Raises :class:`PathSafetyError` otherwise.
    """
    raw = str(candidate)
    if "\x00" in raw:
        raise PathSafetyError("Invalid path")

    allowed = [Path(r).expanduser().resolve() for r in roots] if roots is not None \
        else configured_ingestion_roots()

    # Staged uploads are a configured application input location: files the
    # authenticated user uploaded through the app itself are always
    # ingestible, independent of INGESTION_ROOTS (which gates arbitrary
    # server paths). The staging root is added BEFORE the fail-closed check
    # so upload-then-ingest works even when server-path ingestion is off.
    try:
        from core.app_paths import get_data_root

        staged_root = (Path(get_data_root()) / "uploads").resolve()
        if staged_root.is_dir():
            allowed = allowed + [staged_root]
    except Exception:
        pass

    if not allowed:
        raise PathSafetyError(
            "Server-path ingestion is disabled: no INGESTION_ROOTS are "
            "configured. Set INGESTION_ROOTS (semicolon-separated, e.g. "
            'INGESTION_ROOTS="C:\\data\\evidence;D:\\inbox") and restart the '
            "server, or upload files instead."
        )

    p = Path(raw).expanduser()

    # POSIX defence: drive-qualified and UNC shapes cannot refer to real
    # POSIX locations (a leading ``C:`` or ``\\`` would only ever be a
    # bizarre literal filename). Reject them when not running on Windows;
    # on Windows they are first-class and validated by containment below.
    if not _WINDOWS:
        if len(raw) >= 2 and raw[0].isascii() and raw[0].isalpha() and raw[1] == ":":
            raise PathSafetyError(
                "Drive-qualified paths are only valid on Windows; this server "
                "runs on POSIX - configure a POSIX path"
            )
        if raw.startswith("\\\\") or raw.startswith("//"):
            raise PathSafetyError(
                "UNC paths are only valid on Windows; this server runs on "
                "POSIX - configure a POSIX path"
            )

    try:
        if p.is_absolute():
            candidates = [p.resolve(strict=False)]
        else:
            # Relative candidates resolve against each configured root -
            # never against the process working directory.
            candidates = [(root / p).resolve(strict=False) for root in allowed]
    except OSError as exc:
        raise PathSafetyError("Path could not be resolved") from exc

    for cand in candidates:
        for root in allowed:
            if _contained(cand, root):
                return cand

    logger.warning("Rejected ingestion path outside approved roots: %s", raw)
    raise PathSafetyError("Path is outside all approved ingestion roots")


def validate_batch_paths(paths: Iterable[str | os.PathLike]) -> List[Path]:
    """Validate a batch; returns resolved paths. Raises on the first bad path."""
    return [validate_ingestion_path(p) for p in paths]
