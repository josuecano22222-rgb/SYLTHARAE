"""Content-addressed file hashing - single authoritative implementation (DB-03).

Rules enforced here:

* Identity is **content** identity: streamed SHA-256 of the file bytes.
* Files of any size are hashed in fixed-size chunks (constant memory).
* Hashing failures are reported as exceptions - **never** silently replaced
  with metadata-derived or time-based pseudo-identifiers.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

#: Default chunk size: 1 MiB.  Memory use is O(chunk), not O(file).
DEFAULT_CHUNK_SIZE = 1024 * 1024

#: Algorithms supported by this service (validated allowlist).
SUPPORTED_ALGORITHMS = ("sha256", "sha1", "md5", "blake2b")
DEFAULT_ALGORITHM = "sha256"


class HashingError(RuntimeError):
    """Raised when a file cannot be hashed. Never fall back to fake identity."""


def _validate_algorithm(algorithm: str) -> str:
    algorithm = (algorithm or DEFAULT_ALGORITHM).lower().strip()
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"Unsupported hash algorithm {algorithm!r}; expected one of {SUPPORTED_ALGORITHMS}"
        )
    return algorithm


def hash_file(
    file_path: str | os.PathLike,
    algorithm: str = DEFAULT_ALGORITHM,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Stream a file through the chosen hash algorithm and return the hex digest.

    Memory-safe for files of any size. Raises :class:`HashingError` (a
    :class:`RuntimeError`) if the file cannot be read; callers must treat that
    as a processing failure rather than inventing an identity.
    """
    algorithm = _validate_algorithm(algorithm)
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    path = Path(file_path)

    def _digest() -> str:
        h = hashlib.new(algorithm)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    try:
        # Routed through the compute gateway: device selection and per-workload
        # metering in one place.  The CPU path is what is used whenever no
        # verified accelerator backend exists, and it produces exactly the same
        # digest - the gateway is told the workload, never the other way round,
        # so extraction identity cannot drift with the chosen device.
        from core.compute import WorkloadKind, submit_compute

        return submit_compute(WorkloadKind.HASHING, _digest)
    except HashingError:
        raise
    except OSError as exc:
        logger.error("Hashing failed for %s: %s", path, exc)
        raise HashingError(f"Unable to hash file: {exc.__class__.__name__}") from exc


#: Hex digest lengths for the supported algorithms. Used to reject values that
#: are not digests at all (sentinels, error strings, fabricated identities).
_DIGEST_LENGTHS = frozenset({32, 40, 64, 128})

#: Values that mean "no identity was computed" rather than being an identity.
NON_IDENTITY_VALUES = frozenset({
    "", "n/a", "na", "error", "skipped_large_file", "none", "null", "unknown",
})


def is_valid_digest(value) -> bool:
    """Return True only for a well-formed hex digest.

    HASH-01: content identity must never be a sentinel, an error marker, or a
    value fabricated from metadata. Callers that receive a hash from an
    upstream producer must validate it before trusting it as an identity.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip().lower()
    if candidate in NON_IDENTITY_VALUES or len(candidate) not in _DIGEST_LENGTHS:
        return False
    return all(char in "0123456789abcdef" for char in candidate)


def hash_bytes(data: bytes, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """Hash an in-memory byte string (used for stored content streams)."""
    algorithm = _validate_algorithm(algorithm)
    return hashlib.new(algorithm, data).hexdigest()


def verify_file_hash(
    file_path: str | os.PathLike, expected_hex: str, algorithm: str = DEFAULT_ALGORITHM
) -> bool:
    """Constant-shape verification of a file against an expected digest."""
    try:
        return hash_file(file_path, algorithm) == (expected_hex or "").lower()
    except (HashingError, ValueError):
        return False


def file_identity(file_path: str | os.PathLike) -> Tuple[str, int]:
    """Return ``(sha256_hex, size_bytes)`` for a file in a single pass."""
    path = Path(file_path)
    try:
        h = hashlib.new("sha256")
        size = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                h.update(chunk)
        return h.hexdigest(), size
    except OSError as exc:
        logger.error("Identity computation failed for %s: %s", path, exc)
        raise HashingError(f"Unable to hash file: {exc.__class__.__name__}") from exc
