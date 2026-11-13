"""docs/ARCHITECTURE.md diagrams must match the code they depict."""
import re
from pathlib import Path

from services.jobs import job_state

DOC = Path(__file__).resolve().parents[2] / "docs" / "ARCHITECTURE.md"


def _mermaid_blocks():
    return re.findall(r"```mermaid\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)


def test_architecture_has_mermaid_diagrams():
    kinds = {block.split("\n", 1)[0].split()[0] for block in _mermaid_blocks()}
    assert {"flowchart", "sequenceDiagram", "stateDiagram-v2"} <= kinds


def test_job_state_diagram_matches_transitions():
    block = next(b for b in _mermaid_blocks() if b.startswith("stateDiagram-v2"))
    edges = set(re.findall(r"^\s*(\w+) --> (\w+)\s*$", block, re.M))
    expected = {(src, dst) for src, dsts in job_state.TRANSITIONS.items() for dst in dsts}
    assert edges == expected
    terminals = set(re.findall(r"^\s*(\w+) --> \[\*\]\s*$", block, re.M))
    assert terminals == set(job_state.TERMINAL_STATES)
