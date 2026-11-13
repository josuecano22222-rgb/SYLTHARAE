"""Benchmark the image reader on a deterministic corpus, resumably.

    python tools/perf/image_ocr_bench.py --count 10000 --work /tmp/imgbench

What it does
------------
* Generates ``--count`` PNG images. Image ``i`` is a pure function of ``i``
  (seeded RNG), so every run and every machine sees the same bytes. One image
  in ten is below the reader's 50 px OCR floor, the rest carry rendered text.
* Runs ``ImageFileReader.read_file`` on each image, in name order.
* Appends one JSON line per image to ``<work>/results.jsonl`` and fsyncs it.
  A rerun reads that file, skips every image already recorded, and continues.
  A torn last line (the process was killed mid-write) is ignored and redone.
* Writes ``<work>/summary.json``: throughput, per-file percentiles, outcome
  counts, duplicate-content counts, and peak resident memory.

Keep the work directory outside the repository. The corpus is about 36 MB for
10,000 images and the results file grows with every run.

The numbers describe the machine they ran on. Nothing here is a claim about
corpora of other sizes; extrapolate only with a measured run at that size.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import resource
import statistics
import sys
import time
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

CORPUS_SUBDIR = "corpus"
RESULTS_FILE = "results.jsonl"
SUMMARY_FILE = "summary.json"
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def image_name(index: int) -> str:
    return f"img_{index:05d}.png"


def expected_words(index: int) -> List[str]:
    """Words drawn on text image ``index`` (ground truth for accuracy checks).

    Replays the seeded draws in ``generate_corpus`` in the same order. Only
    meaningful for text images (``index % 10 != 0``).
    """
    rnd = random.Random(index)
    rnd.randrange(160)  # width draw
    rnd.randrange(60)   # height draw
    words = [rnd.choice(WORDS) for _ in range(4)]
    return words + [str(index)]


def generate_corpus(directory: str, count: int) -> None:
    """Write ``count`` deterministic PNGs into ``directory`` (existing files kept)."""
    from PIL import Image, ImageDraw, ImageFont

    os.makedirs(directory, exist_ok=True)
    font = ImageFont.load_default()
    for index in range(count):
        path = os.path.join(directory, image_name(index))
        if os.path.exists(path):
            continue
        rnd = random.Random(index)
        if index % 10 == 0:
            width, height = 32 + rnd.randrange(10), 32
            img = Image.new("RGB", (width, height), (rnd.randrange(256), 0, 0))
        else:
            width, height = 320 + rnd.randrange(160), 120 + rnd.randrange(60)
            img = Image.new("RGB", (width, height), (255, 255, 255))
            text = " ".join(rnd.choice(WORDS) for _ in range(4)) + f" {index}"
            ImageDraw.Draw(img).text((8, 8 + rnd.randrange(20)), text, fill=(0, 0, 0), font=font)
        img.save(path, format="PNG")


def load_done(results_path: str) -> Dict[str, dict]:
    """Records already written, keyed by file name. A torn last line is dropped."""
    done: Dict[str, dict] = {}
    if not os.path.exists(results_path):
        return done
    with open(results_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # torn write from an interrupted run: redo this file
            if isinstance(record, dict) and "name" in record:
                done[record["name"]] = record
    return done


def classify(result: Optional[dict]) -> str:
    """Outcome of one ``read_file`` result, in the reader's own vocabulary."""
    if not result:
        return "error"
    info = result.get("extraction_info") or {}
    if info.get("skipped"):
        return "skipped"
    if result.get("error") or info.get("error"):
        return "error"
    if result.get("ocr_attempted"):
        return "ocr_text" if (result.get("text") or "").strip() else "ocr_empty"
    return "no_ocr"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    corpus_dir: str,
    work_dir: str,
    count: int,
    reader_read: Callable[[str], Optional[dict]],
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """Process the corpus, resuming from ``results.jsonl``. Returns the summary."""
    os.makedirs(work_dir, exist_ok=True)
    results_path = os.path.join(work_dir, RESULTS_FILE)
    done = load_done(results_path)
    names = [image_name(i) for i in range(count)]
    pending = [name for name in names if name not in done]

    started = time.perf_counter()
    with open(results_path, "a", encoding="utf-8") as handle:
        for position, name in enumerate(pending, 1):
            path = os.path.join(corpus_dir, name)
            t0 = time.perf_counter()
            try:
                digest = _sha256(path)
                result = reader_read(path)
                outcome = classify(result)
                text_chars = len((result or {}).get("text") or "")
                error = None
            except Exception as exc:  # one bad file must not stop the run
                digest, outcome, text_chars = _sha256(path) if os.path.exists(path) else "", "error", 0
                error = f"{type(exc).__name__}: {exc}"
            record = {
                "name": name,
                "sha256": digest,
                "outcome": outcome,
                "text_chars": text_chars,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            }
            if error:
                record["error"] = error[:300]
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            done[name] = record
            if on_progress:
                on_progress(position, len(pending))
    session_seconds = time.perf_counter() - started

    records = [done[name] for name in names if name in done]
    summary = summarize(records, session_seconds, len(pending))
    with open(os.path.join(work_dir, SUMMARY_FILE), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
    return summary


def summarize(records: List[dict], session_seconds: float, processed_now: int) -> dict:
    times = sorted(r["elapsed_ms"] for r in records)
    digests = Counter(r["sha256"] for r in records if r.get("sha256"))
    duplicate_files = sum(c for c in digests.values() if c > 1)
    summary = {
        "files_recorded": len(records),
        "processed_this_session": processed_now,
        "session_seconds": round(session_seconds, 2),
        "outcomes": dict(Counter(r["outcome"] for r in records)),
        "duplicate_content_files": duplicate_files,
        "duplicate_content_groups": sum(1 for c in digests.values() if c > 1),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }
    if times:
        summary["per_file_ms"] = {
            "p50": round(statistics.median(times), 2),
            "p95": round(times[min(len(times) - 1, int(len(times) * 0.95))], 2),
            "max": round(times[-1], 2),
        }
    if processed_now and session_seconds > 0:
        summary["files_per_second_this_session"] = round(processed_now / session_seconds, 3)
    return summary


def _reader_read_factory() -> Callable[[str], Optional[dict]]:
    from reader_file.readers.read_img_fast import ImageFileReader

    reader = ImageFileReader()
    return lambda path: reader.read_file({"path": path})


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--work", required=True, help="work directory (keep it outside the repo)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    corpus_dir = os.path.join(args.work, CORPUS_SUBDIR)
    generate_corpus(corpus_dir, args.count)

    def progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"{done}/{total}", flush=True)

    summary = run(corpus_dir, args.work, args.count, _reader_read_factory(), progress)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
