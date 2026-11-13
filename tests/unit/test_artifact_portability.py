"""Unit: artefacts written from now on must not name this machine.

The check is ``tools/audit/check_artifact_portability.py``; this test is the
gate that keeps it from being a tool nobody runs. Files captured before round 20
are listed in ``audit_report/r20/portability_baseline.txt`` and are left alone -
they are raw logs, and rewriting a path inside a captured run would make the
artefact say something that was never printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.audit.check_artifact_portability import load_baseline, scan  # noqa: E402


def test_no_new_artifact_names_a_home_directory_or_a_loopback_port():
    findings = scan(PROJECT_ROOT, load_baseline(PROJECT_ROOT))
    assert not findings, "\n".join(
        f"{item['file']}:{item['line']} {item['kind']} {item['match']}"
        for item in findings[:20])


def test_the_baseline_only_lists_files_that_exist():
    baseline = load_baseline(PROJECT_ROOT)
    missing = [name for name in baseline if not (PROJECT_ROOT / name).exists()]
    assert not missing, f"baseline names files that are gone: {missing[:5]}"


@pytest.mark.parametrize("fragment", [
    "see /home/alice/SYLTHARAE-Pro/audit_report for details",
    "psql postgresql://postgres@127.0.0.1:5433/analysis",
    "log written to /tmp/probe-4f2a.log",
])
def test_the_patterns_catch_what_they_claim_to(fragment, tmp_path, monkeypatch):
    """A detector that never fires is not a gate."""
    import tools.audit.check_artifact_portability as module

    fake_root = tmp_path / "repo"
    (fake_root / "audit_report" / "r99").mkdir(parents=True)
    (fake_root / "audit_report" / "r99" / "REPORT.md").write_text(fragment,
                                                                  encoding="utf-8")
    findings = module.scan(fake_root, set())
    assert findings, f"no finding for {fragment!r}"
