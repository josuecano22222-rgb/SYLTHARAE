"""Unit tests for the job state machine, options sanitization and throttling."""
import pytest

from services.jobs import job_state
from services.jobs.manager import EventThrottle, JobsConfig
from services.ingesting.options import IngestionOptions


class TestJobStateMachine:
    def test_legal_transitions(self):
        assert job_state.can_transition(job_state.QUEUED, job_state.RUNNING)
        assert job_state.can_transition(job_state.RUNNING, job_state.PAUSED)
        assert job_state.can_transition(job_state.RUNNING, job_state.CANCELLING)
        assert job_state.can_transition(job_state.PAUSED, job_state.RUNNING)
        assert job_state.can_transition(job_state.CANCELLING, job_state.CANCELLED)

    def test_illegal_transitions(self):
        assert not job_state.can_transition(job_state.COMPLETED, job_state.RUNNING)
        assert not job_state.can_transition(job_state.CANCELLED, job_state.RUNNING)
        assert not job_state.can_transition(job_state.QUEUED, job_state.COMPLETED)
        with pytest.raises(ValueError):
            job_state.transition(job_state.COMPLETED, job_state.RUNNING)

    def test_terminal_states(self):
        for s in (job_state.CANCELLED, job_state.COMPLETED,
                  job_state.COMPLETED_WITH_WARNINGS, job_state.FAILED):
            assert job_state.is_terminal(s)
        for s in (job_state.QUEUED, job_state.RUNNING, job_state.PAUSED,
                  job_state.CANCELLING):
            assert not job_state.is_terminal(s)

    def test_final_state_selection(self):
        assert job_state.final_state_for(True, True) == job_state.FAILED
        assert job_state.final_state_for(False, True) == job_state.COMPLETED_WITH_WARNINGS
        assert job_state.final_state_for(False, False) == job_state.COMPLETED

    def test_all_spec_states_present(self):
        # Spec section 5 requires exactly these states.
        assert set(job_state.ALL_STATES) == {
            "QUEUED", "RUNNING", "PAUSED", "CANCELLING", "CANCELLED",
            "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED",
        }


class TestOptionsSanitization:
    def test_workers_clamped(self):
        opts = IngestionOptions(max_workers=10_000).sanitized()
        assert opts.max_workers == 64

    def test_negative_workers_rejected(self):
        opts = IngestionOptions(max_workers=-5).sanitized()
        assert opts.max_workers == 0

    def test_checkpoint_policy_whitelist(self):
        opts = IngestionOptions(checkpoint="; rm -rf /").sanitized()
        assert opts.checkpoint == "auto"
        assert IngestionOptions(checkpoint="fresh").sanitized().checkpoint == "fresh"


class TestEventThrottle:
    def test_always_events_kept(self):
        t = EventThrottle(sample_threshold=2)
        for i in range(50):
            t.add("ERROR", {"i": i})
        assert len(t.pending) == 50

    def test_file_events_sampled(self):
        t = EventThrottle(sample_threshold=5)
        for i in range(100):
            t.add("FILE_COMPLETED", {"i": i})
        kinds = [e["event_type"] for e in t.pending]
        assert kinds.count("FILE_COMPLETED") == 5
        # one sampling notice
        assert kinds.count("WARNING") == 1

    def test_config_bounds(self):
        assert JobsConfig.max_concurrent_jobs() >= 1
        assert JobsConfig.stale_seconds() >= 60
        assert JobsConfig.progress_min_interval() >= 0.2
