#!/usr/bin/env bash
# One command: audit the corpus, then generate the report from the archive.
#
#   tools/audit/run_audit.sh [OUTPUT_DIR] [CORPUS_DIR]
#
# Defaults: audit_report/r18 and test-doc. Every file is audited in its own
# child process with a wall-clock timeout and an address-space cap, so a
# reader that hangs, crashes or eats memory costs one record and not the run.
#
# Reproducing the round-17 numbers exactly:
#
#   PYTHON=/path/to/venv/bin/python tools/audit/run_audit.sh audit_report/r17/repro
#
# Requirements, recorded because a missing one changes the verdicts rather
# than merely the speed: pymupdf, openpyxl, python-docx, python-pptx, odfpy,
# beautifulsoup4, xlrd, pypdf, charset-normalizer, and - for OCR -
# rapidocr-onnxruntime with opencv-python-HEADLESS (the non-headless build
# needs libGL, which this image does not have; when it is installed instead,
# every scanned page silently loses its text).
set -euo pipefail

OUT="${1:-audit_report/r18}"
CORPUS="${2:-test-doc}"
PYTHON="${PYTHON:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TIMEOUT_PER_FILE="${TIMEOUT_PER_FILE:-180}"
MEMORY_MB="${MEMORY_MB:-2048}"

echo "corpus   : $CORPUS"
echo "output   : $OUT"
echo "limits   : ${TIMEOUT_PER_FILE}s per file, ${MEMORY_MB} MiB address space per child"
echo "python   : $PYTHON"

mkdir -p "$OUT/archive"

# Phase 1/7: the archive. --resume makes an interrupted run restartable; the
# record for a file that already has one is never rewritten, so the archive is
# append-only within a run and a re-run of the same directory is a no-op.
"$PYTHON" tools/audit/archive_extraction.py \
    --corpus "$CORPUS" \
    --out "$OUT/archive" \
    --isolate \
    --timeout "$TIMEOUT_PER_FILE" \
    --memory-mb "$MEMORY_MB" \
    --resume

# Phase 7: compress the archive (the deliverable is records.jsonl.gz; the
# summary numbers are computed from the uncompressed file).
gzip -9 -kf "$OUT/archive/records.jsonl"

echo "records  : $(wc -l < "$OUT/archive/records.jsonl")"
echo
echo "Next: generate the report from this archive, replacing <register>.json with"
echo "the register for this round:"
echo "  $PYTHON tools/audit/build_audit_report.py \\"
echo "      --records $OUT/archive/records.jsonl \\"
echo "      --register tools/audit/registers/<register>.json \\"
echo "      --out $OUT"
