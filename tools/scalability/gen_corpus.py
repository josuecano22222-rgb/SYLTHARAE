#!/usr/bin/env python3
"""Generate a representative corpus for scalability measurement.

The point is *representativeness*, not volume: real ingestion corpora are
dominated by small files with a long tail of large ones, spread over a nested
directory tree, in a mix of formats.  Those are exactly the dimensions that
decide whether the pipeline scales, so each is a parameter here:

  * file count and size distribution (``--avg-bytes`` plus a power-law-ish
    spread, ``--large-count``/``--large-bytes`` for the tail);
  * directory nesting (``--dirs-per-level`` x ``--depth``);
  * format mix (``--formats``), including containers (``zip``) that produce
    nested work and PDFs that carry a text layer.

Usage:
    gen_corpus.py /tmp/corpus --files 40000 --dirs-per-level 5 --depth 4 \\
                  --avg-bytes 4096 --formats text,json,csv,binary,zip,pdf \\
                  --large-count 25 --large-bytes 4194304
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

FORMATS = ("text", "json", "csv", "binary", "zip", "pdf")

WORDS = (
    "ledger quarter report invoice payment balance transfer account register "
    "audit compliance retention archive correspondence schedule inventory "
    "shipment vendor contract amendment appendix summary total amount date "
    "reference identifier department analyst review approved pending closed"
).split()


def _text(rng: random.Random, size: int) -> bytes:
    parts = []
    total = 0
    while total < size:
        line = " ".join(rng.choice(WORDS) for _ in range(rng.randint(4, 14)))
        parts.append(line + "\n")
        total += len(line) + 1
    return "".join(parts).encode("utf-8")


def _json(rng: random.Random, size: int) -> bytes:
    records = []
    total = 0
    while total < size:
        record = {
            "id": rng.randint(1, 10 ** 9),
            "name": " ".join(rng.choice(WORDS) for _ in range(3)),
            "amount": round(rng.uniform(1, 100000), 2),
            "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "tags": [rng.choice(WORDS) for _ in range(3)],
        }
        chunk = json.dumps(record) + "\n"
        records.append(chunk)
        total += len(chunk)
    return "".join(records).encode("utf-8")


def _csv(rng: random.Random, size: int) -> bytes:
    rows = ["id,name,amount,date"]
    total = 0
    while total < size:
        row = (f"{rng.randint(1, 10 ** 9)},"
               f"{rng.choice(WORDS)},{rng.uniform(1, 10000):.2f},"
               f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}")
        rows.append(row)
        total += len(row) + 1
    return ("\n".join(rows) + "\n").encode("utf-8")


def _binary(rng: random.Random, size: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(min(size, 65536))) or b"\x00"


def _zip(rng: random.Random, size: int) -> bytes:
    """A small real archive with nested members (drives nested ingestion)."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(3):
            archive.writestr(f"member_{index}.txt",
                             _text(rng, max(256, size // 8)).decode("utf-8"))
    return buffer.getvalue()


def _pdf(rng: random.Random, size: int) -> bytes:
    """A minimal but valid PDF with a searchable text layer."""
    body = _text(rng, max(128, size // 4)).decode("latin-1", "replace")
    body = body.replace("\\", "").replace("(", "").replace(")", "")
    lines = body.splitlines()[:40]
    stream = "BT /F1 10 Tf 40 750 Td 12 TL\n" + "".join(
        f"({line[:90]}) Tj T*\n" for line in lines
    ) + "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = ["%PDF-1.4\n"]
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in out))
        out.append(f"{index} 0 obj\n{obj}\nendobj\n")
    xref_at = sum(len(part) for part in out)
    out.append(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n")
    for offset in offsets:
        out.append(f"{offset:010d} 00000 n \n")
    out.append(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
               f"startxref\n{xref_at}\n%%EOF\n")
    return "".join(out).encode("latin-1")


BUILDERS = {
    "text": _text,
    "json": _json,
    "csv": _csv,
    "binary": _binary,
    "zip": _zip,
    "pdf": _pdf,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--files", type=int, default=1000)
    ap.add_argument("--dirs-per-level", type=int, default=4)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--avg-bytes", type=int, default=2048)
    ap.add_argument("--large-count", type=int, default=0)
    ap.add_argument("--large-bytes", type=int, default=100 * 1024 * 1024)
    ap.add_argument("--formats", default="text,json,csv,binary")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    unknown = [f for f in formats if f not in BUILDERS]
    if unknown:
        raise SystemExit(f"unknown formats: {unknown}; known: {sorted(BUILDERS)}")

    # Build the directory tree first, then spread files across it, so nesting
    # is real rather than one flat directory with 40 000 entries.
    directories = [root]
    frontier = [root]
    for level in range(args.depth):
        next_frontier = []
        for parent in frontier:
            for index in range(args.dirs_per_level):
                child = parent / f"d{level}_{index}"
                child.mkdir(parents=True, exist_ok=True)
                directories.append(child)
                next_frontier.append(child)
        frontier = next_frontier

    written = 0
    total_bytes = 0
    started = __import__("time").time()
    for index in range(args.files):
        directory = directories[index % len(directories)]
        fmt = formats[index % len(formats)]
        size = max(16, int(rng.gauss(args.avg_bytes, args.avg_bytes / 2)))
        if size <= 0:
            size = 16
        payload = BUILDERS[fmt](rng, size)
        path = directory / f"f{index:08d}.{fmt if fmt != 'text' else 'txt'}"
        path.write_bytes(payload)
        written += 1
        total_bytes += len(payload)

    for index in range(args.large_count):
        directory = directories[index % len(directories)]
        payload = _text(rng, args.large_bytes)
        path = directory / f"large_{index:03d}.txt"
        path.write_bytes(payload)
        written += 1
        total_bytes += len(payload)

    print(json.dumps({
        "root": str(root),
        "files_written": written,
        "bytes_written": total_bytes,
        "build_seconds": round(__import__("time").time() - started, 2),
        "dirs": len(directories),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
