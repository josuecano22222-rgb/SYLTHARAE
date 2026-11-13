"""Unit + security tests: ingestion path safety (SEC-06)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.path_safety import (
    PathSafetyError,
    validate_ingestion_path,
    validate_batch_paths,
    configured_ingestion_roots,
)


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    r1 = tmp_path / "ingest1"
    r2 = tmp_path / "ingest2"
    r1.mkdir()
    r2.mkdir()
    monkeypatch.setenv("INGESTION_ROOTS", f"{r1};{r2}")
    return r1, r2


def test_roots_from_environment(roots):
    r1, r2 = roots
    assert configured_ingestion_roots() == [r1.resolve(), r2.resolve()]


def test_no_roots_configured_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("INGESTION_ROOTS", raising=False)
    with pytest.raises(PathSafetyError):
        validate_ingestion_path("/etc/passwd")


def test_path_inside_root_accepted(roots, tmp_path):
    r1, _ = roots
    f = r1 / "data.txt"
    f.write_text("x")
    assert validate_ingestion_path(f) == f.resolve()


def test_path_outside_roots_rejected(roots, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(PathSafetyError):
        validate_ingestion_path(outside)


def test_etc_passwd_rejected(roots):
    with pytest.raises(PathSafetyError):
        validate_ingestion_path("/etc/passwd")


def test_traversal_escaping_root_rejected(roots):
    r1, _ = roots
    with pytest.raises(PathSafetyError):
        validate_ingestion_path(str(r1 / ".." / ".." / "etc" / "passwd"))


def test_drive_qualified_rejected(roots):
    # Rejected because it is OUTSIDE the configured allowlist roots - drive
    # letters are not banned per se (on Windows every absolute path is
    # drive-qualified); containment under a configured root is the rule.
    with pytest.raises(PathSafetyError):
        validate_ingestion_path("C:\\Windows\\evil.txt")


def test_unc_path_rejected(roots):
    # Same allowlist semantics for UNC shares: rejected only when the
    # resolved location is outside every configured root.
    with pytest.raises(PathSafetyError):
        validate_ingestion_path("\\\\server\\share\\x")


def test_windows_containment_case_insensitive(monkeypatch):
    """Windows containment is case-insensitive and separator-tolerant."""
    monkeypatch.setattr("core.path_safety._WINDOWS", True)
    from core.path_safety import _contained

    root = Path("C:\\Data\\Evidence")
    assert _contained(Path("C:\\Data\\Evidence\\a.txt"), root)
    assert _contained(Path("C:\\data\\EVIDENCE\\sub\\b.txt"), root)
    assert _contained(Path("C:/Data/Evidence/c.txt"), root)   # forward slashes
    assert _contained(Path("C:\\Data\\Evidence"), root)        # root itself
    assert not _contained(Path("C:\\Data\\Evil\\a.txt"), root)
    # No partial-component match: evidence2 is a sibling, not inside Evidence
    assert not _contained(Path("C:\\Data\\Evidence2\\a.txt"), root)


def test_windows_unc_containment(monkeypatch):
    """UNC paths validate through the same containment rule."""
    monkeypatch.setattr("core.path_safety._WINDOWS", True)
    from core.path_safety import _contained

    root = Path("\\\\server\\share")
    assert _contained(Path("\\\\SERVER\\Share\\cases\\a.txt"), root)
    assert _contained(Path("//server/share/a.txt"), root)
    assert not _contained(Path("\\\\server\\other\\a.txt"), root)
    assert not _contained(Path("\\\\other\\share\\a.txt"), root)


def test_relative_path_resolved_against_root_not_cwd(roots):
    """Relative candidates resolve against configured roots, never the CWD."""
    r1, _ = roots
    resolved = validate_ingestion_path("sub/dir/file.txt")
    assert str(resolved).startswith(str(r1))
    assert resolved == (r1 / "sub" / "dir" / "file.txt").resolve()


def test_no_roots_actionable_error(monkeypatch, tmp_path):
    """Disabled server-path ingestion tells the operator how to enable it."""
    monkeypatch.delenv("INGESTION_ROOTS", raising=False)
    monkeypatch.setattr(
        "core.app_paths.get_data_root", lambda: tmp_path / "nonexistent-data"
    )
    with pytest.raises(PathSafetyError) as excinfo:
        validate_ingestion_path("/etc/passwd")
    assert "INGESTION_ROOTS" in str(excinfo.value)
    assert "disabled" in str(excinfo.value)


def test_nul_byte_rejected(roots):
    with pytest.raises(PathSafetyError):
        validate_ingestion_path("good\x00bad")


def test_batch_validation(roots, tmp_path):
    r1, _ = roots
    good = r1 / "a.txt"
    good.write_text("x")
    assert len(validate_batch_paths([good])) == 1
    with pytest.raises(PathSafetyError):
        validate_batch_paths([good, "/etc/passwd"])


def test_staged_upload_allowed_without_roots(tmp_path, monkeypatch):
    """Operations API staged uploads are a configured application input
    location: ingestible even with no INGESTION_ROOTS, while arbitrary
    server paths stay rejected (SEC-06 fail-closed)."""
    monkeypatch.delenv("INGESTION_ROOTS", raising=False)
    import core.path_safety as ps

    data_root = tmp_path / "appdata"
    (data_root / "uploads" / "abc123").mkdir(parents=True)
    monkeypatch.setattr(
        "core.app_paths.get_data_root", lambda: str(data_root), raising=False
    )
    # patch the symbol the module actually imported
    monkeypatch.setattr(ps, "get_data_root", lambda: str(data_root), raising=False)

    staged = data_root / "uploads" / "abc123" / "doc.txt"
    staged.write_text("x")
    assert validate_ingestion_path(staged) == staged.resolve()

    # anything outside the staging root is still rejected
    with pytest.raises(PathSafetyError):
        validate_ingestion_path(str(tmp_path / "elsewhere.txt"))
