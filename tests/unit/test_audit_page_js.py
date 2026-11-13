"""Audit Log page: the real module (static/js/pages/audit-page.js) against a
DOM stub and a fake server (tests/js/audit_page_smoke.mjs). Server text is
inserted as text; NULL is "none"; the list is the server's page in its order;
Older/Newer follow the server's keyset cursor and "more entries" is the
server's has_more; filters are sent to the server; errors are verbatim.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_audit_page_renders_server_state_as_text():
    proc = subprocess.run(["node", "tests/js/audit_page_smoke.mjs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("ok ") == 21, proc.stdout
