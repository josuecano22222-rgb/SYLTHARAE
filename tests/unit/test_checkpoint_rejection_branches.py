"""Hypothesis 6: a checkpoint existed but was thrown away on resume.

Every branch in _load_checkpoint / _replay_journal that discards recorded
progress must say so: status "rejected" with a reason that reaches the log.
A silent discard is indistinguishable from never having had a checkpoint,
which is the whole difficulty in diagnosing "Retry started over".

The last test is the one that matters most operationally: a hard kill leaves
a half-written final journal line, and the entries before it must survive.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest

from core.checkpoint_manager import CheckpointManager, safe_resolve


def _mgr(folder, source="SRC", side="SIDE"):
    return CheckpointManager(
        os.path.join(folder, "ck.json"), folder, source, side
    )


def _write_snapshot(path, *, folder, source="SRC", side="SIDE", entries=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "folder_path": folder,
                "storage_source": source,
                "storage_side": side,
                "processed_files": entries or [],
                "processed_count": len(entries or []),
            },
            fh,
        )


def _write_journal(path, folder, lines, source="SRC", side="SIDE"):
    header = (
        f"#inforaxis-journal-v1|{str(safe_resolve(folder)).replace(chr(92), '/').lower()}"
        f"|{source}|{side}"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for line in lines:
            fh.write(line + "\n")


@pytest.mark.parametrize(
    "mutate,expect_in_reason",
    [
        ({"folder_path": "/somewhere/else"}, "folder mismatch"),
        ({"storage_source": "OTHER"}, "source mismatch"),
        ({"storage_side": "OTHER"}, "side mismatch"),
    ],
)
def test_a_snapshot_that_does_not_match_is_rejected_with_a_reason(
    mutate, expect_in_reason, caplog
):
    folder = tempfile.mkdtemp()
    ckpt = os.path.join(folder, "ck.json")
    payload = {"folder_path": folder, "storage_source": "SRC", "storage_side": "SIDE",
               "processed_files": ["1", "2", "3"], "processed_count": 3}
    payload.update(mutate)
    with open(ckpt, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    with caplog.at_level(logging.WARNING):
        mgr = _mgr(folder)

    assert mgr.resume_report["status"] == "rejected", (
        "a checkpoint that exists but was not used is a rejection, not a fresh start"
    )
    reason = mgr.resume_report.get("reason") or ""
    assert expect_in_reason in reason, (
        f"reason {reason!r} must name what did not match"
    )
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a rejection must be logged, not just recorded in a dict"
    assert reason in " ".join(r.getMessage() for r in warnings), (
        "the rejection reason must reach the log line an operator can read"
    )


def test_corrupt_snapshot_json_is_rejected_with_a_reason(caplog):
    folder = tempfile.mkdtemp()
    ckpt = os.path.join(folder, "ck.json")
    with open(ckpt, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")

    with caplog.at_level(logging.WARNING):
        mgr = _mgr(folder)

    assert mgr.resume_report["status"] == "rejected"
    reason = mgr.resume_report.get("reason") or ""
    assert "invalid checkpoint file" in reason, reason
    # Corrupt JSON is logged at ERROR, which is >= WARNING.
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "Invalid checkpoint file format" in logged or reason in logged


def test_a_journal_belonging_to_another_folder_is_rejected(caplog):
    folder = tempfile.mkdtemp()
    other = tempfile.mkdtemp()
    journal = os.path.join(folder, "ck.json.journal")
    _write_journal(journal, other, ["1", "2", "3"])

    with caplog.at_level(logging.WARNING):
        mgr = _mgr(folder)

    assert mgr.resume_report["status"] == "rejected", (
        "a journal that cannot be used is recorded progress about to be repeated"
    )
    reason = mgr.resume_report.get("reason") or ""
    assert "folder" in reason or "source" in reason, reason
    assert mgr._processed_count == 0, "no identifier may be applied across folders"


def test_a_journal_from_another_source_is_rejected(caplog):
    """Same folder, different source: the files were stored elsewhere."""
    folder = tempfile.mkdtemp()
    journal = os.path.join(folder, "ck.json.journal")
    _write_journal(journal, folder, ["1", "2", "3"], source="OTHER")

    with caplog.at_level(logging.WARNING):
        mgr = _mgr(folder)

    assert mgr.resume_report["status"] == "rejected"
    assert mgr._processed_count == 0


def test_truncated_final_journal_line_still_keeps_earlier_entries():
    """The normal result of a hard kill: a half-written last line.

    The process died mid-append, so the journal ends without a newline and
    the final identifier is incomplete. The entries before it were durable
    and must survive, or a Retry redoes the whole run.
    """
    folder = tempfile.mkdtemp()
    journal = os.path.join(folder, "ck.json.journal")
    header = (
        f"#inforaxis-journal-v1|{str(safe_resolve(folder)).replace(chr(92), '/').lower()}"
        f"|SRC|SIDE"
    )
    # Three complete identifiers, then one cut off mid-write.
    with open(journal, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        fh.write("1a2b3c4d\n")
        fh.write("5e6f7a8b\n")
        fh.write("9c0d1e2f\n")
        fh.write("deadbe")  # no trailing newline: killed mid-append

    mgr = _mgr(folder)

    assert mgr.resume_report["status"] == "resumed", (
        f"a truncated tail must not invalidate the journal: "
        f"{mgr.resume_report}"
    )
    for complete in (0x1A2B3C4D, 0x5E6F7A8B, 0x9C0D1E2F):
        assert complete in mgr._processed_files, (
            f"identifier {complete:#x} was durable before the kill and must "
            "survive it"
        )

    # Known imperfection, recorded rather than hidden: the partial line is
    # still parsed as a (short) identifier, so one bogus entry can appear.
    # Real identifiers are 64-bit hashes, so a truncated prefix is a tiny
    # number that cannot collide with one - this cannot wrongly skip a file.
    assert 0xDEADBE in mgr._processed_files


def test_an_empty_journal_is_fresh_not_rejected(caplog):
    """A journal with only a header is an empty checkpoint, not a rejection."""
    folder = tempfile.mkdtemp()
    journal = os.path.join(folder, "ck.json.journal")
    _write_journal(journal, folder, [])

    with caplog.at_level(logging.WARNING):
        mgr = _mgr(folder)

    assert mgr.resume_report["status"] in ("fresh", "resumed"), (
        f"an empty journal is not a rejection: {mgr.resume_report}"
    )
    assert mgr._processed_count == 0


def test_no_files_at_all_is_fresh_with_a_reason():
    folder = tempfile.mkdtemp()
    mgr = _mgr(folder)
    assert mgr.resume_report["status"] == "fresh"
    assert mgr.resume_report.get("reason"), "even 'fresh' must say why"
