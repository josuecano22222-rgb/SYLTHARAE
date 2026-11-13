#!/usr/bin/env python3
"""Command line over :mod:`services.importing.taxonomy_sync`.

    python -m tools.i18n.sync_taxonomy --check       # validate the file only
    python -m tools.i18n.sync_taxonomy --dry-run     # show what would change
    python -m tools.i18n.sync_taxonomy --apply       # write the difference
    python -m tools.i18n.sync_taxonomy --watch       # apply on every save

``--rename OLD=NEW`` states a rename the content cannot prove; without it a
renamed worksheet is reported as one category missing and one new, because from
one snapshot those two look identical. ``--auto-rename`` lets content decide:
a stored category and a worksheet holding exactly the same terms and keywords,
paired uniquely both ways, are the same category renamed.

Exit status: 0 when the file is valid and the run succeeded, 1 on a rule
violation (``--check``), 2 when the database refused the change.

The implementation lives in the service so that the command, the
``taxonomy_sync`` job, the API and the scheduler all compute the same plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Re-exported so callers and tests that used this module as the implementation
# keep working: the names are the service's, not a copy of them.
from apps.importing.validators import workbook_rules as rules  # noqa: E402
from services.importing.taxonomy_sync import (  # noqa: F401,E402
    DEFAULT_XLSX, Plan, _decode_keyword, apply_plan, build_plan, connect,
    content_of, detect_renames, export_state, fingerprint, restore_state,
    snapshot, sync_workbook,
)

# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def report_workbook(workbook: rules.Workbook) -> None:
    info = workbook.as_dict()
    print(f"workbook : {workbook.path}")
    print(f"layout   : {workbook.layout}")
    print(f"sheets   : {info['categories']} categories, "
          f"{info['terms']} terms ({info['distinct_terms']} distinct), "
          f"{info['keywords']} keywords ({info['distinct_keywords']} distinct)")
    if workbook.errors:
        print(f"errors   : {len(workbook.errors)}")
        for violation in workbook.errors[:25]:
            print("   ", violation)
        if len(workbook.errors) > 25:
            print(f"    ... {len(workbook.errors) - 25} more")
    else:
        print("errors   : 0")
    if workbook.decisions:
        print(f"decisions: {len(workbook.decisions)} shared keyword(s) the schema "
              f"stores once - see {rules.OWNERS_SHEET}")
        for violation in workbook.decisions[:3]:
            print("   ", violation)
        if len(workbook.decisions) > 3:
            print(f"    ... {len(workbook.decisions) - 3} more (not printed)")


def _file_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_renames(values: Sequence[str]) -> Dict[str, str]:
    out = {}
    for value in values or ():
        if "=" not in value:
            raise SystemExit(f"--rename expects OLD=NEW, got {value!r}")
        old, new = value.split("=", 1)
        out[old.strip()] = new.strip()
    return out


def run(path: str, *, check_only: bool = False, apply: bool = False,
        prune_categories: bool = False, prune_words: bool = False,
        renames: Optional[Dict[str, str]] = None, quiet: bool = False,
        auto_renames: bool = False,
        report_path: Optional[str] = None) -> int:
    workbook = rules.read(path, renames=renames)
    if not quiet:
        report_workbook(workbook)
    if not workbook.ok:
        print("\nrefusing to touch the database: fix the errors above first",
              file=sys.stderr)
        return 1
    if check_only:
        return 0

    conn = connect()
    try:
        before = snapshot(conn)
        plan = build_plan(conn, workbook, prune_categories=prune_categories,
                          prune_words=prune_words, renames=renames,
                          auto_renames=auto_renames)
        result: Dict[str, Any] = {
            "path": path,
            "file_sha256": _file_hash(path),
            "workbook": workbook.as_dict(),
            "plan": plan.as_dict(),
            "before": before,
            "applied": False,
        }
        if not quiet:
            print("\nplan")
            print(f"  categories: {len(plan.create_categories)} to create, "
                  f"{len(plan.rename_categories)} to rename, "
                  f"{len(plan.missing_categories)} in the database only")
            for name in plan.create_categories[:10]:
                print(f"     + {name}")
            for old, new in plan.rename_categories[:10]:
                how = " (from content)" if plan.detected_renames else ""
                print(f"     ~ {old} -> {new}{how}")
            for name in plan.missing_categories[:10]:
                flag = "delete" if prune_categories else "kept (needs --prune-categories)"
                print(f"     - {name}  [{flag}]")
            print(f"  words     : {len(plan.create_words)} new")
            print(f"  terms     : {len(plan.link_terms)} to link, "
                  f"{len(plan.unlink_terms)} to unlink")
            print(f"  keywords  : {len(plan.create_keywords)} to create, "
                  f"{len(plan.delete_keywords)} to delete, "
                  f"{len(plan.keep_keywords)} unchanged")
            if plan.not_stored:
                print(f"  shared    : {len(plan.not_stored)} phrase memberships "
                      f"cannot be stored (one category per phrase) - reported, "
                      f"not dropped silently")
            if plan.orphan_words:
                print(f"  orphans   : {len(plan.orphan_words)} word(s) with no "
                      f"membership (kept unless --prune-words)")

        if apply and plan.changes:
            written = apply_plan(conn, workbook, plan, prune_words=prune_words)
            result["applied"] = True
            result["written"] = written
            if not quiet:
                print("\nwritten:", json.dumps(written))
        elif not apply and plan.changes and not quiet:
            print("\nno database change written (use --apply)")

        result["after"] = snapshot(conn)
        if report_path:
            Path(report_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
            if not quiet:
                print(f"report   : {report_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 - the CLI must explain, not traceback
        conn.rollback()
        print(f"\ndatabase refused the change, rolled back: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def watch(path: str, interval: float, **options) -> int:
    """Apply the workbook whenever the file changes on disk."""
    last = _file_hash(path)
    print(f"watching {path} every {interval:g}s (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(interval)
            current = _file_hash(path)
            if current == last:
                continue
            last = current
            print(f"\n[{time.strftime('%H:%M:%S')}] file changed, syncing")
            run(path, apply=True, **options)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile the database with the category workbook.")
    parser.add_argument("--xlsx", default=DEFAULT_XLSX)
    parser.add_argument("--check", action="store_true",
                        help="validate the workbook and exit (default action)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the difference, write nothing")
    parser.add_argument("--apply", action="store_true", help="write the difference")
    parser.add_argument("--watch", action="store_true",
                        help="apply whenever the file changes")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                        help="a worksheet was renamed; migrate the category")
    parser.add_argument("--auto-rename", action="store_true",
                        help="treat a vanished sheet and a new sheet with"
                             " identical content as one renamed category")
    parser.add_argument("--prune-categories", action="store_true",
                        help="delete categories whose worksheet is gone")
    parser.add_argument("--prune-words", action="store_true",
                        help="delete word rows nothing refers to")
    parser.add_argument("--report", default=None, help="write a JSON report here")
    parser.add_argument("--write-workbook", action="store_true",
                        help="rewrite the file in the two-column layout and exit")
    args = parser.parse_args(argv)

    if args.write_workbook:
        workbook = rules.read(args.xlsx, validate=False)
        counts = rules.write(args.xlsx, workbook)
        print(f"rewrote {args.xlsx}: {json.dumps(counts)}")
        return 0

    renames = _parse_renames(args.rename)
    if args.watch:
        return watch(args.xlsx, args.interval, prune_categories=args.prune_categories,
                     prune_words=args.prune_words, renames=renames,
                     auto_renames=args.auto_rename, report_path=args.report)
    if args.apply:
        return run(args.xlsx, apply=True, prune_categories=args.prune_categories,
                   prune_words=args.prune_words, renames=renames,
                   auto_renames=args.auto_rename, report_path=args.report)
    if args.dry_run:
        return run(args.xlsx, apply=False, prune_categories=args.prune_categories,
                   prune_words=args.prune_words, renames=renames,
                   auto_renames=args.auto_rename, report_path=args.report)
    return run(args.xlsx, check_only=True, renames=renames,
               auto_renames=args.auto_rename)


if __name__ == "__main__":
    sys.exit(main())
