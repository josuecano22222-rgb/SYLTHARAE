"""The audit harness must survive a file that hangs, crashes or eats memory.

Measured on the corpus: three consecutive whole-corpus runs stopped around the
same file count with the process resident set at the machine limit and no
traceback, so the archive was never complete in one pass and every per-file
memory figure was a running maximum of the harness rather than the file's own
footprint. The fix is process isolation, and these tests pin the four ways a
child can fail - success, timeout, crash, memory limit - plus the fact that the
limit is really installed in the child.

The stubs below are written to a temp directory, so the tests do not depend on
any corpus file or on an OCR engine being installed.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.audit.archive_extraction import (
    MEMORY_EXIT,
    _limit_own_memory,
    run_isolated,
)

REPO = Path(__file__).resolve().parents[2]

SLEEPER = """
import sys, time
for _ in range(600):
    time.sleep(0.1)
"""

CRASHER = """
import sys
sys.stderr.write("boom: reader exploded\\n")
sys.exit(3)
"""

MEMORY_HOG = """
import sys
sys.exit(9)
"""


def _corpus(tmp_path, name="sample.txt", body="hello audit\n"):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / name).write_text(body, encoding="utf-8")
    return corpus


def _stub(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_run_isolated_returns_a_record_with_the_child_peak_memory(tmp_path):
    corpus = _corpus(tmp_path)
    rec = run_isolated(corpus / "sample.txt", corpus, timeout=60, memory_mb=1024)

    assert rec["isolated"] is True
    assert rec["verdict"] in ("COMPLETE", "NEAR-COMPLETE", "PARTIAL",
                              "INCOMPLETE", "EMPTY-AGREED", "OK-NO-ORACLE",
                              "ERROR")
    # A real per-child high-water mark, not the harness's own running maximum.
    assert rec.get("peak_rss_kb", 0) > 0
    assert rec.get("wall_ms", 0) > 0


def test_a_hanging_file_becomes_a_timeout_record(tmp_path):
    corpus = _corpus(tmp_path)
    stub = _stub(tmp_path, "sleeper.py", SLEEPER)

    rec = run_isolated(corpus / "sample.txt", corpus, timeout=1, memory_mb=512,
                       script=stub)

    assert rec["verdict"] == "TIMEOUT"
    assert "killed after" in rec["error"]
    assert rec["stored_chars"] == 0


def test_a_crashing_child_becomes_an_error_record_with_its_stderr(tmp_path):
    corpus = _corpus(tmp_path)
    stub = _stub(tmp_path, "crasher.py", CRASHER)

    rec = run_isolated(corpus / "sample.txt", corpus, timeout=60, memory_mb=512,
                       script=stub)

    assert rec["verdict"] == "ERROR"
    assert rec["returncode"] == 3
    assert "boom" in rec["error"]
    assert not rec.get("memory_limited")


def test_a_child_killed_on_memory_is_flagged_as_such(tmp_path):
    corpus = _corpus(tmp_path)
    stub = _stub(tmp_path, "hog.py", MEMORY_HOG)

    rec = run_isolated(corpus / "sample.txt", corpus, timeout=60, memory_mb=512,
                       script=stub)

    assert rec["verdict"] == "ERROR"
    assert rec["returncode"] == MEMORY_EXIT
    assert rec["memory_limited"] is True


def test_every_failure_record_keeps_the_identity_of_the_file(tmp_path):
    corpus = _corpus(tmp_path)
    stub = _stub(tmp_path, "crasher.py", CRASHER)

    rec = run_isolated(corpus / "sample.txt", corpus, timeout=60, memory_mb=512,
                       script=stub)

    assert rec["path"] == "sample.txt"
    assert rec["ext"] == ".txt"
    assert rec["bytes"] > 0


def test_the_memory_limit_is_installed_in_the_child():
    code = (
        "from tools.audit.archive_extraction import _limit_own_memory\n"
        "import resource\n"
        "_limit_own_memory(256)\n"
        "print(resource.getrlimit(resource.RLIMIT_AS)[0])\n"
        "try:\n"
        "    bytearray(600 * 1024 * 1024)\n"
        "except MemoryError:\n"
        "    print('refused')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    limit_line, _, refusal = out.stdout.strip().partition("\n")
    assert int(limit_line) == 256 * 1024 * 1024
    assert refusal.strip() == "refused"


def test_the_isolated_record_is_json_serialisable(tmp_path):
    corpus = _corpus(tmp_path)
    rec = run_isolated(corpus / "sample.txt", corpus, timeout=60, memory_mb=1024)
    json.dumps(rec)  # the archive is JSONL; a record that cannot be written is no record
