"""Guard: every shipped JavaScript file must at least parse.

Reported from the running application (browser console):

    Uncaught SyntaxError: Unexpected token '}'
    .../static/js/pages/full-content-page.js:418
    universal-initializer.js:124 Page module for file-detail does not
    export a default init function

One stray ``}`` left behind by an earlier edit of that page module (a deleted
initializer's closing brace, with the call it used to wrap) made the whole
file fail to parse. A module that does not parse is not "mostly working": the
browser discards it entirely, so the reader lost its chunked content loading,
its search, and its wrap/font/theme controls, and every later error the page
would have logged never happened.

Static analysis of the Python code cannot see this, and nothing else in the
suite reads the JavaScript, so the file shipped broken. This test parses every
module under ``static/js`` with node's own parser.

Skipped when node is not installed - the check needs a JavaScript parser, and
a fake one would not have caught the defect.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
JS_ROOT = PROJECT_ROOT / "static" / "js"

#: Vendored, minified or generated bundles are not our source.
SKIP_MARKERS = ("min.js", "jquery", "bootstrap", "chart.umd", "dist/")


def _sources():
    files = []
    for path in sorted(JS_ROOT.rglob("*.js")):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if any(marker in relative for marker in SKIP_MARKERS):
            continue
        files.append(path)
    return files


def test_the_javascript_tree_is_not_empty():
    assert len(_sources()) > 50, "expected the shipped JavaScript modules"


def test_every_javascript_module_parses(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    sources = _sources()
    assert sources, "no JavaScript sources found"

    failures = []
    for path in sources:
        # Modules must be checked as modules: a bare `}` that closes nothing is
        # an error in module scope, and that is exactly the defect this guards.
        probe = tmp_path / "probe.mjs"
        probe.write_bytes(path.read_bytes())
        proc = subprocess.run(
            [node, "--check", str(probe)], capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            first_line = (proc.stderr or "").strip().splitlines()[:1]
            failures.append(f"{path.relative_to(PROJECT_ROOT)}: {first_line}")

    assert not failures, "JavaScript that does not parse:\n" + "\n".join(failures)


def test_content_formatter_display_harness_passes():
    """Run the formatters' own harness - it pins the rendered structure.

    ``tests/js/content_formatter_smoke.mjs`` drives the real display
    formatters and checks the markup they produce. It is the only place the
    Word table shape is asserted (the "Table N" marker must open a table and
    the tab-delimited rows must come back as rows, not as prose), so a change
    that parses but flattens the tables has to fail here.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    harness = PROJECT_ROOT / "tests" / "js" / "content_formatter_smoke.mjs"
    proc = subprocess.run(
        [node, str(harness)], capture_output=True, text=True,
        timeout=300, cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, (
        f"display harness failed\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    assert "checks passed" in proc.stdout, proc.stdout[-2000:]
