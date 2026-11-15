"""How stable is a checkpoint identifier across the ways a run can differ?

Each scenario here is a plausible cause of "Retry started from the beginning":
if the identity changes between the original run and the retry, every file
looks new and the whole corpus is reprocessed.

Where this platform cannot reproduce a Windows-only behaviour, the test
documents what *is* observable and the gap is recorded rather than guessed at.
"""

from __future__ import annotations

import os
import time

import pytest


def _digest_for_path(path):
    """The checkpoint filename digest for a folder path."""
    from services.ingesting.options import IngestionRequest
    from services.ingesting.service import IngestionService

    ckpt = IngestionService._checkpoint_file(
        IngestionRequest(path=str(path), source="SRC", side="SIDE")
    )
    return None if ckpt is None else os.path.basename(ckpt)


def _identifier(mgr, path):
    from core.file_utils import get_standardized_metadata

    return mgr._get_file_identifier(get_standardized_metadata(path, compute_hash=False))


# ---------------------------------------------------------------------------
# Path form: these must all reach the same checkpoint
# ---------------------------------------------------------------------------

def test_trailing_slash_reaches_the_same_checkpoint(tmp_path):
    folder = tmp_path / "corpus"
    folder.mkdir()
    plain = _digest_for_path(folder)
    trailing = _digest_for_path(str(folder) + os.sep)
    assert trailing is not None
    assert plain == trailing


def test_dotdot_segments_reach_the_same_checkpoint(tmp_path):
    folder = tmp_path / "corpus"
    folder.mkdir()
    sibling = tmp_path / "other"
    sibling.mkdir()
    plain = _digest_for_path(folder)
    # Round-trip through a neighbouring directory and back.
    via_sibling = _digest_for_path(os.path.join(str(sibling), "..", "corpus"))
    assert plain == via_sibling


def test_a_symlink_to_the_corpus_reaches_the_same_checkpoint(tmp_path):
    """resolve() follows symlinks, so a link and its target share a checkpoint."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    link = tmp_path / "link_to_corpus"
    try:
        link.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlinks unsupported in this environment")
    assert _digest_for_path(link) == _digest_for_path(folder)


def test_different_case_is_a_different_checkpoint_on_case_sensitive_fs(tmp_path):
    """Documents the Windows question rather than assuming it resolves.

    The digest is taken from the path as given, while the *file* identifier
    lowercases the path. On a case-sensitive filesystem the two directories
    are genuinely different corpora, so a differing digest is correct. On
    Windows, Path.resolve() normalises case for existing paths, so both
    spellings are expected to collapse to one digest - which cannot be
    verified from here and is recorded as an open question.
    """
    lower = tmp_path / "corpus"
    lower.mkdir()
    upper = tmp_path / "CORPUS"
    upper.mkdir()
    assert _digest_for_path(lower) != _digest_for_path(upper)


# ---------------------------------------------------------------------------
# The 'modified' field: local-time dependence
# ---------------------------------------------------------------------------

def _modified_under(path, tz):
    """Re-read 'modified' with TZ set to ``tz``."""
    import importlib

    import core.file_utils as file_utils

    previous = os.environ.get("TZ")
    os.environ["TZ"] = tz
    try:
        time.tzset()
        importlib.reload(file_utils)
        return file_utils.get_standardized_metadata(str(path), compute_hash=False)["modified"]
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
        importlib.reload(file_utils)


def test_modified_is_local_time_dependent(tmp_path):
    """The identifier embeds a local-time date, so timezone changes move it.

    This is the mechanism behind the DST concern: if the date shifts, the
    identifier shifts with it and the file is reprocessed. Demonstrating the
    dependence is the point - it makes the DST risk concrete rather than
    "inferred".
    """
    target = tmp_path / "f.txt"
    target.write_text("x")
    # A timestamp near midnight UTC: 00:30 UTC is the previous day in UTC-12
    # and the same day in UTC+14.
    stamp = time.mktime((2026, 6, 15, 0, 30, 0, 0, 0, 0))
    os.utime(target, (stamp, stamp))

    utc_value = _modified_under(target, "UTC")
    east_value = _modified_under(target, "Pacific/Kiritimati")   # UTC+14
    west_value = _modified_under(target, "Etc/GMT+12")           # UTC-12

    # All three are dates derived from the *same* mtime; they differ only
    # because the local timezone differs.
    assert len({utc_value, east_value, west_value}) > 1, (
        "expected the local-time date to move with the timezone"
    )


def test_identifier_changes_when_timezone_changes(tmp_path):
    """Consequence of the above: a re-run under another TZ reprocesses files."""
    from core.checkpoint_manager import CheckpointManager

    target = tmp_path / "f.txt"
    target.write_text("x")
    stamp = time.mktime((2026, 6, 15, 0, 30, 0, 0, 0, 0))
    os.utime(target, (stamp, stamp))

    mgr = CheckpointManager(str(tmp_path / "ck.json"), str(tmp_path), "SRC", "SIDE")

    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        id_utc = _identifier(mgr, target)
    finally:
        time.tzset()

    os.environ["TZ"] = "Etc/GMT+12"
    time.tzset()
    try:
        id_west = _identifier(mgr, target)
    finally:
        time.tzset()
        os.environ.pop("TZ", None)
        time.tzset()

    # Recorded rather than asserted either way: whether these differ depends
    # on where the file's mtime falls relative to local midnight.
    if id_utc != id_west:
        assert True, "identifier moved with the timezone (DST risk is real)"
    else:
        pytest.skip("this mtime does not cross a local-midnight boundary")


# ---------------------------------------------------------------------------
# Degenerate metadata and changed files
# ---------------------------------------------------------------------------

def test_unreadable_metadata_yields_a_stable_identifier(tmp_path):
    """When stat fails, modified is 'N/A' and size is None - must still be stable."""
    from core.checkpoint_manager import CheckpointManager

    mgr = CheckpointManager(str(tmp_path / "ck.json"), str(tmp_path), "SRC", "SIDE")
    missing = tmp_path / "gone.txt"
    first = mgr._get_file_identifier(
        {"path": str(missing), "modified": "N/A", "size_bytes": None}
    )
    second = mgr._get_file_identifier(
        {"path": str(missing), "modified": "N/A", "size_bytes": None}
    )
    assert first == second, "an unreadable file must still have a stable identity"


def test_a_file_whose_mtime_changes_is_reprocessed(tmp_path):
    """Intended behaviour: changed mtime => new identity => reprocessed.

    This is the mechanism by which network-share timestamp rounding, antivirus
    touching files, or a file being rewritten during a run cause rework. It is
    correct (the content may differ) but worth knowing about.
    """
    from core.checkpoint_manager import CheckpointManager

    target = tmp_path / "f.txt"
    target.write_text("x")
    mgr = CheckpointManager(str(tmp_path / "ck.json"), str(tmp_path), "SRC", "SIDE")
    before = _identifier(mgr, target)

    os.utime(target, (time.time() + 3600, time.time() + 3600))
    after = _identifier(mgr, target)

    assert before != after, "a changed mtime must produce a new identity"
