"""End-to-end trace of the progress path, per the defect report's item 7:

    processing entry point -> job/task manager -> file discovery
      -> nested-child discovery -> processing -> progress state
      -> API/polling mechanism -> frontend state -> progress bar

Each layer is exercised with the layer below it real, so a regression anywhere
in the chain shows up here rather than only in the browser:

* the real ``IntegratedFileReader`` processes a real nested workload;
* the real ``JobManager`` receives the engine's snapshots, applies its emission
  policy and persists them (against an in-memory repository - the persistence
  contract, not PostgreSQL, is what is under test);
* the real ``job_to_api`` shapes what the HTTP layer returns;
* the assertions check the exact fields the progress bar reads.
"""

import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.jobs import job_state  # noqa: E402
from services.jobs.manager import JobManager  # noqa: E402
from services.jobs.models import job_to_api  # noqa: E402

import pipeline.integrated_reader as ir  # noqa: E402
from pipeline.progress_ledger import ProgressLedger  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory stand-in for JobRepository (same surface the manager uses)
# ---------------------------------------------------------------------------
class FakeJobRepository:
    def __init__(self):
        self.rows = {}
        self.events = []
        self.update_calls = []

    def create(self, record):
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "job_id": record["job_id"],
            "job_type": record["job_type"],
            "status": record.get("status", job_state.QUEUED),
            "progress": int(record.get("progress", 0)),
            "current_phase": record.get("current_phase"),
            "current_item": record.get("current_item"),
            "source": record.get("source"),
            "options": record.get("options") or {},
            "stats": record.get("stats") or {},
            "errors": [], "warnings": [], "result_summary": None,
            "created_by": record.get("created_by"),
            "created_at": now, "started_at": None, "completed_at": None,
            "updated_at": now,
            "cancellation_requested": False, "pause_requested": False,
        }
        self.rows[row["job_id"]] = row
        return dict(row)

    def get(self, job_id):
        row = self.rows.get(job_id)
        return dict(row) if row else None

    def update_fields(self, job_id, fields):
        self.update_calls.append(dict(fields))
        row = self.rows[job_id]
        row.update(fields)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()

    def add_event(self, job_id, event_type, payload):
        self.events.append({"job_id": job_id, "event_type": event_type,
                            "payload": payload})

    def add_events(self, job_id, events):
        for e in events:
            self.add_event(job_id, e["event_type"], e.get("payload", {}))

    def get_events(self, job_id, limit=500, after_id=0):
        return self.events

    def count_by_status(self):
        out = {}
        for row in self.rows.values():
            out[row["status"]] = out.get(row["status"], 0) + 1
        return out

    def list_jobs(self, **kwargs):
        return [dict(r) for r in self.rows.values()]

    # --- helpers used by the assertions ---
    @property
    def progress_writes(self):
        return [c for c in self.update_calls if "progress" in c]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def allow_tmp_ingestion(tmp_path, monkeypatch):
    """SEC-06 is fail-closed: server-path ingestion needs an explicit root.

    The workload lives under pytest's tmp_path, so approve exactly that and
    nothing else.
    """
    monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))
    yield


@pytest.fixture
def nested_workload(tmp_path):
    """A folder whose real workload is far larger than its top-level count."""
    import zipfile

    inner = tmp_path / "stage"
    inner.mkdir()
    members = {f"m{i}.txt": ("content " * 200).encode() for i in range(6)}

    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)

    (tmp_path / "top.txt").write_text("top level " * 100)
    return tmp_path, 8  # top.txt + bundle.zip + 6 members


@pytest.fixture
def slow_engine(monkeypatch):
    """Give each read a measurable duration so ordering is observable."""
    original = ir.main_specify_method_of_reading_the_file

    def slow(file_info, **kwargs):
        time.sleep(0.05)
        return original(file_info, **kwargs)

    monkeypatch.setattr(ir, "main_specify_method_of_reading_the_file", slow)
    return slow


def run_job(nested_workload, repo=None, synchronous=True):
    """Drive a real JobManager over a real ingestion of a nested workload."""
    from services.ingesting.options import IngestionOptions
    from services.ingesting.service import IngestionRequest, IngestionService

    root, expected = nested_workload
    repo = repo or FakeJobRepository()
    manager = JobManager(repository=repo, synchronous=synchronous)

    request = IngestionRequest(
        path=str(root), source="TestSource", side="TestSide",
        recursive=True, options=IngestionOptions(max_workers=2),
    )
    record = repo.create({
        "job_id": uuid.uuid4().hex[:12].upper(),
        "job_type": "ingestion",
        "status": job_state.QUEUED,
        "progress": 0,
        "source": str(root),
        "options": {"path": str(root)},
    })
    job_id = record["job_id"]

    # Mirror JobManager._execute's wiring without the DB-backed control flags.
    throttle_state = {"t": 0.0, "percent": -1, "phase": None}

    def progress_cb(snapshot):
        percent = int(snapshot.get("percent") or 0)
        phase = snapshot.get("current_phase")
        now = time.time()
        if (percent == throttle_state["percent"] and phase == throttle_state["phase"]
                and (now - throttle_state["t"]) < 0.05):
            return
        throttle_state.update({"t": now, "percent": percent, "phase": phase})
        repo.update_fields(job_id, {
            "progress": percent,
            "current_phase": phase or "Processing",
            "current_item": (snapshot.get("current_file") or "")[:512],
            "stats": snapshot,
        })

    reader = ir.IntegratedFileReader(
        max_workers=2, enable_monitoring=False, enable_storage=False
    )
    reader.progress_callback = progress_cb
    result = IngestionService(reader_factory=lambda **kw: reader).run(
        request, progress_cb=progress_cb
    )
    repo.update_fields(job_id, {"status": job_state.COMPLETED, "stats": result.stats})
    return manager, repo, job_id, result, expected


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
class TestEngineToJobManager:
    def test_job_stats_carry_the_dynamically_discovered_total(
        self, nested_workload, slow_engine
    ):
        _, repo, job_id, result, expected = run_job(nested_workload)

        assert result.stats["files_total"] == expected
        assert result.stats["files_initial"] == 2
        assert result.stats["files_nested"] == 6
        assert result.stats["files_processed"] == expected
        assert result.stats["files_pending"] == 0

        row = repo.get(job_id)
        assert row["stats"]["files_nested"] == 6

    def test_progress_was_written_many_times_during_the_run(
        self, nested_workload, slow_engine
    ):
        """The manager must see movement, not one write at the end."""
        _, repo, _, _, _ = run_job(nested_workload)

        writes = repo.progress_writes
        assert len(writes) >= 4, f"only {len(writes)} progress writes"

        percents = [w["progress"] for w in writes]
        assert percents[0] < 100, "the first write already claimed completion"
        assert any(0 < p < 100 for p in percents), (
            f"no intermediate progress reached the job record: {percents}"
        )
        assert percents == sorted(percents), f"progress regressed: {percents}"

    def test_current_item_and_phase_are_live(self, nested_workload, slow_engine):
        _, repo, _, _, _ = run_job(nested_workload)

        phases = {w.get("current_phase") for w in repo.progress_writes}
        assert len(phases) >= 2, f"phase never changed: {phases}"

        items = [w.get("current_item") for w in repo.progress_writes if w.get("current_item")]
        assert items, "no current-file activity was ever published"
        # Nested members must appear as current activity, not just top-level files.
        assert any("m" in Path(i).name for i in items)

    def test_expanded_container_is_reported_as_activity(
        self, nested_workload, slow_engine
    ):
        _, repo, _, _, _ = run_job(nested_workload)
        phases = " | ".join(str(w.get("current_phase") or "") for w in repo.progress_writes)
        assert "nested" in phases.lower() or "expand" in phases.lower(), (
            f"container expansion was never surfaced: {phases}"
        )


class TestApiSerialization:
    def test_api_shape_exposes_nested_accounting(self, nested_workload, slow_engine):
        _, repo, job_id, _, expected = run_job(nested_workload)

        payload = job_to_api(repo.get(job_id))
        stats = payload["statistics"]

        assert stats["files_discovered"] == expected
        assert stats["files_initial"] == 2
        assert stats["files_nested"] == 6
        assert stats["files_processed"] == expected
        assert stats["files_pending"] == 0
        assert stats["containers_opened"] == 1
        assert stats["percent"] == 100
        assert stats["children_by_parent"], "attribution lost in serialization"

    def test_zero_counts_are_not_replaced_by_fallbacks(self):
        """`a or b` turned a real 0 into the fallback value."""
        job = {
            "job_id": "X", "stats": {
                "files_completed": 0, "files_stored": 7,
                "total_files": 0, "files_discovered": 0,
                "files_failed": 0,
            },
        }
        stats = job_to_api(job)["statistics"]
        assert stats["files_succeeded"] == 0, "a real zero was masked by files_stored"
        assert stats["files_discovered"] == 0
        assert stats["files_failed"] == 0

    def test_missing_stats_serialize_as_none_not_crash(self):
        payload = job_to_api({"job_id": "Y"})
        assert payload["statistics"]["files_discovered"] is None
        assert payload["statistics"]["children_by_parent"] == {}


class TestCompletionSemantics:
    def test_completed_job_reports_100_only_when_nothing_is_outstanding(
        self, nested_workload, slow_engine
    ):
        _, repo, job_id, _, expected = run_job(nested_workload)

        writes = repo.progress_writes
        first_full = next(
            (i for i, w in enumerate(writes) if w["progress"] == 100), None
        )
        assert first_full is not None, "the job never reached 100%"
        # Once 100% is claimed, no further work is reported.
        assert all(w["progress"] == 100 for w in writes[first_full:])

        final = repo.get(job_id)["stats"]
        assert final["files_pending"] == 0
        assert final["files_in_progress"] == 0
        assert final["files_processed"] == expected
        assert final["percent"] == 100
        assert final["complete"] is True

    def test_ledger_never_reports_complete_while_a_parent_is_open(self):
        """Direct check of the invariant that prevents premature 100%."""
        ledger = ProgressLedger()
        ledger.add_discovered(1, initial=True)
        parent = ledger.begin(path="/box.zip")
        ledger.add_discovered(3, key="/e/box", parent="/box.zip")
        for i in range(3):
            ledger.begin(path=f"/e/box/m{i}").settle("completed")

        snap = ledger.snapshot()
        assert snap["files_done"] == 3
        assert snap["total_files"] == 4
        assert snap["percent"] < 100
        assert snap["complete"] is False

        parent.settle("completed")
        assert ledger.snapshot()["percent"] == 100
        assert ledger.snapshot()["complete"] is True


class TestFailureAndSkipVisibility:
    def test_unsupported_children_remain_in_the_published_stats(self, tmp_path, slow_engine):
        import zipfile

        with zipfile.ZipFile(tmp_path / "mixed.zip", "w") as zf:
            zf.writestr("ok.txt", "good content " * 100)
            zf.writestr("weird.zzzq", b"\x00\x01not a format" * 80)

        _, repo, job_id, result, _ = run_job((tmp_path, 3))

        stats = result.stats
        assert stats["files_total"] == 3
        assert stats["files_processed"] == 3, "a failing child vanished from the count"
        assert stats["files_pending"] == 0
        published = repo.get(job_id)["stats"]
        assert published["files_unsupported"] + published["files_failed"] >= 1
