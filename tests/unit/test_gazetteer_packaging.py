"""Packaging guard: the gazetteer seed and its build inputs must ship with the repo.

Regression test for the defect where ``data/`` in ``.gitignore`` silently kept
``data/gazetteer/`` out of git, so migration m0019 failed on every clone.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "data/gazetteer/places_seed.json",
    "data/gazetteer/curation.json",
    "data/gazetteer/SOURCES.md",
    "data/gazetteer/raw/wdqs_batch0.txt",
    "data/gazetteer/raw/wdqs_batch1.txt",
    "data/gazetteer/raw/wdqs_batch2.txt",
]


def test_seed_inputs_exist():
    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    assert not missing, f"gazetteer files missing: {missing}"


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="not a git checkout")
def test_seed_inputs_are_not_git_ignored():
    proc = subprocess.run(["git", "check-ignore", "--no-index", *REQUIRED], cwd=ROOT,
                          capture_output=True, text=True)
    ignored = proc.stdout.split()
    assert not ignored, f".gitignore excludes shipped gazetteer files: {ignored}"
