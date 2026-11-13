"""Unit + security tests: archive extraction safety (SEC-05)."""

import io
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.archive_safety import (
    ArchiveSafetyError,
    ExtractionPolicy,
    extract_zip,
    extract_tar,
    validate_member_path,
    safe_destination,
)


def make_zip(tmp_path, entries: dict):
    zpath = tmp_path / "test.zip"
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return zpath


class TestMemberValidation:
    def test_rejects_absolute_path(self):
        with pytest.raises(ArchiveSafetyError):
            validate_member_path("/etc/passwd")

    def test_rejects_dotdot_traversal(self):
        with pytest.raises(ArchiveSafetyError):
            validate_member_path("../../etc/passwd")

    def test_rejects_inner_traversal(self):
        with pytest.raises(ArchiveSafetyError):
            validate_member_path("a/b/../../../etc/passwd")

    def test_rejects_drive_qualified(self):
        with pytest.raises(ArchiveSafetyError):
            validate_member_path("C:\\Windows\\system32\\evil.dll")

    def test_rejects_unc_path(self):
        with pytest.raises(ArchiveSafetyError):
            validate_member_path("\\\\server\\share\\evil.txt")

    def test_rejects_nul_bytes(self):
        with pytest.raises(ArchiveSafetyError):
            validate_member_path("good\x00name")

    def test_accepts_normal_relative(self):
        assert validate_member_path("a/b/c.txt") == "a/b/c.txt"

    def test_accepts_dot_segments(self):
        assert validate_member_path("./a/./b.txt") == "a/b.txt"


class TestZipExtraction:
    def test_normal_extraction(self, tmp_path):
        z = make_zip(tmp_path, {"a.txt": b"alpha", "sub/b.txt": b"beta"})
        out = tmp_path / "out"
        result = extract_zip(z, out)
        assert (out / "a.txt").read_bytes() == b"alpha"
        assert (out / "sub" / "b.txt").read_bytes() == b"beta"
        assert result.files_extracted == 2

    def test_zip_slip_rejected(self, tmp_path):
        z = make_zip(tmp_path, {"../../evil.txt": b"pwned"})
        out = tmp_path / "out"
        with pytest.raises(ArchiveSafetyError):
            extract_zip(z, out)
        assert not (tmp_path / "evil.txt").exists()

    def test_absolute_member_rejected(self, tmp_path):
        z = make_zip(tmp_path, {"/tmp/evil.txt": b"pwned"})
        out = tmp_path / "out"
        with pytest.raises(ArchiveSafetyError):
            extract_zip(z, out)

    def test_file_count_limit(self, tmp_path):
        entries = {f"f{i}.txt": b"x" for i in range(5)}
        z = make_zip(tmp_path, entries)
        out = tmp_path / "out"
        policy = ExtractionPolicy(max_files=3)
        with pytest.raises(ArchiveSafetyError):
            extract_zip(z, out, policy=policy)

    def test_compression_ratio_limit(self, tmp_path):
        z = make_zip(tmp_path, {"zeros.txt": b"0" * 1_000_000})
        out = tmp_path / "out"
        policy = ExtractionPolicy(max_compression_ratio=10)
        with pytest.raises(ArchiveSafetyError):
            extract_zip(z, out, policy=policy)

    def test_total_byte_limit(self, tmp_path):
        z = make_zip(tmp_path, {"big.txt": b"x" * 1000})
        out = tmp_path / "out"
        policy = ExtractionPolicy(max_bytes=100)
        with pytest.raises(ArchiveSafetyError):
            extract_zip(z, out, policy=policy)

    def test_timeout(self, tmp_path):
        z = make_zip(tmp_path, {"a.txt": b"x", "b.txt": b"y", "c.txt": b"z"})
        out = tmp_path / "out"
        policy = ExtractionPolicy(timeout_seconds=-1)  # already expired
        with pytest.raises(ArchiveSafetyError):
            extract_zip(z, out, policy=policy)


class TestTarExtraction:
    def _make_tar(self, tmp_path, entries):
        import tarfile

        tpath = tmp_path / "test.tar"
        with tarfile.open(tpath, "w") as tf:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return tpath

    def test_tar_slip_rejected(self, tmp_path):
        t = self._make_tar(tmp_path, {"../../evil.txt": b"pwned"})
        out = tmp_path / "out"
        with pytest.raises(ArchiveSafetyError):
            extract_tar(t, out)
        assert not (tmp_path / "evil.txt").exists()

    def test_tar_symlink_refused(self, tmp_path):
        import tarfile

        tpath = tmp_path / "symlink.tar"
        with tarfile.open(tpath, "w") as tf:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        out = tmp_path / "out"
        result = extract_tar(tpath, out)
        assert result.files_extracted == 0
        assert not (out / "link").exists()  # never materialised

    def test_normal_tar(self, tmp_path):
        t = self._make_tar(tmp_path, {"a.txt": b"data"})
        out = tmp_path / "out"
        extract_tar(t, out)
        assert (out / "a.txt").read_bytes() == b"data"


class TestDestinationResolution:
    def test_safe_destination_containment(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        dest = safe_destination(root, "sub/file.txt")
        assert root in dest.parents

    def test_safe_destination_escape(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ArchiveSafetyError):
            safe_destination(root, "../outside.txt")
