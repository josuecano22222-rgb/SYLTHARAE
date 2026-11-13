"""Integration: the Windows acceptance harness itself must be sound.

``scripts/verify_windows_ingestion.py`` is what the operator runs on the Windows
production box before accepting a build: it walks the three ways files enter
(one file, a whole folder, a server path) plus the Windows-hostile names, and
prints PASS/FAIL per check. A harness that has quietly rotted is worse than no
harness at all, because it still says PASS - so it is run here, in the suite,
against the disposable database, and every check it can perform on this host
must pass.

The checks that only the real platform can answer (can this filesystem *write*
a name Windows reserves? is a differently-cased path the same folder?) are
performed by the same script on the operator's machine; here they run against
POSIX rules and still have to hold.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import verify_windows_ingestion as harness  # noqa: E402  (path set above)

pytestmark = pytest.mark.integration


def test_every_check_the_harness_can_run_here_passes(
    app, admin_credentials, tmp_path, monkeypatch
):
    root = tmp_path / "ingestion-root"
    (root / "case 2026-014" / "scans").mkdir(parents=True)
    (root / "case 2026-014" / "report.txt").write_text("report\n")
    (root / "case 2026-014" / "scans" / "page-01.txt").write_text("page\n")
    monkeypatch.setenv("INGESTION_ROOTS", str(root))

    username, password = admin_credentials
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = harness.main([
            "--username", username,
            "--password", password,
            "--server-path", str(root / "case 2026-014"),
        ])
    report = output.getvalue()

    assert "0 failed" in report, report
    assert code == 0, report
    # The three inputs were genuinely exercised, not skipped.
    assert "a file chosen on its own" in report
    assert "folder selection keeps its tree" in report
    assert "a typed path is accepted and counted" in report
    assert "awkward names are renamed, never lost" in report
    assert "1 passed" not in report.replace("11 passed", "")  # no trivially short run


def test_the_harness_reports_failures_rather_than_passing_them(
    app, admin_credentials, tmp_path, monkeypatch
):
    """A wrong answer must be reported as FAIL with a non-zero exit code.

    The harness is the evidence for acceptance on Windows, so it is checked
    that it can fail: a renamed-behaviour regression is simulated by pointing
    the server-path check at a folder that is *not* inside the roots, which
    must come back as a failed check rather than a quiet pass.
    """
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("INGESTION_ROOTS", str(root))

    username, password = admin_credentials
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = harness.main([
            "--username", username, "--password", password,
            "--server-path", str(outside),
        ])
    report = output.getvalue()

    assert code == 1, report
    assert "failed" in report
    assert "a typed path is accepted and counted" in report
