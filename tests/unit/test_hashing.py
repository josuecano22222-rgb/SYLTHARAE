"""Unit tests: content hashing (DB-03)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.hashing import hash_file, hash_bytes, file_identity, verify_file_hash, HashingError


def test_small_file_hash_matches_known_vector(tmp_path):
    f = tmp_path / "small.txt"
    f.write_bytes(b"hello world\n")
    # well-known sha256 of b"hello world\n"
    assert hash_file(f) == "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447"


def test_large_file_streamed_hash_matches_chunked_reference(tmp_path):
    # ~40 MiB is enough to prove streaming without slowing the suite.
    size = 40 * 1024 * 1024
    f = tmp_path / "large.bin"
    import hashlib

    ref = hashlib.sha256()
    with open(f, "wb") as out:
        block = bytes(range(256)) * 4096  # 1 MiB block
        written = 0
        while written < size:
            out.write(block)
            ref.update(block)
            written += len(block)
    assert hash_file(f) == ref.hexdigest()
    # constant memory: identity helper agrees
    h, sz = file_identity(f)
    assert h == ref.hexdigest()
    assert sz == size


def test_hash_failure_raises_instead_of_faking_identity(tmp_path):
    missing = tmp_path / "does-not-exist.bin"
    with pytest.raises(HashingError):
        hash_file(missing)


def test_unsupported_algorithm_rejected(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        hash_file(f, algorithm="rot13")


def test_verify_file_hash(tmp_path):
    f = tmp_path / "v.txt"
    f.write_bytes(b"abc")
    digest = hash_file(f)
    assert verify_file_hash(f, digest)
    assert not verify_file_hash(f, "0" * 64)
    assert not verify_file_hash(f, "")


def test_hash_bytes_matches_file_hash(tmp_path):
    f = tmp_path / "b.txt"
    f.write_bytes(b"payload")
    assert hash_bytes(b"payload") == hash_file(f)
