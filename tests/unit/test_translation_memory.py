"""The translation memory: a local SQLite file, and what it must guarantee.

Translation memory is the one piece of this feature that is not derivable from
a query, so it does not live in PostgreSQL (schema freeze) - it is a SQLite
file under ``APP_DATA_DIR``, the same pattern as ``map_data/registry.db``.

Four guarantees, each a test below:

1. an exact-match reuse returns the stored translation without calling an
   engine;
2. a user correction wins over the engine, permanently - a later engine result
   for the same source does not overwrite it (``origin = 'user'``);
3. a glossary term is applied consistently to every segment of one request;
4. every row records which engine and which version produced it, so a
   translation can never be attributed to "the system".
"""

import sqlite3

import pytest

from services.translation.memory import TranslationMemory


@pytest.fixture()
def memory(tmp_path):
    return TranslationMemory(path=tmp_path / "translation_memory.db")


class StubEngine:
    id = "stub"
    version = "stub-1.0"
    license = "test"
    document_safe = True

    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    def availability(self):
        return True, None

    def translate(self, texts, source_lang, target_lang):
        self.calls.append(list(texts))
        return [self.table.get(text, f"[{target_lang}] {text}") for text in texts]


def test_the_memory_is_a_sqlite_file_under_the_given_path(tmp_path):
    path = tmp_path / "nested" / "translation_memory.db"
    TranslationMemory(path=path).remember(
        source_lang="en", target_lang="he", source_text="Hello.",
        target_text="שלום.", origin="engine", engine="stub", engine_version="stub-1.0")
    assert path.exists()
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tm_entries", "tm_glossary"} <= tables


def test_an_exact_match_is_reused_and_records_engine_and_version(memory):
    memory.remember(source_lang="en", target_lang="he", source_text="Good morning.",
                    target_text="בוקר טוב.", origin="engine",
                    engine="stub", engine_version="stub-1.0")
    hit = memory.lookup(source_lang="en", target_lang="he",
                        source_text="Good morning.")
    assert hit is not None
    assert hit["target_text"] == "בוקר טוב."
    assert hit["origin"] == "engine"
    assert hit["engine"] == "stub"
    assert hit["engine_version"] == "stub-1.0"
    assert hit["uses"] == 1           # the lookup counts as a use


def test_a_user_correction_overrides_the_engine_and_stays(memory):
    memory.remember(source_lang="en", target_lang="ar", source_text="Open the file.",
                    target_text="افتح الملف", origin="engine",
                    engine="stub", engine_version="stub-1.0")
    memory.correct(source_lang="en", target_lang="ar", source_text="Open the file.",
                   target_text="افتح هذا الملف", actor="analyst1")
    hit = memory.lookup(source_lang="en", target_lang="ar", source_text="Open the file.")
    assert hit["target_text"] == "افتح هذا الملف"
    assert hit["origin"] == "user"
    assert hit["actor"] == "analyst1"

    # The engine translating the same sentence again must not undo the
    # correction: remember() cannot replace a 'user' row.
    memory.remember(source_lang="en", target_lang="ar", source_text="Open the file.",
                    target_text="افتح الملف", origin="engine",
                    engine="stub", engine_version="stub-2.0")
    hit = memory.lookup(source_lang="en", target_lang="ar", source_text="Open the file.")
    assert hit["target_text"] == "افتح هذا الملف"
    assert hit["origin"] == "user"


def test_the_glossary_is_applied_consistently_to_every_segment(memory):
    memory.set_glossary_term(source_lang="en", target_lang="hr",
                             term="Source", translation="Izvor", actor="analyst1")
    memory.set_glossary_term(source_lang="en", target_lang="hr",
                             term="Side", translation="Strana", actor="analyst1")
    glossary = memory.glossary(source_lang="en", target_lang="hr")
    assert glossary == {"Source": "Izvor", "Side": "Strana"}

    first = memory.apply_glossary("The Source is open.", glossary)
    second = memory.apply_glossary("Each Side has a Source.", glossary)
    assert "Izvor" in first and "Source" not in first
    assert "Strana" in second and "Izvor" in second


def test_fuzzy_suggestions_are_a_separate_phase_not_a_lookup(memory):
    memory.remember(source_lang="en", target_lang="he",
                    source_text="The report was delivered yesterday.",
                    target_text="הדוח נמסר אתמול.", origin="engine",
                    engine="stub", engine_version="stub-1.0")
    # Not an exact match: lookup must not answer it.
    assert memory.lookup(source_lang="en", target_lang="he",
                         source_text="The report was delivered today.") is None

    suggestions = memory.suggest(source_lang="en", target_lang="he",
                                 source_text="The report was delivered today.",
                                 limit=3)
    assert suggestions, "a near-identical sentence should be suggested"
    best = suggestions[0]
    assert best["source_text"] == "The report was delivered yesterday."
    assert best["target_text"] == "הדוח נמסר אתמול."
    assert 0 < best["score"] < 1
    assert best["origin"] == "engine"


def test_the_memory_never_leaks_between_language_pairs(memory):
    memory.remember(source_lang="en", target_lang="he", source_text="Yes.",
                    target_text="כן.", origin="engine", engine="stub",
                    engine_version="stub-1.0")
    assert memory.lookup(source_lang="en", target_lang="ar", source_text="Yes.") is None
    assert memory.lookup(source_lang="hr", target_lang="he", source_text="Yes.") is None
