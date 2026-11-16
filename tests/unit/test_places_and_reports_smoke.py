"""The gazetteer and reports JS smokes, run by the suite instead of by hand.

`tests/js/*.mjs` are only real tests if something runs them. Two of them were
not run by anything, and both had rotted:

* `places_settings_smoke.mjs` reported 8/9 forever. The ninth check asserted
  against a load-history inspector that does not exist - there is no
  `gazetteer-loads-tab`, no `gazetteerLoadsTableBody` and no
  `gazetteerLoadDetail` in the settings template, no load-history table in the
  schema, and no endpoint serving one. The harness had invented the markup it
  was inspecting, so the check could never pass and never said so. It now
  checks that the module inserts server text as text rather than markup, which
  is a property of the code that does exist.
* `reports_page_smoke.mjs` imports `static/js/pages/reports-page.js` and reads
  a `reports-page-labels` element from the Reports template. Neither exists, so
  the harness aborts before running a single check.

Both are now executed here. The reports one is an xfail that names what is
missing, so if the page is ever built the suite reports XPASS and says so,
rather than the harness quietly staying broken.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gazetteer_settings_smoke_passes():
    """Every check in the harness passes, and there is more than one of them."""
    proc = subprocess.run(["node", "tests/js/places_settings_smoke.mjs"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout, proc.stdout
    assert "checks passed" in proc.stdout, proc.stdout
    # A harness that quietly checks nothing is worse than no harness.
    total = int(proc.stdout.strip().splitlines()[-1].split("/")[1].split()[0])
    assert total >= 8, f"the harness only made {total} checks"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.xfail(
    strict=True,
    reason="static/js/pages/reports-page.js and the reports-page-labels element "
           "in templates/Reports/reports.html do not exist; the harness aborts "
           "before running any check. Remove this xfail when the page is built.",
)
def test_reports_page_smoke_is_not_yet_runnable():
    proc = subprocess.run(["node", "tests/js/reports_page_smoke.mjs"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
