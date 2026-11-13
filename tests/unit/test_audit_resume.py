"""A run that is interrupted must not lose, duplicate or half-write a record.

Three whole-corpus passes ended early (measured: each stopped near 604/698 with
the process at the machine limit). The archive is only usable if that is
survivable: every line already written must be complete JSON, ``--resume`` must
skip exactly what is written, and re-running must not duplicate a path.

The child process per file (see test_audit_isolation.py) is what makes the run
restartable at all; these tests pin the archive side of that contract.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "audit" / "archive_extraction.py"


def _corpus(tmp_path, count=3):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for i in range(count):
        (corpus / f"note{i}.txt").write_text(f"line one {i}\nline two {i}\n",
                                             encoding="utf-8")
    return corpus


def _run(corpus, out, *extra, timeout=600):
    cmd = [sys.executable, str(SCRIPT), "--isolate", "--corpus", str(corpus),
           "--out", str(out), "--timeout", "60", *extra]
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          timeout=timeout)


def _records(out):
    path = Path(out) / "records.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_written_line_is_complete_json(tmp_path):
    corpus, out = _corpus(tmp_path), tmp_path / "out"
    proc = _run(corpus, out)
    assert proc.returncode == 0, proc.stderr[-1500:]
    records = _records(out)
    assert len(records) == 3
    for rec in records:
        assert {"path", "verdict", "text"} <= set(rec)


def test_resume_does_not_duplicate_or_drop_a_file(tmp_path):
    corpus, out = _corpus(tmp_path, count=4), tmp_path / "out"
    assert _run(corpus, out).returncode == 0
    first = _records(out)
    assert _run(corpus, out, "--resume").returncode == 0
    second = _records(out)

    assert len(second) == len(first) == 4
    assert sorted(r["path"] for r in second) == sorted(r["path"] for r in first)
    assert len({r["path"] for r in second}) == 4


def test_an_interrupted_run_leaves_only_whole_records_and_resumes(tmp_path):
    corpus, out = _corpus(tmp_path, count=6), tmp_path / "out"
    cmd = [sys.executable, str(SCRIPT), "--isolate", "--corpus", str(corpus),
           "--out", str(out), "--timeout", "60"]
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    # Let it write something, then kill it the way a run dies - mid-flight.
    path = Path(out) / "records.jsonl"
    while True:
        if path.exists() and len(path.read_text(encoding="utf-8").splitlines()) >= 2:
            break
        if proc.poll() is not None:
            break
    proc.kill()
    proc.wait(timeout=120)

    partial = _records(out)  # every line must still parse
    assert partial, "the run wrote nothing before it was killed"
    assert _run(corpus, out, "--resume").returncode == 0
    resumed = _records(out)
    assert sorted(r["path"] for r in resumed) == [f"note{i}.txt" for i in range(6)]
    assert len(resumed) == 6, "resume must not re-write a file it already has"
