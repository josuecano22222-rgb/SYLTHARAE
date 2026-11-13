"""Print a JUnit XML report's failures as GitHub Actions annotations.

    python tools/ci/junit_annotations.py report.xml

Job logs are not always at hand (they live in blob storage); annotations are
shown on the pull request and returned by the checks API. One ::notice:: line
carries the totals and one more lists every skipped test with its reason, so
no skip is silent; one ::error:: line lists every failed test with the first
line of its message (GitHub shows only ten error annotations per step), then
one per failure with its message. Exit status is always 0: pytest's own status
decides the job.
"""
import sys
import xml.etree.ElementTree as ET  # nosec B405 - our own CI report, not untrusted input

LIMIT = 45


def _escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text):
    """Property values (title=...) also end at ':' and ','."""
    return _escape(text).replace(":", "%3A").replace(",", "%2C")


def main(path):
    root = ET.parse(path).getroot()  # nosec B314 - see the import
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    print("::notice title=pytest totals::" + ", ".join(f"{k}={v}" for k, v in totals.items()))
    skipped = []
    for c in root.iter("testcase"):
        node = c.find("skipped")
        if node is not None:
            reason = (node.get("message") or node.text or "").strip().splitlines()[:1]
            skipped.append(f"{c.get('classname', '')}::{c.get('name', '')}"
                           + (f"  --  {reason[0][:300]}" if reason else ""))
    if skipped:
        print(f"::notice title={_escape_property(f'{len(skipped)} skipped tests')}::"
              + _escape("\n".join(skipped))[:60000])
    failed = []
    for c in root.iter("testcase"):
        node = c.find("failure") if c.find("failure") is not None else c.find("error")
        if node is not None:
            first = (node.get("message") or "").strip().splitlines()[:1]
            failed.append(f"{c.get('classname', '')}::{c.get('name', '')}"
                          + (f"  --  {first[0][:300]}" if first else ""))
    if failed:
        # GitHub shows only ten error annotations per step: list them all once.
        print(f"::error title={_escape_property(f'{len(failed)} failed tests')}::" + _escape("\n".join(failed))[:60000])
    shown = 0
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            shown += 1
            if shown > LIMIT:
                continue
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            # Job logs are not always reachable, so a failure's own diagnosis
            # (e.g. tests/unit/test_ocr_matrix.py::_diagnosis) must fit here.
            detail = (node.get("message") or node.text or "")[:4000]
            print(f"::error title={_escape_property(kind + ' ' + name)[:250]}::{_escape(detail)}")
    if shown > LIMIT:
        print(f"::error title=more failures::{shown - LIMIT} more not shown")


if __name__ == "__main__":
    main(sys.argv[1])
