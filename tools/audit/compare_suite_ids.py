#!/usr/bin/env python
"""Classify every test id by its outcome at two commits.

Inputs are two JUnit XML files produced from the same command at BASE and HEAD.
The output is one row per test id with the outcome at each commit and a class:

    pre-existing  fails/errors at both
    regression    fails/errors only at HEAD
    fixed         fails/errors only at BASE
    flaky         the id changed outcome between repeats of the same commit
    stable        the same non-failing outcome at both

    python tools/audit/compare_suite_ids.py BASE.xml HEAD.xml \
        --csv audit_report/r20/id_diff_unit.csv
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import xml.etree.ElementTree as ET


def outcomes(path: pathlib.Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    result = {}
    for case in suite.iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None:
            result[name] = "fail"
        elif case.find("error") is not None:
            result[name] = "error"
        elif case.find("skipped") is not None:
            result[name] = "skip"
        else:
            result[name] = "pass"
    return result


def classify(base: str | None, head: str | None) -> str:
    bad = {"fail", "error"}
    if base in bad and head in bad:
        return "pre-existing"
    if base in bad and head not in bad:
        return "fixed"
    if head in bad and base not in bad:
        return "regression"
    return "stable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=pathlib.Path)
    parser.add_argument("head", type=pathlib.Path)
    parser.add_argument("--csv", type=pathlib.Path, required=True)
    options = parser.parse_args()

    base = outcomes(options.base)
    head = outcomes(options.head)
    ids = sorted(set(base) | set(head))
    rows = [{"id": name, "base": base.get(name, "absent"),
             "head": head.get(name, "absent"),
             "class": classify(base.get(name), head.get(name))}
            for name in ids]
    options.csv.parent.mkdir(parents=True, exist_ok=True)
    with options.csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "base", "head", "class"])
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    print(f"{len(rows)} ids -> {options.csv}")
    for klass, number in sorted(counts.items()):
        print(f"  {klass:12} {number}")
    for row in rows:
        if row["class"] in ("regression", "fixed"):
            print(f"  {row['class']:10} {row['id']} ({row['base']} -> {row['head']})")
    return 1 if counts.get("regression") else 0


if __name__ == "__main__":
    raise SystemExit(main())
