"""Measure what each reader lost, token by token, against an independent oracle.

``analyse_extraction`` summarises; this tool adjudicates. For every archived
file whose format has a parser that is not the project's own, it re-reads the
file with that parser, tokenises both sides and reports:

* ``recall``      share of the oracle's word occurrences that reached storage
* ``missing``     the words that did not, longest first - the actual loss
* ``stored_only`` words the reader produced that the oracle never saw (metadata,
                  synthesised headers, OCR of a scan): additions, not errors
* ``by_container`` for DOCX, which part of the package the missing words live in
                  (body, header, footer, footnotes, endnotes, comments,
                  text boxes), so a loss names its cause

Nothing here re-runs a reader: the stored text comes from the archive, so the
comparison is between what was *stored* and what the file *contains*.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive_extraction import _oracle_text, oracle_for  # noqa: E402

TOKEN_RE = re.compile(r"[0-9A-Za-z\u00c0-\u024f\u0400-\u04ff\u0590-\u05ff"
                      r"\u0600-\u06ff]+")


def tokens(text: str) -> collections.Counter:
    return collections.Counter(TOKEN_RE.findall(text or ""))


def load(path: Path) -> List[Dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def best_recall(oracle, stored: str):
    """Oracle text may be several readings of one file; keep the best."""
    readings = oracle if isinstance(oracle, (list, tuple)) else [oracle]
    best = None
    for reading in readings:
        if not (reading or "").strip():
            continue
        o, s = tokens(reading), tokens(stored)
        total = sum(o.values())
        if not total:
            continue
        kept = sum(min(n, s.get(t, 0)) for t, n in o.items())
        missing = [(t, n - s.get(t, 0)) for t, n in o.items() if s.get(t, 0) < n]
        missing.sort(key=lambda kv: (-len(kv[0]), -kv[1]))
        candidate = {
            "recall": round(kept / total, 4),
            "oracle_words": total,
            "missing_words": total - kept,
            "missing": [t for t, _ in missing[:25]],
            "reading": reading,
        }
        if best is None or candidate["recall"] > best["recall"]:
            best = candidate
    return best


# --------------------------------------------------------------------------- #
# DOCX: attribute the loss to a part of the package
# --------------------------------------------------------------------------- #
_W_RE = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
_FALLBACK_RE = re.compile(r"<mc:Fallback>.*?</mc:Fallback>", re.S)
_PART_RE = re.compile(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")
_BOX_RE = re.compile(r"<w:txbxContent>(.*?)</w:txbxContent>", re.S)


def docx_containers(path: Path) -> Dict[str, Dict[str, Any]]:
    """Tokens per package part, and per text box inside the body."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            parts = {n: _FALLBACK_RE.sub("", zf.read(n).decode("utf-8", "replace"))
                     for n in zf.namelist() if _PART_RE.match(n)}
    except Exception as exc:
        return {"error": {"detail": f"{type(exc).__name__}: {exc}"}}
    for name, xml in parts.items():
        boxes = [" ".join(_W_RE.findall(box)) for box in _BOX_RE.findall(xml)]
        out[name] = {
            "tokens": tokens(" ".join(_W_RE.findall(xml))),
            "text_boxes": [tokens(b) for b in boxes if b.strip()],
        }
    return out


def attribute_docx(containers, stored_tokens) -> List[Dict[str, Any]]:
    """Which container holds each missing word, and how many are in it."""
    hits = []
    for name, info in containers.items():
        if name == "error":
            continue
        part_missing = {t: n - stored_tokens.get(t, 0)
                        for t, n in info["tokens"].items()
                        if stored_tokens.get(t, 0) < n}
        if part_missing:
            hits.append({"container": name,
                         "missing_words": sum(part_missing.values()),
                         "sample": sorted(part_missing, key=len, reverse=True)[:12]})
        for index, box in enumerate(info["text_boxes"], 1):
            box_missing = {t: n - stored_tokens.get(t, 0)
                           for t, n in box.items() if stored_tokens.get(t, 0) < n}
            if box_missing:
                hits.append({"container": f"{name} text box {index}",
                             "missing_words": sum(box_missing.values()),
                             "sample": sorted(box_missing, key=len, reverse=True)[:8]})
    hits.sort(key=lambda h: -h["missing_words"])
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--corpus", default="test-doc")
    ap.add_argument("--out", default="audit_report/r15/completeness.json")
    args = ap.parse_args(argv)

    records = load(Path(args.archive))
    corpus = Path(args.corpus)
    results: List[Dict[str, Any]] = []

    for record in records:
        path = corpus / record["path"]
        ext = record.get("ext") or path.suffix.lower()
        identity = oracle_for(path, ext)
        if not identity.get("oracle"):
            continue
        try:
            oracle = _oracle_text(path, ext)
        except Exception as exc:
            results.append({"path": record["path"], "ext": ext,
                            "oracle": identity["oracle"],
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        stored = record.get("text") or ""
        scored = best_recall(oracle, stored)
        if scored is None:
            continue
        entry = {
            "path": record["path"],
            "ext": ext,
            "family": record.get("family") or ext,
            "reader": record.get("reader"),
            "oracle": identity["oracle"],
            "stored_chars": len(stored),
            "recall": scored["recall"],
            "oracle_words": scored["oracle_words"],
            "missing_words": scored["missing_words"],
            "missing": scored["missing"],
        }
        stored_only = tokens(stored) - tokens(scored["reading"])
        if stored_only:
            entry["stored_only_words"] = sum(stored_only.values())
            entry["stored_only_sample"] = [t for t, _ in stored_only.most_common(10)]
        if entry["recall"] < 0.99:
            if ext in (".docx", ".docm", ".dotx", ".dotm"):
                containers = docx_containers(path)
                entry["by_container"] = attribute_docx(containers, tokens(stored))
            elif ext == ".pdf":
                entry["pages_with_text"] = sum(
                    1 for unit in record.get("units") or []
                    if unit.get("kind") == "page" and unit.get("text"))
                entry["pages_located"] = record.get("unit_kinds", {}).get("page")
        results.append(entry)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    scored = [r for r in results if "recall" in r]
    perfect = [r for r in scored if r["recall"] >= 0.995]
    print(f"files with an oracle: {len(scored)}")
    print(f"  complete (>=0.995): {len(perfect)}")
    print(f"  with loss:          {len(scored) - len(perfect)}")
    by_ext = collections.defaultdict(list)
    for r in scored:
        by_ext[r["ext"]].append(r["recall"])
    for ext, recalls in sorted(by_ext.items(), key=lambda kv: -len(kv[1])):
        worst = min(recalls)
        print(f"  {ext:7} n={len(recalls):3} mean={sum(recalls)/len(recalls):.3f} worst={worst:.3f}")
    worst_ten = sorted(scored, key=lambda r: r["recall"])[:10]
    print("worst:")
    for r in worst_ten:
        print(f"  {r['recall']:.3f} {r['path'][:60]}")
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
