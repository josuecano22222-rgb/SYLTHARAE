"""Unit: Windows-safe handling of archive member names and tool discovery.

Windows is the primary production platform, so member names authored elsewhere
must not become unwritable paths here. Verified from source behaviour - the
assertions do not require a Windows host, they test the normalisation the code
performs before any file is written.

The defect this closes: validate_member_path rejected absolute paths, traversal,
drive-qualified paths, UNC paths and NUL bytes, but passed through every name
that is merely awkward on Windows. Measured before the fix:

    'CON'          -> 'CON'            (a console device, not a file)
    'CON.txt'      -> 'CON.txt'        (extension does not save it)
    'NUL'          -> 'NUL'            (writes are discarded)
    'dir/NUL/x.txt'-> 'dir/NUL/x.txt'  (reserved name as a directory)
    'x' * 300      -> unchanged        (exceeds the component limit)
    'name.'        -> 'name.'          (Windows strips the trailing dot)
    'trailing...   '-> unchanged       (Windows strips trailing dots/spaces)

Renaming is deliberate over rejecting: refusing an entire archive because one
member happened to be called CON would discard every other object in it, and an
awkward child must stay part of the parent's object graph.
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.archive_safety import (  # noqa: E402
    ArchiveSafetyError,
    validate_member_path,
    windows_safe_component,
)


# ------------------------------------------------- reserved device names
@pytest.mark.parametrize("name", [
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM9", "LPT1", "LPT9",
    "CON.txt", "con.TXT", "Con.Doc.Pdf", "nul.log",
])
def test_reserved_device_names_are_defused(name):
    out = validate_member_path(name)
    stem = out.split(".", 1)[0].upper()
    assert stem not in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }, (name, out)


def test_reserved_name_as_a_directory_component_is_defused():
    out = validate_member_path("dir/NUL/x.txt")
    assert "/NUL/" not in out and not out.startswith("NUL/"), out
    assert out == "dir/_NUL/x.txt", out


def test_reserved_name_handling_is_case_insensitive():
    assert validate_member_path("con").startswith("_")
    assert validate_member_path("CoN.TxT").startswith("_")


def test_reserved_prefix_lookalikes_are_left_alone():
    """CONNECT.txt and NULLED.bin are ordinary files, not devices."""
    assert validate_member_path("CONNECT.txt") == "CONNECT.txt"
    assert validate_member_path("NULLED.bin") == "NULLED.bin"
    assert validate_member_path("COM10.log") == "COM10.log"


# ------------------------------------------------------------- lengths
def test_overlong_component_is_truncated():
    out = validate_member_path("x" * 300)
    assert len(out) <= 200, len(out)


def test_truncation_preserves_the_extension():
    """Losing the extension would also lose the type identification."""
    out = validate_member_path("y" * 400 + ".pdf")
    assert out.endswith(".pdf"), out
    assert len(out) <= 200, len(out)


def test_each_component_is_limited_independently():
    out = validate_member_path("a" * 300 + "/" + "b" * 300 + ".txt")
    for part in out.split("/"):
        assert len(part) <= 200, (part, len(part))


def test_long_but_acceptable_name_is_untouched():
    name = "reasonable_report_name_2026.pdf"
    assert validate_member_path(name) == name


# -------------------------------------------------- trailing punctuation
@pytest.mark.parametrize("name,expected", [
    ("name.", "name"),
    ("name...", "name"),
    ("name   ", "name"),
    ("name. . .", "name"),
])
def test_trailing_dots_and_spaces_are_stripped(name, expected):
    assert validate_member_path(name) == expected


def test_a_name_of_only_dots_becomes_usable():
    assert validate_member_path("...") == "_"


# ------------------------------------------------------------ untouched
@pytest.mark.parametrize("name", [
    "normal.txt",
    "report.final.v2.pdf",
    "with space.docx",
    "with-dash_and_underscore.tar.gz",
    "uni\u00e7\u00f6d\u00e9\u4e2d\u6587\u0627\u0644\u0639\u0631\u0628\u064a\u0629.txt",
    "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442.docx",
    "a/b/c/deeply/nested/file.txt",
])
def test_ordinary_and_unicode_names_pass_through_unchanged(name):
    assert validate_member_path(name) == name


def test_unicode_is_preserved_not_transliterated():
    """Windows supports Unicode filenames; mangling them loses information."""
    out = validate_member_path("\u4e2d\u6587\u6587\u4ef6.pdf")
    assert out == "\u4e2d\u6587\u6587\u4ef6.pdf"


# -------------------------------------------------- safety still enforced
@pytest.mark.parametrize("name", [
    "../escape.txt",
    "../../etc/passwd",
    "/tmp/abs.txt",
    "C:\\Windows\\system32\\x.txt",
    "\\\\server\\share\\x.txt",
    "//server/share/x.txt",
    "a\x00b.txt",
    "",
    ".",
    "..",
])
def test_hostile_paths_are_still_rejected(name):
    """Windows-safety must not weaken the existing traversal protections."""
    with pytest.raises(ArchiveSafetyError):
        validate_member_path(name)


def test_excessive_depth_is_still_rejected():
    with pytest.raises(ArchiveSafetyError, match="depth"):
        validate_member_path("/".join(["d"] * 40) + "/f.txt")


def test_component_helper_is_pure_and_idempotent():
    for name in ("CON.txt", "x" * 300, "name.", "plain.txt"):
        once = windows_safe_component(name)
        assert windows_safe_component(once) == once, name


# --------------------------------------------------- external executables
def test_tesseract_cmd_env_var_takes_priority(monkeypatch):
    """An explicit override must beat every guessed location."""
    import core.ocr.engines as engines

    monkeypatch.setenv("TESSERACT_CMD", "/opt/custom/tesseract")
    assert engines._tesseract_candidates()[0] == "/opt/custom/tesseract"


def test_windows_candidates_use_the_standard_install_location(monkeypatch):
    """Swap the whole os module, not os.name.

    Patching os.name to 'nt' makes pathlib believe it is running on Windows,
    and pytest's own failure reporting then raises NotImplementedError trying to
    build a WindowsPath on a POSIX host. The stub is scoped to engines only.
    """
    import types

    import core.ocr.engines as engines

    fake_os = types.SimpleNamespace(
        name="nt",
        environ={
            "PROGRAMFILES": "C:\\Program Files",
            "PROGRAMFILES(X86)": "C:\\Program Files (x86)",
            "LOCALAPPDATA": "C:\\Users\\me\\AppData\\Local",
        },
        path=os.path,
        isfile=os.path.isfile,
    )
    monkeypatch.setattr(engines, "os", fake_os)
    candidates = engines._tesseract_candidates()
    # Separator-agnostic: the production code uses os.path.join, which adapts to
    # the host, while this stub deliberately mixes a Windows base with the host's
    # os.path. What matters is that every standard root is probed.
    normalised = [c.replace("/", "\\") for c in candidates]
    assert len(normalised) == 3, normalised
    assert all(c.endswith("Tesseract-OCR\\tesseract.exe") for c in normalised), normalised
    assert any(c.startswith("C:\\Program Files\\") for c in normalised), normalised
    assert any("Program Files (x86)" in c for c in normalised), normalised
    assert any("AppData\\Local" in c for c in normalised), normalised


def test_configure_leaves_an_explicit_tesseract_cmd_alone(monkeypatch):
    import core.ocr.engines as engines

    class _Stub:
        tesseract_cmd = "/opt/explicit/tesseract"

    stub = type("M", (), {"pytesseract": _Stub})
    engines._configure_tesseract_cmd(stub)
    assert stub.pytesseract.tesseract_cmd == "/opt/explicit/tesseract"


def test_configure_sets_a_binary_that_exists_but_is_not_on_path(monkeypatch, tmp_path):
    """The Windows scenario: installed correctly, PATH never extended."""
    import core.ocr.engines as engines

    fake = tmp_path / "tesseract"
    fake.write_text("stub")
    monkeypatch.setenv("TESSERACT_CMD", str(fake))

    class _Stub:
        tesseract_cmd = "tesseract"

    stub = type("M", (), {"pytesseract": _Stub})
    engines._configure_tesseract_cmd(stub)
    assert stub.pytesseract.tesseract_cmd == str(fake)


def test_configure_survives_a_module_without_the_attribute():
    """A pytesseract variant lacking tesseract_cmd must not crash the probe."""
    import core.ocr.engines as engines

    stub = type("M", (), {"pytesseract": object()})
    engines._configure_tesseract_cmd(stub)  # must not raise
