#!/usr/bin/env python3
"""Run the integration suite in several module orders and compare the results.

A suite that passes in one order and fails in another has an isolation defect,
and the defect is usually invisible in the order the developer happens to get:
directory order is alphabetical, which is stable, arbitrary and rarely the order
a change happens to break. This tool runs the same tests in:

* **forward** - the order pytest collects in;
* **reverse** - the collection order reversed;
* **seeded** - N shuffles of the module list, each with a recorded seed, so any
  failure can be reproduced exactly.

It writes one JUnit file per order plus a summary JSON, and exits non-zero when
the failing-id sets differ between orders - a difference is the defect, not a
flake to be averaged away.

Usage::

    python3 tools/audit/order_matrix.py --python /tmp/rv/bin/python \\
        --seeds 1 2 3 --out /tmp/an/orders
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]


def modules(path: pathlib.Path) -> list:
    """Every ``test_*.py`` in ``path``, sorted by path (pytest's own order)."""
    return sorted(str(p.relative_to(ROOT)) for p in path.glob("test_*.py"))


def counts_and_failures(junit: pathlib.Path) -> dict:
    root = ET.parse(junit).getroot()
    suites = root.findall(".//testsuite") or [root]
    total = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    failing = sorted(
        f"{case.get('classname')}::{case.get('name')}"
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None)
    return {"tests": total, "passed": total - failures - errors - skipped,
            "failed": failures, "errors": errors, "skipped": skipped,
            "failing_ids": failing}


def run_order(python: str, order: list, junit: pathlib.Path, log: pathlib.Path) -> dict:
    junit.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            [python, "-m", "pytest", *order, "-q", "-p", "no:cacheprovider",
             f"--junitxml={junit}"],
            cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    result = counts_and_failures(junit)
    result["exit"] = completed.returncode
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--suite", default="tests/integration")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("/tmp/orders"))
    args = parser.parse_args(argv)

    collected = modules(ROOT / args.suite)
    orders = [("forward", collected), ("reverse", list(reversed(collected)))]
    for seed in args.seeds:
        shuffled = list(collected)
        random.Random(seed).shuffle(shuffled)
        orders.append((f"seed-{seed}", shuffled))

    summary = {"suite": args.suite, "modules": len(collected), "orders": {}}
    for name, order in orders:
        print(f"running {name} ({len(order)} modules) ...", flush=True)
        result = run_order(args.python, order,
                           args.out / f"{name}.xml", args.out / f"{name}.log")
        result["order_head"] = order[:3]
        summary["orders"][name] = result
        print(f"  {name}: tests={result['tests']} passed={result['passed']} "
              f"failed={result['failed']} errors={result['errors']} "
              f"skipped={result['skipped']} failing_ids={len(result['failing_ids'])}",
              flush=True)

    sets = {name: set(result["failing_ids"]) for name, result in summary["orders"].items()}
    reference_name = orders[0][0]
    reference = sets[reference_name]
    disagreements = {
        name: {"only_here": sorted(ids - reference),
               "missing_here": sorted(reference - ids)}
        for name, ids in sets.items() if ids != reference}
    summary["reference"] = reference_name
    summary["failing_ids_agree"] = not disagreements
    summary["disagreements"] = disagreements

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                           encoding="utf-8")

    print("\norder      tests  passed  failed  errors  skipped  failing ids")
    for name, result in summary["orders"].items():
        print(f"{name:10} {result['tests']:6} {result['passed']:7} "
              f"{result['failed']:6} {result['errors']:7} {result['skipped']:7} "
              f"{len(result['failing_ids']):11}")

    if disagreements:
        print("\nORDERS DISAGREE - each id below is an isolation defect to fix "
              "or a limit to document:")
        for name, diff in disagreements.items():
            for test_id in diff["only_here"]:
                print(f"    only in {name}: {test_id}")
            for test_id in diff["missing_here"]:
                print(f"    missing from {name}: {test_id}")
        return 1
    if any(result["exit"] not in (0, 1) for result in summary["orders"].values()):
        print("\nan order did not finish (exit code other than 0/1): see the logs")
        return 2
    print("\nall orders agree on the failing-id set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
