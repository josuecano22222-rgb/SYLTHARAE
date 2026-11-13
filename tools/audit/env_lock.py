#!/usr/bin/env python3
"""Fingerprint the installed environment, and refuse to classify a different one.

`requirements.txt` pins nothing, so two rebuilds of "the same" environment are
not the same environment - and "environment" has been absorbing differences this
audit could not otherwise explain. This tool makes the difference visible:

    python3 tools/audit/env_lock.py --check
    python3 tools/audit/env_lock.py --check --label "engine venv (+rapidocr)"

The set hash is taken over the sorted ``name==version`` lines of the installed
distributions, normalised the way ``pip freeze`` prints them, so it can be
recomputed anywhere. It deliberately ignores this repository - the point is to
say whether *the packages* match, not whether the tree does.

What a package hash cannot capture is recorded beside it, because each of these
has already changed a result here:

* native libraries an installed package needs at import time (OpenCV wants
  ``libGL.so.1``, which is absent in this sandbox - so ``import cv2`` fails with
  ``opencv-python 5.0.0.93`` installed and importable-looking);
* binaries not owned by pip (``tesseract``, ``ffmpeg``);
* the PostgreSQL build ``pgserver`` unpacks, which differs per platform.

``--fingerprint`` prints one line a report can quote; ``--check`` exits non-zero
when the installed set differs from ``requirements.lock`` unless a label says the
run is deliberately somewhere else.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = ROOT / "requirements.lock"

#: The packages whose *working* state the recorded runs depended on. A version
#: alone does not decide this - OpenCV is installed and still cannot import.
ENGINE_PROBES = (
    ("cv2", "opencv (needs libGL.so.1)"),
    ("rapidocr_onnxruntime", "rapidocr (OCR fallback)"),
    ("pytesseract", "pytesseract (needs the tesseract binary)"),
    ("fitz", "pymupdf (pdf text)"),
    ("docx", "python-docx (embedded images)"),
    ("pptx", "python-pptx (embedded images)"),
    ("openpyxl", "openpyxl (xlsx)"),
    ("mutagen", "mutagen (audio tags)"),
)


def installed_lines() -> list:
    """The sorted ``name==version`` lines, as ``pip freeze`` prints them."""
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                            capture_output=True, text=True)
    if freeze.returncode == 0 and freeze.stdout.strip():
        lines = [line.strip() for line in freeze.stdout.splitlines()]
        return sorted(line for line in lines
                      if line and not line.startswith(("#", "-e", "pip ", "setuptools ",
                                                       "wheel ")))
    # No pip (a bare interpreter): fall back to importlib metadata.
    from importlib import metadata
    return sorted(f"{dist.metadata['Name']}=={dist.version}"
                  for dist in metadata.distributions())


def lock_lines() -> list:
    if not LOCK.exists():
        return []
    return sorted(line.strip() for line in LOCK.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.startswith("#"))


def set_hash(lines) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


def engines() -> dict:
    """Which optional engines are *usable* here, not merely installed."""
    out = {}
    for module, label in ENGINE_PROBES:
        try:
            out[module] = "importable" if importlib.util.find_spec(module) else "absent"
        except (ImportError, ValueError):
            out[module] = "absent"
    # find_spec says nothing about a native library, which is exactly the trap.
    probe = subprocess.run(
        [sys.executable, "-c",
         "import importlib,sys\n"
         "for m in ('cv2','rapidocr_onnxruntime','fitz','docx','pptx','openpyxl',"
         "'mutagen','pytesseract'):\n"
         "    try:\n"
         "        importlib.import_module(m); print(m, 'ok')\n"
         "    except Exception as exc:\n"
         "        print(m, 'fail', type(exc).__name__, str(exc).splitlines()[0][:80])\n"],
        capture_output=True, text=True)
    for line in probe.stdout.splitlines():
        parts = line.split(" ", 3)
        if len(parts) >= 2 and parts[0] in out:
            out[parts[0]] = parts[1] if parts[1] == "ok" else f"fail: {parts[3] if len(parts) > 3 else ''}"
    out["tesseract"] = "present" if shutil.which("tesseract") else "absent"
    out["ffmpeg"] = "present" if shutil.which("ffmpeg") else "absent"
    return out


#: Distributions that write into ``cv2/``; more than one is the known trap.
CV2_OWNERS = ("opencv-python", "opencv-python-headless", "opencv-contrib-python",
              "opencv-contrib-python-headless")


def opencv_state() -> dict:
    """Whether ``cv2`` actually imports here, and which distributions own it.

    ``find_spec('cv2')`` says a directory exists; it says nothing about the
    compiled extension, which is what fails when the GUI wheel's files are the
    ones on disk and ``libGL.so.1`` is absent.
    """
    probe = subprocess.run(
        [sys.executable, "-c",
         "import cv2, sys\n"
         "print(cv2.__version__)\n"
         "print(cv2.__file__)\n"],
        capture_output=True, text=True)
    from importlib import metadata

    owners = []
    for dist_name in CV2_OWNERS:
        try:
            metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            continue
        owners.append(dist_name)
    state = {"usable": probe.returncode == 0, "owners": sorted(owners)}
    if probe.returncode == 0:
        version, _, location = probe.stdout.partition("\n")
        state["version"] = version.strip()
        state["file"] = location.strip()
    else:
        state["error"] = (probe.stderr or probe.stdout).strip().splitlines()[-1:] or [""]
        state["error"] = state["error"][0]
    return state


def report(label=None) -> dict:
    installed, locked = installed_lines(), lock_lines()
    actual = set_hash(installed)
    expected = set_hash(locked) if locked else None
    missing = sorted(set(locked) - set(installed))
    extra = sorted(set(installed) - set(locked))
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
        "executable": sys.executable,
        "packages": len(installed),
        "set_hash": actual,
        "lock_hash": expected,
        "lock_present": bool(locked),
        "match": bool(locked) and actual == expected,
        "label": label,
        "engines": engines(),
        "missing_from_this_env": missing,
        "not_in_the_lock": extra,
        "opencv": opencv_state(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when the installed set differs from the lock")
    parser.add_argument("--fingerprint", action="store_true",
                        help="print only the set hash, for a report line")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--label", default=None,
                        help="declare this run a deliberately different environment "
                             "(an isolating run). Without it, a mismatch is a refusal")
    parser.add_argument("--write", action="store_true",
                        help="write requirements.lock from this interpreter")
    args = parser.parse_args(argv)

    if args.write:
        lines = installed_lines()
        header = ("# Written by tools/audit/env_lock.py --write from\n"
                  f"# python {sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}; set hash {set_hash(lines)}\n")
        LOCK.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {LOCK.relative_to(ROOT)} ({len(lines)} packages)")
        return 0

    info = report(args.label)
    if args.fingerprint:
        print(info["set_hash"])
        return 0
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        print(f"python      {info['python']}  ({info['executable']})")
        print(f"packages    {info['packages']}")
        print(f"set hash    {info['set_hash']}")
        print(f"lock hash   {info['lock_hash'] or '(no requirements.lock)'}")
        print(f"match       {'yes' if info['match'] else 'NO'}")
        for name, state in info["engines"].items():
            print(f"  engine {name:22} {state}")
        opencv = info["opencv"]
        print(f"  opencv {'usable ' + opencv.get('version', '') if opencv['usable'] else 'CANNOT LOAD'}"
              f" (owners: {', '.join(opencv['owners']) or 'none'})")
        if not opencv["usable"] and opencv.get("file"):
            print(f"         imported from {opencv['file']}")
        if info["missing_from_this_env"]:
            print("missing from this environment (in the lock):")
            for line in info["missing_from_this_env"]:
                print("   -", line)
        if info["not_in_the_lock"]:
            print("not in the lock (this environment has extra):")
            for line in info["not_in_the_lock"]:
                print("   +", line)

    locked_opencv = [line for line in lock_lines()
                     if line.split("==")[0].lower() in CV2_OWNERS]
    if args.check and locked_opencv and not info["opencv"]["usable"]:
        print(f"\nREFUSING: the lock carries {', '.join(locked_opencv)} but `import cv2` "
              f"fails in this interpreter ({info['opencv'].get('error', 'unknown error')}). "
              f"Two distributions can own cv2/ and whichever was installed last wins; "
              f"rebuild with `python3 tools/audit/rebuild_lock_env.py --venv <path>`.",
              file=sys.stderr)
        return 3

    if args.check and not info["match"]:
        if info["label"]:
            print(f"\nREFUSAL OVERRIDDEN by label: {info['label']!r}. The numbers "
                  f"from this run are only comparable with runs that carry the "
                  f"same label.", file=sys.stderr)
            return 0
        print("\nREFUSING: this interpreter's package set is not the locked one. "
              "A result measured here cannot be compared with the recorded runs. "
              "Install requirements.lock, or pass --label to record that the run "
              "is somewhere else on purpose.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
