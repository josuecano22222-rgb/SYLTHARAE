"""Normalize FileReaderService.read_file() payloads for metrics and storage."""

from __future__ import annotations

from typing import Any, Dict, Tuple

_STORAGE_PIPELINE = None


def read_result_success(result: Any) -> bool:
    if not result or not isinstance(result, dict):
        return False
    if result.get("error"):
        return False
    return True


def content_dict_from_read_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Dict passed to StoragePipeline._extract_text_from_content."""
    if not isinstance(result, dict):
        return {}
    nested = result.get("content")
    if isinstance(nested, dict):
        return nested
    if isinstance(nested, str) and nested.strip():
        return {"text": nested}
    # Office / structured readers return fields at top level
    if any(
        k in result
        for k in (
            "elements",
            "paragraphs",
            "sheets",
            "sheet_names",
            "slides",
            "rows",
            "headers",
            "text",
            "body",
        )
    ):
        return result
    if isinstance(nested, str):
        return {"text": nested}
    return result


def text_metrics_from_read_result(result: Dict[str, Any]) -> Tuple[int, int]:
    """Return (character_count, byte_length) for tier classification."""
    if not read_result_success(result):
        return 0, 0
    if isinstance(result.get("character_count"), int) and result["character_count"] > 0:
        text = str(result.get("content") or "")
        enc = text.encode("utf-8", errors="replace")
        return result["character_count"], len(enc)
    content = content_dict_from_read_result(result)
    if isinstance(content.get("forensic_text"), str) and content["forensic_text"].strip():
        t = content["forensic_text"]
        return len(t), len(t.encode("utf-8", errors="replace"))
    text = _extract_text_light(content)
    if not text.strip():
        try:
            global _STORAGE_PIPELINE
            if _STORAGE_PIPELINE is None:
                from pipeline.storage_pipeline import StoragePipeline

                _STORAGE_PIPELINE = StoragePipeline()
            text = _STORAGE_PIPELINE._extract_text_from_content(content)
        except Exception:
            pass
    return len(text), len(text.encode("utf-8", errors="replace"))


def _extract_text_light(content: Dict[str, Any]) -> str:
    """DB-free text estimate for perf matrices and tests."""
    if not isinstance(content, dict):
        return str(content or "")
    if content.get("text"):
        return str(content["text"])
    parts: list = []
    for para in content.get("paragraphs") or []:
        if isinstance(para, dict) and para.get("text"):
            parts.append(str(para["text"]))
        elif isinstance(para, str):
            parts.append(para)
    for slide in content.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        for el in slide.get("elements") or []:
            if isinstance(el, dict) and el.get("text"):
                parts.append(str(el["text"]))
        parts.extend(str(t) for t in (slide.get("texts") or []) if t)
    if content.get("sheets") and isinstance(content["sheets"], dict):
        for sheet in content["sheets"].values():
            if not isinstance(sheet, dict):
                continue
            for row in sheet.get("data") or []:
                if isinstance(row, (list, tuple)):
                    parts.append("\t".join(str(c) for c in row if c is not None))
    joined = "\n".join(p for p in parts if p)
    if joined.strip():
        return joined
    nested = content.get("content")
    if isinstance(nested, str):
        return nested
    return str(content.get("forensic_text") or "")
