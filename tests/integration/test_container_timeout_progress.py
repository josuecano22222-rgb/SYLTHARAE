"""End-to-end: a container that outlives its own file timeout.

This reproduces the shape of the recorded PST failure at a size that runs in
seconds: one container publishes hundreds of members, and the file-time budget
for that container expires while its members are still being processed.

Before the fix the run declared the container timed out, then settled every
member that had not reached a worker as "never reached a worker / skipped", and
finished reporting success - the exact loss the user reported ("why didn't it
process all the files? why did it stop?").

The assertions are therefore about *completion*, not about speed:

* every discovered unit reaches a terminal state (pending == 0);
* nothing is left "skipped";
* the members are all completed, so the container's work was actually done;
* the container is not left in flight at the end of the run.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


MEMBER_COUNT = 300


@pytest.fixture()
def container_corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    # The container itself: one archive with many members.
    with zipfile.ZipFile(root / "container.zip", "w") as archive:
        for i in range(MEMBER_COUNT):
            archive.writestr(
                f"member_{i:04d}.txt",
                "payload " * 40 + f"record {i}\n",
            )
    return root


def _reader(ledger=None, **kwargs):
    """A reader for a top-level run.

    Without ``ledger`` the reader owns its ledger, which is what makes the
    "initial workload" (the folder) distinguishable from everything the run
    discovers afterwards - the basis of the ``files_nested`` figure.
    """
    from pipeline.integrated_reader import IntegratedFileReader

    return IntegratedFileReader(
        max_workers=4,
        enable_monitoring=False,
        enable_storage=False,
        progress_ledger=ledger,
        **kwargs,
    )


def test_container_members_are_all_processed_when_the_file_budget_expires(
        container_corpus, monkeypatch):
    import pipeline.integrated_reader as reader_module

    # A deliberately tiny per-file budget: 2 s is far less than the time needed
    # to process 300 members, which is what the 1 322 s budget was for the
    # 2.1 GB PST. The deadline must extend on progress, not fire.
    class _TinyBudget:
        file_processing_timeout = 2
        max_file_timeout_s = 600

    monkeypatch.setattr(reader_module, "get_processing_config", lambda: _TinyBudget())
    monkeypatch.setattr(
        reader_module, "_configured_max_file_timeout", lambda: 600.0)

    with _reader() as reader:
        reader.process_folder(str(container_corpus))
        snapshot = reader.get_live_progress()

    assert snapshot["files_discovered"] == MEMBER_COUNT + 1
    assert snapshot["files_nested"] == MEMBER_COUNT
    assert snapshot["files_completed"] >= MEMBER_COUNT, (
        f"only {snapshot['files_completed']} of {MEMBER_COUNT + 1} units completed: "
        f"pending={snapshot['files_pending']} skipped={snapshot['files_skipped']}"
    )
    assert snapshot["files_skipped"] == 0, (
        "work that was still running must not be written off as skipped"
    )
    assert snapshot["files_pending"] == 0
    assert snapshot["files_retryable"] == 0, "the container must not time out"
    assert snapshot["containers_in_flight"] == 0
    assert snapshot["complete"] is True


def test_container_members_are_attributed_to_their_container(container_corpus):
    from pipeline.progress_ledger import ProgressLedger

    ledger = ProgressLedger(name="attribution")
    with _reader(ledger) as reader:
        reader.process_folder(str(container_corpus))
        snapshot = reader.get_live_progress()

    container = str(container_corpus / "container.zip")
    # The container published the members; after the run nothing is outstanding,
    # and the total attributed to it is exactly the member count.
    attributed = snapshot["children_by_parent"].get(container, 0)
    assert attributed == MEMBER_COUNT, (
        f"container attribution lost members: {attributed} != {MEMBER_COUNT}"
    )
    assert snapshot["container_work_outstanding"] == 0


def test_statistics_report_nested_work_not_the_retained_window(container_corpus):
    """The CLI's "Total Files" came from len(results) - a bounded window."""
    with _reader() as reader:
        results = reader.process_folder(str(container_corpus))
        stats = reader.get_statistics()

    ledger_view = stats["ledger"]
    assert ledger_view["files_discovered"] == MEMBER_COUNT + 1
    assert ledger_view["files_nested"] == MEMBER_COUNT
    # The retained window may be smaller than the work done; the ledger is the
    # authority and is what the reports must use.
    assert len(results) <= MEMBER_COUNT + 1
    assert stats["partial_run"] is False


def test_deadline_window_uses_the_run_budget_not_a_constant():
    """The watchdog window must scale with the file, as ``file_timeout_seconds`` does.

    ``_file_window`` used to call ``calculate_file_timeout``, a closure local to
    ``process_folder``: the NameError was caught and every entry silently got the
    300 s fallback, so a multi-gigabyte container was extended on a window that
    had nothing to do with its size or with the configured base timeout.
    """
    from pipeline.integrated_reader import IntegratedFileReader, file_timeout_seconds

    reader = IntegratedFileReader(max_workers=1)
    reader._base_file_timeout = 1200
    file_info = {"size_bytes": 4 * 1024 ** 3, "path": "huge.pst"}
    entry = [None, None, file_info, None]

    window = reader._file_window(entry)
    expected = float(max(30, file_timeout_seconds(
        file_info, 1200, reader._observed_bytes_per_second(),
        max_timeout=reader.max_file_timeout_s,
    )))
    assert window == expected
    assert window > 300.0, "fell back to the constant instead of the budget"


def test_deadline_window_falls_back_to_the_configured_base_outside_a_run():
    """Called outside a run it still uses configuration, never a hard-coded 300 s."""
    from pipeline.integrated_reader import (
        IntegratedFileReader, _configured_base_file_timeout, file_timeout_seconds,
    )

    reader = IntegratedFileReader(max_workers=1)
    reader._base_file_timeout = None
    file_info = {"size_bytes": 8 * 1024 ** 3, "path": "huge.pst"}
    expected = float(max(30, file_timeout_seconds(
        file_info, _configured_base_file_timeout(), 0.0,
        max_timeout=reader.max_file_timeout_s,
    )))
    assert reader._file_window([None, None, file_info, None]) == expected
    assert expected > 300.0
