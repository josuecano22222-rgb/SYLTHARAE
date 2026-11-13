"""The image benchmark harness: determinism, resume, torn writes, duplicates."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from tools.perf import image_ocr_bench as bench


def _stub(path):
    # Stands in for the real reader: no OCR, deterministic outcome per file.
    if os.path.basename(path) == "img_00003.png":
        raise RuntimeError("boom")
    return {"text": "", "ocr_attempted": False, "extraction_info": {}}


def test_the_corpus_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    bench.generate_corpus(str(a), 12)
    bench.generate_corpus(str(b), 12)
    for index in range(12):
        name = bench.image_name(index)
        assert bench._sha256(str(a / name)) == bench._sha256(str(b / name))


def test_a_rerun_skips_recorded_files_and_adds_only_new_ones(tmp_path):
    corpus = tmp_path / "corpus"
    work = tmp_path / "work"
    bench.generate_corpus(str(corpus), 12)

    first = bench.run(str(corpus), str(work), 5, _stub)
    second = bench.run(str(corpus), str(work), 12, _stub)

    assert first["processed_this_session"] == 5
    assert second["processed_this_session"] == 7
    assert second["files_recorded"] == 12
    lines = (work / bench.RESULTS_FILE).read_text(encoding="utf-8").splitlines()
    names = [json.loads(line)["name"] for line in lines]
    assert len(names) == len(set(names)) == 12


def test_a_torn_last_line_is_redone_not_trusted(tmp_path):
    corpus = tmp_path / "corpus"
    work = tmp_path / "work"
    bench.generate_corpus(str(corpus), 6)
    bench.run(str(corpus), str(work), 3, _stub)
    with open(work / bench.RESULTS_FILE, "a", encoding="utf-8") as handle:
        handle.write('{"name": "img_00004.png", "outc')  # killed mid-write

    summary = bench.run(str(corpus), str(work), 6, _stub)

    assert summary["files_recorded"] == 6
    assert summary["processed_this_session"] == 3  # 3, 4, 5 redone; 0-2 kept
    records = bench.load_done(str(work / bench.RESULTS_FILE))
    assert records["img_00004.png"]["outcome"] == "no_ocr"


def test_one_failing_file_is_recorded_and_the_run_continues(tmp_path):
    corpus = tmp_path / "corpus"
    work = tmp_path / "work"
    bench.generate_corpus(str(corpus), 6)

    summary = bench.run(str(corpus), str(work), 6, _stub)

    records = bench.load_done(str(work / bench.RESULTS_FILE))
    assert records["img_00003.png"]["outcome"] == "error"
    assert "RuntimeError: boom" in records["img_00003.png"]["error"]
    assert summary["outcomes"]["error"] == 1
    assert summary["outcomes"]["no_ocr"] == 5


def test_identical_content_is_counted_as_duplicates(tmp_path):
    corpus = tmp_path / "corpus"
    work = tmp_path / "work"
    bench.generate_corpus(str(corpus), 4)
    shutil.copyfile(corpus / bench.image_name(1), corpus / bench.image_name(2))

    summary = bench.run(str(corpus), str(work), 4, _stub)

    assert summary["duplicate_content_groups"] == 1
    assert summary["duplicate_content_files"] == 2


@pytest.mark.parametrize(
    "result, outcome",
    [
        (None, "error"),
        ({"extraction_info": {"skipped": True}}, "skipped"),
        ({"error": "x"}, "error"),
        ({"ocr_attempted": True, "text": "  "}, "ocr_empty"),
        ({"ocr_attempted": True, "text": "hi"}, "ocr_text"),
        ({"ocr_attempted": False, "text": ""}, "no_ocr"),
    ],
)
def test_outcomes_follow_the_reader_vocabulary(result, outcome):
    assert bench.classify(result) == outcome


def test_expected_words_match_the_text_the_generator_draws(tmp_path, monkeypatch):
    """The accuracy ground truth must replay the generator's own draws exactly."""
    from PIL import ImageDraw

    drawn = {}
    original_text = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.setdefault("texts", []).append(text)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    bench.generate_corpus(str(tmp_path), 30)

    text_indices = [i for i in range(30) if i % 10 != 0]
    assert len(drawn["texts"]) == len(text_indices)
    for index, text in zip(text_indices, drawn["texts"]):
        assert text == " ".join(bench.expected_words(index)), index
