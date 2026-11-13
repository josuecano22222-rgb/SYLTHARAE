"""What the application does at startup with an out-of-step workbook.

The mechanism that makes "the workbook is authoritative" true without a human
running a command is this decision, so every branch is pinned here: an unchanged
file costs a hash, a changed one is either reported or written depending on the
mode, a workbook that breaks a rule is never written, and nothing raises into
the web app's import.

A fake service stands in for the database, so the branches are tested without
PostgreSQL; the real service is exercised in
``tests/integration/test_sync_taxonomy.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.importing import taxonomy_startup as startup  # noqa: E402
from services.importing.taxonomy_sync_service import (  # noqa: E402
    TaxonomySyncResult,
)


class FakeService:
    """Just enough of TaxonomySyncService for the decision, plus a record."""

    def __init__(self, *, pending=0, ok=True, applied=False, errors=()):
        self.pending = pending
        self.ok = ok
        self.applied = applied
        self.errors = list(errors)
        self.calls = []

    def resolve_data_file(self, _name):
        return "classification/domain_data.xlsx"

    def drift(self):
        self.calls.append("drift")
        return TaxonomySyncResult(
            success=self.ok, dry_run=True,
            stats={"planned_changes": self.pending, "plan": {"changes": self.pending}},
            errors=self.errors)

    def run(self, request, progress_cb=None):
        self.calls.append(("run", request.apply))
        return TaxonomySyncResult(
            success=self.ok, applied=self.applied, dry_run=not request.apply,
            stats={"written": {"categories_created": 1} if self.applied else {},
                   "planned_changes": self.pending},
            errors=self.errors,
            snapshot={"categories": {}} if self.applied else None)


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    """Point the fingerprint state at a temporary directory."""
    from core import app_paths

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_paths, "_cached_root", tmp_path)
    yield tmp_path


@pytest.fixture()
def fingerprint(monkeypatch):
    monkeypatch.setattr(
        "services.importing.taxonomy_sync.fingerprint", lambda _path: "abc123")
    return "abc123"


def test_off_means_off_and_touches_nothing(state_dir, fingerprint):
    service = FakeService()
    assert startup.reconcile("off", service=service)["action"] == "disabled"
    assert service.calls == []


@pytest.mark.parametrize("mode", ["0", "no", "false", "OFF"])
def test_every_off_spelling_is_off(mode, state_dir, fingerprint):
    assert startup.reconcile(mode, service=FakeService())["action"] == "disabled"


def test_an_unchanged_workbook_costs_no_database_work(state_dir, fingerprint):
    from services.importing.taxonomy_sync_service import write_state

    write_state(fingerprint=fingerprint, data_file="domain_data.xlsx")
    service = FakeService()
    outcome = startup.reconcile("apply", service=service)
    assert outcome["action"] == "unchanged"
    assert service.calls == [], "a cached fingerprint still hit the database"


def test_check_mode_reports_drift_without_writing(state_dir, fingerprint):
    service = FakeService(pending=7)
    outcome = startup.reconcile("check", service=service)
    assert outcome["action"] == "drift" and outcome["pending"] == 7
    assert service.calls == ["drift"], "check mode must never apply"


def test_check_mode_records_the_fingerprint_when_nothing_is_pending(state_dir, fingerprint):
    from services.importing.taxonomy_sync_service import read_state

    outcome = startup.reconcile("check", service=FakeService(pending=0))
    assert outcome["action"] == "already_in_sync"
    assert read_state()["fingerprint"] == fingerprint, (
        "the next start would plan the same work again")


def test_apply_mode_writes_and_reports_what_it_wrote(state_dir, fingerprint):
    service = FakeService(pending=3, applied=True)
    outcome = startup.reconcile("apply", service=service)
    assert outcome["action"] == "applied"
    assert outcome["written"] == {"categories_created": 1}
    assert outcome["has_snapshot"] is True
    assert service.calls == [("run", True)]
    from services.importing.taxonomy_sync_service import read_state

    assert read_state()["fingerprint"] == fingerprint


def test_apply_mode_with_nothing_pending_does_not_rewrite(state_dir, fingerprint):
    outcome = startup.reconcile("apply", service=FakeService(pending=0, applied=False))
    assert outcome["action"] == "already_in_sync"


def test_a_refused_workbook_is_reported_and_not_written(state_dir, fingerprint):
    service = FakeService(ok=False, errors=["Networks!A2 [TERM_HAS_WHITESPACE]"])
    outcome = startup.reconcile("apply", service=service)
    assert outcome["action"] == "refused"
    assert "Networks!A2" in outcome["errors"][0]
    assert service.calls == [("run", True)]


def test_a_missing_workbook_never_raises_into_the_app(state_dir, monkeypatch):
    def explode(_self, _name=None):
        raise FileNotFoundError("no workbook here")

    monkeypatch.setattr(
        "services.importing.taxonomy_sync_service.TaxonomySyncService.resolve_data_file",
        explode)
    outcome = startup.reconcile("apply")
    assert outcome["action"] == "error"
    assert "FileNotFoundError" in outcome["error"]


def test_the_mode_comes_from_the_environment_when_not_given(monkeypatch, state_dir,
                                                          fingerprint):
    monkeypatch.setenv("SYLTHARAE_TAXONOMY_SYNC", "off")
    assert startup.mode_from_env() == "off"
    assert startup.reconcile(service=FakeService())["action"] == "disabled"


def test_every_mode_spelling_is_understood():
    assert set(startup.APPLY_MODES) == {"apply", "1", "yes", "true"}
    assert set(startup.OFF_MODES) == {"0", "off", "no", "false"}
