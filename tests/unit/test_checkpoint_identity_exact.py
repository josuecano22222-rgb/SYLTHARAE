"""Regression: a top-level file edited later the same day must get a new checkpoint identity.

`modified` from file metadata is a calendar date, so the identifier used to be
path + date. The size and exact mtime are now part of it.
"""
import os
import time

import pytest

from core.checkpoint_manager import CheckpointManager


def _manager(tmp_path):
    return CheckpointManager(str(tmp_path / "ck.json"), str(tmp_path), "s", "s")


def test_same_day_edit_changes_the_identifier(tmp_path):
    from core.file_utils import get_standardized_metadata

    p = tmp_path / "a.txt"
    p.write_text("first version")
    before = get_standardized_metadata(str(p), compute_hash=False)
    time.sleep(0.05)
    p.write_text("second version, longer")
    after = get_standardized_metadata(str(p), compute_hash=False)

    cm = _manager(tmp_path)
    assert before.get("modified") == after.get("modified")  # the date did not change
    assert cm._get_file_identifier(before) != cm._get_file_identifier(after)


def test_unchanged_file_keeps_its_identifier(tmp_path):
    from core.file_utils import get_standardized_metadata

    p = tmp_path / "a.txt"
    p.write_text("stable")
    cm = _manager(tmp_path)
    m1 = get_standardized_metadata(str(p), compute_hash=False)
    m2 = get_standardized_metadata(str(p), compute_hash=False)
    assert cm._get_file_identifier(m1) == cm._get_file_identifier(m2)
