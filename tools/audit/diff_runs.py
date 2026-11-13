"""Diff two audit archives: what changed, and did anything regress.

Phase 7 asks for the final run to be compared against the Phase 1 baseline
record by record. The comparison is mechanical - same corpus, two archives -
so it is a script rather than a paragraph: a hand-written summary of 698 rows
is exactly the kind of claim that drifts from the data.

    python tools/audit/diff_runs.py --baseline <a>/records.jsonl \
        --final <b>/records.jsonl --out <dir>

Writes `diff.json` and `DIFF.md`. A file that exists in only one archive is
reported as missing, never ignored: a run that silently dropped 94 files is
the failure this comparison exists to catch.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

#: Verdicts that mean "the reader produced something measurable".
GOOD = {"COMPLETE", "NEAR-COMPLETE", "PARTIAL", "OK-NO-ORACLE", "EMPTY-PLAUSIBLE",
        "EMPTY-AGREED", "UNVERIFIABLE"}


def load(path: Path) -> Dict[str, Dict[str, Any]]:
    """Read a record set, gzipped or not.

    The tracked artefact of a run is records.jsonl.gz (the uncompressed file is
    ~130 MB and is deliberately not committed), so a baseline from an earlier
    round is only available compressed. Diffing against it is the whole point of
    the regression gate.
    """
    records = {}
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records[record["path"]] = record
    return records


def compare(baseline: Dict[str, Dict[str, Any]],
            final: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Return the differences that matter for a completeness claim."""
    only_baseline = sorted(set(baseline) - set(final))
    only_final = sorted(set(final) - set(baseline))
    shared = sorted(set(baseline) & set(final))

    verdict_changes = []
    recall_changes = []
    char_changes = []
    for path in shared:
        before, after = baseline[path], final[path]
        if before.get("verdict") != after.get("verdict"):
            verdict_changes.append({
                "path": path,
                "baseline": before.get("verdict"),
                "final": after.get("verdict"),
                "baseline_recall": before.get("recall"),
                "final_recall": after.get("recall"),
            })
        r0, r1 = before.get("recall"), after.get("recall")
        if r0 is not None and r1 is not None and abs(r1 - r0) > 0.0005:
            recall_changes.append({"path": path, "baseline": r0, "final": r1,
                                   "delta": round(r1 - r0, 4)})
        c0, c1 = before.get("stored_chars", 0), after.get("stored_chars", 0)
        if c0 and abs(c1 - c0) / c0 > 0.02:
            char_changes.append({"path": path, "baseline": c0, "final": c1,
                                 "delta_pct": round(100 * (c1 - c0) / c0, 1)})

    regressions = [c for c in recall_changes if c["delta"] < 0]
    summary = {
        "baseline_files": len(baseline),
        "final_files": len(final),
        "only_in_baseline": only_baseline,
        "only_in_final": only_final,
        "verdict_counts_baseline": dict(collections.Counter(
            r.get("verdict") for r in baseline.values()).most_common()),
        "verdict_counts_final": dict(collections.Counter(
            r.get("verdict") for r in final.values()).most_common()),
        "verdict_changes": sorted(verdict_changes, key=lambda c: c["path"]),
        "recall_changes": sorted(recall_changes, key=lambda c: -abs(c["delta"])),
        "recall_regressions": regressions,
        "storage_changes": sorted(char_changes, key=lambda c: -abs(c["delta_pct"]))[:50],
        "stored_chars_baseline": sum(r.get("stored_chars", 0) for r in baseline.values()),
        "stored_chars_final": sum(r.get("stored_chars", 0) for r in final.values()),
    }
    return summary


def render(summary: Dict[str, Any]) -> str:
    lines = ["# Final run versus baseline (generated)", ""]
    lines.append(f"- Files: baseline {summary['baseline_files']}, "
                 f"final {summary['final_files']}")
    lines.append(f"- Stored characters: baseline {summary['stored_chars_baseline']:,}, "
                 f"final {summary['stored_chars_final']:,}")
    lines.append(f"- Files only in the baseline: "
                 f"{len(summary['only_in_baseline'])}")
    lines.append(f"- Files only in the final run: "
                 f"{len(summary['only_in_final'])}")
    lines.append(f"- Recall regressions: {len(summary['recall_regressions'])}")
    lines.append("")

    lines.append("| Verdict | Baseline | Final |")
    lines.append("|---|---|---|")
    keys = sorted(set(summary["verdict_counts_baseline"]) |
                  set(summary["verdict_counts_final"]))
    for key in keys:
        lines.append(f"| {key} | {summary['verdict_counts_baseline'].get(key, 0)} | "
                     f"{summary['verdict_counts_final'].get(key, 0)} |")
    lines.append("")

    if summary["only_in_baseline"]:
        lines.append("## Files missing from the final run")
        lines.append("")
        for path in summary["only_in_baseline"]:
            lines.append(f"- `{path}`")
        lines.append("")

    if summary["verdict_changes"]:
        lines.append("## Verdict changes")
        lines.append("")
        lines.append("| file | baseline | final | recall before | recall after |")
        lines.append("|---|---|---|---|---|")
        for change in summary["verdict_changes"]:
            lines.append(f"| `{change['path']}` | {change['baseline']} | "
                         f"{change['final']} | {change['baseline_recall']} | "
                         f"{change['final_recall']} |")
        lines.append("")

    if summary["recall_changes"]:
        lines.append("## Recall changes (more than 0.0005)")
        lines.append("")
        lines.append("| file | before | after | delta |")
        lines.append("|---|---|---|---|")
        for change in summary["recall_changes"]:
            marker = " **regression**" if change["delta"] < 0 else ""
            lines.append(f"| `{change['path']}` | {change['baseline']} | "
                         f"{change['final']} | {change['delta']:+}{marker} |")
        lines.append("")

    if summary["storage_changes"]:
        lines.append("## Storage changes (more than 2 %)")
        lines.append("")
        lines.append("| file | chars before | chars after | change |")
        lines.append("|---|---|---|---|")
        for change in summary["storage_changes"]:
            lines.append(f"| `{change['path']}` | {change['baseline']:,} | "
                         f"{change['final']:,} | {change['delta_pct']:+} % |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--final", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    summary = compare(load(Path(args.baseline)), load(Path(args.final)))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "diff.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False),
                                   encoding="utf-8")
    (out / "DIFF.md").write_text(render(summary), encoding="utf-8")
    print(f"wrote {out / 'DIFF.md'}: {len(summary['verdict_changes'])} verdict changes, "
          f"{len(summary['recall_changes'])} recall changes, "
          f"{len(summary['recall_regressions'])} regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
