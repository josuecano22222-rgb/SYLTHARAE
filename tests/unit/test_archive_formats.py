"""Unit: the archive reader against every format it claims, plus hostile input.

get_supported_extensions() declares .zip .tar .gz .bz2 .rar .7z. This file
exercises each one with a real fixture built here, rather than trusting the
declaration, and records what actually happens.

Results as measured on this checkout (Python 3.11, rapidocr available, no
unrar/7z binaries on PATH):

    .zip       extracts     .tar      extracts
    .tar.gz    extracts     .tar.bz2  extracts
    .tar.xz    extracts     .gz       extracts
    .bz2       extracts     .7z       extracts (py7zr is a declared dependency)
    .rar       members stored uncompressed are read and CRC-verified with no
               external tool; compressed members are named as needing a
               decoder. Decoding a compressed member is NOT verified here -
               no unrar/7z/bsdtar exists on this machine - which is exactly
               the condition the reader now reports instead of failing the
               file. See test_rar_without_a_decoder_states_what_it_could_not_read
               and tests/unit/test_rar_without_decoder.py.

Safety behaviour, all asserted below: traversal in zip and tar is rejected and
nothing escapes the extraction root, an absolute member path is rejected, a
60 MiB-of-zeros bomb is rejected on compression ratio, malformed input yields an
error rather than a crash, and an encrypted archive is reported as encrypted
rather than as an opaque failure.
"""

import bz2
import gzip
import io
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.archive_safety import (  # noqa: E402
    ArchiveEncrypted,
    ArchiveSafetyError,
    ExtractionPolicy,
    extract_7z,
    extract_zip,
)
from reader_file.readers.read_archive import ArchiveFileReader  # noqa: E402

PAYLOAD_NAME = "inner.txt"
PAYLOAD = b"Marker ARCHIVEFORMAT payload inside\n" * 5


@pytest.fixture
def reader():
    return ArchiveFileReader()


def _members(path):
    out = []
    for root, _, files in os.walk(path):
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), path))
    return sorted(out)


def _read(reader, path, ext=None):
    return reader.read_file(
        {"path": str(path), "extension": ext or Path(path).suffix}
    )


# ---------------------------------------------------------------- builders
def build_zip(p):
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(PAYLOAD_NAME, PAYLOAD)


def _tar_add(t):
    ti = tarfile.TarInfo(PAYLOAD_NAME)
    ti.size = len(PAYLOAD)
    t.addfile(ti, io.BytesIO(PAYLOAD))


def build_tar(p):
    with tarfile.open(p, "w") as t:
        _tar_add(t)


def build_tar_gz(p):
    with tarfile.open(p, "w:gz") as t:
        _tar_add(t)


def build_tar_bz2(p):
    with tarfile.open(p, "w:bz2") as t:
        _tar_add(t)


def build_tar_xz(p):
    with tarfile.open(p, "w:xz") as t:
        _tar_add(t)


def build_gz(p):
    with gzip.open(p, "wb") as f:
        f.write(PAYLOAD)


def build_bz2(p):
    with bz2.open(p, "wb") as f:
        f.write(PAYLOAD)


def build_7z(p):
    py7zr = pytest.importorskip("py7zr")
    src = tempfile.mkdtemp()
    Path(src, PAYLOAD_NAME).write_bytes(PAYLOAD)
    with py7zr.SevenZipFile(p, "w") as z:
        z.write(Path(src, PAYLOAD_NAME), PAYLOAD_NAME)


# ------------------------------------------------------- format matrix
FORMATS = [
    ("a.zip", build_zip, [PAYLOAD_NAME]),
    ("a.tar", build_tar, [PAYLOAD_NAME]),
    ("a.tar.gz", build_tar_gz, [PAYLOAD_NAME]),
    ("a.tar.bz2", build_tar_bz2, [PAYLOAD_NAME]),
    ("a.tar.xz", build_tar_xz, [PAYLOAD_NAME]),
    ("a.gz", build_gz, None),      # single-stream: member name is derived
    ("a.bz2", build_bz2, None),
    ("a.7z", build_7z, [PAYLOAD_NAME]),
]


@pytest.mark.parametrize("name,builder,expected", FORMATS,
                         ids=[f[0] for f in FORMATS])
def test_declared_format_actually_extracts(reader, tmp_path, name, builder, expected):
    """Every declared extension must really extract, not merely be recognised."""
    path = tmp_path / name
    builder(str(path))
    res = _read(reader, path)
    assert res.get("status") == "success", f"{name}: {res.get('error')}"
    assert res.get("files_extracted", 0) >= 1, res
    members = _members(res["extraction_path"])
    assert members, f"{name} extracted nothing"
    if expected is not None:
        assert members == expected, (name, members)


def test_extracted_content_is_the_real_payload(reader, tmp_path):
    """Extraction must preserve bytes, not just produce a file."""
    path = tmp_path / "content.zip"
    build_zip(str(path))
    res = _read(reader, path)
    out = Path(res["extraction_path"]) / PAYLOAD_NAME
    assert out.read_bytes() == PAYLOAD


def test_files_extracted_is_surfaced_not_discarded(reader, tmp_path):
    """archive_safety counts members; the reader used to throw the count away."""
    path = tmp_path / "three.zip"
    with zipfile.ZipFile(path, "w") as z:
        for i in range(3):
            z.writestr(f"f{i}.txt", b"x" * 10)
    res = _read(reader, path)
    assert res["files_extracted"] == 3, res
    assert res["bytes_extracted"] == 30, res
    assert res["skipped_members"] == []


def test_valid_but_empty_archive_records_the_fact(reader, tmp_path):
    """An empty archive is legitimate, but must not look like a full one."""
    path = tmp_path / "empty_valid.zip"
    zipfile.ZipFile(str(path), "w").close()
    res = _read(reader, path)
    assert res["status"] == "success"
    assert res["files_extracted"] == 0
    assert res["extraction_info"]["warning"] == "archive_opened_but_no_members_extracted"


# ------------------------------------------------------------- traversal
def test_zip_traversal_is_rejected_and_nothing_escapes(reader, tmp_path):
    path = tmp_path / "slip.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("../../../tmp/zipslip_pwned.txt", b"escaped")
    res = _read(reader, path)
    assert res.get("status") != "success", res
    assert "traversal" in (res.get("error") or "").lower()
    assert not Path("/tmp/zipslip_pwned.txt").exists()


def test_tar_traversal_is_rejected_and_nothing_escapes(reader, tmp_path):
    path = tmp_path / "slip.tar"
    with tarfile.open(path, "w") as t:
        ti = tarfile.TarInfo("../../../tmp/tarslip_pwned.txt")
        ti.size = 7
        t.addfile(ti, io.BytesIO(b"escaped"))
    res = _read(reader, path)
    assert res.get("status") != "success", res
    assert "traversal" in (res.get("error") or "").lower()
    assert not Path("/tmp/tarslip_pwned.txt").exists()


def test_absolute_member_path_is_rejected(reader, tmp_path):
    path = tmp_path / "abs.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("/tmp/abs_pwned.txt", b"abs")
    res = _read(reader, path)
    assert res.get("status") != "success", res
    assert "absolute" in (res.get("error") or "").lower()
    assert not Path("/tmp/abs_pwned.txt").exists()


def test_traversal_still_escapes_at_the_safety_layer(tmp_path):
    """Belt and braces: the guard belongs in archive_safety, not the reader."""
    path = tmp_path / "slip2.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("../out.txt", b"x")
    with pytest.raises(ArchiveSafetyError):
        extract_zip(str(path), tmp_path / "out")


# -------------------------------------------------------- resource limits
def test_decompression_bomb_is_rejected_on_ratio(reader, tmp_path):
    """61 KB on disk expanding to 60 MiB must not be written out."""
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("bomb.bin", b"\x00" * (60 * 1024 * 1024))
    assert path.stat().st_size < 200_000, "fixture is not actually compressing"
    res = _read(reader, path)
    assert res.get("status") != "success", res
    assert "ratio" in (res.get("error") or "").lower(), res
    extracted = res.get("extraction_path")
    if extracted and Path(extracted).is_dir():
        assert not (Path(extracted) / "bomb.bin").exists()


def test_member_count_limit_is_enforced(tmp_path):
    path = tmp_path / "many.zip"
    with zipfile.ZipFile(path, "w") as z:
        for i in range(30):
            z.writestr(f"f{i}.txt", b"x")
    policy = ExtractionPolicy(max_files=10)
    with pytest.raises(ArchiveSafetyError, match="members"):
        extract_zip(str(path), tmp_path / "out", policy=policy)


def test_total_byte_limit_is_enforced(tmp_path):
    path = tmp_path / "big.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("big.bin", b"a" * 5000)
    policy = ExtractionPolicy(max_bytes=1000, max_compression_ratio=100000)
    with pytest.raises(ArchiveSafetyError, match="byte limit"):
        extract_zip(str(path), tmp_path / "out", policy=policy)


# --------------------------------------------------------------- malformed
@pytest.mark.parametrize("name,content", [
    ("trunc.zip", b"PK\x03\x04" + b"\x00" * 10),
    ("garbage.zip", b"this is not a zip file at all" * 4),
    ("zero.zip", b""),
    ("trunc.tar", b"ustar" + b"\x00" * 100),
    ("garbage.7z", b"7z\xbc\xaf\x27\x1c" + b"\x00" * 50),
])
def test_malformed_input_errors_without_raising(reader, tmp_path, name, content):
    """Worst input must fail safely: an error result, never an exception."""
    path = tmp_path / name
    path.write_bytes(content)
    res = _read(reader, path)
    assert res.get("status") != "success", res
    assert res.get("error"), f"{name} failed without recording why"


# --------------------------------------------------------------- encrypted
def test_encrypted_7z_is_reported_as_encrypted(reader, tmp_path):
    """A locked archive is actionable; it must not look like corruption."""
    py7zr = pytest.importorskip("py7zr")
    src = tmp_path / "src"
    src.mkdir()
    (src / "secret.txt").write_bytes(b"hidden payload")
    path = tmp_path / "locked.7z"
    with py7zr.SevenZipFile(str(path), "w", password="hunter2") as z:
        z.writeall(str(src), "")
    res = _read(reader, path)
    assert res.get("archive_locked") is True, res
    assert res["extraction_info"]["warning"] == "archive_password_protected"
    assert "password" in (res.get("error") or "").lower()


def test_encrypted_7z_is_detected_at_the_safety_layer(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    src = tmp_path / "src2"
    src.mkdir()
    (src / "s.txt").write_bytes(b"x")
    path = tmp_path / "locked2.7z"
    with py7zr.SevenZipFile(str(path), "w", password="hunter2") as z:
        z.writeall(str(src), "")
    with pytest.raises(ArchiveEncrypted):
        extract_7z(str(path), tmp_path / "out")


def test_encrypted_zip_is_reported_as_encrypted(reader, tmp_path):
    """pyzipper (a declared ``dev`` dependency) writes a real AES-encrypted ZIP.

    Imported directly, not via importorskip: a missing test dependency must
    fail the run, not quietly skip the encrypted-archive check.
    """
    import pyzipper
    path = tmp_path / "locked.zip"
    with pyzipper.AESZipFile(str(path), "w",
                             compression=pyzipper.ZIP_DEFLATED,
                             encryption=pyzipper.WZ_AES) as z:
        z.setpassword(b"hunter2")
        z.writestr("secret.txt", b"hidden payload")
    res = _read(reader, path)
    assert res.get("archive_locked") is True, res
    assert res["extraction_info"]["warning"] == "archive_password_protected"


# --------------------------------------------------------------- rAR status
def test_rar_without_a_decoder_states_what_it_could_not_read(reader):
    """RAR support, stated precisely - the previous version of this test was a
    placeholder that recorded RAR as UNVERIFIED.

    What is verified here (see tests/unit/test_rar_without_decoder.py for the
    full suite): rarfile parses RAR4/RAR5 headers in Python and reads members
    stored uncompressed without any external tool, an archive whose members
    need a decoder reports which ones and why, and no decoder is reported as
    corruption. What is NOT verified here: decoding compressed members, because
    no unrar/7z/bsdtar exists on this machine - the suite asserts that such a
    member is named rather than silently missing.

    This test keeps the old hostile input: a RAR signature followed by junk is
    not a container, and must not be reported as one.
    """
    assert ".rar" in reader.get_supported_extensions()
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "fake.rar"
    path.write_bytes(bytes.fromhex("526172211a070100") + b"\x00garbage" * 40)
    res = _read(reader, path)
    # Either it refuses, or it reports zero members and says so - never a
    # silent success with invented content.
    if res.get("status") == "success":
        assert res.get("files_extracted") == 0, res
        assert res["extraction_info"]["warning"] == (
            "archive_opened_but_no_members_extracted"
        )
    else:
        assert res.get("error")


def test_unsupported_archive_extension_is_rejected(reader, tmp_path):
    path = tmp_path / "mystery.xyz"
    path.write_bytes(b"\x01\x02\x03\x04 no known archive magic")
    res = reader.read_file({"path": str(path), "extension": ".xyz"})
    assert res.get("status") != "success"
    assert "unsupported" in (res.get("error") or "").lower()
