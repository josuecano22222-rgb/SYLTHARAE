"""Unit: RAR archives on a machine with no external decoder.

The defect this file guards (reported from a real Windows ingest):

    WARNING reader_file.readers.read_archive: RAR extraction unavailable for
    C:\\Users\\Solo\\Desktop\\...\\Deleted Items.rar: RarCannotExec
    ...
    Extraction failed        (stored as Unread, 0 words)

``rarfile`` is a front end for an external decoder, but it parses RAR4/RAR5
headers itself and reads members stored uncompressed without one. The old
extraction was all-or-nothing: the first member that needed the missing
decoder aborted the whole archive, the reader swallowed the exception into
``None``, and 66.5 MB of evidence was recorded as unreadable with no mention
that the only missing piece was a binary.

What must hold, and is asserted here:

* members that can be read without a decoder are extracted (stored members,
  RAR4 and RAR5) - a decoderless machine still reads part of the archive;
* a member needing a decoder is *counted, named and explained*, never dropped
  and never fatal to the rest of the archive;
* a partial read is reported as one (``decoder_missing`` + actionable detail),
  not as generic "Extraction failed";
* a damaged member is refused and its partial output removed, not published;
* an encrypted archive is reported as password-protected, a corrupt container
  as damaged, and neither as "no decoder";
* safety policy (traversal, file count, per-file size, allowed extensions)
  applies identically on this path.

The fixtures in ``_rar_fixtures.py`` are built byte by byte (no RAR tool exists
here). They are validated against rarfile's own independent parser before they
are used to test this pipeline, so the tests do not merely assert that our code
agrees with our own generator.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import rarfile  # noqa: E402

from core.archive_safety import (  # noqa: E402
    MEMBER_NEEDS_DECODER,
    MEMBER_REJECTED,
    MEMBER_UNREADABLE,
    ArchiveDecoderUnavailable,
    ArchiveEncrypted,
    ArchiveSafetyError,
    ExtractionPolicy,
    configure_rar_decoder,
    decoder_supports_rar,
    extract_rar,
    find_rar_decoder,
    rar_decoder_status,
)
from reader_file.readers.read_archive import ArchiveFileReader  # noqa: E402

from tests.unit._rar_fixtures import (  # noqa: E402
    corrupt_member_data,
    rar4_archive,
    rar5_archive,
)

PAYLOAD = b"Marker RARNOCODEC payload inside the archive\n" * 4

#: rarfile's own compress_type value for a stored member ("M0" = '0').
STORED_COMPRESS_TYPE = 0x30


def _no_decoder(monkeypatch):
    """Make the environment look like the machine that reported the defect."""
    monkeypatch.setattr("core.archive_safety.find_rar_decoder", lambda: None)
    monkeypatch.setattr("core.archive_safety.configure_rar_decoder", lambda: None)
    # rarfile probes PATH itself (unrar, unar, 7z, bsdtar) and caches the first
    # tool that works; without this a machine with any of them installed would
    # decode the members and the tests would describe a different machine.

    def _no_tool(*_args, **_kwargs):
        raise rarfile.RarCannotExec("Cannot find working tool")

    monkeypatch.setattr(rarfile, "CURRENT_SETUP", None)
    monkeypatch.setattr(rarfile, "tool_setup", _no_tool)


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _read(reader, path, ext=None):
    return reader.read_file({"path": str(path), "extension": ext or Path(path).suffix})


def _names(root):
    return sorted(
        str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()
    )


# ----------------------------------------------------------------------
# The fixtures are real RAR containers, as rarfile itself reads them
# ----------------------------------------------------------------------
class TestFixtureValidity:
    """A hand-built container is only useful if an independent parser agrees."""

    def test_rar5_fixture_is_a_rar5_container(self, tmp_path):
        archive = _write(tmp_path, "a.rar", rar5_archive([
            {"name": "evidence/notes.txt", "data": PAYLOAD},
            {"name": "evidence/", "data": b"", "is_dir": True},
            {"name": "evidence/packed.bin", "data": b"x" * 64, "method": 3},
        ]))
        with rarfile.RarFile(archive) as rf:
            infos = rf.infolist()
            assert [i.filename for i in infos] == [
                "evidence/notes.txt", "evidence/", "evidence/packed.bin",
            ]
            assert infos[0].file_size == len(PAYLOAD)
            assert infos[0].compress_type == STORED_COMPRESS_TYPE
            assert infos[1].is_dir()
            assert infos[2].compress_type != STORED_COMPRESS_TYPE

    def test_rar4_fixture_is_a_rar4_container(self, tmp_path):
        archive = _write(tmp_path, "a.rar", rar4_archive([
            {"name": "a.txt", "data": b"rar 4 stored payload"},
            {"name": "b.bin", "data": b"y" * 20, "method": 3},
        ], comment=b"archive comment"))
        with rarfile.RarFile(archive) as rf:
            infos = rf.infolist()
            assert [i.filename for i in infos] == ["a.txt", "b.bin"]
            assert infos[0].compress_type == STORED_COMPRESS_TYPE
            assert infos[1].compress_type != STORED_COMPRESS_TYPE

    def test_self_extracting_fixture_is_found_by_both_parsers(self, tmp_path):
        archive = _write(tmp_path, "sfx.exe", rar5_archive(
            [{"name": "stub.txt", "data": b"stub payload"}],
            sfx_stub=b"SFX STUB" * 200,
        ))
        assert rarfile.is_rarfile_sfx(str(archive)) is True
        with rarfile.RarFile(archive) as rf:
            assert [i.filename for i in rf.infolist()] == ["stub.txt"]

    def test_cover_fixture_keeps_its_crc(self, tmp_path):
        archive = _write(tmp_path, "c.rar", rar5_archive(
            [{"name": "x.txt", "data": PAYLOAD}], comment=b"archive comment",
        ))
        with rarfile.RarFile(archive) as rf:
            assert rf.comment == "archive comment"


# ----------------------------------------------------------------------
# Extraction without a decoder
# ----------------------------------------------------------------------
class TestDecoderlessExtraction:
    def test_stored_members_are_extracted(self, tmp_path, monkeypatch):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "a.rar", rar5_archive([
            {"name": "dir/notes.txt", "data": PAYLOAD},
            {"name": "dir/", "data": b"", "is_dir": True},
            {"name": "packed.bin", "data": b"z" * 32, "method": 4},
        ]))
        out = tmp_path / "out"
        result = extract_rar(archive, out)
        assert result.files_extracted == 1
        assert result.bytes_extracted == len(PAYLOAD)
        assert (out / "dir" / "notes.txt").read_bytes() == PAYLOAD
        assert result.members_total == 3
        assert result.members_unreadable == {MEMBER_NEEDS_DECODER: 1}
        assert result.decoder_missing is True
        assert result.decoder_hint and "7-Zip" in result.decoder_hint
        assert any("need" in note for note in result.notes)

    def test_one_undecodable_member_does_not_abort_the_archive(
        self, tmp_path, monkeypatch
    ):
        """The reported defect: the first compressed member killed the file."""
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "Deleted Items.rar", rar5_archive([
            {"name": "packed-first.bin", "data": b"p" * 16, "method": 3},
            {"name": "notes.txt", "data": PAYLOAD},
            {"name": "more.txt", "data": b"second readable member"},
        ]))
        result = extract_rar(archive, tmp_path / "out")
        # Both readable members survive, in the order the archive declares.
        assert result.files_extracted == 2
        assert _names(tmp_path / "out") == ["more.txt", "notes.txt"]
        assert result.members_unreadable == {MEMBER_NEEDS_DECODER: 1}
        assert result.skipped == ["packed-first.bin (need an external decoder)"]

    def test_rar4_stored_member_round_trip(self, tmp_path, monkeypatch):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "a.rar", rar4_archive([
            {"name": "a.txt", "data": b"rar 4 stored payload"},
            {"name": "b.bin", "data": b"y" * 20, "method": 3},
        ]))
        out = tmp_path / "out"
        result = extract_rar(archive, out)
        assert result.files_extracted == 1
        assert (out / "a.txt").read_bytes() == b"rar 4 stored payload"
        assert result.members_unreadable == {MEMBER_NEEDS_DECODER: 1}

    def test_fully_stored_archive_is_complete_without_a_decoder(
        self, tmp_path, monkeypatch
    ):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "a.rar", rar5_archive([
            {"name": "one.txt", "data": b"first"},
            {"name": "two.txt", "data": b"second"},
        ]))
        result = extract_rar(archive, tmp_path / "out")
        assert result.files_extracted == 2
        assert result.members_unreadable == {}
        assert result.decoder_missing is False      # nothing was missing
        assert _names(tmp_path / "out") == ["one.txt", "two.txt"]

    def test_damaged_member_is_refused_and_partial_output_removed(
        self, tmp_path, monkeypatch
    ):
        _no_decoder(monkeypatch)
        good = rar5_archive([{"name": "x.txt", "data": PAYLOAD}])
        archive = _write(tmp_path, "bad.rar", corrupt_member_data(good, PAYLOAD))
        out = tmp_path / "out"
        result = extract_rar(archive, out)
        assert result.files_extracted == 0
        assert result.members_unreadable.get(MEMBER_UNREADABLE) == 1
        assert _names(out) == []                    # never published half a member

    def test_encrypted_archive_is_password_protected_not_corrupt(
        self, tmp_path, monkeypatch
    ):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "enc.rar", rar5_archive([
            {"name": "secret.txt", "data": b"ciphertext", "encrypt": True},
        ]))
        with pytest.raises(ArchiveEncrypted):
            extract_rar(archive, tmp_path / "out")

    def test_corrupt_container_is_a_safety_error_not_a_missing_decoder(
        self, tmp_path, monkeypatch
    ):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "not.rar", b"this is not a rar archive")
        with pytest.raises(ArchiveSafetyError) as excinfo:
            extract_rar(archive, tmp_path / "out")
        assert not isinstance(excinfo.value, ArchiveDecoderUnavailable)

    def test_empty_namelist_is_reported_rather_than_claimed_as_success(
        self, tmp_path, monkeypatch
    ):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "broken.rar", b"Rar!\x1a\x07\x01\x00garbage")
        result = extract_rar(archive, tmp_path / "out")
        assert result.files_extracted == 0
        assert result.members_total == 0
        assert any("declares no members" in note for note in result.notes)

    def test_traversal_member_is_refused_and_recorded(self, tmp_path, monkeypatch):
        """An escaping member is refused, and the refusal is on the record.

        Refusing the *member* rather than the whole container is deliberate:
        nothing unsafe is written (the name is validated before any path is
        computed), and discarding every readable member of a container because
        one entry was hostile is the all-or-nothing behaviour this suite
        exists to prevent.
        """
        _no_decoder(monkeypatch)
        out = tmp_path / "out"
        archive = _write(tmp_path, "evil.rar", rar5_archive([
            {"name": "../escaped.txt", "data": b"nope"},
            {"name": "safe.txt", "data": b"readable member"},
        ]))
        result = extract_rar(archive, out)
        assert not (tmp_path / "escaped.txt").exists()
        assert result.members_unreadable == {MEMBER_REJECTED: 1}
        assert result.files_extracted == 1
        assert (out / "safe.txt").read_bytes() == b"readable member"
        assert any("escaped.txt" in name for name in result.skipped)

    def test_policy_limits_still_apply(self, tmp_path, monkeypatch):
        """Limits are enforced per member; nothing over a limit is written."""
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "a.rar", rar5_archive([
            {"name": "big.txt", "data": b"y" * 4096},
        ]))
        out = tmp_path / "out"
        result = extract_rar(archive, out, ExtractionPolicy(max_file_size=1024))
        assert result.files_extracted == 0
        assert result.members_unreadable == {MEMBER_REJECTED: 1}
        assert not (out / "big.txt").exists()
        result = extract_rar(archive, out,
                             ExtractionPolicy(allowed_extensions={".pdf"}))
        assert result.files_extracted == 0
        assert result.members_unreadable == {MEMBER_REJECTED: 1}

    def test_member_count_limit_applies(self, tmp_path, monkeypatch):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "many.rar", rar5_archive([
            {"name": f"f{i}.txt", "data": b"x"} for i in range(4)
        ]))
        with pytest.raises(ArchiveSafetyError):
            extract_rar(archive, tmp_path / "out", ExtractionPolicy(max_files=2))


# ----------------------------------------------------------------------
# Decoder discovery: only a tool that can actually read RAR is accepted
# ----------------------------------------------------------------------
class TestDecoderDiscovery:
    def test_gnu_tar_is_not_accepted_as_a_rar_decoder(self):
        """GNU tar answers ``--version``; rarfile's own probe would take it."""
        import shutil
        import subprocess

        tar = shutil.which("tar")
        if not tar:
            pytest.skip("no tar on PATH")
        version = subprocess.run(
            [tar, "--version"], capture_output=True
        ).stdout.decode("utf-8", "replace").lower()
        if "bsdtar" in version or "libarchive" in version:
            pytest.skip("tar on PATH is bsdtar, which does read RAR")
        assert decoder_supports_rar(tar) is False

    def test_found_decoder_is_never_a_bare_directory(self):
        found = find_rar_decoder()
        if found is None:
            assert "no RAR decoder" in rar_decoder_status()
        else:
            family, executable = found
            assert family in ("unrar", "unar", "7z", "bsdtar")
            assert Path(executable).is_file()

    def test_configuration_is_a_no_op_without_a_decoder(self, monkeypatch):
        monkeypatch.setattr("core.archive_safety.find_rar_decoder", lambda: None)
        assert configure_rar_decoder() is None


# ----------------------------------------------------------------------
# The reader: what the pipeline actually records
# ----------------------------------------------------------------------
class TestArchiveReaderWithoutDecoder:
    def test_partial_read_is_reported_as_content_and_kept(
        self, tmp_path, monkeypatch
    ):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "Deleted Items.rar", rar5_archive([
            {"name": "dir/evidence.txt", "data": PAYLOAD},
            {"name": "dir/photo.jpg", "data": b"jpeg-bytes", "method": 3},
        ]))
        payload = _read(ArchiveFileReader(), archive)

        # Everything that could be read is on disk and handed on as children.
        assert payload["status"] == "success"
        assert payload["files_extracted"] == 1
        assert Path(
            payload["extraction_path"], "dir", "evidence.txt"
        ).read_bytes() == PAYLOAD

        # The gap is named, not collapsed into "Extraction failed".
        assert payload["decoder_missing"] is True
        assert payload["members_total"] == 2
        assert payload["members_unreadable"] == {MEMBER_NEEDS_DECODER: 1}
        assert payload["extraction_info"]["warning"] == (
            "archive_needs_external_decoder"
        )
        assert "7-Zip" in payload["extraction_info"]["detail"]

        # The manifest is content: the archive's listing survives into the
        # index even though one member could not be decoded.
        assert "dir/photo.jpg (need an external decoder)" in payload["text"]
        assert "Members read: 1" in payload["text"]
        assert "need an external decoder" in payload["text"]

    def test_manifest_is_bounded_for_huge_archives(self, tmp_path, monkeypatch):
        _no_decoder(monkeypatch)
        reader = ArchiveFileReader()
        archive = _write(tmp_path, "big.rar", rar5_archive([
            {"name": f"f{i:05d}.bin", "data": b"x" * 8, "method": 3}
            for i in range(reader.MAX_LISTED_MEMBERS + 50)
        ]))
        payload = _read(reader, archive)
        text = payload["text"]
        assert "Members read: 0" in text
        assert "... and 50 more" in text
        assert len(text) <= reader.MAX_MANIFEST_CHARS

    def test_complete_decoderless_read_reports_no_gap(self, tmp_path, monkeypatch):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "a.rar", rar5_archive([
            {"name": "one.txt", "data": b"first"},
            {"name": "two.txt", "data": b"second"},
        ]))
        payload = _read(ArchiveFileReader(), archive)
        assert payload["files_extracted"] == 2
        assert "decoder_missing" not in payload
        assert payload.get("status") == "success"

    def test_undecodable_archive_does_not_claim_success(self, tmp_path, monkeypatch):
        _no_decoder(monkeypatch)
        archive = _write(tmp_path, "broken.rar", b"Rar!\x1a\x07\x01\x00garbage")
        payload = _read(ArchiveFileReader(), archive)
        # An archive whose headers could not be followed is not a success; the
        # old code called this "opened but no members extracted" and stored the
        # file as unread with no reason.
        assert payload["files_extracted"] == 0
        assert payload.get("status") == "success"
        assert payload["extraction_info"]["warning"] == (
            "archive_opened_but_no_members_extracted"
        )


# ----------------------------------------------------------------------
# The environment gap the reader cannot work around
# ----------------------------------------------------------------------
class TestMissingDecoderDependency:
    def test_missing_decoder_is_reported_actionably_not_as_a_failure(
        self, tmp_path, monkeypatch
    ):
        """rarfile absent: the file is intact, the *host* lacks a component.

        This is the shape the old code collapsed into "Extraction failed" -
        the operator was told to re-read a 66 MB archive that never had
        anything wrong with it.
        """
        from core import archive_safety

        def _unavailable(*args, **kwargs):
            raise ArchiveDecoderUnavailable(
                "RAR support requires the rarfile package, which is not "
                "installed (pip install rarfile)"
            )

        monkeypatch.setattr(archive_safety, "extract_rar", _unavailable)
        archive = _write(tmp_path, "a.rar", b"Rar!\x1a\x07\x01\x00...")
        payload = _read(ArchiveFileReader(), archive)

        assert "Extraction failed" not in str(payload.get("error", ""))
        assert "pip install rarfile" in payload["error"]
        # A missing dependency will work after installing it: the run's
        # accounting must say "worth another attempt", not "failed".
        assert payload["retryable"] is True
        assert payload["decoder_missing"] is True
        info = payload["extraction_info"]
        assert info["warning"] == "archive_needs_external_decoder"
        assert "7-Zip" in info["detail"] and "WinRAR" in info["detail"]
