"""tools/ci/junit_annotations.py: totals, skips with reasons, failures."""
import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ci" / "junit_annotations.py"
REPORT = """<?xml version="1.0"?>
<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1">
  <testcase classname="tests.unit.test_a" name="test_ok"/>
  <testcase classname="tests.unit.test_a" name="test_engine_only">
    <skipped message="only applies to the rapidocr fallback"/></testcase>
  <testcase classname="tests.unit.test_a" name="test_bad">
    <failure message="assert 1 == 2&#10;more">trace</failure></testcase>
</testsuite></testsuites>"""


def _run(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("junit_annotations", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = tmp_path / "r.xml"
    report.write_text(REPORT)
    mod.main(str(report))
    return capsys.readouterr().out.splitlines()


def test_every_skip_is_named_with_its_reason(tmp_path, capsys):
    out = _run(tmp_path, capsys)
    [line] = [l for l in out if "skipped tests" in l]
    assert line.startswith("::notice title=1 skipped tests::")
    assert "test_a::test_engine_only  --  only applies to the rapidocr fallback" in line


def test_totals_and_failures_are_reported(tmp_path, capsys):
    out = _run(tmp_path, capsys)
    assert out[0] == "::notice title=pytest totals::tests=3, failures=1, errors=0, skipped=1"
    assert any(l.startswith("::error title=1 failed tests::") and "assert 1 == 2" in l for l in out)
