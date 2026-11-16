#!/usr/bin/env python
"""Inspect the checkpoint a run would use, and what it contains.

Answering "why did my Retry start from the beginning?" used to require reading
the source. This prints everything that determines the answer: where the
checkpoint lives, whether it exists, how much work it holds, and whether it
would actually be used.

Usage:
    python scripts/checkpoint_inspect.py --path /data/corpus --source SRC --side SIDE
    python scripts/checkpoint_inspect.py --files a.txt b.txt --source SRC --side SIDE
    python scripts/checkpoint_inspect.py --list
    python scripts/checkpoint_inspect.py --file <checkpoint.json>

Exit status: 0 on success, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _checkpoint_for(args) -> str:
    """Same derivation the service uses, so this cannot disagree with it."""
    from services.ingesting.options import IngestionOptions, IngestionRequest
    from services.ingesting.service import IngestionService

    request = IngestionRequest(
        path=args.path,
        file_paths=list(args.files or []),
        source=args.source or "",
        side=args.side or "",
        options=IngestionOptions(checkpoint=args.policy),
    )
    return IngestionService._checkpoint_file(request)


def _identity_from_disk(checkpoint_file: str):
    """(folder, source, side) the checkpoint on disk was written for.

    Read from the snapshot when there is one, otherwise from the journal
    header. Getting this wrong is not cosmetic: the checkpoint is validated
    against this identity, so a probe that guesses wrong reports a healthy
    checkpoint as "rejected" - which is exactly the misdiagnosis this tool
    exists to prevent.
    """
    from core.checkpoint_manager import JOURNAL_HEADER

    path = Path(checkpoint_file)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return (data.get("folder_path") or str(Path.cwd()),
                    data.get("storage_source"), data.get("storage_side"))
        except (OSError, ValueError):
            pass

    journal = path.with_suffix(path.suffix + ".journal")
    if journal.exists():
        try:
            with open(journal, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith(JOURNAL_HEADER):
                        parts = line.strip().split("|")
                        if len(parts) >= 4:
                            return parts[1], parts[2] or None, parts[3] or None
                    break  # the header is only ever the first line
        except (OSError, ValueError):
            pass
    return str(Path.cwd()), None, None


def _describe(checkpoint_file: str) -> dict:
    from core.checkpoint_manager import CheckpointManager

    folder, source, side = _identity_from_disk(checkpoint_file)
    # The manager loads and reports; a throwaway instance is enough, and it
    # cannot write anything (nothing is marked).
    probe = CheckpointManager(
        checkpoint_file=checkpoint_file,
        folder_path=folder,
        storage_source=source,
        storage_side=side,
    )
    return probe.resume_report


def _print(report: dict, stream=sys.stdout) -> None:
    status = report.get("status")
    print(f"Checkpoint file   : {report.get('checkpoint_file')}", file=stream)
    print(f"Journal file      : {report.get('journal_file')}", file=stream)
    print(f"Folder            : {report.get('folder')}", file=stream)
    print(f"Source / side     : {report.get('source')} / {report.get('side')}", file=stream)
    print(
        f"Snapshot          : {'present' if report.get('snapshot_present') else 'absent'}"
        f" ({report.get('snapshot_entries')} entries)", file=stream,
    )
    print(
        f"Journal           : {'present' if report.get('journal_present') else 'absent'}"
        f" ({report.get('journal_entries')} entries)", file=stream,
    )
    print(f"Status            : {status}", file=stream)
    if report.get("reason"):
        print(f"Reason            : {report['reason']}", file=stream)
    print(f"Would skip        : {report.get('resumed_files')} file(s)", file=stream)
    print(file=stream)
    if status == "resumed" and report.get("resumed_files"):
        print("A retry would resume from here.", file=stream)
    elif status == "rejected":
        print(
            "A retry would START OVER: the checkpoint exists but would not be\n"
            "used. The reason above is the cause.", file=stream,
        )
    else:
        print("A retry would start fresh: there is nothing to resume.", file=stream)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", help="folder being ingested")
    parser.add_argument("--files", nargs="*", help="explicit file list (a batch)")
    parser.add_argument("--source", help="storage source name")
    parser.add_argument("--side", help="storage side name")
    parser.add_argument("--policy", default="auto", choices=("auto", "fresh", "off"),
                        help="checkpoint policy (default: auto)")
    parser.add_argument("--file", help="inspect an existing checkpoint file directly")
    parser.add_argument("--list", action="store_true",
                        help="list every checkpoint in the checkpoint directory")
    args = parser.parse_args(argv)

    try:
        from core.app_paths import get_checkpoints_dir

        ckpt_dir = get_checkpoints_dir()
    except Exception:
        ckpt_dir = Path("data/checkpoints")

    if args.list:
        print(f"Checkpoint directory: {ckpt_dir}\n")
        # Snapshots and journals both count as checkpoints: a run that died
        # before its first compaction has no snapshot at all, and that is
        # precisely the case an operator comes here to check.
        found = sorted(
            list(ckpt_dir.glob("checkpoint_*.json"))
            + [p for p in ckpt_dir.glob("checkpoint_*.journal")]
        )
        if not found:
            print("(no checkpoints)")
            return 0
        for entry in found:
            kind = "journal " if entry.name.endswith(".journal") else "snapshot"
            print(f"  {kind} {entry.name}  ({entry.stat().st_size} bytes)")
        return 0

    if args.file:
        _print(_describe(args.file))
        return 0

    if not args.path and not args.files:
        parser.error("give --path, --files, --file, or --list")
        return 2

    checkpoint_file = _checkpoint_for(args)
    if checkpoint_file is None:
        print("Checkpointing is OFF for this request "
              "(policy 'off', or no path/file_paths given).")
        print("Nothing is written, so a retry cannot resume.")
        return 0

    print(f"Policy            : {args.policy}")
    _print(_describe(checkpoint_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
