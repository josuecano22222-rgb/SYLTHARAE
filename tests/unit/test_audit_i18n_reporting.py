"""The audit's i18n checks must not answer "0" when they cannot see their input.

``tools/audit/check_structure.py`` used to read
``audit_report/inventory/i18n_msgids.csv`` and, if that file was absent (it is
generated, not checked in), write an empty ``i18n_gaps.csv`` and print
``msgids with an incomplete catalogue: 0``. A release sign-off that quotes that
line would be certifying a measurement nobody ran.

Two behaviours are pinned here:

* a missing inventory is reported as **not measured**, with the generator named;
* the second axis - entries that exist and are empty or fuzzy - is measured
  straight from the catalogues, so there is always a real number.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.audit import check_structure
from tools.i18n import export_translation_gaps as gaps

ROOT = Path(__file__).resolve().parents[2]


def test_a_missing_inventory_is_not_a_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(check_structure, "ROOT", tmp_path)
    monkeypatch.setattr(check_structure, "OUT", tmp_path / "findings")
    (tmp_path / "findings").mkdir()

    assert check_structure.i18n_coverage() is None
    # The finding file still has its header, so a downstream sort cannot fail.
    rows = list(csv.reader((tmp_path / "findings" / "i18n_gaps.csv").open(encoding="utf-8")))
    assert rows == [["msgid", "used_in", "missing_from", "status"]]


def test_the_check_says_so_when_it_cannot_measure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_structure, "ROOT", tmp_path)
    monkeypatch.setattr(check_structure, "OUT", tmp_path / "findings")
    (tmp_path / "findings").mkdir()

    check_structure.main(["--only", "i18n"])
    printed = capsys.readouterr().out
    assert "not measured" in printed
    assert "build_inventories.py" in printed
    assert "msgids with an incomplete catalogue: 0" not in printed


def test_a_built_inventory_is_read_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(check_structure, "ROOT", tmp_path)
    monkeypatch.setattr(check_structure, "OUT", tmp_path / "findings")
    inventory = tmp_path / "audit_report" / "inventory"
    inventory.mkdir(parents=True)
    (tmp_path / "findings").mkdir()
    (inventory / "i18n_msgids.csv").write_text(
        "msgid,first_uses,catalogs,coverage\n"
        "Save,templates/a.html,\"en,ar\",gap\n"
        "Cancel,templates/b.html,\"en,ar,he,fa,hr\",ok\n",
        encoding="utf-8")

    rows = check_structure.i18n_coverage()
    assert rows == [("Save", "templates/a.html", "he,fa,hr")]


def test_the_catalogue_gap_is_measured_without_an_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(check_structure, "ROOT", tmp_path)   # no inventory at all

    counts = check_structure.catalogue_translation_gap()
    assert counts is not None
    assert set(counts) == set(gaps.LANGUAGES)
    for language, count in counts.items():
        assert count == len(gaps.collect(language)), language
        assert count > 0, language


def test_the_check_prints_the_catalogue_gap_it_measured(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(check_structure, "ROOT", tmp_path)
    monkeypatch.setattr(check_structure, "OUT", tmp_path / "findings")
    (tmp_path / "findings").mkdir()

    check_structure.main(["--only", "i18n"])
    printed = capsys.readouterr().out
    total = sum(len(gaps.collect(language)) for language in gaps.LANGUAGES)
    assert f"untranslated/fuzzy catalogue entries: {total}" in printed
    assert "TRANSLATION_HANDOFF.md" in printed
