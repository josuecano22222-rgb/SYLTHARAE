#!/usr/bin/env python3
"""194-file-type extraction matrix for test-doc/ (structured JSONL report)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime

bootstrap_runtime()

from tools.perf.benchmark_common import hardware_profile, utc_now_iso, write_jsonl
from tools.perf.read_result_utils import (
    content_dict_from_read_result,
    read_result_success,
    text_metrics_from_read_result,
)


def _classify(duration_ms: float, success: bool, output_bytes: int) -> str:
    if not success:
        return "RED"
    if duration_ms > 5000 or output_bytes == 0:
        return "YELLOW"
    return "GREEN"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "test-doc")
    parser.add_argument("--limit", type=int, default=0, help="Max files (0 = all)")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "perf" / "runs" / "extraction_testdoc.jsonl")
    args = parser.parse_args()

    from reader_file.services.file_reader_service import FileReaderService

    service = FileReaderService()
    files = sorted(p for p in args.root.rglob("*") if p.is_file())
    if args.limit:
        files = files[: args.limit]

    records: List[Dict[str, Any]] = [
        {"type": "meta", "hardware": hardware_profile(), "file_count": len(files), "started_at": utc_now_iso()}
    ]
    for path in files:
        ext = path.suffix.lower().lstrip(".") or "(none)"
        started = time.perf_counter()
        success = False
        output_bytes = 0
        characters = 0
        extractor = None
        warnings: List[str] = []
        error = None
        try:
            result = service.read_file({
                "path": str(path),
                "extension": path.suffix.lstrip("."),
            })
            success = read_result_success(result)
            content = content_dict_from_read_result(result) if isinstance(result, dict) else {}
            characters, output_bytes = (
                text_metrics_from_read_result(result)
                if isinstance(result, dict)
                else (0, 0)
            )
            if isinstance(content, dict):
                extractor = content.get("reader") or content.get("format")
            if not success:
                warnings.append(str((result or {}).get("error") or "read returned success=false"))
        except Exception as exc:
            error = str(exc)
            warnings.append(traceback.format_exc(limit=2))
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        tier = _classify(duration_ms, success and not error, output_bytes)
        records.append({
            "type": "extraction",
            "path": str(path.relative_to(ROOT)),
            "extension": ext,
            "extractor": extractor,
            "success": success and error is None,
            "duration_ms": duration_ms,
            "output_bytes": output_bytes,
            "characters": characters,
            "tier": tier,
            "error": error,
            "warnings": warnings[:3],
        })

    summary = {"GREEN": 0, "YELLOW": 0, "RED": 0}
    for row in records:
        if row.get("type") == "extraction":
            summary[row.get("tier", "RED")] = summary.get(row.get("tier", "RED"), 0) + 1
    records.append({"type": "summary", "tiers": summary, "finished_at": utc_now_iso()})
    write_jsonl(args.out, records)
    print(args.out)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
