"""Build the extraction archive: text, its location, and its completeness.

For every file in the corpus this runs the product's reader, keeps the text
**per location unit** (one entry per PDF page, Word paragraph, Excel cell,
PowerPoint shape, archive member, OCR word box), and scores completeness by
token recall against an independent oracle.

Why token recall rather than character counts: formatting differs between any
two extractors (whitespace, join order, added headings), so a character ratio
punishes or rewards the wrong things. A word that the file contains and the
stored text does not is a real loss; a tab that one side omits is not.

The archive is one gzip-compressed JSONL: lossless, streamable, one record per
file, full text and full location data included. A CSV would have to flatten
the location tree; a database would not survive the repository.

Usage:
    python tools/audit/archive_extraction.py --corpus test-doc --out <dir>
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import html
import re
import resource
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

class Timeout(Exception):
    """Raised by the per-file alarm when a reader does not return."""


def _alarm(signum, frame):  # noqa: ARG001
    raise Timeout("per-file time limit reached")


SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def scrub(value: Any) -> Any:
    """Make a value JSON-safe, without hiding that it was unsafe."""
    if isinstance(value, str):
        return SURROGATE_RE.sub("\ufffd", value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def oracle_for(path: Path, ext: str) -> Dict[str, Any]:
    """Independent identity of the oracle used for this file (not the text)."""
    if ext == ".pdf":
        return {"oracle": "pymupdf"}
    if ext in (".docx", ".docm", ".dotx", ".dotm"):
        return {"oracle": "ooxml-w:t"}
    if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return {"oracle": "openpyxl"}
    if ext == ".xls":
        return {"oracle": "xlrd"}
    if ext in (".pptx", ".pptm", ".ppsx", ".potx"):
        return {"oracle": "ooxml-a:t"}
    if ext in (".odt", ".ods", ".odp", ".ott", ".ots", ".otp", ".odg"):
        return {"oracle": "content.xml"}
    if ext in (".html", ".htm", ".xhtml"):
        return {"oracle": "bs4"}
    if ext == ".rtf":
        return {"oracle": "striprtf"}
    if ext in (".txt", ".md", ".py", ".csv", ".tsv", ".log", ".json", ".xml",
               ".yml", ".yaml", ".ini", ".cfg"):
        return {"oracle": "direct-read"}
    if ext in (".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"):
        return {"oracle": None, "note": "container: members, not text"}
    return {"oracle": None, "note": "no independent parser for this format"}

TOKEN_RE = re.compile(r"[0-9A-Za-z\u00c0-\u024f\u0400-\u04ff\u0590-\u05ff"
                      r"\u0600-\u06ff]+")


#: This checkout's own absolute path. Reader error messages quote the file they
#: failed on, so the archive would bake a machine path into a tracked file - and
#: tests/unit/test_no_machine_paths.py scans tracked files for exactly that.
_CHECKOUT = str(Path(__file__).resolve().parents[2])


def scrub_paths(value):
    """Remove this checkout's absolute path from any string in a record."""
    if isinstance(value, str):
        return value.replace(_CHECKOUT + "/", "").replace(_CHECKOUT, ".")
    if isinstance(value, dict):
        return {k: scrub_paths(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_paths(v) for v in value]
    return value


def tokens(text: str) -> collections.Counter:
    return collections.Counter(TOKEN_RE.findall(text or ""))


def recall(oracle_text, stored_text: str) -> Dict[str, Any]:
    """Share of the oracle's word occurrences that survived to storage.

    ``oracle_text`` may be a list of readings of the same file - Word splits a
    word across runs whenever the formatting changes, so "<w:t>In</w:t>
    <w:t>voice</w:t>" is one word and "<w:t>Gmail</w:t><w:t>Shared</w:t>" in two
    cells is two. Rather than guess, the caller passes both joins and the
    reader gets the benefit of the better one: run boundaries are formatting,
    and a metric that punished them would report formatting as loss.
    """
    if isinstance(oracle_text, (list, tuple)):
        scored = [recall(text, stored_text) for text in oracle_text]
        scored = [s for s in scored if s["oracle_tokens"]]
        if not scored:
            return {"recall": None, "oracle_tokens": 0, "missing_tokens": 0,
                    "missing_sample": []}
        return max(scored, key=lambda s: s["recall"])

    o, s = tokens(oracle_text), tokens(stored_text)
    total = sum(o.values())
    if not total:
        return {"recall": None, "oracle_tokens": 0, "missing_tokens": 0,
                "missing_sample": []}
    kept = sum(min(n, s.get(t, 0)) for t, n in o.items())
    missing = [(t, n - s.get(t, 0)) for t, n in o.items() if s.get(t, 0) < n]
    missing.sort(key=lambda kv: (-len(kv[0]), -kv[1]))
    return {
        "recall": round(kept / total, 4),
        "oracle_tokens": total,
        "missing_tokens": total - kept,
        "missing_sample": [t for t, _ in missing[:20]],
    }


class _UnitList(list):
    """A list of units that also reports how many existed before the cap."""

    total = 0


def units_from_content(content: Dict[str, Any],
                       limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Turn a reader's output into located units, without inventing structure.

    Every unit names the container it came from, so a reader can point at the
    exact page, paragraph, cell, slide, member or box the text occupies.

    ``limit`` bounds how many unit dicts are built. A spreadsheet with 250 000
    cells produced 249 990 of them, and materialising that list next to the
    extracted text was enough to exhaust the child process: the record of a
    file that had been read successfully could not be written. The count is
    still reported (``units_total``), because a truncated list that does not
    say it is truncated is a lie about coverage.
    """
    units = _UnitList()
    total = [0]

    def add(kind: str, loc: str, text: Any, allow_empty: bool = False,
            **meta: Any) -> None:
        if isinstance(text, (int, float)):
            text = str(text)
        if not allow_empty and (not isinstance(text, str) or not text.strip()):
            return
        total[0] += 1
        if limit is not None and len(units) >= limit:
            return
        unit = {"kind": kind, "loc": loc, "chars": len(text), "text": text}
        if meta:
            unit["meta"] = meta
        units.append(unit)

    pages = content.get("pages")
    if isinstance(pages, list) and pages and isinstance(pages[0], dict):
        for i, page in enumerate(pages, 1):
            number = page.get("page_number", page.get("number", i))
            # An empty page is a measurement: it says the page exists and
            # carries no text this reader could reach. Dropping it would make a
            # scanned page indistinguishable from a page that is not there.
            add("page", f"page {number}", page.get("text"), allow_empty=True,
                width=page.get("width"), height=page.get("height"),
                rotation=page.get("rotation"), columns=page.get("columns"),
                method=page.get("method"))
            # Where on the page the text sits. A PDF page has no paragraphs, so
            # the block geometry is the only location record the format offers.
            for block in (page.get("blocks") or []):
                if not isinstance(block, dict):
                    continue
                add("pdf-block", f"page {number} block {block.get('index')}",
                    (block.get("text") or ""), allow_empty=True,
                    bbox=block.get("bbox"), column=block.get("column"),
                    dir=block.get("dir"), lines=block.get("lines"),
                    font_size=block.get("font_size"), block_type=block.get("type"))
            for table in (page.get("tables") or []):
                if not isinstance(table, dict):
                    continue
                add("pdf-table", f"page {number} table {table.get('table_number')}",
                    table.get("text"), rows=table.get("row_count"),
                    columns=table.get("column_count"), bbox=table.get("bbox"))

    sheets = content.get("sheets")
    sheet_list = []
    if isinstance(sheets, dict):
        for name, sheet in sheets.items():
            if isinstance(sheet, dict):
                sheet_list.append(dict(sheet, name=sheet.get("name") or name))
    elif isinstance(sheets, list):
        sheet_list = [s for s in sheets if isinstance(s, dict)]
    for sheet in sheet_list:
        name = sheet.get("name") or "?"
        rows = sheet.get("rows") or sheet.get("data") or []
        for ri, row in enumerate(rows, 1):
            if isinstance(row, (list, tuple)):
                for ci, cell in enumerate(row, 1):
                    add("cell", f"{name}!R{ri}C{ci}", cell)
            else:
                add("cell", f"{name}!R{ri}", row)

    elements = content.get("elements")
    if isinstance(elements, list) and elements and isinstance(elements[0], dict):
        for i, el in enumerate(elements, 1):
            kind = el.get("type") or "element"
            loc = (f"element {el.get('position', el.get('index', i))}"
                   f" ({kind})")
            if kind == "table" and isinstance(el.get("rows"), list):
                for ri, row in enumerate(el["rows"], 1):
                    add("table-row", f"{loc} row {ri}",
                        "\t".join(str(c) for c in row) if isinstance(row, list) else row)
            else:
                add(kind, loc, el.get("text"), style=el.get("style"))

    slides = content.get("slides")
    if isinstance(slides, list) and slides and isinstance(slides[0], dict):
        for i, slide in enumerate(slides, 1):
            n = slide.get("slide_number", slide.get("number", i))
            shapes = slide.get("elements") or slide.get("shapes") or \
                slide.get("texts") or []
            if shapes:
                for si, shape in enumerate(shapes, 1):
                    kind = shape.get("type") if isinstance(shape, dict) else "shape"
                    pos = (shape.get("position", si) if isinstance(shape, dict) else si)
                    text = shape.get("text") if isinstance(shape, dict) else shape
                    add("shape", f"slide {n} {kind} {pos}", text)
            else:
                add("slide", f"slide {n}", slide.get("text"))
            notes = slide.get("notes") or slide.get("speaker_notes")
            if isinstance(notes, str) and notes.strip():
                add("slide-notes", f"slide {n} notes", notes)

    members = content.get("members")
    if isinstance(members, list):
        for m in members:
            if isinstance(m, dict):
                add("member", m.get("name") or "member", m.get("text") or m.get("name"))
            else:
                add("member", str(m), str(m))

    coords = content.get("ocr_coordinates")
    if isinstance(coords, list) and coords:
        for i, box in enumerate(coords[:4000], 1):
            if isinstance(box, dict):
                text = box.get("text") or box.get("word") or ""
                bbox = box.get("bbox")
                if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                    loc = f"word {i} bbox={list(bbox)}"
                else:
                    loc = (f"word {i} at ({box.get('x')},{box.get('y')}) "
                           f"{box.get('width')}x{box.get('height')}")
                add("ocr-word", loc, text, confidence=box.get("confidence"))

    if not units:
        paragraphs = content.get("paragraphs")
        if isinstance(paragraphs, list):
            for i, p in enumerate(paragraphs, 1):
                add("paragraph", f"paragraph {i}",
                    p.get("text") if isinstance(p, dict) else p)
        tables = content.get("tables")
        if isinstance(tables, list):
            for ti, table in enumerate(tables, 1):
                rows = table.get("rows") if isinstance(table, dict) else table
                for ri, row in enumerate(rows or [], 1):
                    add("table-row", f"table {ti} row {ri}",
                        "\t".join(str(c) for c in row) if isinstance(row, list) else row)

    if not units:
        for key in ("text", "content", "raw_text", "body"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                add("document", "whole file", value)
                break

    units.total = total[0]
    return units


def probe(path: Path, svc, pipe, corpus: Path, timeout: int,
          oracle_reader=None) -> Dict[str, Any]:
    rel = path.relative_to(corpus).as_posix()
    ext = path.suffix.lower()
    rec: Dict[str, Any] = {
        "path": rel, "folder": rel.split("/")[0], "ext": ext,
        "bytes": path.stat().st_size,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def peak_memory_kb() -> int:
        """High-water mark of this process, in KiB.

        Not the file's own footprint: the harness reads files in one process, so
        this is a running maximum, and it is reported as an upper bound rather
        than per-file attribution. A true per-file figure needs a child process
        per file (see the backlog); a bound that is honest beats a number that
        is invented.
        """
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    import signal

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(timeout)
    try:
        rec["sha256"] = sha256_file(path)
        reader, decision = svc.resolve_reader_for_file(str(path), ext)
        rec["reader"] = reader.__class__.__name__ if reader else None
        rec["declared_ext"] = decision.get("declared_extension")
        rec["detected_ext"] = decision.get("detected_extension")
        rec["format_id"] = decision.get("format_id")
        rec["mime_type"] = decision.get("mime_type")
        rec["extension_mismatch"] = decision.get("extension_mismatch")

        t0 = time.perf_counter()
        result = svc.read_file({"path": str(path), "extension": ext}) or {}
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        if not isinstance(result, dict):
            result = {}

        if result.get("error") and not result.get("pages") and not result.get("text"):
            rec["error"] = str(result["error"])

        content = result.get("content") if isinstance(result, dict) and \
            isinstance(result.get("content"), dict) and "elements" not in result else result

        # The storage boundary is a stage of its own: it can fail (memory,
        # time) on text the reader produced happily, and a bare "MemoryError"
        # on the record would blame the reader for it. Measured on
        # Spreadsheets/testRecordSizeExceeded.xlsx.
        try:
            stored = pipe._extract_text_from_content(content) or ""
        except MemoryError:
            rec["storage_error"] = (
                "MemoryError in _extract_text_from_content: the reader produced "
                f"{len(rec.get('units') or [])} units of text that the storage "
                "text builder could not hold")
            stored = ""
        except Exception as exc:
            rec["storage_error"] = f"{type(exc).__name__}: {exc}"
            stored = ""
        rec["stored_chars"] = len(stored)
        rec["stored_sha256"] = sha256_text(stored)
        rec["lone_surrogates"] = len(re.findall(r"[\ud800-\udfff]", stored))
        rec["peak_rss_kb"] = peak_memory_kb()
        # Units are capped, with the total kept: a 250 000-cell sheet produced
        # 249 990 unit dicts, and materialising them alongside the 10 MB text
        # pushed the child past its address-space limit - the harness died
        # building its own report of a file it had read successfully.
        all_units = units_from_content(content, limit=UNITS_CAP)
        rec["units_total"] = getattr(all_units, "total", len(all_units))
        rec["units_truncated"] = rec["units_total"] > len(all_units)
        rec["units"] = list(all_units)
        rec.update(storage_boundary(content))
        rec["unit_kinds"] = dict(collections.Counter(u["kind"] for u in rec["units"]))

        if not rec.get("error") and rec.get("storage_error"):
            rec["error"] = rec["storage_error"]

        # Proxies are all the record needs from the reader's content from here
        # on; the dict itself can be gigabytes (a workbook whose cells are the
        # content), and holding it while the oracle and the token counters are
        # built is what exhausted the child on testRecordSizeExceeded.xlsx.
        proxies = ocr_proxies(content)
        content = None
        import gc

        gc.collect()

        o = oracle_for(path, ext)
        rec["oracle"] = {k: v for k, v in o.items() if k != "text"}
        # The oracle is a second full read of the same file, so it can run out
        # of memory even when the reader succeeded - measured on
        # Spreadsheets/testRecordSizeExceeded.xlsx, where a successful 10 MB
        # extraction was reported as ERROR because the comparison could not be
        # made. A measurement that could not be taken is not a reader failure.
        try:
            oracle_text = (oracle_reader or _oracle_text)(path, ext)
        except MemoryError:
            rec["oracle_error"] = "MemoryError while building the oracle"
            oracle_text = ""
        except Exception as exc:  # a broken oracle must not become a reader verdict
            rec["oracle_error"] = f"{type(exc).__name__}: {exc}"
            oracle_text = ""
        readings = oracle_text if isinstance(oracle_text, (list, tuple)) else [oracle_text]
        if any(len(r or "") > ORACLE_SAMPLE_CHARS for r in readings):
            readings = [(r or "")[:ORACLE_SAMPLE_CHARS] for r in readings]
            rec["oracle_sampled_chars"] = ORACLE_SAMPLE_CHARS
            rec["recall_scope"] = (f"oracle prefix of {ORACLE_SAMPLE_CHARS:,} characters; "
                                   "the stored side is complete")
        if not any((r or "").strip() for r in readings):
            if rec.get("error"):
                rec["verdict"] = "ERROR"
            elif not stored.strip():
                rec["verdict"] = "EMPTY-PLAUSIBLE"
            else:
                if proxies.get("kind") == "ocr":
                    rec["verdict"] = "UNVERIFIABLE"
                    rec["proxies"] = proxies
                else:
                    rec["verdict"] = "OK-NO-ORACLE"
        else:
            # the oracle text is re-derived from the file, never stored;
            # score the (possibly sampled) readings, not the full text
            try:
                rec.update(recall(readings, stored))
            except MemoryError:
                rec["oracle_error"] = ("MemoryError while scoring: the file is "
                                       "too large to tokenise on both sides")
                rec["verdict"] = "OK-NO-ORACLE"
                rec["text"] = stored
                return rec
            r = rec.get("recall")
            if rec.get("error"):
                rec["verdict"] = "ERROR"
            elif r is None:
                rec["verdict"] = "OK-NO-ORACLE" if stored.strip() else "EMPTY-AGREED"
            elif r >= 0.995:
                rec["verdict"] = "COMPLETE"
            elif r >= 0.95:
                rec["verdict"] = "NEAR-COMPLETE"
            elif r >= 0.75:
                rec["verdict"] = "PARTIAL"
            else:
                rec["verdict"] = "INCOMPLETE"
        rec["text"] = stored
    except Timeout:
        rec["error"] = f"timeout after {timeout}s"
        rec["verdict"] = "TIMEOUT"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc(limit=2)
        rec["verdict"] = "ERROR"
    finally:
        import signal

        signal.alarm(0)
    rec.setdefault("verdict", "ERROR")
    return rec


#: Word at a line end broken by a hyphen. The product's PDF reader rejoins
#: these (correctly - "peri-\nodic" is one word in the document); if the oracle
#: did not do the same, every join would be scored as two lost tokens.
_ORACLE_BREAK_RE = re.compile(r"(?<=[^\W\d_])\-[ \t]*\n[ \t]*(?=[^\W\d_])", re.UNICODE)

#: Presentation ligatures ("\ufb01le"), which no search finds as "file".
_ORACLE_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
                     "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}


def normalise_for_recall(text: str) -> str:
    """Orthographic normalisation only - never content removal.

    Both sides of the comparison get the same treatment, so the score measures
    whether text survived, not whether two tools agree about hyphenation.
    """
    for ligature, letters in _ORACLE_LIGATURES.items():
        text = text.replace(ligature, letters)
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    return _ORACLE_BREAK_RE.sub("", text)


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _ooxml_runs(xml: str, namespace: str) -> List[str]:
    """Text of every ``<t>`` run under ``namespace``, tolerant of bad XML.

    A regex over ``<w:t>(.*?)</w:t>`` is only safe on well-formed input: this
    corpus contains a Word document with an unclosed run, and the regex happily
    swallowed the following markup into the "text", so the oracle counted
    attribute names (``smallCaps``, ``hAnsi``, ``Arial``) as document words and
    every extraction looked lossy.

    ElementTree is authoritative, so it is tried first; a part that will not
    parse falls back to the regex with any residual markup removed.
    """
    import xml.etree.ElementTree as ET

    prefix = "w" if "wordprocessingml" in namespace else "a"
    paragraph_tag = namespace + "p"

    def join_paragraph(runs) -> str:
        # Runs inside one paragraph concatenate with *no* separator: the spaces
        # a document has are inside its runs. Joining them with a space
        # fabricates word breaks - testWORD_various.docx formats a single word
        # as <w:t>ita</w:t><w:t>li</w:t><w:t>c</w:t> - and every such document
        # then looked lossy because the oracle held "ita", "li", "c" where the
        # reader correctly stored "italic".
        return "".join(runs)

    try:
        root = ET.fromstring(xml)
        # A text box anchored in a paragraph carries its own w:p inside a
        # w:txbxContent subtree. Counting its runs as part of the anchoring
        # paragraph as well stores every boxed caption twice.
        box_tag = namespace + "txbxContent"

        def paragraph_text(paragraph) -> str:
            collected: List[str] = []

            def collect(node) -> None:
                for child in node:
                    if child.tag == box_tag:
                        continue
                    if child.tag == namespace + "t":
                        collected.append(child.text or "")
                    collect(child)

            collect(paragraph)
            return join_paragraph(collected)

        paragraphs: List[str] = []

        def walk(node, inside_paragraph: bool = False) -> None:
            for child in node:
                if child.tag == paragraph_tag:
                    if not inside_paragraph:
                        paragraphs.append(paragraph_text(child))
                    walk(child, True)
                elif child.tag == box_tag:
                    walk(child, False)
                else:
                    walk(child, inside_paragraph)

        walk(root)
        if not paragraphs:
            return [node.text or "" for node in root.iter(namespace + "t")]
        return paragraphs
    except ET.ParseError:
        pattern = re.compile(r"<{0}:t(?:\s[^>]*)?>(.*?)</{0}:t>".format(prefix), re.S)
        paragraphs = re.split(r"</{0}:p>".format(prefix), xml)
        out = []
        for paragraph in paragraphs:
            runs = [re.sub(r"<[^>]*>", "", capture)
                    for capture in pattern.findall(paragraph)]
            if runs:
                out.append("".join(runs))
        return out or [re.sub(r"<[^>]*>", "", capture)
                       for capture in pattern.findall(xml)]


def ocr_proxies(content: dict) -> dict:
    """What *can* be said about text no oracle can check.

    A scanned page has no ground truth: the only honest verdict is
    UNVERIFIABLE. Reporting OK-NO-ORACLE for it claimed the extraction was fine
    because nothing objected. The proxies below are what evidence exists -
    coverage (pages that produced text), the engine's own confidence, and how
    much text per page - together with how it could actually be verified.
    """
    pages = content.get("pages") if isinstance(content, dict) else None
    if not isinstance(pages, list) or not pages:
        text = str(content.get("text") or "") if isinstance(content, dict) else ""
        if not text.strip():
            return {}
        return {"kind": "text", "chars": len(text),
                "how_to_verify": "second reader or human transcription"}

    ocr_pages = [p for p in pages if "ocr" in str(p.get("method") or "")]
    confidences = [float(p["ocr_confidence"]) for p in pages
                   if isinstance(p.get("ocr_confidence"), (int, float))]
    with_text = [p for p in pages if str(p.get("text") or "").strip()]
    return {
        "kind": "ocr",
        "pages": len(pages),
        "ocr_pages": len(ocr_pages),
        "pages_with_text": len(with_text),
        "coverage": round(len(with_text) / len(pages), 3) if pages else 0.0,
        "chars_per_page": round(
            sum(len(str(p.get("text") or "")) for p in pages) / len(pages), 1),
        "mean_confidence": (round(sum(confidences) / len(confidences), 3)
                            if confidences else None),
        "engines": sorted({str(p.get("ocr_engine") or p.get("method") or "?")
                           for p in ocr_pages}),
        "how_to_verify": ("recognition of a scanned page cannot be compared with "
                          "an independent reading of the same file; verify with a "
                          "second engine on a sample, or human transcription"),
    }



def storage_consumed_keys() -> List[str]:
    """Keys of a reader's content dict that reach the stored text.

    Read off ``StoragePipeline._extract_text_from_content`` by walking its
    syntax tree, not by reading it by eye: the question "is this key stored?"
    has a mechanical answer, and a hand-kept list drifts from the code. The
    function's mapping is "key -> stored", so a captured key that is not in
    this set is text the reader produced and the pipeline discarded.
    """
    import ast

    source = (Path(__file__).resolve().parents[2] / "pipeline" / "storage_pipeline.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_extract_text_from_content":
            continue
        def is_content_name(expr) -> bool:
            # Only direct ``content`` access counts. Reader dicts are reached
            # through locals with other names; including every string constant
            # in the function (values such as 'Normal', 'SQLite') would turn
            # the "not persisted" list into an undercount of real loss.
            return isinstance(expr, ast.Name) and expr.id == "content"

        for sub in ast.walk(node):
            if isinstance(sub, ast.Compare):
                # ``'key' in content`` - the gate that decides whether a
                # content category is stored at all.
                if len(sub.comparators) == 1 and is_content_name(sub.comparators[0]) \
                        and isinstance(sub.left, ast.Constant) \
                        and isinstance(sub.left.value, str):
                    keys.add(sub.left.value)
            elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "get" and is_content_name(sub.func.value) \
                    and sub.args and isinstance(sub.args[0], ast.Constant) \
                    and isinstance(sub.args[0].value, str):
                keys.add(sub.args[0].value)
            elif isinstance(sub, ast.Subscript) and is_content_name(sub.value) \
                    and isinstance(sub.slice, ast.Constant) \
                    and isinstance(sub.slice.value, str):
                keys.add(sub.slice.value)
    return sorted(keys)


#: Computed once per process; empty when the source cannot be parsed.
STORAGE_KEYS: List[str] = []


def _storage_keys() -> set:
    """The consumed-key set, computed on first use.

    Computed lazily because there are two entry points - the in-process loop
    and the ``--one`` child - and a global only the first one initialised made
    every key look unpersisted in isolated runs.
    """
    global STORAGE_KEYS
    if not STORAGE_KEYS:
        STORAGE_KEYS = storage_consumed_keys()
    return set(STORAGE_KEYS)


def storage_boundary(content: Any) -> Dict[str, Any]:
    """Which of this file's content keys the storage layer consumes."""
    if not isinstance(content, dict):
        return {"content_keys": [], "keys_not_persisted": []}
    populated = sorted(k for k, v in content.items()
                       if v not in (None, "", [], {}, (), 0, False))
    consumed = _storage_keys()
    return {
        "content_keys": populated,
        "keys_not_persisted": [k for k in populated if k not in consumed],
    }


def _oracle_text(path: Path, ext: str) -> str:
    """Oracle text, kept out of the archive but used for the recall score."""
    try:
        if ext == ".pdf":
            import pymupdf

            with pymupdf.open(path) as doc:
                return normalise_for_recall("".join(p.get_text() for p in doc))
        if ext in (".docx", ".docm", ".dotx", ".dotm"):
            import zipfile

            # <w:t ...> only: a naive <w:t[^>]*> also matches <w:tbl>, which
            # put tag names into the oracle and made every DOCX look lossy.
            t_re = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
            # mc:Fallback repeats the whole DrawingML block (text boxes
            # included) for old readers. Counting it would ask the reader to
            # store every boxed caption twice.
            fallback_re = re.compile(r"<mc:Fallback>.*?</mc:Fallback>", re.S)
            with zipfile.ZipFile(path) as zf:
                parts = [n for n in zf.namelist()
                         if n == "word/document.xml"
                         or re.match(r"word/(header|footer|footnotes|endnotes|comments)\d*\.xml$", n)]
                runs = {n: _ooxml_runs(fallback_re.sub("", zf.read(n).decode("utf-8", "replace")), W_NS)
                        for n in sorted(parts)}
                # One reading, with Word's own semantics: runs inside a
                # paragraph abut (a word may be formatted in pieces), and
                # paragraphs are separated. A second, word-separated reading was
                # kept here once to tolerate readers that glued paragraphs
                # together; it also raised the score of a reader that *split*
                # words at formatting boundaries, which is the same defect seen
                # from the other side, so it is gone.
                return "\n".join("\n".join(r) for r in runs.values())
        if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    out.extend(str(v) for v in row if v is not None)
            wb.close()
            return " ".join(out)
        if ext == ".xls":
            import xlrd

            book = xlrd.open_workbook(str(path), on_demand=True)
            out = []
            for sheet in book.sheets():
                for ri in range(sheet.nrows):
                    out.extend(str(v) for v in sheet.row_values(ri) if v not in ("", None))
            book.release_resources()
            return " ".join(out)
        if ext in (".pptx", ".pptm", ".ppsx", ".potx"):
            import zipfile

            with zipfile.ZipFile(path) as zf:
                slides = [n for n in zf.namelist()
                          if re.match(r"ppt/slides/slide\d+\.xml$", n)]
                notes = [n for n in zf.namelist()
                         if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]
                return "\n".join("\n".join(_ooxml_runs(zf.read(n).decode("utf-8", "replace"), A_NS))
                                 for n in slides + notes)
        if ext in (".odt", ".ods", ".odp", ".ott", ".ots", ".otp", ".odg"):
            import zipfile

            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if "META-INF/manifest.xml" in names and b"encryption-data" in zf.read(
                        "META-INF/manifest.xml"):
                    # Ciphertext has no oracle. Decoding it anyway produced
                    # tokens such as "j7U" and scored a protected document as a
                    # total extraction loss, which is not a measurement of the
                    # reader at all.
                    return ""
                xml = zf.read("content.xml").decode("utf-8", "replace")
            # An index title *template* is the model LibreOffice regenerates the
            # heading from, not text the document shows: the rendered copy sits
            # next to it in text:index-title. Tag-stripping counted both and
            # made a reader that stores the visible one look lossy. Drop the
            # template, keep everything else.
            xml = re.sub(r"<text:index-title-template\b.*?</text:index-title-template>",
                         " ", xml, flags=re.S)
            # Inline formatting carries no whitespace: `1<text:span>er</text:span>`
            # reads as "1er", and inserting a space at the tag boundary split it
            # into two tokens the reader was then blamed for not storing.
            # Block-level boundaries do separate words, so they still become
            # a space. (``testODT-TIKA-6000.odt``: "1er cycle", twice.)
            xml = re.sub(r"<text:(?:span|a)\b[^>]*>|</text:(?:span|a)>", "", xml)
            xml = re.sub(r"<text:bookmark(?:-start|-end)?\b[^>]*/?>", "", xml)
            # XML escapes are markup, not content: a reader that stores
            # "l'apostrophe" was being marked down for the four tokens
            # `apos`, `quot`, `lt`, `gt` that the escape spelling produced.
            return html.unescape(re.sub(r"<[^>]+>", " ", xml))
        if ext in (".html", ".htm", ".xhtml"):
            from bs4 import BeautifulSoup

            # Decode the way a browser would: the page's own charset first.
            # These corpus pages are windows-1251; reading them as UTF-8 with
            # replacement characters left the oracle with 40 readable tokens
            # against 2 515 stored ones, so a perfectly good extraction scored
            # 0.80 and the "missing" tokens were bytes the oracle itself had
            # destroyed. The oracle keeps its own parser; only the decode is
            # shared with reality.
            raw = path.read_bytes()
            declared = re.search(rb"charset\s*=\s*['\"]?([a-zA-Z0-9_\-]+)", raw[:8192])
            candidates = []
            # A page's charset declaration can be wrong - the corpus contains a
            # file whose *point* is a misleading charset - so the reading used
            # comes from the shared decoder, with the declared charset kept as
            # a second reading and the scoring keeping the best. One declared
            # decode scored a good extraction at 0.018.
            try:
                from core.text_encoding import decode_bytes

                candidates.append(decode_bytes(raw, declared=None)[1])
            except Exception:
                pass
            if declared:
                candidates.append(declared.group(1).decode("ascii", "ignore"))
            candidates += ["utf-8", "utf-16", "cp1251", "cp1252", "latin-1"]
            readings = []
            for encoding in candidates:
                try:
                    decoded = raw.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    continue
                readings.append(BeautifulSoup(decoded, "html.parser").get_text(" "))
            if not readings:
                readings = [BeautifulSoup(raw.decode("utf-8", "replace"),
                                          "html.parser").get_text(" ")]
            return readings
        if ext in (".txt", ".md", ".py", ".csv", ".tsv", ".log", ".json", ".xml",
                   ".yml", ".yaml", ".ini", ".cfg"):
            # One reading is a guess about the encoding, and a plain text file
            # has no declaration to check the guess against. The corpus holds
            # cp866 and EBCDIC (cp500) files: read as cp1252 they are printable
            # nonsense, so a correct Cyrillic or EBCDIC extraction scored 0.0
            # against a single-reading oracle. Every candidate that decodes
            # without replacement characters becomes a reading; the scoring
            # keeps the best. The list is fixed and small, and the reader still
            # has to produce one complete reading of the file to match any of
            # them - this is a decoding question, not a free pass.
            raw = path.read_bytes()
            readings = []
            try:
                from core.text_encoding import decode_bytes

                detected, _ = decode_bytes(raw)
                readings.append(detected)
            except Exception:
                pass
            for enc in ("utf-8", "utf-16", "cp1252", "latin-1", "cp1251",
                        "cp866", "cp037", "cp500", "koi8-r"):
                try:
                    decoded = raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
                if "\ufffd" in decoded or not decoded.strip():
                    continue
                readings.append(decoded)
            return readings or [raw.decode("utf-8", "replace")]
        if ext == ".rtf":
            from striprtf.striprtf import rtf_to_text

            return rtf_to_text(path.read_text("latin-1", "replace"))
    except Exception:
        return ""
    return ""


#: Default ceiling for one child process, in MiB. The sandbox has 3.9 GiB and
#: the previous in-process harness died at 604/698 files with the process
#: resident set near the machine limit; a child that exceeds this is killed and
#: recorded rather than allowed to take the run down with it.
DEFAULT_MEMORY_MB = 2048

#: Exit code the child returns when it hit its own address-space limit.
MEMORY_EXIT = 9

#: Characters of oracle text scored per file. Token recall needs a Counter of
#: the oracle's words; on a 10 MB spreadsheet that Counter is large enough to
#: exhaust the child on its own (measured: MemoryError inside recall()). The
#: sample is the oracle's own prefix and the record says so, so a sampled score
#: is never mistaken for a whole-file one.
ORACLE_SAMPLE_CHARS = 2_000_000

#: Located units kept per record. The total is recorded separately, so a
#: truncated list is evidence of scale rather than a silent omission.
UNITS_CAP = 2000


def _limit_own_memory(memory_mb: int) -> None:
    """Cap this process's address space, so one file cannot exhaust the box."""
    if memory_mb <= 0:
        return
    try:
        import resource

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception as exc:  # never let the guard itself be the failure
        print(f"memory limit not applied: {exc}", file=sys.stderr, flush=True)


def run_one(rel: str, corpus: Path, out_file: Path, timeout: int,
            memory_mb: int) -> int:
    """Child entry point: audit a single file and write its record as JSON.

    Kept as its own process because reader libraries leak: three passes over
    the corpus each stopped near the same file count with the resident set at
    the machine limit, and the failure arrived as a bare process death with no
    traceback. A child process makes the leak harmless, makes the per-file peak
    memory a real measurement (``ru_maxrss`` of that process, not a running
    maximum of the harness), and turns a hang into a timeout the parent can
    record instead of a run that never finishes.
    """
    _limit_own_memory(memory_mb)
    from reader_file.services.file_reader_service import FileReaderService
    from pipeline.storage_pipeline import StoragePipeline

    path = (corpus / rel).resolve()
    try:
        rec = probe(path, FileReaderService(), object.__new__(StoragePipeline),
                    corpus, timeout)
    except MemoryError:
        out_file.write_text(json.dumps({
            "path": rel, "verdict": "ERROR", "memory_limited": True,
            "error": f"MemoryError under a {memory_mb} MiB address-space limit",
        }), encoding="utf-8")
        return MEMORY_EXIT
    payload = json.dumps(scrub_paths(scrub(rec)), ensure_ascii=False)
    out_file.write_text(payload, encoding="utf-8")
    return 0


def run_isolated(path: Path, corpus: Path, timeout: int, memory_mb: int,
                 script: Optional[str] = None) -> Dict[str, Any]:
    """Audit ``path`` in a child process; return its record or a failure record.

    Every way the child can fail becomes a record: a timeout, a killed process,
    a crash with a traceback, or a memory limit. A file that cannot be audited
    is a finding, never a missing row.
    """
    import subprocess
    import tempfile

    rel = path.relative_to(corpus).as_posix()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / "record.json"
        cmd = [sys.executable, script or __file__, "--one", rel,
               "--corpus", str(corpus), "--result", str(out_file),
               "--timeout", str(timeout), "--memory-mb", str(memory_mb)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=max(1, timeout + 15),
                                  cwd=str(corpus.parent))
        except subprocess.TimeoutExpired:
            return {
                "path": rel, "folder": rel.split("/")[0], "ext": path.suffix.lower(),
                "bytes": path.stat().st_size, "verdict": "TIMEOUT",
                "error": f"killed after {timeout + 15}s in a child process",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "isolated": True, "stored_chars": 0, "text": "",
            }
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        if out_file.exists():
            try:
                rec = json.loads(out_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                rec = {"path": rel, "verdict": "ERROR",
                       "error": f"child wrote unreadable JSON: {exc}"}
        else:
            tail = (proc.stderr or "").strip().splitlines()[-6:]
            rec = {
                "path": rel, "folder": rel.split("/")[0], "ext": path.suffix.lower(),
                "bytes": path.stat().st_size, "verdict": "ERROR",
                "error": (f"child exited {proc.returncode} without a record; "
                          + " | ".join(tail))[:2000],
                "returncode": proc.returncode,
                "memory_limited": proc.returncode in (MEMORY_EXIT, -9)
                or "MemoryError" in (proc.stderr or ""),
                "stored_chars": 0, "text": "",
            }
        rec.setdefault("elapsed_ms", elapsed)
        rec["isolated"] = True
        rec["wall_ms"] = elapsed
        return rec


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="test-doc")
    ap.add_argument("--out", default="audit_report/r15/extraction_archive")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated corpus paths")
    ap.add_argument("--skip", default=None,
                    help="comma-separated corpus paths to leave out; a file that "
                         "cannot be interrupted is recorded separately")
    ap.add_argument("--isolate", action="store_true",
                    help="audit each file in its own process, with a wall-clock "
                         "timeout and an address-space limit; failures become "
                         "TIMEOUT/ERROR records instead of ending the run")
    ap.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB,
                    help=f"address-space limit per child in --isolate mode "
                         f"(default {DEFAULT_MEMORY_MB})")
    ap.add_argument("--one", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--result", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    corpus = Path(args.corpus).resolve()
    if args.one:
        return run_one(args.one, corpus, Path(args.result), args.timeout,
                       args.memory_mb)
    out = Path(args.out).resolve()
    (out / "text").mkdir(parents=True, exist_ok=True)
    jsonl = out / "records.jsonl"

    done = set()
    if args.resume and jsonl.exists():
        done = {json.loads(l)["path"] for l in jsonl.open(encoding="utf-8")}

    files = sorted(p for p in corpus.rglob("*") if p.is_file())
    if args.only:
        wanted = {w.strip() for w in args.only.split(",")}
        files = [p for p in files if p.relative_to(corpus).as_posix() in wanted]
    if args.skip:
        skipped = {w.strip() for w in args.skip.split(",")}
        files = [p for p in files if p.relative_to(corpus).as_posix() not in skipped]
    files = [p for p in files if p.relative_to(corpus).as_posix() not in done]

    from reader_file.services.file_reader_service import FileReaderService
    from pipeline.storage_pipeline import StoragePipeline

    svc = FileReaderService()
    pipe = object.__new__(StoragePipeline)
    global STORAGE_KEYS
    STORAGE_KEYS = storage_consumed_keys()

    print(f"archiving {len(files)} files", flush=True)
    with jsonl.open("a", encoding="utf-8") as fh:
        for i, path in enumerate(files, 1):
            if args.isolate:
                rec = run_isolated(path, corpus, args.timeout, args.memory_mb)
            else:
                rec = probe(path, svc, pipe, corpus, args.timeout)
            fh.write(json.dumps(scrub_paths(scrub(rec)), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"[{i}/{len(files)}] {rec['verdict']:<16} "
                  f"recall={rec.get('recall')} units={len(rec.get('units', [])):4} "
                  f"{rec['path'][:60]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
