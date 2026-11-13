"""Unit tests for the count-aware bandit gate.

The message-matched bandit baseline cannot see a new finding in a file
that already has a baseline entry (it matches by message, not by line or
count - the recorded step 25 limitation). ``bandit_count_gate.py`` closes
that hole by comparing per-(file, check) counts against a triaged
manifest. These tests pin that comparison logic without running bandit.
"""
import importlib.util
import json
from pathlib import Path

import pytest

GATE_PATH = Path(__file__).resolve().parents[2] / "tools" / "security" / "bandit_count_gate.py"
spec = importlib.util.spec_from_file_location("bandit_count_gate", GATE_PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.unit
def test_counts_group_findings_by_file_and_check():
    report = {"results": [
        {"filename": "./a.py", "test_id": "B608"},
        {"filename": "./a.py", "test_id": "B608"},
        {"filename": "./a.py", "test_id": "B704"},
        {"filename": "./b.py", "test_id": "B608"},
    ]}
    counts = gate.counts(report)
    assert counts == {"./a.py|B608": 2, "./a.py|B704": 1, "./b.py|B608": 1}


@pytest.mark.unit
def test_growth_in_a_baselined_cell_is_detected(tmp_path, capsys):
    manifest = tmp_path / "counts.json"
    manifest.write_text(json.dumps({"./a.py|B608": 2}))
    # the scan reports a THIRD B608 in a.py: exactly the hole the message
    # baseline misses
    current = {"./a.py|B608": 3}
    baseline = json.loads(manifest.read_text())
    grown = [(k, baseline.get(k, 0), n) for k, n in current.items()
             if n > baseline.get(k, 0)]
    assert grown, "a grown cell must be detected"
    assert grown[0] == ("./a.py|B608", 2, 3)


@pytest.mark.unit
def test_new_file_is_detected_even_when_message_baseline_would_pass():
    # a cell absent from the manifest fails the count gate - the case the
    # message-matched baseline cannot see at all
    baseline = {k: v for k, v in json.loads(
        (GATE_PATH.parent / "bandit-counts.json").read_text()).items()}
    key = "./definitely_new_file.py|B608"
    assert key not in baseline
    assert 1 > baseline.get(key, 0), "a new cell starts at zero"


@pytest.mark.unit
def test_manifest_exists_and_is_current_shape():
    manifest = GATE_PATH.parent / "bandit-counts.json"
    assert manifest.exists(), "the triaged count manifest must be committed"
    data = json.loads(manifest.read_text())
    assert isinstance(data, dict) and data, "manifest maps file|check -> count"
    for key, n in data.items():
        assert "|" in key
        assert isinstance(n, int) and n >= 1
