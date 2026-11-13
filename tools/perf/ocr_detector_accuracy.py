"""Compare RapidOCR detector input sizes on the deterministic corpus.

    python tools/perf/ocr_detector_accuracy.py <corpus_dir> <out.jsonl> 736,480,320 150

Ground truth is replayed from the generator's seeded draws
(image_ocr_bench.expected_words), so it is exact for the synthetic images only.
The input is what the reader sends: its _fast_preprocess output, RGB converted
to BGR. Each row records one image at one detector size: elapsed seconds, words
expected, words found (substring match after removing spaces and punctuation).

This is evidence for a decision, not a default change. Synthetic clean text does
not show how real small-type scans behave; run the same tool on real documents
before changing the detector size.
"""
import json
import os
import re
import sys
import time

import numpy as np
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from tools.perf.image_ocr_bench import expected_words, image_name  # noqa: E402
from reader_file.readers.read_img_fast import ImageFileReader  # noqa: E402
from rapidocr_onnxruntime import RapidOCR  # noqa: E402


def main():
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    corpus, out_path = sys.argv[1], sys.argv[2]
    sizes = [int(x) for x in sys.argv[3].split(",")]
    max_images = int(sys.argv[4])
    reader = ImageFileReader()
    indices = [i for i in range(10000) if i % 10 != 0][:max_images]
    engines = {s: RapidOCR(det_limit_side_len=s) for s in sizes}
    with open(out_path, "a", encoding="utf-8") as out:
        for index in indices:
            path = os.path.join(corpus, image_name(index))
            img = Image.open(path).convert("RGB")
            arr = np.array(img)
            processed = reader._fast_preprocess(arr, reader._get_libraries())
            bgr = np.array(Image.fromarray(processed).convert("RGB"))[:, :, ::-1]
            expected = expected_words(index)
            for size, engine in engines.items():
                t0 = time.perf_counter()
                raw, _ = engine(bgr)
                elapsed = time.perf_counter() - t0
                text = " ".join(item[1] for item in (raw or []) if isinstance(item, (list, tuple)) and len(item) >= 3)
                squashed = re.sub(r"[^a-z0-9]", "", text.lower())
                hit = sum(1 for w in expected if w.lower() in squashed)
                out.write(json.dumps({"index": index, "limit": size, "elapsed_s": round(elapsed, 4),
                                      "expected": len(expected), "hit": hit,
                                      "text": text[:300]}) + "\n")
                out.flush()


if __name__ == "__main__":
    main()
