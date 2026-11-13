"""
Checkpoints for archive members (nested readers).

A top-level run checkpoints the files it processes. Files found *inside* a
container (archive members, email attachments, embedded objects) are handled by
a nested reader that is created without a checkpoint file. Without one, a
restarted run re-extracts every container and re-processes every member, and
only the database duplicate check prevents double storage.

This module lets a nested reader find the checkpoint of the run that spawned
it and keep its own, stable, per-container checkpoint next to it:

* The top-level reader calls :func:`register_checkpoint_base` with its
  checkpoint file (or ``None`` when checkpointing is off), keyed by storage
  source and side. Registering ``None`` clears a previous run's entry, so a run
  with checkpointing off never inherits one.
* A nested reader calls :func:`member_checkpoint_file` with its container path.
  The result is ``<base>.members/<digest>.json``, where the digest is derived
  from :func:`container_identity`.

``container_identity`` must not depend on extraction time. Files under the
extraction base are identified by their path relative to that base and their
size. Their extraction-time mtime changes on every run, so it is ignored. Files
outside the extraction base keep their path, size and mtime.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_BASES: dict = {}


def _key(storage_source: Optional[str], storage_side: Optional[str]) -> Tuple[str, str]:
    return (str(storage_source or ""), str(storage_side or ""))


def register_checkpoint_base(storage_source, storage_side, checkpoint_file) -> None:
    """Record the top-level checkpoint file for this source/side (``None`` clears it)."""
    with _LOCK:
        _BASES[_key(storage_source, storage_side)] = (
            str(checkpoint_file) if checkpoint_file else None
        )


def container_identity(container_path: str) -> str:
    """Return an identity for a container that is stable across runs."""
    from core.path_utils import get_extraction_base_folder

    path = os.path.abspath(str(container_path))
    try:
        base = os.path.abspath(str(get_extraction_base_folder()))
    except Exception:
        base = None

    try:
        size = os.stat(path).st_size
    except OSError:
        size = -1

    if base and (path == base or path.startswith(base + os.sep)):
        # Extracted copy: path relative to the extraction base is stable.
        rel = os.path.relpath(path, base).replace('\\', '/').lower()
        return f"extracted:{rel}|{size}"

    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = -1
    return f"source:{path.replace(chr(92), '/').lower()}|{size}|{mtime}"


def member_checkpoint_file(storage_source, storage_side, container_path) -> Optional[Path]:
    """Checkpoint file for members of ``container_path``, or ``None`` if checkpointing is off."""
    with _LOCK:
        base = _BASES.get(_key(storage_source, storage_side))
    if not base or not container_path:
        return None
    base_path = Path(base)
    digest = hashlib.sha256(container_identity(container_path).encode('utf-8')).hexdigest()[:24]
    return base_path.with_name(base_path.stem + '.members') / f"{digest}.json"
