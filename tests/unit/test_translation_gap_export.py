"""The translation worksheets handed to the translators, and their freshness.

The four incomplete catalogues are a handoff, not a code defect: the entries are
legal, intelligence and safety wording, and filling them by machine would make
the coverage test green while degrading the product. What the release needs is
the *list*, in a shape a translator can work in, plus a guarantee that the list
is not a stale artifact nobody regenerates.

So the exporter is tested, not trusted:

* the committed worksheets still match the catalogues (``--check``), which is the
  same check CI can run after a catalog edit;
* every ``translation`` column is empty, in every row, for every language - the
  tool never translates, and never writes into ``translations/``;
* a fuzzy row carries its untrusted draft so a reviewer can see what changed, and
  a missing row carries none, so nobody mistakes an empty string for a draft;
* every row says where the string is used, because a translator cannot work from
  a msgid alone.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from tools.i18n import export_translation_gaps as export

ROOT = Path(__file__).resolve().parents[2]
FINDINGS = ROOT / "audit_report" / "findings"


def _rows(language: str) -> list[dict]:
    path = FINDINGS / f"i18n_untranslated_{language}.csv"
    assert path.exists(), f"{path.relative_to(ROOT)} is missing; run the exporter"
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _catalogue_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "translations").rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_the_worksheets_agree_with_the_catalogues():
    """Regenerate in the same commit that changes a catalogue."""
    assert export.main(["--check"]) == 0


def test_every_language_in_the_handoff_has_a_worksheet():
    for language in export.LANGUAGES:
        rows = _rows(language)
        assert rows, language
        assert {row["status"] for row in rows} <= {"missing", "fuzzy", "partial"}, language


def test_the_export_never_translates_anything():
    for language in export.LANGUAGES:
        for row in _rows(language):
            assert row["translation"] == "", (language, row["msgid"])


def test_the_export_never_writes_into_the_catalogues():
    before = _catalogue_hash()
    assert export.main(["--locale", "ar"]) == 0
    assert _catalogue_hash() == before


def test_a_fuzzy_row_shows_its_draft_and_a_missing_row_does_not():
    fuzzy = [row for row in _rows("ar") if row["status"] == "fuzzy"]
    missing = [row for row in _rows("ar") if row["status"] == "missing"]
    assert fuzzy and missing, "the ar catalogue changed shape; re-check the handoff"
    assert all(row["current_draft"].strip() for row in fuzzy)
    assert all(row["current_draft"] == "" for row in missing)


def test_every_row_says_where_the_string_is_used():
    for language in export.LANGUAGES:
        for row in _rows(language):
            assert row["sources"].strip(), (language, row["msgid"])
            assert row["module"].strip(), (language, row["msgid"])


def test_the_summary_counts_match_the_worksheets():
    summary = json.loads((FINDINGS / "i18n_untranslated_summary.json").read_text(encoding="utf-8"))
    assert summary["total"] == sum(len(_rows(language)) for language in export.LANGUAGES)
    for language, entry in summary["languages"].items():
        rows = _rows(language)
        assert entry["total"] == len(rows), language
        assert sum(entry["by_status"].values()) == len(rows), language


def test_the_croatian_catalogue_is_in_the_handoff_even_though_it_is_not_in_the_ui():
    """``hr`` is maintained but not selectable; its gap is still a translator's job.

    The reason lives in docs/TRANSLATION_HANDOFF.md (three options, none chosen
    here); the export must not quietly drop the locale because the UI cannot
    reach it.
    """
    rows = _rows("hr")
    assert rows
    assert "hr" in export.LANGUAGES


def test_the_exporter_rejects_an_unknown_locale():
    with pytest.raises(SystemExit):
        export.main(["--locale", "xx"])
