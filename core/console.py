"""Thread-safe console output.

Why this exists
---------------
Processing runs on many worker threads at once.  Each worker writes progress,
per-file metrics and "Elapsed time" blocks straight to stdout with bare
``print`` calls, so two threads could interleave inside a single line.  In the
production logs that showed up as one line being overwritten by the next
(``Progress: 3/1000`` appearing inside another line) and as mangled
``Elapsed time`` blocks containing fragments of two different reports.

This module serializes console writes and keeps the carriage-return progress
line from being overwritten by a regular line:

* :meth:`Console.progress` writes the in-place ``\\r`` progress line;
* :meth:`Console.line` ends the progress line first, then writes a full line;
* :meth:`Console.block` writes a multi-line report atomically, so a metrics
  block can never be interleaved with another thread's output.

The console is disabled for non-interactive streams (a pipe or a file): a
carriage-return line there would smear together in the captured log, which is
exactly the garbled output this module exists to prevent.
"""
import sys
import threading


class Console:
    """Serializes writes to the process console."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()
            cls._instance._progress_active = False
        return cls._instance

    # ------------------------------------------------------------------
    # Stream capability
    # ------------------------------------------------------------------
    @staticmethod
    def supports_inplace_progress() -> bool:
        """True when the current stdout is an interactive terminal."""
        stream = sys.stdout
        try:
            return bool(stream is not None and stream.isatty())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def progress(self, text: str) -> None:
        """Write (or refresh) the in-place progress line.

        Falls back to a plain line when output is redirected, so a captured
        log contains readable updates instead of one long carriage-returned
        smear.
        """
        with self._lock:
            if not self.supports_inplace_progress():
                # Only emit updates that change the text boundary, never every
                # single item, to keep captured logs usable.
                if text != getattr(self, '_last_redirected', None):
                    self._last_redirected = text
                    print(text, flush=True)
                return
            print(f"\r{text}", end='', flush=True)
            self._progress_active = True

    def end_progress(self) -> None:
        """Terminate the in-place progress line, if one is open."""
        with self._lock:
            if self._progress_active:
                print()
                self._progress_active = False

    def line(self, text: str = "") -> None:
        """Write one complete line, ending any open progress line first."""
        with self._lock:
            self.end_progress()
            print(text, flush=True)

    def block(self, lines) -> None:
        """Write a multi-line report as one indivisible unit."""
        with self._lock:
            self.end_progress()
            print("\n".join(lines), flush=True)


#: Process-wide instance.
console = Console()


def progress(text: str) -> None:
    """Module-level shortcut for :meth:`Console.progress`."""
    console.progress(text)


def end_progress() -> None:
    """Module-level shortcut for :meth:`Console.end_progress`."""
    console.end_progress()


def line(text: str = "") -> None:
    """Module-level shortcut for :meth:`Console.line`."""
    console.line(text)


def block(lines) -> None:
    """Module-level shortcut for :meth:`Console.block`."""
    console.block(lines)
