#!/usr/bin/env python3
"""Measure translation throughput: wall time, peak RSS, and a CSV of the run.

What this measures and what it does not
=======================================

It measures *cost*: how long a batch of segments takes, how much memory the
process holds at its peak, and how the same input behaves cold and warm. It says
nothing about *quality*: no BLEU, no chrF, no human judgement, no comparison of
one engine's output against another's. A number here is "how expensive", never
"how good" - and with the fallback engine installed (see below) the output is
not a translation at all.

The two kinds of run
====================

``--dry-run`` (the default)
    Uses ``StubEngine``: a deterministic, dependency-free stand-in that uppercases
    each segment. It exists so the harness itself can be exercised on a machine
    with no model installed, and so the timing *code* is testable. A dry run
    proves the measurement works. It is not a translation benchmark and its
    numbers must never be reported as one.

``--engine <id>``
    Uses the real adapter from ``services/translation/engines.py`` (``argos``,
    ``opus-mt``, ``nllb``). If the adapter is not available the run stops and
    says why - it does not silently fall back, because a benchmark that quietly
    measures a stub is worse than no benchmark.

Peak RSS
========

``resource.getrusage(RUSAGE_SELF).ru_maxrss`` is the high-water mark for the
whole process, so it includes the interpreter and every import. On Linux it is
reported in kilobytes and converted to MB here. It is sampled before the engine
is loaded (``rss_before_mb``) and reported after (``peak_rss_mb``), so the
engine's footprint is visible as the difference.

CSV columns
===========

    run_id, engine, engine_version, dry_run, source_lang, target_lang,
    segments, chars, wall_seconds, segments_per_second, chars_per_second,
    peak_rss_mb, rss_before_mb, rss_delta_mb, memory_hits, engine_calls,
    started_at, notes

Usage
=====

    python tools/translation_benchmark.py --dry-run --csv /tmp/bench.csv
    python tools/translation_benchmark.py --engine argos --target ar --repeats 3

Exit codes: 0 measured, 2 the requested engine is unavailable (nothing was
measured), 3 bad input.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

COLUMNS = [
    "run_id", "engine", "engine_version", "dry_run", "source_lang", "target_lang",
    "segments", "chars", "wall_seconds", "segments_per_second", "chars_per_second",
    "peak_rss_mb", "rss_before_mb", "rss_delta_mb", "memory_hits", "engine_calls",
    "started_at", "notes",
]

#: A short, neutral sample. Deliberately not taken from any corpus in the
#: repository: the point is to time the machinery, not to celebrate a corpus.
SAMPLE_TEXT = (
    "The vessel arrived at the harbour before dawn.\n"
    "Customs officers inspected the manifest and the cargo list.\n"
    "Three containers were held for a second inspection.\n"
    "The shipping agent asked for a copy of the clearance certificate.\n"
    "A follow-up meeting was scheduled for the next morning.\n"
)


def peak_rss_mb() -> float:
    """High-water resident set size for this process, in MB."""
    # Linux reports kilobytes, macOS bytes. The project targets both, and the
    # difference is a factor of 1024 in the number a reader would trust.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(raw / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


class StubEngine:
    """Deterministic stand-in used by ``--dry-run``. Not a translation."""

    id = "stub"
    version = "stub-1"
    license = "n/a"
    document_safe = True
    non_commercial = False

    def availability(self):
        return True, None

    def translate(self, texts: Sequence[str], source_lang: str, target_lang: str) -> List[str]:
        return [text.upper() for text in texts]

    def describe(self) -> Dict:
        return {"id": self.id, "version": self.version, "license": self.license,
                "document_safe": True, "non_commercial": False,
                "available": True, "reason": None}


def _engine(engine_id: Optional[str], dry_run: bool):
    """``(engine, is_dry_run, refusal)`` - the adapter, or why there is none."""
    if dry_run or not engine_id:
        return StubEngine(), True, None
    from services.translation import engines as registry

    try:
        engine = registry.get_engine(engine_id)
    except registry.EngineUnavailable as exc:
        return None, False, str(exc)
    available, reason = engine.availability()
    if not available:
        return None, False, reason or f"{engine_id} is not available"
    return engine, False, None


def _memory(tmp_dir: Optional[Path]):
    """A throw-away translation memory, so a warm run cannot touch real data."""
    from services.translation.memory import TranslationMemory

    if tmp_dir is None:
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="tm_bench_"))
    return TranslationMemory(path=tmp_dir / "translation_memory.db")


def measure(engine, *, memory, text: str, source_lang: str, target_lang: str,
            run_id: str, dry_run: bool, notes: str = "") -> Dict:
    """Translate *text* once through the real service and record the cost."""
    from services.translation.service import TranslationService

    service = TranslationService(memory=memory, engine=engine)
    rss_before = peak_rss_mb()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    began = time.perf_counter()
    result = service.translate_text(text, target_lang=target_lang,
                                    source_lang=source_lang, actor="benchmark")
    wall = time.perf_counter() - began

    segments = result.get("segments") or []
    chars = sum(len(segment.get("source") or "") for segment in segments)
    return {
        "run_id": run_id,
        "engine": getattr(engine, "id", "?"),
        "engine_version": str(getattr(engine, "version", "?")),
        "dry_run": dry_run,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "segments": len(segments),
        "chars": chars,
        "wall_seconds": round(wall, 4),
        "segments_per_second": round(len(segments) / wall, 2) if wall else None,
        "chars_per_second": round(chars / wall, 1) if wall else None,
        "peak_rss_mb": peak_rss_mb(),
        "rss_before_mb": rss_before,
        "rss_delta_mb": round(peak_rss_mb() - rss_before, 1),
        "memory_hits": sum(1 for segment in segments if segment.get("from_memory")),
        "engine_calls": result.get("engine_calls", 0),
        "started_at": started_at,
        "notes": notes,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", help="real engine id: argos, opus-mt, nllb")
    parser.add_argument("--dry-run", action="store_true",
                        help="measure the harness with the stub engine (default)")
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="ar")
    parser.add_argument("--repeats", type=int, default=3,
                        help="runs per mode (cold, then warm from the memory)")
    parser.add_argument("--text-file", help="text to translate instead of the built-in sample")
    parser.add_argument("--csv", default="translation_benchmark.csv")
    parser.add_argument("--memory-dir", help="where the throw-away memory lives")
    parser.add_argument("--json", action="store_true", help="also print the rows as JSON")
    args = parser.parse_args(argv)

    if args.repeats < 1:
        print("error: --repeats must be at least 1", file=sys.stderr)
        return 3
    text = SAMPLE_TEXT
    if args.text_file:
        try:
            text = Path(args.text_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read --text-file: {exc}", file=sys.stderr)
            return 3

    engine, dry_run, refusal = _engine(args.engine, args.dry_run)
    if engine is None:
        print(f"NOT MEASURED: {args.engine} is unavailable: {refusal}", file=sys.stderr)
        print("Install the engine, or run with --dry-run to exercise the harness.",
              file=sys.stderr)
        return 2

    memory = _memory(Path(args.memory_dir) if args.memory_dir else None)
    mode = "DRY RUN (stub engine - NOT a translation)" if dry_run else f"engine={engine.id}"
    print(f"# translation benchmark - {mode}")
    print(f"# source={args.source_lang} target={args.target_lang} repeats={args.repeats}")
    if dry_run:
        print("# dry run: timings prove the measurement works; they are not a "
              "translation benchmark and say nothing about quality.")

    rows: List[Dict] = []
    for index in range(args.repeats):
        notes = "cold" if index == 0 else "warm (memory should answer)"
        row = measure(engine, memory=memory, text=text,
                      source_lang=args.source_lang, target_lang=args.target_lang,
                      run_id=f"{int(time.time())}-{index + 1}", dry_run=dry_run,
                      notes=notes)
        rows.append(row)
        print(f"  run {index + 1}: {row['wall_seconds']:.4f}s  "
              f"{row['segments_per_second']} seg/s  "
              f"{row['chars_per_second']} chars/s  "
              f"peak {row['peak_rss_mb']} MB (+{row['rss_delta_mb']})  "
              f"memory_hits={row['memory_hits']} engine_calls={row['engine_calls']}")

    walls = [row["wall_seconds"] for row in rows]
    print(f"# median wall {statistics.median(walls):.4f}s  "
          f"min {min(walls):.4f}s  max {max(walls):.4f}s  "
          f"peak RSS {max(row['peak_rss_mb'] for row in rows)} MB")

    csv_path = Path(args.csv)
    if csv_path.parent and not csv_path.parent.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)
    print(f"# csv: {csv_path.resolve()} ({len(rows)} row(s) appended)")

    if args.json:
        print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
