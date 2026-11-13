"""Batch import must account for every submitted file.

`ImportService.import_batch_files` answers with per-file results and four
counters. Before this test existed, a file that passed path validation but
could not be given a processing task (the concurrent-task cap raises
``RuntimeError``) was counted as *accepted* and as an *error* entry at the same
time, while ``rejected`` stayed at zero: the response said ``accepted: 30,
queued: 5, rejected: 0`` and 25 files had vanished from the counters.

The rule these tests pin down is that nothing may be silent:

* every submitted path is accounted for exactly once — either it was rejected
  by path validation, or it was accepted (and then it is either queued now or
  deferred because no task slot was free);
* each file's own ``status`` says which of those happened to it.
"""

from __future__ import annotations

import pytest

from Api.services.import_service import ImportService


class _TaskManager:
    """Stands in for the process task manager, with a fixed number of slots."""

    def __init__(self, slots: int):
        self.slots = slots
        self.created: list[str] = []

    def create_task(self, file_path, source_id, side_id, task_name=None):
        if len(self.created) >= self.slots:
            raise RuntimeError(
                f"Maximum concurrent tasks ({self.slots}) reached")
        task_id = f"task-{len(self.created)}"
        self.created.append(file_path)
        return task_id


@pytest.fixture()
def documents(tmp_path, monkeypatch):
    """Six real files inside an approved ingestion root."""
    root = tmp_path / "inbox"
    root.mkdir()
    paths = []
    for index in range(6):
        document = root / f"document_{index}.txt"
        document.write_text(f"document {index}\n", encoding="utf-8")
        paths.append(str(document))
    monkeypatch.setenv("INGESTION_ROOTS", str(root))
    return paths


def _install(monkeypatch, manager):
    import Api.task_manager as task_manager_module

    monkeypatch.setattr(task_manager_module, "get_task_manager", lambda: manager)


def test_a_file_without_a_free_task_slot_is_deferred_not_accepted(monkeypatch, documents):
    _install(monkeypatch, _TaskManager(slots=2))

    payload = ImportService.import_batch_files(documents, source_id=1, side_id=1)

    assert payload["valid"] is True
    assert payload["total"] == 6
    assert payload["queued"] == 2
    # the four files that could not be started are deferred, not accepted
    assert payload["deferred"] == 4
    assert payload["rejected"] == 0
    # accepted is the validated set: the two that run now plus the four that
    # have to wait for a slot. No file is counted twice and none is missing.
    assert payload["accepted"] == 6
    assert payload["accepted"] == payload["queued"] + payload["deferred"]
    assert payload["accepted"] + payload["rejected"] == payload["total"]


def test_every_submitted_file_is_accounted_for_exactly_once(monkeypatch, documents, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("not in an approved root\n", encoding="utf-8")
    submitted = documents[:3] + [str(outside), str(tmp_path / "missing.txt")]
    _install(monkeypatch, _TaskManager(slots=1))

    payload = ImportService.import_batch_files(submitted, source_id=1, side_id=1)

    assert payload["total"] == len(submitted) == 5
    # accepted is the set that passed validation; it splits into the files that
    # are running now and the files deferred for want of a task slot
    assert payload["accepted"] + payload["rejected"] == payload["total"]
    assert payload["queued"] + payload["deferred"] == payload["accepted"]
    assert payload["deferred"] == 2
    assert payload["queued"] == 1
    assert payload["deferred"] == 2
    assert payload["rejected"] == 2


def test_the_per_file_status_says_what_happened_to_each_file(monkeypatch, documents):
    _install(monkeypatch, _TaskManager(slots=1))

    payload = ImportService.import_batch_files(documents, source_id=1, side_id=1)

    statuses = [entry["status"] for entry in payload["results"]]
    assert statuses[0] == "queued"
    assert statuses[1:] == ["deferred"] * 5
    for entry in payload["results"]:
        assert "task_id" in entry if entry["status"] == "queued" else True
    # a deferred file carries a reason the operator can act on
    assert all("no processing slot" in entry.get("error", "").lower()
               for entry in payload["results"][1:])


def test_without_capacity_pressure_nothing_is_deferred(monkeypatch, documents):
    _install(monkeypatch, _TaskManager(slots=10))

    payload = ImportService.import_batch_files(documents, source_id=1, side_id=1)

    assert payload["accepted"] == 6
    assert payload["queued"] == 6
    assert payload["deferred"] == 0
    assert payload["rejected"] == 0
