"""Analysis figures, derived from recorded data only.

The batch-analysis page used to show an average processing time of 2.3 seconds
and a 98.5% success rate whenever it could not compute real ones. Those numbers
were constants in the source, but an operator reading the page cannot tell a
constant from a measurement - which makes them worse than showing nothing.

Every figure here is therefore a :class:`~core.measurements.Measurement`:

* the success rate is **measured** from the ``paths`` rows stored in the
  window, and is unavailable when nothing was stored in it;
* the average processing time is **estimated** from completed jobs - the
  elapsed wall time of jobs that actually finished, divided by the files they
  actually processed - and is unavailable when no job has recorded both;
* the queue estimate is derived from that average, and inherits its
  availability: with no measured per-file time there is no honest estimate of
  how long the queue will take, so it says so instead of showing 0s.

The job system is the authoritative source for processing behaviour, so jobs
(not a second timer inside the web process) are what these read.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from core.measurements import (
    INSUFFICIENT_DATA_TEXT,
    Measurement,
    estimated,
    measured,
    unavailable,
)

logger = logging.getLogger(__name__)

#: Job states that represent work which ran to a finish. A cancelled or failed
#: job's elapsed time says nothing about per-file throughput.
_FINISHED_STATES = ("COMPLETED", "COMPLETED_WITH_WARNINGS")


def _query(execute_query: Callable[..., Any], sql: str, params: tuple = ()) -> Any:
    return execute_query(sql, params, fetch="one") if params else execute_query(sql, fetch="one")


def processing_success_rate(execute_query: Callable[..., Any], days: int = 7) -> Measurement:
    """Share of objects stored in the window that were read successfully.

    Measured. ``file_status = 'Read'`` is the status the pipeline sets once a
    reader returned content for the object, so this is a real outcome ratio
    over the requested window - not a lifetime average diluted by history.
    """
    try:
        row = _query(
            execute_query,
            """
            SELECT COUNT(*) FILTER (WHERE file_status = 'Read') AS succeeded,
                   COUNT(*) AS total
            FROM paths
            WHERE date_creation >= CURRENT_DATE - (%s * INTERVAL '1 day')
            """,
            (int(days),),
        )
    except Exception as exc:  # the page must still render
        logger.warning("Could not measure the processing success rate: %s", exc)
        return unavailable("Could not read processing outcomes", unit="%")

    succeeded, total = (row or (0, 0))[0] or 0, (row or (0, 0))[1] or 0
    if not total:
        return unavailable(
            f"{INSUFFICIENT_DATA_TEXT}: no objects were stored in the last {days} days",
            unit="%",
        )
    return measured(
        100.0 * succeeded / total,
        unit="%",
        sample_size=total,
        detail=f"{succeeded} of {total} objects read in the last {days} days",
    )


def average_processing_time(execute_query: Callable[..., Any], days: int = 30) -> Measurement:
    """Average seconds per processed file, from jobs that finished.

    Estimated, and labelled as such: it is elapsed job time divided by files
    the job reported processing, so it includes the job's own overhead
    (discovery, hashing, writing) rather than being a stopwatch around a single
    reader. The sample size is shown so the operator can judge it.
    """
    states = ", ".join(f"'{state}'" for state in _FINISHED_STATES)
    try:
        row = _query(
            execute_query,
            f"""
            SELECT COUNT(*) AS jobs,
                   SUM(EXTRACT(EPOCH FROM (completed_at - started_at))) AS seconds,
                   SUM(COALESCE((stats->>'files_completed')::numeric, 0)) AS files
            FROM jobs
            WHERE status IN ({states})
              AND started_at IS NOT NULL
              AND completed_at IS NOT NULL
              AND completed_at > started_at
              AND completed_at >= NOW() - (%s * INTERVAL '1 day')
              AND COALESCE((stats->>'files_completed')::numeric, 0) > 0
            """,
            (int(days),),
        )
    except Exception as exc:
        logger.warning("Could not estimate the average processing time: %s", exc)
        return unavailable("Could not read completed job timings", unit="s")

    jobs, seconds, files = (row or (0, 0, 0))
    jobs = int(jobs or 0)
    files = float(files or 0)
    if not jobs or not seconds or not files:
        return unavailable(
            f"{INSUFFICIENT_DATA_TEXT}: no completed job has recorded timings"
            f" in the last {days} days",
            unit="s",
        )
    return estimated(
        float(seconds) / files,
        unit="s",
        sample_size=jobs,
        detail=f"Elapsed time ÷ files processed, across {jobs} completed job"
               f"{'s' if jobs != 1 else ''} in the last {days} days",
    )


def estimated_queue_duration(queue_size: int, per_file: Measurement) -> Measurement:
    """How long the waiting queue is likely to take, given a measured average.

    With no basis for the per-file time there is no basis for this either, so
    it reports unavailable rather than 0s (0s reads as "instant", which is a
    claim nobody made).
    """
    if not per_file.available:
        return unavailable(
            "Cannot be estimated without an average processing time", unit="min"
        )
    if queue_size <= 0:
        return measured(0.0, unit="min", detail="Nothing is waiting to be analysed")
    minutes = queue_size * float(per_file.value) / 60.0
    return estimated(
        minutes,
        unit="min",
        sample_size=queue_size,
        detail=f"{queue_size} waiting × {per_file.display()} per file",
    )


def analysis_measurements(
    execute_query: Callable[..., Any],
    queue_size: int,
    days: int = 7,
) -> dict:
    """Everything the batch-analysis page reports, as measurements."""
    success = processing_success_rate(execute_query, days=days)
    per_file = average_processing_time(execute_query)
    return {
        "success_rate": success.to_dict(),
        "avg_processing_time": per_file.to_dict(),
        "estimated_time": estimated_queue_duration(queue_size, per_file).to_dict(),
    }


def unavailable_measurements(reason: str = "") -> dict:
    """The page's three figures, all explicitly unavailable.

    Used when the data behind them could not be read at all: the page still
    renders (an operator with a broken query still needs the retry tools on
    it), but it makes no claim about processing behaviour.
    """
    reason = reason or INSUFFICIENT_DATA_TEXT
    return {
        "success_rate": unavailable(reason, unit="%").to_dict(),
        "avg_processing_time": unavailable(reason, unit="s").to_dict(),
        "estimated_time": unavailable(reason, unit="min").to_dict(),
    }
