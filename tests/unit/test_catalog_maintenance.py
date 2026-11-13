"""The catalog maintenance tool keeps the five entry states apart.

``scripts/catalog_maintenance.py`` is the only automated writer of the
translation catalogs, and the rule it exists to keep is that a *missing*
translation stays missing: English in the msgstr column is not a translation,
and counting it as one is how a page ships half in English (the failure mode
``tests/integration/test_translation_coverage.py`` guards for readers).

Every test works on a throwaway catalog under ``tmp_path``, so the shipped
catalogs are never touched by a unit run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import catalog_maintenance as maintenance  # noqa: E402

HEADER = '''msgid ""
msgstr ""
"Project-Id-Version: test\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"
'''


def _catalog(root: Path, body: str, lang: str = "ar") -> Path:
    path = root / "translations" / lang / "LC_MESSAGES" / "messages.po"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
    return path


def test_a_missing_translation_is_never_filled_with_english(tmp_path):
    """The msgid is not a translation: the entry stays visibly empty."""
    path = _catalog(tmp_path, 'msgid "Hello"\nmsgstr ""\n')
    report = maintenance.inspect("ar", tmp_path)
    assert report["empty"] == ["Hello"]
    assert report["restorable"] == []
    assert report["unreviewed"] == ["Hello"]

    # `--write` on the real tree would compile the shipped catalogs, so the
    # throwaway one is driven through the tool's own functions.
    restored, pruned = maintenance._write_catalog(
        "ar", restore=report["restorable"], root=tmp_path)
    assert (restored, pruned) == (0, 0)
    text = path.read_text(encoding="utf-8")
    assert 'msgid "Hello"\nmsgstr ""' in text, "the msgstr was not English-filled"
    assert "Hello" not in text.split('msgid "Hello"')[1].split("msgid")[0]


def test_an_obsolete_translation_returns_to_the_entry_it_was_reviewed_for(tmp_path):
    """A msgid that left and came back keeps its reviewed translation."""
    body = ('#, fuzzy\n'
            'msgid "Assigned At"\n'
            'msgstr "related data"\n'
            '\n'
            '#~ msgid "Assigned At"\n'
            '#~ msgstr "assignment date"\n')
    path = _catalog(tmp_path, body)
    report = maintenance.inspect("ar", tmp_path)
    assert report["fuzzy"] == ["Assigned At"]
    assert report["restorable"] == ["Assigned At"]

    restored, pruned = maintenance._write_catalog(
        "ar", restore=report["restorable"], root=tmp_path)
    assert (restored, pruned) == (1, 0)
    text = path.read_text(encoding="utf-8")
    assert 'msgstr "assignment date"' in text
    assert "related data" not in text, "the fuzzy carry-over was replaced"
    assert "#~ msgstr " in text, "catalog history was kept"
    assert maintenance.inspect("ar", tmp_path)["restorable"] == []


def test_a_translation_with_different_placeholders_is_left_for_a_human(tmp_path):
    """A restored string that drops ``%(n)s`` would print a broken sentence."""
    body = ('#, fuzzy\n'
            'msgid "%(n)s word record"\n'
            'msgstr "record"\n'
            '\n'
            '#~ msgid "%(n)s word record"\n'
            '#~ msgstr "word records"\n')
    path = _catalog(tmp_path, body)
    report = maintenance.inspect("ar", tmp_path)
    assert report["restorable"] == [], "placeholders disagree: not a valid restore"
    assert report["unreviewed"] == ["%(n)s word record"]
    maintenance._write_catalog("ar", restore=[], root=tmp_path)
    assert 'msgstr "record"' in path.read_text(encoding="utf-8")


def test_an_approved_translation_is_never_overwritten(tmp_path):
    body = ('msgid "Side"\n'
            'msgstr "janib"\n'
            '\n'
            '#~ msgid "Side"\n'
            '#~ msgstr "janib (old)"\n')
    path = _catalog(tmp_path, body)
    report = maintenance.inspect("ar", tmp_path)
    assert report["approved"] == 1 and report["restorable"] == []
    maintenance._write_catalog("ar", restore=report["restorable"], root=tmp_path)
    assert 'msgstr "janib"' in path.read_text(encoding="utf-8")


def test_artifact_entries_are_pruned_and_history_is_kept(tmp_path):
    body = ('msgid "msgid"\n'
            'msgid_plural "msgid_plural"\n'
            'msgstr[0] "side"\n'
            'msgstr[1] ""\n'
            '\n'
            'msgid "Search"\n'
            'msgstr "bahth"\n'
            '\n'
            '#~ msgid "Gone"\n'
            '#~ msgstr "dhahaba"\n')
    path = _catalog(tmp_path, body)
    report = maintenance.inspect("ar", tmp_path)
    assert report["artifacts"] == ["msgid"]

    restored, pruned = maintenance._write_catalog("ar", restore=[], root=tmp_path)
    assert (restored, pruned) == (0, 1)
    text = path.read_text(encoding="utf-8")
    assert 'msgid "msgid"' not in text and "msgid_plural" not in text
    assert 'msgstr "bahth"' in text, "an approved entry was left alone"
    assert "#~ msgid " in text, "the obsolete block survived the prune"
    assert maintenance.inspect("ar", tmp_path)["artifacts"] == []


def test_a_second_run_writes_nothing(tmp_path):
    body = ('msgid "msgid"\nmsgstr "side"\n'
            '\n'
            '#, fuzzy\n'
            'msgid "Assigned At"\n'
            'msgstr "related data"\n'
            '\n'
            '#~ msgid "Assigned At"\n'
            '#~ msgstr "assignment date"\n')
    path = _catalog(tmp_path, body)
    first = maintenance._write_catalog("ar", restore=["Assigned At"], root=tmp_path)
    assert first == (1, 1)
    snapshot = path.read_text(encoding="utf-8")

    report = maintenance.inspect("ar", tmp_path)
    second = maintenance._write_catalog("ar", restore=report["restorable"], root=tmp_path)
    assert second == (0, 0)
    assert path.read_text(encoding="utf-8") == snapshot


def test_a_missing_catalog_fails_instead_of_writing_nothing(tmp_path):
    with pytest.raises(SystemExit, match="no catalog at"):
        maintenance.inspect("he", tmp_path)


def test_a_catalog_with_no_plural_forms_is_refused(tmp_path):
    """``nplurals=0`` cannot hold a plural msgstr: refuse, do not guess."""
    path = _catalog(tmp_path, 'msgid "One"\nmsgstr "wadid"\n')
    path.write_text(path.read_text(encoding="utf-8").replace(
        "nplurals=2", "nplurals=0"), encoding="utf-8")
    with pytest.raises(SystemExit, match="malformed Plural-Forms header"):
        maintenance.inspect("ar", tmp_path)


@pytest.fixture(scope="module")
def extracted_identifiers(tmp_path_factory):
    """Every msgid a literal in ``core``/``services`` produces."""
    from babel.messages.frontend import ExtractMessages
    from babel.messages.pofile import read_po

    destination = tmp_path_factory.mktemp("pot") / "messages.pot"
    command = ExtractMessages()
    command.initialize_options()
    command.mapping_file = str(ROOT / "babel.cfg")
    command.keywords = ["_", "gettext", "ngettext", "lazy_gettext"]
    command.output_file = str(destination)
    command.input_dirs = [str(ROOT / "core"), str(ROOT / "services")]
    command.ignore_dirs = ("/__pycache__",)
    command.finalize_options()
    command.run()
    with destination.open("rb") as handle:
        return {message.id for message in read_po(handle, locale="en")
                if isinstance(message.id, str) and message.id}


def test_the_artifact_vocabulary_is_not_producible_as_a_literal(extracted_identifiers):
    """A prunable name must not be something a page can actually show.

    ``ARTIFACT_IDS`` names entries the tool deletes. If a literal in the sources
    ever produces that text, the name has to leave the list - otherwise the
    tool would delete a message the interface uses.
    """
    assert extracted_identifiers, "the extraction produced nothing to check against"
    still_literal = sorted(set(maintenance.ARTIFACT_IDS) & extracted_identifiers)
    assert still_literal == [], (
        f"{still_literal} is produced by a literal again: remove it from "
        "ARTIFACT_IDS in scripts/catalog_maintenance.py")
