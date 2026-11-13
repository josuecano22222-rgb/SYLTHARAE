"""Detection page: the real module (static/js/pages/detection-page.js)
against a DOM stub and a fake server (tests/js/detection_page_smoke.mjs).
Numbers are the server's; never-analysed and unavailable versions are shown
as such; the per-detector action queues exactly that detector's stale set
with the CSRF header and follows the job; bad ids and an unconfirmed "all"
never reach the server; server errors are verbatim; run rows are text.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_detection_page_renders_server_state_as_text():
    proc = subprocess.run(["node", "tests/js/detection_page_smoke.mjs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("ok ") == 28, proc.stdout
