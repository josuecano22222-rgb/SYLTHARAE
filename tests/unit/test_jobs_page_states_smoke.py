"""Run the jobs-list state harness from the suite (AUDIT-UI-01).

`tests/js/jobs_page_states_smoke.mjs` executes the real page script against the
vm DOM and asserts that an empty list, a failed request and a dead network are
three different answers. A harness nothing runs is documentation; this runs it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_jobs_list_distinguishes_failure_from_emptiness():
    proc = subprocess.run(["node", "tests/js/jobs_page_states_smoke.mjs"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout, proc.stdout
    import re

    match = re.search(r"(\d+)/(\d+) checks passed", proc.stdout)
    assert match, proc.stdout
    assert match.group(1) == match.group(2), proc.stdout
    assert int(match.group(2)) >= 10, "the harness stopped checking"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cancelling_a_job_from_its_page_asks_first():
    """AUDIT-UI-02: the detail page posted a cancel with no question."""
    proc = subprocess.run(["node", "tests/js/job_detail_cancel_smoke.mjs"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout, proc.stdout
    assert "9/9 checks passed" in proc.stdout, proc.stdout
