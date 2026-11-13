"""A full run must not write a tracked file.

"Does this run change the checkout?" has to be answerable, and the only honest
answer is a command: ``git status --porcelain`` after the suites have run. Round
22 found the map viewer's registry being written into the tracked
``map_data/registry.db`` by any run that opened it, which made every later diff
a lie.

``MAP_DATA_DIR`` moves that tree (documented in ``docs/CONFIGURATION.md``) and
``tests/conftest.py`` points it at ``.test_runtime/map_data`` before the
application imports. This module checks the two halves of that:

* the plumbing is in place - the environment variable is set and the shipped
  catalog was copied, so the application can open a registry at all;
* the tree is clean now - nothing in ``git status --porcelain`` that a test run
  could have produced.

The second check is deliberately a *report*, not a rewrite: when it fails, the
answer is to stop the test writing the file, never to clean up after it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

def _porcelain() -> list:
    completed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                               cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                               timeout=120)
    if completed.returncode != 0:
        pytest.skip(f"not a git checkout: {completed.stderr.strip()[:200]}")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def test_the_map_registry_is_redirected_away_from_the_tracked_tree():
    redirected = os.environ.get("MAP_DATA_DIR")
    if not redirected:
        pytest.skip("MAP_DATA_DIR is not set in this process (set before the app imported)")
    assert pathlib.Path(redirected).resolve() != (PROJECT_ROOT / "map_data").resolve(), (
        "MAP_DATA_DIR points at the tracked map_data/ tree, so a run writes "
        "map_data/registry.db")
    assert (pathlib.Path(redirected) / "catalog.json").is_file(), (
        "the redirected tree has no catalog.json; the map viewer needs the "
        "shipped catalog copied into it")


def test_a_run_leaves_the_tracked_tree_as_it_found_it():
    """The measurement: what the run *added* to ``git status``, not what it found.

    A developer running the suite from a dirty tree is normal, so the baseline
    is the status recorded by ``tests/conftest.py`` before the first test
    imported anything. A run that leaves the tree exactly as it found it passes;
    a run that adds a modified or untracked path fails, and the path is named.
    """
    from conftest import GIT_STATUS_AT_START

    if not GIT_STATUS_AT_START.exists():
        pytest.skip("no starting status was recorded (not a git checkout)")
    at_start = {line for line in
                GIT_STATUS_AT_START.read_text(encoding="utf-8").splitlines() if line.strip()}
    now = set(_porcelain())
    added = sorted(now - at_start)
    gone = sorted(at_start - now)
    assert added == [], (
        "this run changed the checkout - a test must not write a tracked "
        "file:\n  " + "\n  ".join(added))
    assert gone == [], (
        "this run removed changes that were present when it started:\n  "
        + "\n  ".join(gone))
