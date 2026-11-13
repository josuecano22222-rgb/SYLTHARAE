"""``docs/reference/`` is generated from the code and must not drift.

Regenerate after changing a signature, a docstring summary or a route::

    python3 tools/docs/generate_reference.py
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_reference_docs_are_current(tmp_path):
    env = dict(os.environ, APP_DATA_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "tools/docs/generate_reference.py", "--check"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def test_every_first_party_package_has_a_page():
    sys.path.insert(0, str(ROOT))
    from tools.docs.generate_reference import PACKAGES

    for package in PACKAGES:
        if (ROOT / package).is_dir():
            assert (ROOT / "docs" / "reference" / "python" / f"{package}.md").exists(), package
