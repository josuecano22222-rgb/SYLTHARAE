"""Server-provided error text is rendered as text on the job and import pages.

Job errors/warnings carry file names and paths (user-controlled); the job
detail page inserted them with innerHTML, as did the Import Center's message
box, preview and source/side menus.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_error_text_is_escaped():
    proc = subprocess.run(["node", "tests/js/job_error_escaping_smoke.mjs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
