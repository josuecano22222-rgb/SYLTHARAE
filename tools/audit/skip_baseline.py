#!/usr/bin/env python3
"""Count the skips in a run and refuse an unexplained increase.

A skip is not a pass. The round removed 109 of the 113 integration skips by
fixing what produced them; the four that remain are opt-in flags and unbuilt
features, and they must stay four. This tool is the enforcement:

* it reads the JUnit XML of a finished run and counts the skipped ids;
* it compares that count with a recorded baseline (one file, checked in);
* a skip count **above** the baseline fails the run, unless the module of each
  newly skipped id is named in the round's edit table - an increase has to be
  written down and justified, not discovered later;
* a count below the baseline is not a failure, but it must be re-recorded so
  the improvement is pinned and cannot silently come back.

Reasons are classified from the skip message, so the report answers "how many
skips, and of what kind" rather than "some tests did not run".

Usage::

    python3 tools/audit/skip_baseline.py --current /tmp/an/junit_head.xml \\
        --baseline audit_report/r22_workbook/skip_baseline.json

    python3 tools/audit/skip_baseline.py --current /tmp/an/junit_head.xml \\
        --baseline audit_report/r22_workbook/skip_baseline.json --record
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BASELINE = ROOT / "audit_report" / "r22_workbook" / "skip_baseline.json"
DEFAULT_EXPLANATIONS = ROOT / "audit_report" / "r22_workbook" / "TEST_EDITS.md"

#: ``(category, first-match pattern)``. The order matters: the most specific
#: message wins, and an unrecognised message lands in ``OTHER`` - a category
#: that is reported explicitly rather than folded into a friendly one.
CATEGORIES = (
    ("KNOWN-GAP", r"known gap"),
    ("DATABASE", r"PostgreSQL not reachable"),
    ("ENGINE-OCR", r"no OCR engine installed"),
    ("OPT-IN-FLAG", r"set [A-Z0-9_]+=\d+ to "),
    ("EXTERNAL-BINARY", r"nginx or openssl not installed|no ffmpeg binary"),
    ("NOT-IMPLEMENTED", r"Requires full ingestion pipeline"),
    ("FIXTURE", r"fixture unavailable|corpus file missing|sample"),
)


def categorise(reason: str) -> str:
    """The kind of skip a message describes."""
    for category, pattern in CATEGORIES:
        if re.search(pattern, reason or ""):
            return category
    return "OTHER"


def module_of(classname: str, name: str = "") -> str:
    """``tests.integration.test_x.TestY`` -> ``tests/integration/test_x.py``.

    Same rule the baseline tool uses: pytest keeps the class in ``classname``
    and the function in ``name``, and a rule table (or this project's edit
    table) is keyed by file.
    """
    parts = [p for p in (classname or "").split(".") if p]
    keep = []
    for part in parts:
        if keep and not part.startswith("test_") and part[:1].isupper():
            break
        keep.append(part)
    return "/".join(keep) + ".py"


def skips(xml_path: pathlib.Path) -> dict:
    """``{id: reason}`` for every skipped testcase in a JUnit file."""
    root = ET.parse(xml_path).getroot()
    out = {}
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        out[f"{classname}::{name}"] = skipped.get("message") or skipped.get("type") or ""
    return out


def summarise(found: dict) -> dict:
    """``{category: count}`` plus the ids behind each category."""
    by_category: dict[str, list] = {}
    for test_id, reason in sorted(found.items()):
        by_category.setdefault(categorise(reason), []).append(test_id)
    return {category: ids for category, ids in sorted(by_category.items())}


def load_baseline(path: pathlib.Path) -> dict:
    """``{id: reason}`` from the recorded file, which also carries counts."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("ids", payload)


def _provenance(path: pathlib.Path, found: dict) -> dict:
    """Enough about a JUnit file to find it again: name, digest, skip count."""
    import hashlib

    data = path.read_bytes() if path.exists() else b""
    return {"file": path.name, "sha256": hashlib.sha256(data).hexdigest()[:16],
            "skips": len(found)}


def explained_modules(text: str) -> set:
    """Module paths the edit table names, e.g. ``tests/unit/test_x.py``."""
    return set(re.findall(r"tests/[A-Za-z0-9_/]+\.py", text or ""))


def compare(found: dict, baseline: dict, explanations: str) -> dict:
    """What changed against the baseline, and whether the change is excused.

    ``baseline`` is ``{id: reason}``. A newly skipped id is excused when its
    module is named in the explanation text - the increase was written down in
    the same change set that caused it.
    """
    new = {test_id: reason for test_id, reason in found.items()
           if test_id not in baseline}
    gone = sorted(set(baseline) - set(found))
    allowed_modules = explained_modules(explanations)
    excused, unexcused = {}, {}
    for test_id, reason in new.items():
        module = module_of(test_id.partition("::")[0])
        (excused if module in allowed_modules else unexcused)[test_id] = reason
    return {
        "current": len(found),
        "baseline": len(baseline),
        "new": new,
        "gone": gone,
        "excused": excused,
        "unexcused": unexcused,
    }


def _print_table(title: str, by_category: dict) -> None:
    print(f"\n{title}")
    print(f"{'category':18} {'count':>5}")
    for category, ids in by_category.items():
        print(f"{category:18} {len(ids):5}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--current", type=pathlib.Path, action="append", required=True,
                        help="JUnit XML of the finished run; repeat for several files")
    parser.add_argument("--baseline", type=pathlib.Path, default=DEFAULT_BASELINE)
    parser.add_argument("--explanations", type=pathlib.Path, default=DEFAULT_EXPLANATIONS,
                        help="the edit table that has to justify an increase")
    parser.add_argument("--record", action="store_true",
                        help="pin the current count as the new baseline")
    args = parser.parse_args(argv)

    found = {}
    per_file = []
    for path in args.current:
        one = skips(path)
        per_file.append(one)
        found.update(one)
    baseline = load_baseline(args.baseline)
    explanations = (args.explanations.read_text(encoding="utf-8")
                    if args.explanations.exists() else "")

    by_category = summarise(found)
    _print_table(f"skips now ({len(found)})", by_category)
    if baseline:
        _print_table(f"skips recorded in {args.baseline.name} ({len(baseline)})",
                     summarise(baseline))

    delta = compare(found, baseline, explanations)
    status = 0
    if not baseline:
        print("\nno baseline recorded: run with --record once the count is agreed")
        status = 1
    if delta["unexcused"]:
        print(f"\nSKIP COUNT GREW BY {len(delta['unexcused'])} UNEXPLAINED ID(S): "
              f"name the module in {args.explanations.name}, or fix the skip")
        for test_id, reason in sorted(delta["unexcused"].items()):
            print(f"    {module_of(test_id.partition('::')[0]):55} {test_id.partition('::')[2]}"
                  f"  ({categorise(reason)})")
        status = 1
    if delta["excused"]:
        print(f"\n{len(delta['excused'])} new skip(s), each explained in the edit table")
        for test_id in sorted(delta["excused"]):
            print(f"    {test_id}")
    if delta["gone"]:
        print(f"\n{len(delta['gone'])} recorded skip(s) no longer skip - re-record "
              f"with --record so the improvement is pinned")
        status = 1
    if delta["current"] > delta["baseline"] and not delta["new"]:
        # Same ids cannot outnumber the baseline without new ones; kept as a
        # guard in case a JUnit file repeats an id.
        print("\nskip count exceeds the baseline without a new id: refusing")
        status = 1

    if args.record:
        if delta["unexcused"]:
            # Only an *increase over a recorded baseline* has to be explained.
            # The first recording has nothing to grow from: the suite already
            # has skips before this tool exists, and refusing to pin them would
            # make the guard unusable on the day it is added - while refusing
            # nothing at all would let a later increase hide inside the first
            # number. So the first recording is taken as a whole and labelled
            # as one; every later run is compared against it.
            if args.baseline.exists():
                print("\nrefusing to record: the increase is not explained")
                return 1
            print(f"\nno baseline existed: recording all {len(found)} skip(s) as the "
                  f"first baseline, by category")
            for category, ids in by_category.items():
                print(f"    {category:18} {len(ids):4}")
            print("    every id is pinned in the file; a later increase is refused "
                  "unless the edit table names the module")
        previous = len(baseline)
        payload = {"count": len(found), "ids": dict(sorted(found.items())),
                   "by_category": {k: len(v) for k, v in by_category.items()},
                   "first_recording": not args.baseline.exists(),
                   "previous_count": previous,
                   # Which run produced this pin. A baseline whose provenance is
                   # "some run, some day" cannot be audited; these are the files
                   # it was measured from, with their own counts.
                   "recorded_from": [_provenance(path, found_) for path, found_ in
                                     zip(args.current, per_file)]}
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"\nrecorded {len(found)} skips in {args.baseline}")
        return 0

    if status == 0:
        if len(found) == len(baseline):
            print(f"\n{len(found)} skips: equal to the recorded baseline")
        else:
            # Every difference is excused, so the run passes - but saying
            # "equal" about two different numbers is how a report starts lying.
            print(f"\n{len(found)} skips: {len(delta['excused'])} new and named in "
                  f"{args.explanations.name}, {len(delta['gone'])} recorded and gone; "
                  f"baseline was {len(baseline)} - re-record to pin this state")
    return status


if __name__ == "__main__":
    sys.exit(main())
