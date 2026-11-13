#!/usr/bin/env python3
"""Build the locked environment, and refuse to hand back a broken one.

    python3 tools/audit/rebuild_lock_env.py --venv /tmp/rv

`requirements.lock` is a complete freeze (every transitive package is in it), so
it is installed with ``--no-deps``. That is not an optimisation, it is the fix
for a real defect: resolving dependencies again lets ``rapidocr-onnxruntime``
pull in the **GUI** ``opencv-python``, which unpacks into the same ``cv2/``
directory as ``opencv-python-headless`` and needs ``libGL.so.1``. Whichever
wheel is written last owns the directory, so the environment looked correct and
``import cv2`` raised ``ImportError`` - the cause of 31 failing ids and 84 skips
in the previous round.

Three things are checked before this tool exits 0:

* the installed set hashes to exactly the lock (`env_lock.py --check`);
* ``import cv2`` works, in this interpreter, without a system library;
* no distribution that owns ``cv2/`` other than the headless one is installed.

A failure here is loud on purpose: an environment whose OpenCV cannot load can
still run most of the suite, so nothing else would notice.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = ROOT / "requirements.lock"

#: Distributions that write into ``cv2/``. Exactly one may be installed.
CV2_OWNERS = ("opencv-python", "opencv-python-headless", "opencv-contrib-python",
              "opencv-contrib-python-headless")


def _run(command, label):
    print(f"+ {' '.join(str(part) for part in command)}", flush=True)
    completed = subprocess.run([str(part) for part in command], text=True)
    if completed.returncode != 0:
        raise SystemExit(f"{label} failed (exit {completed.returncode})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--venv", default="/tmp/rv", help="where to build")
    parser.add_argument("--python", default="python3.11",
                        help="interpreter used to create the venv")
    parser.add_argument("--reuse", action="store_true",
                        help="install into an existing venv instead of creating one")
    args = parser.parse_args(argv)

    venv = pathlib.Path(args.venv)
    python = venv / "bin" / "python"
    if not python.exists():
        _run([args.python, "-m", "venv", venv], "creating the venv")
    elif not args.reuse:
        print(f"{venv} already exists; pass --reuse to install into it")

    _run([python, "-m", "pip", "install", "--quiet", "--no-deps", "-r", LOCK],
         "installing the lock")

    installed = subprocess.run(
        [python, "-c", "import importlib.metadata as m\n"
                       "print('\\n'.join(sorted(d.metadata['Name'] for d in m.distributions())))"],
        capture_output=True, text=True)
    owners = sorted({line.strip() for line in installed.stdout.splitlines()
                     if line.strip().lower().replace('_', '-') in CV2_OWNERS})
    if len(owners) != 1 or owners[0].lower() != "opencv-python-headless":
        raise SystemExit(f"REFUSING: cv2/ is owned by {owners or 'nothing'}; "
                         f"exactly one distribution may provide it, and it must be "
                         f"opencv-python-headless")

    probe = subprocess.run([python, "-c", "import cv2; print(cv2.__version__)"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        raise SystemExit("REFUSING: cv2 cannot be imported in the environment just "
                         "built:\n" + (probe.stderr or probe.stdout).strip()[-2000:])
    print(f"cv2 {probe.stdout.strip()} imports; cv2/ owned by {owners[0]}")

    check = subprocess.run([python, str(ROOT / "tools" / "audit" / "env_lock.py"),
                            "--check"], text=True)
    if check.returncode != 0:
        raise SystemExit("REFUSING: the installed set does not match the lock "
                         "(see the report above)")
    print(f"\n{venv} is the locked environment, and its OpenCV works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
