import subprocess
import time

from core.search.session import SearchSession, SearchSessionRegistry


def test_cancel_is_idempotent():
    session = SearchSession("test-correlation")
    session.cancel()
    session.cancel()
    assert session.is_cancelled
    assert session.cancel_timing_ms() is not None


def test_registry_cancel_unknown_returns_false():
    assert SearchSessionRegistry.cancel("missing-id") is False


def test_cancel_terminates_rg_process():
    session = SearchSession("rg-test")
    proc = subprocess.Popen(["python", "-c", "import time; time.sleep(30)"])
    session.register_rg(proc)
    session.cancel()
    time.sleep(0.2)
    assert proc.poll() is not None
