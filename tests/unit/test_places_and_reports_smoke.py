"""The gazetteer JS smoke, and the reports page's real contract.

`tests/js/*.mjs` are only real tests if something runs them. `places_settings_smoke.mjs`
was not run by anything and had rotted: it reported 8/9 forever because its ninth
check asserted against a load-history inspector that does not exist (no
`gazetteer-loads-tab`, no `gazetteerLoadsTableBody`, no endpoint serving a load
history). It now checks that the module inserts server text as text rather than
markup, which is a property of the code that does exist.

`reports_page_smoke.mjs` was a different case and has been deleted rather than
kept as an xfail. It imported `static/js/pages/reports-page.js` and read a
`reports-page-labels` JSON element from `templates/Reports/reports.html`; neither
ever existed in this repository (``git log --all -- static/js/pages/reports-page.js``
is empty), so the harness aborted before running a single check, and the xfail
only recorded that it was broken. The page is **server-rendered on purpose** - its
own template says so in a comment: "no inline script (CSP), no page module, and
the same statement fills the filters, the totals and the tables". An xfail that
says "this will pass when the client module is written" invites exactly the
change the CSP policy forbids.

What the harness wanted to assert is asserted against the product that exists:
the page renders its numbers server-side, it carries no page module and no
inline script, and it is fully translated without new catalog entries. That is
`test_the_reports_page_is_server_rendered_without_a_page_module` below, and
`tests/unit/test_csp_no_inline_script.py` renders the same page for real.
"""

from __future__ import annotations

import re
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


def test_the_reports_page_is_server_rendered_without_a_page_module():
    """The contract the deleted harness was reaching for, checked where it lives.

    Server-rendered means: the numbers arrive in the HTML (the filters, the
    totals and the tables come from the same statement), the page loads no
    module of its own, and it contains no inline script - the CSP has no
    ``unsafe-inline``, so an inline handler would be dead code in a browser.
    """
    template = (ROOT / "templates" / "Reports" / "reports.html").read_text(encoding="utf-8")

    assert "reports-page.js" not in template, (
        "the page now references a client module that has never existed")
    assert "reports-page-labels" not in template
    # Server-side numbers, not placeholders a script fills in later.
    for marker in ('id="reportTotal"', "{{ totals.total }}", 'id="reportProcessed"',
                   'id="reportFailed"', 'id="reportSuccessRate"'):
        assert marker in template, marker

    inline = re.findall(r"<script(?![^>]*\bsrc\s*=)[^>]*>", template, re.I)
    assert not inline, f"the server-rendered page has inline script: {inline[:3]}"
    # The template states the design; a future edit that breaks it should have to
    # delete the sentence it contradicts.
    assert "server-rendered on purpose" in template
