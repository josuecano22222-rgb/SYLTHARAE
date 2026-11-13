"""Unit: staging names and path limits for uploads from any OS.

A file selected on Windows, macOS or Linux has to become a writable path under
the staging directory on the *server's* filesystem. Two directions matter:

* a Windows client sends what Windows allows — drive-qualified or UNC paths for
  a folder selection, and names that are reserved there (``CON``, ``NUL``,
  ``LPT1``) are simply absent from a Windows selection, so the awkward names
  arrive from macOS/Linux clients (``report.``, ``a:b.txt``, ``..foo``);
* a POSIX server must not turn any of that into a path that walks out of the
  staging directory, and a Windows server must not fail to write a file it
  promised to stage.

The rules reuse ``core.archive_safety.windows_safe_component`` — the same
primitive the archive extractor uses for member names — so an upload and an
archive member behave identically on every platform. These tests exercise the
functions directly; no server or filesystem semantics are simulated.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from Api.routes.operations_api import (  # noqa: E402
    _checked_destination,
    _safe_relative_parts,
    _StagingError,
    _staged_path_limit,
)


# --------------------------------------------------------------- separators --
@pytest.mark.parametrize("supplied, expected", [
    # A single file: bare name.
    ("report.pdf", ["report.pdf"]),
    # A folder selection: forward slashes (what every browser sends).
    ("Case 1/scans/page 1.jpg", ["Case 1", "scans", "page 1.jpg"]),
    # The same selection from a Windows client that sent backslashes.
    ("Case 1\\scans\\page 1.jpg", ["Case 1", "scans", "page 1.jpg"]),
    # Mixed separators (older Windows browsers did this).
    ("Case 1\\scans/page 1.jpg", ["Case 1", "scans", "page 1.jpg"]),
])
def test_paths_from_any_client_keep_their_tree(supplied, expected):
    assert _safe_relative_parts(supplied) == expected


@pytest.mark.parametrize("supplied, expected", [
    (r"C:\Evidence\2026\report.pdf", ["Evidence", "2026", "report.pdf"]),
    # Drive-relative ("C:Evidence") is not a path shape a browser produces, and
    # without the separator it is not a drive: the colon is mapped instead.
    (r"C:Evidence\report.pdf", ["C_Evidence", "report.pdf"]),
    (r"\\server\share\cases\report.pdf", ["server", "share", "cases", "report.pdf"]),
    ("//server/share/cases/report.pdf", ["server", "share", "cases", "report.pdf"]),
    ("/data/inbox/report.pdf", ["data", "inbox", "report.pdf"]),
])
def test_absolute_and_unc_shapes_are_reduced_to_what_lies_below(supplied, expected):
    """A folder selection may arrive absolute; only the part below the root is ours."""
    assert _safe_relative_parts(supplied) == expected


def test_traversal_cannot_leave_the_batch_directory():
    parts = _safe_relative_parts("../../../../etc/passwd")
    assert parts == ["etc", "passwd"]
    assert ".." not in parts


def test_empty_and_dot_segments_are_dropped():
    assert _safe_relative_parts("dir/./sub//file.txt") == ["dir", "sub", "file.txt"]
    assert _safe_relative_parts("") == ["upload.bin"]
    # '...' is a real name on POSIX and unwritable on Windows; it is defused the
    # same way an archive member is (windows_safe_component), never dropped.
    assert _safe_relative_parts("...") == ["_"]


def test_a_colon_inside_a_name_is_mapped_not_read_as_a_drive():
    """'a:b.txt' is a legal macOS/Linux name; the colon is illegal on Windows."""
    assert _safe_relative_parts("a:b.txt") == ["a_b.txt"]
    assert _safe_relative_parts("Case 1/a:b.txt") == ["Case 1", "a_b.txt"]


# ------------------------------------------------------------ Windows names --
@pytest.mark.parametrize("name", ["CON", "con.txt", "NUL", "COM1", "LPT9.log", "aux.doc"])
def test_reserved_device_names_are_defused_not_dropped(name):
    parts = _safe_relative_parts(name)
    assert len(parts) == 1
    assert parts[0].startswith("_"), parts           # still the operator's file
    assert parts[0].endswith(name.split(".")[-1]) if "." in name else True


def test_reserved_name_is_defused_at_every_depth():
    assert _safe_relative_parts("dir/NUL/x.txt") == ["dir", "_NUL", "x.txt"]


def test_leading_dots_are_kept_trailing_dots_are_not():
    """`.gitignore` must stay `.gitignore`; `report.` cannot be written on Windows."""
    assert _safe_relative_parts(".gitignore") == [".gitignore"]
    assert _safe_relative_parts("report.") == ["report"]
    assert _safe_relative_parts("folder./name.txt") == ["folder", "name.txt"]


def test_characters_windows_refuses_are_mapped_not_dropped():
    assert _safe_relative_parts('a:b*c?d"e<f>g|h.txt') == ["a_b_c_d_e_f_g_h.txt"]


def test_overlong_component_is_truncated_keeping_the_extension():
    parts = _safe_relative_parts("x" * 300 + ".pdf")
    assert len(parts[0]) <= 200
    assert parts[0].endswith(".pdf")


def test_a_nul_byte_is_refused_rather_than_renamed():
    with pytest.raises(_StagingError) as excinfo:
        _safe_relative_parts("bad\x00name.txt")
    assert excinfo.value.code == "BAD_NAME"
    assert excinfo.value.status == 400


# ------------------------------------------------------------- path length --
def test_staged_path_limit_defaults_to_the_host_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("OPERATIONS_MAX_STAGED_PATH_CHARS", raising=False)
    import os

    default = _staged_path_limit()
    assert default == (240 if os.name == "nt" else 4096)

    monkeypatch.setenv("OPERATIONS_MAX_STAGED_PATH_CHARS", "120")
    assert _staged_path_limit() == 120

    for broken in ("", "wide", "0", "-5"):
        monkeypatch.setenv("OPERATIONS_MAX_STAGED_PATH_CHARS", broken)
        assert _staged_path_limit() == default, broken


def test_a_path_over_the_limit_is_refused_before_anything_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATIONS_MAX_STAGED_PATH_CHARS", "80")
    batch = tmp_path / "batch"

    with pytest.raises(_StagingError) as excinfo:
        _checked_destination(batch, ["x" * 60, "y" * 60, "report.pdf"])
    assert excinfo.value.code == "PATH_TOO_LONG"
    assert excinfo.value.status == 400
    assert "limit" in excinfo.value.message
    # Nothing was created: a refused upload leaves no half-built tree behind.
    assert not batch.exists()


def test_a_path_within_the_limit_is_reserved_without_overwriting(tmp_path):
    batch = tmp_path / "batch"
    first = _checked_destination(batch, ["Case 1", "report.pdf"])
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"one")

    second = _checked_destination(batch, ["Case 1", "report.pdf"])
    assert second != first
    assert second.name == "report (2).pdf"


# ------------------------------------------------- client naming collisions --
def test_a_file_and_a_folder_with_the_same_name_are_reported(tmp_path):
    """`Case/a.txt` and `Case/a.txt/inner.txt` cannot both exist, anywhere.

    A client can name both in one selection. The second cannot be created, and
    that is one file's problem: it is reported as a name conflict for that file
    rather than raised as a server error that loses the whole batch.
    """
    batch = tmp_path / "batch"
    taken = _checked_destination(batch, ["Case", "a.txt"])
    taken.parent.mkdir(parents=True, exist_ok=True)
    taken.write_bytes(b"file")

    with pytest.raises(_StagingError) as excinfo:
        _checked_destination(batch, ["Case", "a.txt", "inner.txt"])
    assert excinfo.value.code == "NAME_CONFLICT"
    assert excinfo.value.status == 400
    assert "rename" in excinfo.value.message


def test_unwritable_staging_is_a_storage_failure(tmp_path, monkeypatch):
    """A filesystem refusal that is not the client's naming is a server fault.

    Permissions, a full disk and a path the filesystem rejects must not be
    reported as the operator's mistake: they are a 500 with a plain message,
    while the rest of the selection still stages.
    """
    from Api.routes import operations_api

    def refuse(directory, parts):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(operations_api, "_unique_destination", refuse)

    with pytest.raises(_StagingError) as excinfo:
        _checked_destination(tmp_path / "batch", ["report.pdf"])
    assert excinfo.value.code == "STORAGE_FAILED"
    assert excinfo.value.status == 500
