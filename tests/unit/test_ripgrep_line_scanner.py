from __future__ import annotations

import shutil

import pytest

from core.search.ripgrep import (
    RipgrepConfig,
    RipgrepScanner,
    build_ripgrep_pattern,
    resolve_ripgrep_binary,
)


@pytest.fixture(scope="module")
def rg_binary():
    path = resolve_ripgrep_binary()
    if not path:
        pytest.skip("ripgrep (rg) not installed")
    return path


def test_build_ripgrep_pattern_whole_word():
    pattern = build_ripgrep_pattern(["alpha", "beta"], whole_word=True)
    assert "alpha" in pattern
    assert "beta" in pattern


def test_scan_batch_detailed_returns_line_text(rg_binary):
    scanner = RipgrepScanner(
        RipgrepConfig(enabled=True, binary_path=rg_binary, min_content_bytes=1)
    )
    text = "intro\nneedle here\nfooter\n"
    hits = scanner.scan_batch_detailed({42: text}, "needle", max_matches_per_file=5)
    assert hits[42][0].line_number == 2
    assert "needle" in hits[42][0].line_text
    assert hits[42][0].matched_text == "needle"


def test_scan_batch_finds_line_numbers(rg_binary):
    scanner = RipgrepScanner(
        RipgrepConfig(enabled=True, binary_path=rg_binary, min_content_bytes=1)
    )
    text = "intro\nneedle here\nfooter\n"
    hits = scanner.scan_batch({42: text}, "needle", max_matches_per_file=5)
    assert hits.get(42) == [2]


def test_scan_batch_respects_max_matches(rg_binary):
    scanner = RipgrepScanner(
        RipgrepConfig(enabled=True, binary_path=rg_binary, min_content_bytes=1)
    )
    lines = "\n".join(f"hit {i}" for i in range(20))
    hits = scanner.scan_batch({1: lines}, "hit", max_matches_per_file=3)
    assert len(hits.get(1, [])) <= 3


def test_scanner_unavailable_when_disabled(rg_binary):
    scanner = RipgrepScanner(RipgrepConfig(enabled=False, binary_path=rg_binary))
    assert scanner.available is False
    assert scanner.scan_batch({1: "hello world"}, "hello") == {}


def test_status_reports_version(rg_binary):
    scanner = RipgrepScanner(RipgrepConfig(enabled=True, binary_path=rg_binary))
    status = scanner.status()
    assert status["available"] is True
    assert status["version"]
    assert shutil.which("rg") or rg_binary
