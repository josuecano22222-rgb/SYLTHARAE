#!/usr/bin/env python3
"""Deep office-format audit against test-doc/ (elements, storage text, method)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.perf.bootstrap_runtime import bootstrap_runtime

bootstrap_runtime()

OFFICE_EXTS = {
    "doc", "docx", "dot", "rtf", "odt", "pages",
    "xls", "xlsx", "xlsb", "xlsm", "ods", "numbers", "csv",
    "ppt", "pptx", "pptm", "odp",
}


def _content_metrics(content: Any) -> Dict[str, Any]:
    if not isinstance(content, dict):
        return {"shape": type(content).__name__, "text_len": len(str(content or ""))}
    text = str(content.get("text") or content.get("body") or "")
    elements = content.get("elements")
    sheets = content.get("sheets")
    paragraphs = content.get("paragraphs")
    tables = content.get("tables")
    slides = content.get("slides")
    return {
        "shape": "dict",
        "reader": content.get("reader") or content.get("format"),
        "method": content.get("method") or content.get("extraction_method"),
        "text_len": len(text),
        "text_bytes": len(text.encode("utf-8", errors="replace")),
        "elements_count": len(elements) if isinstance(elements, list) else None,
        "paragraphs_count": len(paragraphs) if isinstance(paragraphs, list) else None,
        "tables_count": len(tables) if isinstance(tables, list) else None,
        "sheets_count": len(sheets) if isinstance(sheets, list) else None,
        "slides_count": len(slides) if isinstance(slides, list) else None,
        "has_error": bool(content.get("error")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "test-doc")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs" / "perf" / "runs" / "office_deep_audit_testdoc.jsonl",
    )
    args = parser.parse_args()

    from reader_file.services.file_reader_service import FileReaderService
    from pipeline.storage_pipeline import StoragePipeline
    from tools.perf.read_result_utils import (
        content_dict_from_read_result,
        read_result_success,
        text_metrics_from_read_result,
    )

    service = FileReaderService()
    pipeline = StoragePipeline()

    files = sorted(
        p
        for p in args.root.rglob("*")
        if p.is_file() and p.suffix.lower().lstrip(".") in OFFICE_EXTS
    )

    records: List[Dict[str, Any]] = [
        {"type": "meta", "office_file_count": len(files), "extensions": sorted(OFFICE_EXTS)}
    ]

    for path in files:
        ext = path.suffix.lower().lstrip(".")
        started = time.perf_counter()
        row: Dict[str, Any] = {
            "type": "office",
            "path": str(path.relative_to(ROOT)),
            "extension": ext,
        }
        try:
            result = service.read_file({"path": str(path), "extension": path.suffix.lstrip(".")})
            ok = read_result_success(result)
            content = content_dict_from_read_result(result) if isinstance(result, dict) else {}
            row["read_ok"] = ok
            row["top_error"] = result.get("error") if isinstance(result, dict) else None
            row["metrics"] = _content_metrics(content)
            chars, out_bytes = (
                text_metrics_from_read_result(result)
                if isinstance(result, dict)
                else (0, 0)
            )
            row["indexed_chars"] = chars
            row["indexed_bytes"] = out_bytes
            storage_text = pipeline._extract_text_from_content(content) if isinstance(content, dict) else ""
            row["storage_text_len"] = len(storage_text)
            row["storage_text_bytes"] = len(storage_text.encode("utf-8", errors="replace"))
            row["storage_empty"] = not bool(storage_text.strip())
            if not ok:
                row["tier"] = "RED"
            elif chars == 0 and row["storage_empty"]:
                row["tier"] = "YELLOW"
            else:
                row["tier"] = "GREEN"
        except Exception as exc:
            row["read_ok"] = False
            row["tier"] = "RED"
            row["exception"] = str(exc)
            row["trace"] = traceback.format_exc(limit=3)
        row["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        records.append(row)

    summary: Dict[str, Any] = {"GREEN": 0, "YELLOW": 0, "RED": 0, "by_ext": {}}
    for row in records:
        if row.get("type") != "office":
            continue
        tier = row.get("tier", "RED")
        summary[tier] = summary.get(tier, 0) + 1
        ext = row["extension"]
        summary["by_ext"].setdefault(ext, {"GREEN": 0, "YELLOW": 0, "RED": 0})
        summary["by_ext"][ext][tier] = summary["by_ext"][ext].get(tier, 0) + 1

    records.append({"type": "summary", "tiers": summary})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(args.out)
    print(json.dumps(summary, indent=2))
    reds = [r for r in records if r.get("type") == "office" and r.get("tier") == "RED"]
    yellows = [r for r in records if r.get("type") == "office" and r.get("tier") == "YELLOW"]
    if reds:
        print("\nRED (%d):" % len(reds))
        for r in reds[:30]:
            print(" ", r["path"], r.get("top_error") or r.get("exception"))
        if len(reds) > 30:
            print("  ... +%d more" % (len(reds) - 30))
    if yellows:
        print("\nYELLOW (%d):" % len(yellows))
        for r in yellows[:20]:
            print(" ", r["path"], r.get("metrics"))
    return 1 if reds else 0


if __name__ == "__main__":
    raise SystemExit(main())
