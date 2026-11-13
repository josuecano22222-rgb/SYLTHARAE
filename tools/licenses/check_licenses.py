"""Check that every Python dependency may be combined with AGPL-3.0-or-later.

SYLTHARAE is licensed under the GNU AGPL, version 3 or later (see LICENSE and
docs/LICENSING.md). This resolves the installed dependency closure of
``requirements.txt`` (and, with ``--extras``, the optional extras declared in
``pyproject.toml``), reads each distribution's licence from its metadata -
``License-Expression``, then the ``License`` field, then ``License ::``
classifiers - and classifies it:

* **compatible**   every licence in the expression (or one alternative of an
                   ``OR``) is on the AGPL-3.0-compatible list below;
* **incompatible** e.g. ``GPL-2.0-only`` or a non-commercial licence - fails;
* **unknown**      the metadata says nothing usable and the package is not in
                   ``REVIEWED`` - fails, because an unreviewed licence is not
                   a checked one.

Packages whose metadata is missing or ambiguous are resolved in ``REVIEWED``
with the upstream licence and where it was read, so a reviewer can re-check.

Usage:
    python tools/licenses/check_licenses.py              # requirements.txt
    python tools/licenses/check_licenses.py --extras     # + pyproject extras
    python tools/licenses/check_licenses.py --require-installed

Exit status: 0 when everything installed is compatible, 1 otherwise.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = None

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
PROJECT_LICENSE = "AGPL-3.0-or-later"

#: SPDX identifiers that may be combined with an AGPL-3.0-or-later work.
#: GPL-3.0 code may be combined with AGPL-3.0 code (section 13 of both);
#: "-or-later" GPL/LGPL 2.x may be used under version 3; permissive and
#: weak-copyleft licences below are GPLv3-compatible per the FSF list.
COMPATIBLE: Set[str] = {
    "0BSD", "Apache-2.0", "Artistic-2.0", "BSD-2-Clause", "BSD-3-Clause",
    "BSL-1.0", "CC0-1.0", "HPND", "ISC", "MIT", "MIT-0", "MIT-CMU", "MPL-2.0",
    "PSF-2.0", "Python-2.0", "Unicode-3.0", "Unlicense", "Zlib", "ZPL-2.1",
    "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "LicenseRef-Public-Domain",
}

#: Known not to be combinable with AGPL-3.0: reported as incompatible.
INCOMPATIBLE: Set[str] = {
    "GPL-1.0-only", "GPL-2.0-only", "LGPL-2.0-only", "EPL-1.0", "CDDL-1.0",
    "CDDL-1.1", "SSPL-1.0", "BUSL-1.1", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
    "LicenseRef-Proprietary", "Apache-1.1", "4-Clause-BSD", "BSD-4-Clause",
}

#: Free-text ``License`` fields and classifier names seen in the wild.
TEXT_TO_SPDX: Dict[str, str] = {
    "mit": "MIT", "mit license": "MIT", "the mit license": "MIT",
    "bsd": "BSD-3-Clause", "bsd license": "BSD-3-Clause", "new bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause", "3-clause bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause", "bsd-2-clause": "BSD-2-Clause",
    "apache 2.0": "Apache-2.0", "apache-2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0", "apache license, version 2.0": "Apache-2.0",
    "isc": "ISC", "isc license (iscl)": "ISC",
    "psfl": "PSF-2.0", "psf-2.0": "PSF-2.0", "python software foundation license": "PSF-2.0",
    "mpl-2.0": "MPL-2.0", "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "lgpl-2.1-or-later": "LGPL-2.1-or-later", "lgpl-2.1+": "LGPL-2.1-or-later",
    "gnu lesser general public license v2 or later (lgplv2+)": "LGPL-2.1-or-later",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "gnu lesser general public license v3 or later (lgplv3+)": "LGPL-3.0-or-later",
    "gnu general public license v3 (gplv3)": "GPL-3.0-only",
    "gnu general public license v3 or later (gplv3+)": "GPL-3.0-or-later",
    "gnu general public license v2 (gplv2)": "GPL-2.0-only",
    "gnu general public license v2 or later (gplv2+)": "GPL-2.0-or-later",
    "gnu affero general public license v3": "AGPL-3.0-only",
    "gnu affero general public license v3 or later (agplv3+)": "AGPL-3.0-or-later",
    "zope public license": "ZPL-2.1", "zpl 2.1": "ZPL-2.1",
    "public domain": "LicenseRef-Public-Domain",
    "proprietary": "LicenseRef-Proprietary", "other/proprietary license": "LicenseRef-Proprietary",
}

#: Distributions whose metadata is missing or ambiguous, resolved by reading
#: the upstream licence. (licence expression, where it was read)
REVIEWED: Dict[str, Tuple[str, str]] = {
    "extract-msg": ("GPL-3.0-only",
                    "metadata says only 'GPL'; LICENSE.txt in the sdist and "
                    "github.com/TeamMsgExtractor/msg-extractor are GPLv3"),
    "docx2txt": ("MIT", "no licence metadata; github.com/ankushshah89/python-docx2txt LICENSE.txt is MIT"),
    "odfpy": ("GPL-2.0-or-later OR Apache-2.0",
              "classifiers list GPL, LGPL and Apache; upstream README "
              "'Redistribution license' and setup.py say GPL-2.0-or-later OR Apache-2.0"),
    "psycopg2-binary": ("LGPL-3.0-or-later",
                        "'LGPL with exceptions'; upstream LICENSE is LGPL-3.0-or-later "
                        "with an OpenSSL linking exception"),
    "pymupdf": ("AGPL-3.0-only",
                "'Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License'; "
                "used under the AGPL"),
    "ebooklib": ("AGPL-3.0-or-later", "classifier 'AGPLv3+'; upstream LICENSE.txt"),
    "pgserver": ("Apache-2.0", "no licence metadata; github.com/orm011/pgserver LICENSE is Apache-2.0 "
                 "(test-only: disposable PostgreSQL for the test suite)"),
    "python-dateutil": ("Apache-2.0 OR BSD-3-Clause", "'Dual License'; classifiers BSD and Apache"),
    "pycryptodomex": ("BSD-2-Clause AND LicenseRef-Public-Domain", "'BSD, Public Domain'; upstream LICENSE.rst"),
    "rollbar": ("MIT", "License field is a copyright line; classifier and upstream LICENSE are MIT"),
    "flask-wtf": ("BSD-3-Clause", "License field is a copyright line; classifier and upstream LICENSE are BSD-3-Clause"),
    "wtforms": ("BSD-3-Clause", "License field is a copyright line; classifier and upstream LICENSE are BSD-3-Clause"),
    "tqdm": ("MPL-2.0 AND MIT", "License field 'MPL-2.0 AND MIT' (an expression in the free-text field)"),
    "numpy": ("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", "License-Expression"),
}

#: Same project, a different wheel: opencv-python-headless is the documented
#: substitute on servers without libGL (docs/CONFIGURATION.md).
ALTERNATIVES: Dict[str, str] = {"opencv-python": "opencv-python-headless"}


@dataclass
class Finding:
    name: str
    version: str
    expression: str
    source: str
    verdict: str        # compatible | incompatible | unknown | not-installed
    detail: str = ""


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(path: Path) -> List[str]:
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(Requirement(line).name)
    return names


def extra_names(pyproject: Path) -> List[str]:
    if tomllib is None:  # pragma: no cover
        return []
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    names = []
    for requirements in data.get("project", {}).get("optional-dependencies", {}).values():
        names.extend(Requirement(r).name for r in requirements)
    return names


def closure(roots: Iterable[str]) -> Dict[str, Optional[metadata.Distribution]]:
    """Every distribution reachable from ``roots`` (None when not installed)."""
    found: Dict[str, Optional[metadata.Distribution]] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        key = canonical(name)
        if key in found:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            alternative = ALTERNATIVES.get(key)
            if alternative:
                stack.append(alternative)
            found[key] = None
            continue
        found[key] = dist
        for requirement in dist.requires or []:
            req = Requirement(requirement)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            stack.append(req.name)
    # A missing package whose alternative is installed is not missing.
    for key, alternative in ALTERNATIVES.items():
        if found.get(key, 1) is None and found.get(canonical(alternative)) is not None:
            del found[key]
    return found


def declared_licence(dist: metadata.Distribution) -> Tuple[str, str]:
    """(SPDX expression, where it came from) - '' when nothing usable."""
    key = canonical(dist.metadata["Name"])
    if key in REVIEWED:
        return REVIEWED[key][0], "reviewed"
    expression = (dist.metadata.get("License-Expression") or "").strip()
    if expression:
        return expression, "License-Expression"
    text = (dist.metadata.get("License") or "").strip().splitlines()
    first = text[0].strip() if text else ""
    if first:
        spdx = TEXT_TO_SPDX.get(first.lower())
        if spdx:
            return spdx, "License"
        if re.fullmatch(r"[A-Za-z0-9.+-]+(?:\s+(?:AND|OR)\s+[A-Za-z0-9.+-]+)*", first) and \
                all(part in COMPATIBLE | INCOMPATIBLE for part in re.split(r"\s+(?:AND|OR)\s+", first)):
            return first, "License"
    classifiers = [c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or []
                   if c.startswith("License ::") and c.strip() != "License :: OSI Approved"]
    mapped = sorted({TEXT_TO_SPDX[c.lower()] for c in classifiers if c.lower() in TEXT_TO_SPDX})
    if mapped and len(mapped) == len(classifiers):
        # Several licence classifiers mean a choice (dual licensing).
        return " OR ".join(mapped), "Classifier"
    return "", f"unrecognised: License={first!r} classifiers={classifiers}"


def verdict(expression: str) -> Tuple[str, str]:
    """Classify an SPDX expression (AND binds tighter than OR; no parentheses)."""
    if not expression:
        return "unknown", "no usable licence metadata"
    alternatives = [alt.strip() for alt in re.split(r"\s+OR\s+", expression)]
    reasons = []
    for alternative in alternatives:
        ids = [part.strip().strip("()") for part in re.split(r"\s+AND\s+", alternative)]
        bad = [i for i in ids if i in INCOMPATIBLE]
        unknown = [i for i in ids if i not in COMPATIBLE and i not in INCOMPATIBLE]
        if not bad and not unknown:
            return "compatible", alternative
        reasons.append(f"{alternative}: " + ", ".join(
            [f"{i} is incompatible" for i in bad] + [f"{i} is not on the list" for i in unknown]))
    if any("incompatible" in r for r in reasons):
        return "incompatible", "; ".join(reasons)
    return "unknown", "; ".join(reasons)


def check(include_extras: bool = False) -> List[Finding]:
    roots = requirement_names(ROOT / "requirements.txt")
    if include_extras:
        roots += extra_names(ROOT / "pyproject.toml")
    findings = []
    for key, dist in sorted(closure(roots).items()):
        if dist is None:
            findings.append(Finding(key, "", "", "", "not-installed"))
            continue
        expression, source = declared_licence(dist)
        result, detail = verdict(expression)
        findings.append(Finding(canonical(dist.metadata["Name"]), dist.version,
                                expression, source, result, detail if result != "compatible" else ""))
    return findings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--extras", action="store_true", help="include pyproject optional extras")
    parser.add_argument("--require-installed", action="store_true",
                        help="fail when a dependency is not installed (so it was not checked)")
    parser.add_argument("--quiet", action="store_true", help="print problems and the summary only")
    args = parser.parse_args(argv)

    findings = check(args.extras)
    failing = {"incompatible", "unknown"} | ({"not-installed"} if args.require_installed else set())
    for f in findings:
        if args.quiet and f.verdict not in failing:
            continue
        mark = "FAIL" if f.verdict in failing else ("SKIP" if f.verdict == "not-installed" else "ok  ")
        print(f"{mark} {f.name:28} {f.version:12} {f.expression or '-':45} [{f.source or f.verdict}]"
              + (f"  {f.detail}" if f.detail else ""))
    counts = {v: sum(1 for f in findings if f.verdict == v)
              for v in ("compatible", "incompatible", "unknown", "not-installed")}
    print(f"\n{PROJECT_LICENSE}: {counts['compatible']} compatible, {counts['incompatible']} "
          f"incompatible, {counts['unknown']} unknown, {counts['not-installed']} not installed")
    return 1 if any(f.verdict in failing for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
