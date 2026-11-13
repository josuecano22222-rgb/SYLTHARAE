"""The final-versus-baseline comparison must not hide a regression or a loss.

Round 17's whole point is that a re-run can be compared with the first run.
That comparison is code, so it gets tests: a file that disappears between the
two runs must be reported (the failure mode of the earlier harness was exactly
a run that produced 604 of 698 records and said nothing about the rest), and a
recall drop must be called a regression rather than folded into an average.
"""
import json

from tools.audit.diff_runs import compare, load, render


def record(path, verdict="COMPLETE", recall=1.0, chars=100):
    return {"path": path, "verdict": verdict, "recall": recall,
            "stored_chars": chars}


def test_a_file_missing_from_the_final_run_is_reported():
    baseline = {r["path"]: r for r in [record("a.txt"), record("b.txt")]}
    final = {r["path"]: r for r in [record("a.txt")]}
    summary = compare(baseline, final)
    assert summary["only_in_baseline"] == ["b.txt"]
    assert "b.txt" in render(summary)


def test_a_recall_drop_is_a_regression():
    baseline = {"a.txt": record("a.txt", recall=1.0)}
    final = {"a.txt": record("a.txt", recall=0.90)}
    summary = compare(baseline, final)
    assert len(summary["recall_regressions"]) == 1
    assert summary["recall_regressions"][0]["delta"] == -0.1
    assert "regression" in render(summary)


def test_an_unchanged_file_is_not_reported():
    baseline = {"a.txt": record("a.txt")}
    final = {"a.txt": record("a.txt")}
    summary = compare(baseline, final)
    assert summary["verdict_changes"] == []
    assert summary["recall_changes"] == []
    assert summary["storage_changes"] == []


def test_a_verdict_change_is_reported_with_both_sides():
    baseline = {"a.dotx": record("a.dotx", verdict="PARTIAL", recall=0.839)}
    final = {"a.dotx": record("a.dotx", verdict="COMPLETE", recall=1.0)}
    summary = compare(baseline, final)
    change = summary["verdict_changes"][0]
    assert (change["baseline"], change["final"]) == ("PARTIAL", "COMPLETE")


def test_a_large_storage_change_is_reported():
    baseline = {"a.docx": record("a.docx", chars=1000)}
    final = {"a.docx": record("a.docx", chars=1500)}
    summary = compare(baseline, final)
    assert summary["storage_changes"][0]["delta_pct"] == 50.0


def test_load_skips_blank_lines_and_round_trips(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps(record("a.txt")) + "\n\n", encoding="utf-8")
    assert list(load(path)) == ["a.txt"]
