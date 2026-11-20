"""Start a multiprocessing manager without letting the caller hang.

``multiprocessing.Manager()`` blocks until the manager process answers. It is
called lazily from request handlers - the concurrency dashboard's
``/concurrency/api/metrics`` builds ``hub.process_mgr`` and ``hub.pool_mgr`` on
demand - and there are hosts where that answer never comes:

* a container without a writable ``/dev/shm`` or with a small one;
* an exhausted semaphore or file-descriptor budget;
* a seccomp profile that refuses the fork;
* and, most insidiously, a **forked** child started from a process that already
  runs threads: the child inherits the locked state of every lock its parent
  held, and ``Manager()`` waits forever on one of them.

Any of those turns a page request into a worker that never returns, with no
error to report and nothing in the log. The start therefore happens in a daemon
thread with a deadline: the caller either gets the manager or an exception it
can report.

The manager is not cancellable from outside, so a start that succeeds after the
deadline leaves an unreferenced manager process behind; it is a child of a
daemon thread's process and exits with the interpreter. That is the price of not
being able to interrupt it, and it is the right trade against a hung request.
"""

from __future__ import annotations

import multiprocessing as mp
import threading

#: How long a manager start may take before the caller is told it did not
#: happen. Five seconds is far longer than a healthy start (tens of
#: milliseconds) and short enough that a dashboard request still answers.
MANAGER_START_TIMEOUT_SECONDS = 5.0


def start_manager_with_timeout(timeout: float = MANAGER_START_TIMEOUT_SECONDS):
    """Start a multiprocessing manager, or fail within ``timeout`` seconds."""
    outcome: dict = {}

    def _boot() -> None:
        try:
            outcome["manager"] = mp.Manager()
        except BaseException as exc:  # noqa: BLE001 - reported verbatim below
            outcome["error"] = exc

    starter = threading.Thread(target=_boot, name="manager-boot", daemon=True)
    starter.start()
    starter.join(timeout)
    if "manager" in outcome:
        return outcome["manager"]
    error = outcome.get("error")
    if error is not None:
        raise RuntimeError(
            f"the multiprocessing manager could not be started: {error}") from error
    raise TimeoutError(
        f"the multiprocessing manager did not start within {timeout:g}s")


__all__ = ["MANAGER_START_TIMEOUT_SECONDS", "start_manager_with_timeout"]
