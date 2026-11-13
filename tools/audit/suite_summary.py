"""Summarise pytest JUnit XML: per-run totals and failures grouped by module.

Written for the round-19 record so the numbers in the report are produced from
the artefact rather than typed by hand.

    python -m tools.audit.suite_summary audit_report/r19/junit_unit.xml \
        audit_report/r19/junit_integration.xml --csv audit_report/r19/suite_summary.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import xml.etree.ElementTree as ET
from pathlib import Path


def suite_of(path: Path) -> ET.Element:
    root = ET.parse(path).getroot()
    return root if root.tag == "testsuite" else root.find("testsuite")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", nargs="+", type=Path)
    parser.add_argument("--csv", type=Path, default=None)
    options = parser.parse_args()

    rows = []
    for path in options.xml:
        suite = suite_of(path)
        counts = {k: int(suite.get(k) or 0)
                  for k in ("tests", "failures", "errors", "skipped")}
        print(f"{path.name}: " + " ".join(f"{k}={v}" for k, v in counts.items()))
        groups: collections.Counter[str] = collections.Counter()
        for case in suite.iter("testcase"):
            bad = case.find("failure") is not None or case.find("error") is not None
            if not bad:
                continue
            module = (case.get("classname") or "").replace("tests.", "")
            groups[module] += 1
            rows.append({"part": path.stem.replace("junit_", ""),
                         "module": module,
                         "test": case.get("name"),
                         "kind": "error" if case.find("error") is not None else "failure"})
        for module, count in groups.most_common():
            print(f"  {count:>3}  {module}")

    if options.csv:
        options.csv.parent.mkdir(parents=True, exist_ok=True)
        with options.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["part", "module", "test", "kind"])
            writer.writeheader()
            writer.writerows(rows)
        print("written:", options.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
