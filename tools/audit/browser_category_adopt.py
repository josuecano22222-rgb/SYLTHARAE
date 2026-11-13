#!/usr/bin/env python
"""Item 2: drive the parent-above / adopt create path from the list page.

The dialog offers three ways to create a category; the third - "create a parent
above and move these under it" - is the one that touches the hierarchy
endpoint and is the one this check exercises, end to end, in a browser:

  mode radio -> adopt list populated from the server -> two adopted ->
  POST /api/categories/tree/parent-above -> the rows read back with the new
  parent id -> GET /api/categories/tree shows the children under it.

Result: ``audit_report/r14/category_adopt.json`` (+ screenshot).

Usage::

    python tools/audit/browser_category_adopt.py \
        --base http://127.0.0.1:5000 --user admin --password '...'
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_report" / "r14"
NODE_DIR = pathlib.Path("/tmp/br")
SCRIPT = pathlib.Path(__file__).with_name("category_adopt.mjs")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True)
    ap.add_argument("--stamp", default=f"R14-{random.randint(100000, 999999)}")
    args = ap.parse_args()

    NODE_DIR.mkdir(parents=True, exist_ok=True)
    target = NODE_DIR / "category_adopt.mjs"
    target.write_text(SCRIPT.read_text())
    shot = OUT / "screenshots" / f"{args.stamp}-adopt.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"base": args.base, "user": args.user, "password": args.password,
           "stamp": args.stamp, "shot": str(shot)}
    env = dict(os.environ)
    env["ADOPT_CONFIG"] = json.dumps(cfg)
    env["LD_LIBRARY_PATH"] = "/tmp/stubgen:" + env.get("LD_LIBRARY_PATH", "")
    proc = subprocess.run(["node", str(target)], cwd=str(NODE_DIR), env=env,
                          capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        print(proc.stdout[-2500:], proc.stderr[-2500:], file=sys.stderr)
        return 2
    data = json.loads(proc.stdout)
    (OUT / "category_adopt.json").write_text(json.dumps(data, indent=2))
    for c in data["checks"]:
        print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['label']}: {str(c['detail'])[:160]}")
    print(f"checks: {len(data['checks'])}  failed: {data['failed']}")
    return 0 if data["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
