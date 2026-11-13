"""Content identity must never be fabricated (HASH-01).

Defect: ``core.file_utils.get_standardized_metadata`` hashed files under 100 MB
for real, but for larger files substituted
``sha256(f"{absolute_path}|{size}|{mtime}")``. That value is 64 lowercase hex
characters, so ``pipeline.storage_pipeline`` accepted it as the content
identity - it only rejected 'N/A', 'SKIPPED_LARGE_FILE' and 'ERROR'.

Measured before the fix, two byte-identical 105 MB files:

    real content sha256 (both) = 076bc278...9d2822     <- identical, correct
    Metadata['hash'] a.bin     = 72cc7a03...ca57       <- path+mtime derived
    Metadata['hash'] b.bin     = 0ae4e4cb...f14fb      <- different

so identical content was stored twice under two different "content hashes",
and renaming or touching a large file changed its identity.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.file_utils as file_utils  # noqa: E402
from core.file_utils import get_standardized_metadata  # noqa: E402
from core.hashing import (  # noqa: E402
    DEFAULT_ALGORITHM,
    SUPPORTED_ALGORITHMS,
    hash_file,
    is_valid_digest,
)

REAL_SHA256 = "076bc278798bc72e32d5515ad9e33db60545e707f5cd2e9da7ebdfb7bf9d2822"


class TestIsValidDigest:
    @pytest.mark.parametrize(
        "value",
        [
            REAL_SHA256,                                        # sha256
            "a" * 32,                                           # md5
            "a" * 40,                                           # sha1
            "a" * 128,                                          # blake2b
            REAL_SHA256.upper(),                                # case tolerated
            f"  {REAL_SHA256}  ",                               # surrounding space
        ],
    )
    def test_accepts_well_formed_digests(self, value):
        assert is_valid_digest(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "SKIPPED_LARGE_FILE",
            "N/A",
            "NA",
            "ERROR",
            "none",
            "null",
            "unknown",
            "",
            "   ",
            None,
            12345,
            b"0" * 64,
            "deadbeef",                                         # too short
            "g" * 64,                                           # not hex
            "076bc278798bc72e32d5515ad9e33db60545e707f5cd2e9da7ebdfb7bf9d282",  # 63
            REAL_SHA256 + "0",                                  # 65
        ],
    )
    def test_rejects_non_identities(self, value):
        assert is_valid_digest(value) is False

    def test_supported_algorithms_all_have_a_length(self):
        """Every advertised algorithm must produce an accepted digest shape."""
        import hashlib

        for algorithm in SUPPORTED_ALGORITHMS:
            digest = hashlib.new(algorithm, b"probe").hexdigest()
            assert is_valid_digest(digest), algorithm
        assert DEFAULT_ALGORITHM == "sha256"


class TestMetadataNeverFabricatesIdentity:
    def test_small_file_is_hashed_for_real(self, tmp_path):
        target = tmp_path / "small.bin"
        target.write_bytes(b"identical payload")
        metadata = get_standardized_metadata(str(target))
        assert metadata["hash"] == hash_file(str(target))

    def test_large_file_defers_instead_of_fabricating(self, tmp_path, monkeypatch):
        """The core regression: no path/mtime-derived pseudo-identity."""
        monkeypatch.setattr(file_utils, "HASH_INLINE_MAX_BYTES", 8)
        target = tmp_path / "large.bin"
        target.write_bytes(b"0123456789abcdef")  # 16 bytes > 8-byte threshold

        metadata = get_standardized_metadata(str(target))

        assert metadata["hash"] == file_utils.HASH_DEFERRED_SENTINEL
        assert metadata["hash"] != hash_file(str(target))
        # Must be rejected as an identity so storage recomputes it.
        assert is_valid_digest(metadata["hash"]) is False

    def test_deferred_hash_is_independent_of_path_and_mtime(self, tmp_path, monkeypatch):
        """Two identical large files at different paths must agree."""
        monkeypatch.setattr(file_utils, "HASH_INLINE_MAX_BYTES", 8)
        first = tmp_path / "a" / "payload.bin"
        second = tmp_path / "b" / "payload.bin"
        for path in (first, second):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"0123456789abcdef")

        hashes = {get_standardized_metadata(str(p))["hash"] for p in (first, second)}
        assert len(hashes) == 1
        assert hashes == {file_utils.HASH_DEFERRED_SENTINEL}

    def test_unreadable_file_is_marked_not_fabricated(self, tmp_path):
        """A file that cannot be hashed yields a sentinel, never a fake digest."""
        metadata = get_standardized_metadata(str(tmp_path / "missing.bin"))
        assert metadata["hash"] in ("N/A", "ERROR")
        assert is_valid_digest(metadata["hash"]) is False


class TestStorageIdentityResolution:
    """Replays the exact decision storage_pipeline makes, with real functions."""

    @staticmethod
    def resolve(metadata_hash, path):
        from core.hashing import is_valid_digest

        file_hash = metadata_hash
        if not is_valid_digest(file_hash):
            file_hash = hash_file(path)
        return file_hash

    def test_identical_large_files_collapse_to_one_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(file_utils, "HASH_INLINE_MAX_BYTES", 8)
        first = tmp_path / "a.bin"
        second = tmp_path / "b.bin"
        first.write_bytes(b"0123456789abcdef")
        second.write_bytes(b"0123456789abcdef")

        identities = {
            self.resolve(get_standardized_metadata(str(p))["hash"], str(p))
            for p in (first, second)
        }
        assert len(identities) == 1, "identical content must yield one identity"
        assert identities == {hash_file(str(first))}

    def test_renamed_large_file_keeps_its_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(file_utils, "HASH_INLINE_MAX_BYTES", 8)
        original = tmp_path / "original-name.bin"
        original.write_bytes(b"0123456789abcdef")
        renamed = tmp_path / "completely-different-name.bin"
        renamed.write_bytes(original.read_bytes())

        assert (
            self.resolve(get_standardized_metadata(str(original))["hash"], str(original))
            == self.resolve(get_standardized_metadata(str(renamed))["hash"], str(renamed))
        )

    def test_different_content_keeps_different_identities(self, tmp_path, monkeypatch):
        monkeypatch.setattr(file_utils, "HASH_INLINE_MAX_BYTES", 8)
        first = tmp_path / "a.bin"
        second = tmp_path / "b.bin"
        first.write_bytes(b"0123456789abcdef")
        second.write_bytes(b"0123456789abcdeF")

        identities = {
            self.resolve(get_standardized_metadata(str(p))["hash"], str(p))
            for p in (first, second)
        }
        assert len(identities) == 2

    def test_zero_byte_file_has_a_stable_identity(self, tmp_path):
        first = tmp_path / "empty1.bin"
        second = tmp_path / "empty2.bin"
        first.write_bytes(b"")
        second.write_bytes(b"")
        assert get_standardized_metadata(str(first))["hash"] == hash_file(str(first))
        assert (
            self.resolve(get_standardized_metadata(str(first))["hash"], str(first))
            == self.resolve(get_standardized_metadata(str(second))["hash"], str(second))
        )
