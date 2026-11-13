"""Unit: the batch-analysis figures come from recorded data or say nothing.

The page used to hard-code ``avg_processing_time = 2.3`` and
``success_rate = 98.5`` whenever it could not compute them, and the template
rendered both as plain numbers ("2.3s Avg Speed", "98.5% Success Rate") plus a
three-entry history panel with invented batches. To an operator those look
exactly like readings of their own system.

These tests pin the replacement behaviour:

* a figure is measured from the rows/jobs that were actually recorded, with the
  sample size carried along;
* where nothing was recorded, the figure is *unavailable* and says so - it is
  never a constant;
* a constant cannot come back: the route source and the template are checked
  for the specific fabricated values.
"""

import re
from pathlib import Path

import pytest

from Api.services.analysis_stats import (
    analysis_measurements,
    average_processing_time,
    estimated_queue_duration,
    processing_success_rate,
    unavailable_measurements,
)
from core.measurements import MEASURED, UNAVAILABLE

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class FakeQuery:
    """Stands in for ``execute_query``: answers by matching the SQL text."""

    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []

    def __call__(self, sql, params=(), fetch=None):
        self.calls.append((sql, params, fetch))
        for fragment, row in self.rows.items():
            if fragment in sql:
                return row
        return (0, 0) if "COUNT(*) FILTER" in sql or "COUNT(*) AS jobs" in sql else None

    @property
    def last_params(self):
        return self.calls[-1][1]


class TestSuccessRate:
    def test_is_measured_from_stored_objects(self):
        query = FakeQuery({"COUNT(*) FILTER": (184, 200)})
        value = processing_success_rate(query, days=7)
        assert value.state == MEASURED
        assert value.value == pytest.approx(92.0)
        assert value.sample_size == 200
        assert "184 of 200" in value.detail

    def test_is_unavailable_when_nothing_was_stored(self):
        value = processing_success_rate(FakeQuery({"COUNT(*) FILTER": (0, 0)}), days=7)
        assert value.state == UNAVAILABLE
        assert value.value is None
        assert "Insufficient data" in value.detail
        assert "7 days" in value.detail

    def test_window_is_a_bound_parameter_not_interpolated_sql(self):
        query = FakeQuery({"COUNT(*) FILTER": (1, 1)})
        processing_success_rate(query, days=14)
        assert query.last_params == (14,)
        assert "14" not in query.calls[0][0]

    def test_a_failing_query_does_not_invent_a_rate(self):
        def exploding(sql, params=(), fetch=None):
            raise RuntimeError('psycopg2: connection to server at "db.internal" failed')

        value = processing_success_rate(exploding)
        assert value.state == UNAVAILABLE
        assert value.value is None


class TestAverageProcessingTime:
    def test_is_estimated_from_completed_jobs(self):
        query = FakeQuery({"COUNT(*) AS jobs": (12, 48.0, 24)})
        value = average_processing_time(query)
        assert value.state == "estimated"
        assert value.value == pytest.approx(2.0)
        assert value.sample_size == 12
        assert "12 completed jobs" in value.detail
        # It must say what the derivation is, not present itself as a stopwatch.
        assert "Elapsed time" in value.detail

    def test_is_unavailable_without_completed_jobs(self):
        value = average_processing_time(FakeQuery({"COUNT(*) AS jobs": (0, None, 0)}))
        assert value.state == UNAVAILABLE
        assert value.value is None
        assert "no completed job has recorded timings" in value.detail

    def test_only_finished_job_states_are_counted(self):
        query = FakeQuery({"COUNT(*) AS jobs": (1, 3.0, 3)})
        average_processing_time(query)
        sql = query.calls[0][0]
        assert "COMPLETED_WITH_WARNINGS" in sql
        assert "'FAILED'" not in sql  # a failed job's elapsed time is not throughput
        assert "completed_at > started_at" in sql


class TestQueueEstimate:
    def test_inherits_the_unavailability_of_its_basis(self):
        value = estimated_queue_duration(25, average_processing_time(
            FakeQuery({"COUNT(*) AS jobs": (0, None, 0)})))
        assert value.state == UNAVAILABLE
        assert value.value is None
        assert "average processing time" in value.detail

    def test_empty_queue_is_measured_zero_not_a_guess(self):
        per_file = average_processing_time(FakeQuery({"COUNT(*) AS jobs": (3, 6.0, 3)}))
        value = estimated_queue_duration(0, per_file)
        assert value.state == MEASURED
        assert value.value == 0.0
        assert "Nothing is waiting" in value.detail

    def test_derives_from_the_measured_per_file_time(self):
        per_file = average_processing_time(FakeQuery({"COUNT(*) AS jobs": (3, 6.0, 3)}))
        value = estimated_queue_duration(queue_size=30, per_file=per_file)
        assert value.state == "estimated"
        assert value.value == pytest.approx(1.0)  # 30 files * 2s = 60s = 1 min
        assert "30 waiting" in value.detail


class TestAggregate:
    def test_returns_the_three_figures_the_page_renders(self):
        stats = analysis_measurements(
            FakeQuery({"COUNT(*) FILTER": (9, 10), "COUNT(*) AS jobs": (2, 8.0, 4)}),
            queue_size=4,
        )
        assert set(stats) == {"success_rate", "avg_processing_time", "estimated_time"}
        assert all(isinstance(v, dict) for v in stats.values())
        assert stats["success_rate"]["state"] == MEASURED
        assert stats["avg_processing_time"]["state"] == "estimated"

    def test_the_failure_path_reports_nothing_rather_than_defaults(self):
        stats = unavailable_measurements("The processing figures could not be read")
        for key, payload in stats.items():
            assert payload["state"] == UNAVAILABLE, key
            assert payload["value"] is None, key
            assert payload["display"].startswith("The processing figures"), key


class TestFabricatedValuesCannotReturn:
    """The specific defect from the specification, pinned at the source."""

    ROUTE = PROJECT_ROOT / "Api/routes/analysis.py"
    TEMPLATE = PROJECT_ROOT / "templates/Analysis/analysis_batch.html"

    @staticmethod
    def _code_only(path: Path) -> str:
        """The file with comments removed.

        A comment that records *why* the constants were removed is useful
        documentation; a constant in executable code is the defect. Checking
        code tokens keeps both true.
        """
        import io
        import tokenize

        pieces = []
        with tokenize.open(path) as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type != tokenize.COMMENT:
                    pieces.append(token.string)
        return " ".join(pieces)

    def test_route_has_no_synthetic_fallbacks(self):
        source = self._code_only(self.ROUTE)
        assert "2.3" not in source, "the 2.3s average processing time fallback is back"
        assert "98.5" not in source, "the 98.5% success-rate fallback is back"

    def test_template_shows_no_hardcoded_metrics(self):
        template = self.TEMPLATE.read_text()
        assert "98.5" not in template
        assert "2.3s" not in template

    def test_history_panel_is_not_a_fixture_list(self):
        """The invented batches were presentation, not data."""
        template = self.TEMPLATE.read_text()
        assert "Batch</h6>" not in template
        assert "Batch') }} #" not in template
        assert "recent_jobs" in template, "the panel must render recorded jobs"
        assert re.search(r"\{% for job in recent_jobs %\}", template)

    def test_template_renders_the_state_of_each_figure(self):
        template = self.TEMPLATE.read_text()
        for key in ("avg_processing_time", "success_rate", "estimated_time"):
            assert f"{key}.available" in template, key
            assert f"{key}.display" in template, key
        assert "measurement-unavailable" in template
