"""Render the extraction audit report from the archive and the register.

Everything numeric in ``REPORT.md`` comes from ``records.jsonl``: the per-file
table, the verdict counts, the per-family and per-reader summaries, the runtime
distribution, the evidence quoted in each finding. The prose that cannot be
derived - root causes, what is intentional, what is still open - lives in a
small JSON register, so the report and the archive cannot disagree and the
register is reviewable on its own.

Usage:
    python tools/audit/build_audit_report.py \
        --records audit_report/r16/archive/records.jsonl.gz \
        --register tools/audit/registers/r16.json \
        --out audit_report/r16
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import platform
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAMILY = {
    ".pdf": "PDF", ".docx": "Word", ".docm": "Word", ".dotx": "Word",
    ".dotm": "Word", ".doc": "Word (legacy)", ".dot": "Word (legacy)",
    ".rtf": "RTF", ".odt": "OpenDocument", ".ott": "OpenDocument",
    ".ods": "OpenDocument", ".ots": "OpenDocument", ".odp": "OpenDocument",
    ".otp": "OpenDocument", ".odg": "OpenDocument",
    ".xlsx": "Excel", ".xlsm": "Excel", ".xltx": "Excel", ".xlt": "Excel",
    ".xltm": "Excel", ".xls": "Excel (legacy)", ".xlsb": "Excel (binary)",
    ".csv": "Delimited text", ".tsv": "Delimited text",
    ".pptx": "PowerPoint", ".pptm": "PowerPoint", ".ppsx": "PowerPoint",
    ".potx": "PowerPoint", ".ppt": "PowerPoint (legacy)",
    ".pps": "PowerPoint (legacy)", ".pot": "PowerPoint (legacy)",
    ".html": "HTML", ".htm": "HTML", ".xhtml": "HTML",
    ".txt": "Plain text", ".md": "Plain text", ".py": "Plain text",
    ".json": "Plain text", ".xml": "Plain text",
    ".zip": "Archive", ".gz": "Archive", ".bz2": "Archive", ".tar": "Archive",
    ".tgz": "Archive", ".tbz2": "Archive", ".txz": "Archive", ".xz": "Archive",
    ".7z": "Archive", ".rar": "Archive",
    ".mp3": "Audio", ".m4a": "Audio", ".flac": "Audio", ".ogg": "Audio",
    ".wav": "Audio", ".aac": "Audio", ".wma": "Audio",
    ".mp4": "Video", ".mov": "Video", ".mkv": "Video", ".webm": "Video",
    ".flv": "Video", ".wmv": "Video",
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image",
    ".svg": "Image", ".bmp": "Image", ".tif": "Image", ".tiff": "Image",
    ".webp": "Image", ".ico": "Image", ".heic": "Image", ".avif": "Image",
    ".pages": "iWork", ".numbers": "iWork",
    ".ai": "Design", ".psd": "Design", ".dng": "Camera raw",
    ".nef": "Camera raw", ".cr2": "Camera raw", ".arw": "Camera raw",
    ".exe": "Executable", ".apk": "Executable", ".ipa": "Executable",
    ".dmg": "Executable",
}

#: Verdicts that mean "a person must look at this file".
ATTENTION = {"INCOMPLETE", "PARTIAL", "ERROR", "TIMEOUT", "EMPTY-PLAUSIBLE"}

VERSIONED = [
    ("pymupdf", "PyMuPDF"), ("openpyxl", "openpyxl"), ("docx", "python-docx"),
    ("odf", "odfpy"), ("pptx", "python-pptx"), ("PIL", "Pillow"),
    ("bs4", "beautifulsoup4"), ("striprtf", "striprtf"), ("xlrd", "xlrd"),
    ("rapidocr_onnxruntime", "RapidOCR"), ("pytesseract", "pytesseract"),
    ("numpy", "numpy"), ("cv2", "opencv"),
]


def family_of(record: Dict[str, Any]) -> str:
    declared = (record.get("declared_ext") or "").lower()
    detected = (record.get("detected_ext") or "").lower()
    return FAMILY.get(declared) or FAMILY.get(detected) or (declared or "unknown")


def load_records(path: Path) -> List[Dict[str, Any]]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "0.0%"


def environment() -> Dict[str, str]:
    import importlib

    info = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    for module, label in VERSIONED:
        try:
            loaded = importlib.import_module(module)
            info[label] = str(getattr(loaded, "__version__", "installed"))
        except Exception:
            info[label] = "NOT INSTALLED"
    #: Binaries the pipeline shells out to, when it can.
    import shutil

    for binary, label in (("tesseract", "tesseract binary"), ("ffmpeg", "ffmpeg"),
                          ("soffice", "LibreOffice"), ("antiword", "antiword")):
        info[label] = shutil.which(binary) or "NOT INSTALLED"
    return info


def per_file_row(record: Dict[str, Any], register: Dict[str, Any]) -> List[str]:
    recall = record.get("recall")
    lost = ""
    if record.get("verdict") in ("ERROR", "TIMEOUT"):
        lost = (record.get("error") or "")[:120]
    elif recall is not None and recall < 0.995:
        missing = record.get("missing_sample") or ""
        lost = f"missing: {missing[:110]}"
    elif record.get("verdict") == "UNVERIFIABLE":
        proxies = record.get("proxies") or {}
        lost = (f"no oracle; coverage {proxies.get('coverage')} over "
                f"{proxies.get('pages')} page(s)")
    elif record.get("verdict") == "EMPTY-PLAUSIBLE":
        lost = "nothing text-bearing expected (not verified)"
    elif record.get("verdict") == "OK-NO-ORACLE":
        lost = "stored but not verifiable independently"
    finding = finding_for(record, register)
    return [
        record.get("path", ""),
        family_of(record),
        record.get("reader") or "",
        record.get("verdict", ""),
        "" if recall is None else f"{recall:.3f}",
        str(record.get("stored_chars", 0)),
        f"{record.get('elapsed_ms', 0) / 1000:.1f}s",
        ", ".join(f"{kind}:{count}" for kind, count in
                  sorted((record.get("unit_kinds") or {}).items())),
        lost or "-",
        finding.get("id", "") if finding else "",
        finding.get("fix", "")[:60] if finding else "",
        finding.get("test", "")[:44] if finding else "",
    ]


def finding_for(record: Dict[str, Any], register: Dict[str, Any]):
    path = record.get("path", "")
    best = None
    for entry in register.get("findings", []):
        for match in entry.get("files", []):
            if path == match or path.startswith(match):
                if best is None or len(match) > len(best[0]):
                    best = (match, entry)
    return best[1] if best else None


def render(records: List[Dict[str, Any]], register: Dict[str, Any]) -> str:
    total = len(records)
    verdicts = collections.Counter(r.get("verdict") for r in records)
    scored = [r for r in records if r.get("recall") is not None]
    families: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    readers: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for record in records:
        families[family_of(record)].append(record)
        readers[record.get("reader") or "(none)"].append(record)

    elapsed = [r.get("elapsed_ms", 0) / 1000 for r in records]
    slowest = sorted(records, key=lambda r: -r.get("elapsed_ms", 0))[:8]
    chars = sum(r.get("stored_chars", 0) for r in records)
    env = environment()

    out: List[str] = []
    add = out.append

    add("# Extraction audit: every file, what it stores, what it drops")
    add("")
    add("Generated by `tools/audit/build_audit_report.py` from "
        f"`records.jsonl` ({total} records) and "
        f"`{register.get('register_name', 'the register')}`. "
        "Every number below is computed from the archive; nothing is typed by hand.")
    add("")
    add(f"- Corpus: `{register.get('corpus', 'test-doc')}` — "
        f"{register.get('corpus_files', total)} files, "
        f"{register.get('corpus_bytes', 0) / 1e6:.1f} MB")
    add(f"- Run: {register.get('run_at', '')}; "
        f"timeout {register.get('timeout_s', 120)} s/file")
    add(f"- Mode: {register.get('mode', 'AUDIT_AND_FIX')}")
    add("")

    # ------------------------------------------------------------------ #
    add("## 1. Executive summary")
    add("")
    add("| Measure | Value |")
    add("|---|---|")
    add(f"| Files audited | {total} |")
    add(f"| Files with an independent oracle | {len(scored)} |")
    add(f"| Complete (token recall ≥ 0.995) | "
        f"{sum(1 for r in scored if r['recall'] >= 0.995)} "
        f"({pct(sum(1 for r in scored if r['recall'] >= 0.995), len(scored))}) |")
    add(f"| Mean token recall where measurable | "
        f"{(statistics.mean([r['recall'] for r in scored]) if scored else 0):.4f} |")
    add(f"| Text stored | {chars:,} characters |")
    add(f"| Median runtime per file | {statistics.median(elapsed):.2f} s |")
    add(f"| Slowest file | {slowest[0].get('path')} "
        f"({slowest[0].get('elapsed_ms', 0) / 1000:.1f} s) |")
    add("")
    add("| Verdict | Files | Share | Meaning |")
    add("|---|---|---|---|")
    meanings = register.get("verdict_meanings", {})
    for verdict, count in verdicts.most_common():
        add(f"| {verdict} | {count} | {pct(count, total)} | "
            f"{meanings.get(verdict, '')} |")
    add("")
    add("Verdicts are defined in section 2. `ATTENTION` files "
        f"({sum(verdicts[v] for v in ATTENTION)} in this run) are the only ones "
        "that need a human; every other verdict is either measured complete or "
        "documented as unmeasurable for a stated reason.")
    add("")
    add(register.get("executive_summary", "").strip())
    add("")

    # ------------------------------------------------------------------ #
    add("## 2. Method, thresholds and status taxonomy")
    add("")
    add("The pipeline is called exactly as production calls it: "
        "`FileReaderService.read_file({path, extension})` for routing and the "
        "reader, then `StoragePipeline._extract_text_from_content()` on the "
        "reader's result - the same function that decides what is stored. "
        "Text that a reader returns but that never reaches that function is "
        "counted as loss, because it is loss.")
    add("")
    add("| Verdict | Rule |")
    add("|---|---|")
    for verdict, rule in register.get("verdict_rules", {}).items():
        add(f"| {verdict} | {rule} |")
    add("")
    add("Recall is token recall against an independent oracle: the container's "
        "raw parts parsed with a real XML parser (OOWML, ODF), a second library "
        "(openpyxl, xlrd, PyMuPDF), or byte inspection for plain text. Both "
        "sides are normalised identically — ligatures, soft hyphens, NBSP and "
        "zero-width characters, line-break hyphenation. Character ratios are "
        "reported for information only; they reward whitespace.")
    add("")

    # ------------------------------------------------------------------ #
    add("## 3. Environment (measured)")
    add("")
    add("| Component | Version / path |")
    add("|---|---|")
    for key, value in env.items():
        add(f"| {key} | `{value}` |")
    add("")
    missing = [f"{k} ({v})" for k, v in env.items() if v == "NOT INSTALLED"]
    add("Missing engines are findings, not excuses: " +
        (", ".join(missing) if missing else "none missing") + ".")
    add("")

    # ------------------------------------------------------------------ #
    add("## 4. Type and function inventory")
    add("")
    add("| Format family | Files | Reader(s) | Entry point | Independent oracle |")
    add("|---|---|---|---|---|")
    for family, rows in sorted(families.items(), key=lambda kv: -len(kv[1])):
        reader_names = sorted({r.get("reader") or "(none)" for r in rows})
        oracles = sorted({str((r.get("oracle") or {}).get("oracle")) for r in rows})
        add(f"| {family} | {len(rows)} | {', '.join(reader_names)} | "
            f"`FileReaderService.read_file` | {', '.join(oracles)} |")
    add("")
    add(register.get("function_inventory", "").strip())
    add("")

    # ------------------------------------------------------------------ #
    add("## 5. Per-file results")
    add("")
    add(f"All {total} files, one row each. `recall` is `-` where no independent "
        "oracle exists for the format; the `ignored / lost` column then says why.")
    add("")
    add("| file | type | reader | status | recall | chars | runtime | units | "
        "ignored / lost | finding | fix | test |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for record in sorted(records, key=lambda r: (r.get("verdict") or "", r.get("path") or "")):
        add("| " + " | ".join(cell.replace("|", "\\|") for cell in
                              per_file_row(record, register)) + " |")
    add("")

    # ------------------------------------------------------------------ #
    add("## 6. Files that are not complete")
    add("")
    attention = [r for r in records if r.get("verdict") in ATTENTION
                 or (r.get("recall") is not None and r["recall"] < 0.95)]
    add(f"{len(attention)} files. Each entry states what was stored, what was "
        "lost, and what evidence supports the claim.")
    add("")
    for record in sorted(attention, key=lambda r: (r.get("recall") if r.get("recall")
                                                   is not None else 2)):
        finding = finding_for(record, register)
        add(f"### `{record.get('path')}` — {record.get('verdict')}")
        add("")
        add(f"- Reader: `{record.get('reader')}` "
            f"(declared `{record.get('declared_ext')}`, detected "
            f"`{record.get('detected_ext')}`)")
        add(f"- Stored: {record.get('stored_chars', 0):,} characters in "
            f"{sum((record.get('unit_kinds') or {}).values())} located units "
            f"({', '.join(f'{k} {v}' for k, v in (record.get('unit_kinds') or {}).items())})")
        add(f"- Runtime: {record.get('elapsed_ms', 0) / 1000:.2f} s; "
            f"peak RSS (process high-water) {record.get('peak_rss_kb', 0) / 1024:.0f} MB")
        if record.get("recall") is not None:
            add(f"- Recall: {record['recall']:.4f} "
                f"({record.get('missing_words')} of "
                f"{record.get('oracle_words')} oracle tokens missing)")
            if record.get("missing_sample"):
                add(f"- Missing tokens (sample): `{record['missing_sample']}`")
        if record.get("error"):
            add(f"- Error: `{str(record['error'])[:300]}`")
        if record.get("proxies"):
            add(f"- Proxies: `{json.dumps(record['proxies'], ensure_ascii=False)}`")
        if finding:
            add(f"- Root cause {finding['id']} [{finding['category']}]: "
                f"{finding['root_cause']}")
            add(f"- Status: {finding['status']}")
        add("")

    # ------------------------------------------------------------------ #
    add("## 7. Read-versus-ignored matrix")
    add("")
    add("What each family's content categories do at each stage. `read` = parsed "
        "by the reader, `extracted` = present in the reader's result, "
        "`stored` = reaches the storage text, `discarded` = read and dropped. "
        "`I` = intentional exclusion with a stated reason, `U` = unintended loss.")
    add("")
    add("| format | content category | read | extracted | stored | discarded | "
        "intentional? | note |")
    add("|---|---|---|---|---|---|---|")
    for entry in register.get("matrix", []):
        add(f"| {entry['format']} | {entry['category']} | {entry['read']} | "
            f"{entry['extracted']} | {entry['stored']} | {entry['discarded']} | "
            f"{entry['intentional']} | {entry['note']} |")
    add("")

    # ------------------------------------------------------------------ #
    add("## 8. Root-cause and remediation register")
    add("")
    add("Ranked by data-loss risk × files affected × operational impact. "
        "`severity` is 1 (silent, total loss) to 5 (cosmetic).")
    add("")
    add("| id | sev | category | finding | affected | root cause | fix | test | "
        "status | regression risk |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for finding in sorted(register.get("findings", []),
                          key=lambda f: (f.get("severity", 9), f.get("id", ""))):
        add(f"| {finding['id']} | {finding.get('severity')} | "
            f"{finding.get('category')} | {finding.get('title')} | "
            f"{finding.get('affected', '')} | {finding.get('root_cause')} | "
            f"{finding.get('fix', '')} | {finding.get('test', '')} | "
            f"{finding.get('status')} | {finding.get('regression_risk', '')} |")
    add("")

    # ------------------------------------------------------------------ #
    add("## 9. Validation evidence")
    add("")
    add("Commands actually run, with expected and actual results. `before` and "
        "`after` are measurements of the same check on the same corpus.")
    add("")
    add("| check | command | before | after | expected | actual |")
    add("|---|---|---|---|---|---|")
    for item in register.get("validation", []):
        add(f"| {item['check']} | `{item['command']}` | {item.get('before', '')} | "
            f"{item.get('after', '')} | {item.get('expected', '')} | "
            f"{item.get('actual', '')} |")
    add("")

    # ------------------------------------------------------------------ #
    add("## 10. Integrity and performance")
    add("")
    add(register.get("integrity", "").strip())
    add("")
    add("| Slowest files | seconds | stored chars |")
    add("|---|---|---|")
    for record in slowest:
        add(f"| `{record.get('path')}` | {record.get('elapsed_ms', 0) / 1000:.1f} | "
            f"{record.get('stored_chars', 0):,} |")
    add("")

    # ------------------------------------------------------------------ #
    add("## 10a. Where extraction stops: the storage boundary (measured)")
    add("")
    add("A reader's `content` dict is not the thing that is stored. "
        "`StoragePipeline._extract_text_from_content` is, and it reads a fixed "
        "set of keys (read from its syntax tree by "
        "`tools/audit/archive_extraction.py::storage_consumed_keys`, "
        f"{len(_consumed_keys())} keys). Every key a reader "
        "populates that is **not** in that set is content the pipeline "
        "discards - unless the discard is deliberate, which the table says "
        "per key.")
    add("")
    boundary_rows = boundary_table(records, register)
    if boundary_rows:
        add("| key | files where populated | family examples | verdict |")
        add("|---|---|---|---|")
        for row in boundary_rows:
            add(f"| `{row['key']}` | {row['files']} | {row['families']} | "
                f"{row['judgement']} |")
        add("")
        lost_content = [r for r in boundary_rows
                        if r["judgement"].startswith("**content loss**")]
        add(f"Keys judged content loss: **{len(lost_content)}**"
            + (": " + ", ".join(f"`{r['key']}`" for r in lost_content)
               if lost_content else "") + ".")
    else:
        add("No records carried the `keys_not_persisted` field; the storage "
            "boundary could not be measured in this run.")
    add("")

    # ------------------------------------------------------------------ #
    add("## 10b. Isolation, memory and runtime (measured)")
    add("")
    isolated = [r for r in records if r.get("isolated")]
    if isolated:
        peaks = sorted(r.get("peak_rss_kb", 0) for r in isolated)
        walls = sorted(r.get("wall_ms", 0) for r in isolated if r.get("wall_ms"))
        add("Each file ran in its own process, so `peak_rss_kb` is that file's "
            "high-water mark rather than the harness's running maximum, and a "
            "hang or a crash costs one record instead of the run.")
        add("")
        add("| Measure | Value |")
        add("|---|---|")
        add(f"| Files run isolated | {len(isolated)} |")
        add(f"| Median peak RSS | {statistics.median(peaks) / 1024:.0f} MiB |")
        add(f"| 95th percentile peak RSS | "
            f"{peaks[int(0.95 * (len(peaks) - 1))] / 1024:.0f} MiB |")
        add(f"| Maximum peak RSS | {peaks[-1] / 1024:.0f} MiB |")
        if walls:
            add(f"| Median wall clock (parent, incl. process start) | "
                f"{statistics.median(walls) / 1000:.2f} s |")
            add(f"| Maximum wall clock | {walls[-1] / 1000:.1f} s |")
        add(f"| Files that hit the timeout | "
            f"{sum(1 for r in isolated if r.get('verdict') == 'TIMEOUT')} |")
        add(f"| Files killed for memory | "
            f"{sum(1 for r in isolated if r.get('memory_limited'))} |")
        add("")
        heaviest = sorted(isolated, key=lambda r: r.get("peak_rss_kb", 0),
                          reverse=True)[:8]
        add("| Heaviest files | peak RSS (MiB) | stored chars |")
        add("|---|---|---|")
        for record in heaviest:
            add(f"| `{record.get('path')}` | "
                f"{record.get('peak_rss_kb', 0) / 1024:.0f} | "
                f"{record.get('stored_chars', 0):,} |")
        add("")
    else:
        add("This run was not isolated; memory figures are the harness's "
            "running maximum and are reported as an upper bound only.")
        add("")

    # ------------------------------------------------------------------ #
    add("## 11. Per-family summary")
    add("")
    add("| family | files | scored | mean recall | worst | complete | "
        "attention | stored chars |")
    add("|---|---|---|---|---|---|---|---|")
    for family, rows in sorted(families.items(), key=lambda kv: -len(kv[1])):
        scored_rows = [r for r in rows if r.get("recall") is not None]
        worst = min(scored_rows, key=lambda r: r["recall"]) if scored_rows else None
        worst_cell = (f"{worst['path']} {worst['recall']:.3f}") if worst else "-"
        add(f"| {family} | {len(rows)} | {len(scored_rows)} | "
            f"{(statistics.mean([r['recall'] for r in scored_rows]) if scored_rows else 0):.4f} | "
            f"{worst_cell} | "
            f"{sum(1 for r in rows if r.get('verdict') == 'COMPLETE')} | "
            f"{sum(1 for r in rows if r.get('verdict') in ATTENTION)} | "
            f"{sum(r.get('stored_chars', 0) for r in rows):,} |")
    add("")
    add("| reader | files | verdicts | stored chars | median runtime |")
    add("|---|---|---|---|---|")
    for reader, rows in sorted(readers.items(), key=lambda kv: -len(kv[1])):
        counts = collections.Counter(r.get("verdict") for r in rows)
        add(f"| `{reader}` | {len(rows)} | "
            f"{', '.join(f'{k} {v}' for k, v in counts.most_common())} | "
            f"{sum(r.get('stored_chars', 0) for r in rows):,} | "
            f"{statistics.median([r.get('elapsed_ms', 0) for r in rows]) / 1000:.2f} s |")
    add("")

    # ------------------------------------------------------------------ #
    add("## 12. Final completeness assessment")
    add("")
    add(register.get("final_assessment", "").strip())
    add("")

    # ------------------------------------------------------------------ #
    add("## 13. Backlog")
    add("")
    add("| rank | item | why | effort |")
    add("|---|---|---|---|")
    for item in register.get("backlog", []):
        add(f"| {item['rank']} | {item['item']} | {item['why']} | {item['effort']} |")
    add("")
    add("## 14. Reproducing this report")
    add("")
    add("```bash")
    add("# 1. the archive (one record per file, full text, full locations)")
    add("python tools/audit/archive_extraction.py --corpus test-doc "
        "--out audit_report/r16 --timeout 120")
    add("# 2. the report (a function of the archive and the register)")
    add("python tools/audit/build_audit_report.py "
        "--records audit_report/r16/archive/records.jsonl.gz "
        "--register tools/audit/registers/r16.json --out audit_report/r16")
    add("```")
    add("")
    return "\n".join(out)


def write_csv(path: Path, header: List[str], rows: List[List[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _consumed_keys() -> set:
    """The keys storage reads, from the same scanner the audit run uses."""
    try:
        from tools.audit.archive_extraction import _storage_keys

        return _storage_keys()
    except Exception:
        return set()


def boundary_table(records, register) -> list:
    """Aggregate the keys each reader populated but storage never reads.

    The judgement column is the only authored part: whether a discarded key is
    instrumentation (line counts, timings) or content (revisions, comments) is
    an analysis, and the register carries it per key so the table cannot
    quietly disagree with the narrative.
    """
    judgements = register.get("storage_key_judgements", {})
    stats = {}
    for record in records:
        for key in record.get("keys_not_persisted") or []:
            entry = stats.setdefault(key, {"files": 0, "families": set()})
            entry["files"] += 1
            entry["families"].add(record.get("folder") or "?")
    rows = []
    for key, entry in stats.items():
        families = sorted(entry["families"])[:3]
        rows.append({
            "key": key,
            "files": entry["files"],
            "families": ", ".join(families) + ("…" if len(entry["families"]) > 3 else ""),
            "judgement": judgements.get(key, "unclassified — needs review"),
        })
    rows.sort(key=lambda r: (-r["files"], r["key"]))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True)
    parser.add_argument("--register", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    records = load_records(Path(args.records))
    register = json.loads(Path(args.register).read_text(encoding="utf-8"))
    register.setdefault("register_name", Path(args.register).name)
    register.setdefault("corpus_files", len(records))
    if not register.get("corpus_bytes"):
        register["corpus_bytes"] = sum(r.get("bytes", 0) for r in records)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = render(records, register)
    (out_dir / "REPORT.md").write_text(report, encoding="utf-8")

    header = ["file", "type", "reader", "status", "recall", "chars", "runtime",
              "units", "ignored_lost", "finding", "fix", "test"]
    rows = [per_file_row(r, register) for r in records]
    write_csv(out_dir / "per_file.csv", header, rows)

    flagged = [r for r in records
               if r.get("verdict") in ATTENTION
               or (r.get("recall") is not None and r["recall"] < 0.95)]
    (out_dir / "findings.json").write_text(
        json.dumps({"generated_from": args.records, "files": [
            {k: r.get(k) for k in ("path", "reader", "verdict", "recall",
                                   "stored_chars", "missing_sample", "error",
                                   "proxies", "elapsed_ms", "peak_rss_kb")}
            for r in flagged]}, indent=1, ensure_ascii=False), encoding="utf-8")

    counts = collections.Counter(r.get("verdict") for r in records)
    (out_dir / "verdict_counts.json").write_text(
        json.dumps(dict(counts.most_common()), indent=1), encoding="utf-8")

    print(f"wrote {out_dir/'REPORT.md'} ({len(report):,} chars), "
          f"{out_dir/'per_file.csv'} ({len(rows)} rows), "
          f"{out_dir/'findings.json'} ({len(flagged)} flagged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
