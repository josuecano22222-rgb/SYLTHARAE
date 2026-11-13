"""``tools/i18n/sync_taxonomy.py`` against a real PostgreSQL.

The tool is what makes the workbook authoritative: an operator edits
``classification/domain_data.xlsx``, runs the tool, and the database follows.
These tests exercise exactly the operations an operator can perform, in the
order they would perform them, and check the database afterwards - because the
plan printing "1 to create" is not evidence that a row exists.

Every test builds its own workbook in ``tmp_path`` with unique tokens, so the
session's shared database is safe and repeated runs cannot collide through a
leftover phrase.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

import openpyxl
import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.validators import workbook_rules as rules  # noqa: E402
from services.importing import taxonomy_sync as sync  # noqa: E402

pytestmark = pytest.mark.integration


def token() -> str:
    return uuid.uuid4().hex[:8]


def workbook_path() -> Path:
    """A new file per call, so a later read cannot see an earlier write."""
    return Path(tempfile.mkdtemp()) / "domain_data.xlsx"


def build(path: Path, sheets: dict, owners=None) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, content in sheets.items():
        sheet = book.create_sheet(name)
        sheet.cell(row=1, column=1, value=rules.TERM_HEADER)
        sheet.cell(row=1, column=2, value=rules.KEYWORD_HEADER)
        for row, term in enumerate(content.get("terms", []), start=2):
            sheet.cell(row=row, column=1, value=term)
        for row, keyword in enumerate(content.get("keywords", []), start=2):
            sheet.cell(row=row, column=2, value=keyword)
    if owners:
        sheet = book.create_sheet(rules.OWNERS_SHEET)
        sheet.cell(row=1, column=1, value="Phrase")
        sheet.cell(row=1, column=2, value="Category that owns it")
        for row, (phrase, owner) in enumerate(owners.items(), start=2):
            sheet.cell(row=row, column=1, value=phrase)
            sheet.cell(row=row, column=2, value=owner)
    book.save(path)
    return path


@pytest.fixture(scope="module")
def sync_db(pg_db):
    """A migrated database of this module's own.

    The tool truncates nothing and the tests create categories, so sharing the
    session database would make one test's leftovers another test's orphans -
    which is how a real bug gets masked by a stale row.
    """
    from database.bootstrap import bootstrap_database

    name = f"{pg_db['database']}_sync_taxonomy"
    admin = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                             password=pg_db["password"], dbname="postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    cfg = {key: pg_db[key] for key in ("host", "port", "user", "password")}
    cfg["database"] = name
    bootstrap_database(cfg)
    return cfg


@pytest.fixture()
def conn(sync_db):
    """A connection to that database, emptied before every test."""
    connection = psycopg2.connect(host=sync_db["host"], port=sync_db["port"],
                                  user=sync_db["user"], password=sync_db["password"],
                                  dbname=sync_db["database"])
    with connection.cursor() as cur:
        cur.execute("TRUNCATE words, categorys, keywords, words_categorys RESTART IDENTITY CASCADE")
    connection.commit()
    yield connection
    connection.close()


def sync_workbook(conn, path, **kwargs):
    workbook = rules.read(str(path), validate=True, **{"renames": kwargs.pop("renames", None)})
    assert workbook.ok, [str(v) for v in workbook.errors]
    plan = sync.build_plan(conn, workbook, **kwargs)
    written = sync.apply_plan(conn, workbook, plan, prune_words=kwargs.get("prune_words", False))
    conn.commit()
    return workbook, plan, written


def scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def category_id(conn, name):
    return scalar(conn, "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
                        " WHERE w.word = %s", (name,))


def terms_of(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT tw.word FROM words_categorys wc"
                    " JOIN categorys c ON c.id = wc.category_id"
                    " JOIN words cw ON cw.id = c.word_id"
                    " JOIN words tw ON tw.id = wc.word_id"
                    " WHERE cw.word = %s ORDER BY 1", (name,))
        return [row[0] for row in cur.fetchall()]


def keywords_of(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT keyword FROM keywords WHERE category_id = %s", (category_id(conn, name),))
        return {sync._decode_keyword(conn, blob) for (blob,) in cur.fetchall()}


def stored_keyword_count(conn, categories):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM keywords WHERE category_id IN"
                    " (SELECT id FROM categorys WHERE word_id IN"
                    "  (SELECT id FROM words WHERE word = ANY(%s)))", (categories,))
        return cur.fetchone()[0]


def category_names(conn, prefix):
    with conn.cursor() as cur:
        cur.execute("SELECT w.word FROM categorys c JOIN words w ON w.id = c.word_id"
                    " WHERE w.word LIKE %s ORDER BY 1", (f"{prefix}%",))
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
def test_a_new_worksheet_creates_a_category_with_its_content(conn):
    suffix = token()
    name = f"Zeta Taxonomy {suffix}"
    path = build(workbook_path(),
                 {name: {"terms": [f"Alpha{suffix}", f"Beta{suffix}"],
                         "keywords": [f"Gamma delta {suffix}"]}})
    _, plan, written = sync_workbook(conn, path)

    assert plan.create_categories == [name]
    assert written["categories_created"] == 1
    assert category_id(conn, name) is not None
    assert terms_of(conn, name) == [f"Alpha{suffix}", f"Beta{suffix}"]
    assert keywords_of(conn, name) == {f"Gamma delta {suffix}"}


def test_a_second_run_changes_nothing(conn):
    suffix = token()
    name = f"Zeta Idempotent {suffix}"
    path = build(workbook_path(),
                 {name: {"terms": [f"Alpha{suffix}"],
                         "keywords": [f"Gamma delta {suffix}"]}})
    sync_workbook(conn, path)

    workbook = rules.read(str(path))
    plan = sync.build_plan(conn, workbook)
    written = sync.apply_plan(conn, workbook, plan)
    assert written == {
        "categories_created": 0, "categories_deleted": 0, "categories_renamed": 0,
        "words_created": 0, "terms_linked": 0, "terms_unlinked": 0,
        "keywords_created": 0, "keywords_deleted": 0, "words_pruned": 0,
        "words_kept_for_names": 0,
    }
    assert plan.create_categories == [] and plan.link_terms == []
    assert plan.create_keywords == [] and plan.delete_keywords == []


def test_a_deleted_row_is_removed_from_the_database(conn):
    """Deleting a cell in Excel must delete the row in the database."""
    suffix = token()
    name = f"Zeta Deletions {suffix}"
    keep, drop = f"Alpha{suffix}", f"Beta{suffix}"
    phrase, gone = f"Gamma delta {suffix}", f"Epsilon zeta {suffix}"
    first = build(workbook_path(),
                  {name: {"terms": [keep, drop], "keywords": [phrase, gone]}})
    sync_workbook(conn, first)
    assert terms_of(conn, name) == sorted([keep, drop])
    assert keywords_of(conn, name) == {phrase, gone}

    second = build(workbook_path(),
                   {name: {"terms": [keep], "keywords": [phrase]}})
    _, plan, written = sync_workbook(conn, second)

    assert plan.unlink_terms == [(name, drop)]
    assert written["terms_unlinked"] == 1 and written["keywords_deleted"] == 1
    assert terms_of(conn, name) == [keep]
    assert keywords_of(conn, name) == {phrase}
    # the word row survives: the vocabulary is shared, only the link is removed
    assert scalar(conn, "SELECT count(*) FROM words WHERE word = %s", (drop,)) == 1


def test_a_renamed_worksheet_renames_the_category_and_keeps_its_content(conn):
    suffix = token()
    old, new = f"Zeta Old {suffix}", f"Zeta New {suffix}"
    term, phrase = f"Alpha{suffix}", f"Gamma delta {suffix}"
    first = build(workbook_path(),
                  {old: {"terms": [term], "keywords": [phrase]}})
    sync_workbook(conn, first)
    before = category_id(conn, old)

    second = build(workbook_path(),
                   {new: {"terms": [term], "keywords": [phrase]}})
    workbook = rules.read(str(second), renames={old: new})
    plan = sync.build_plan(conn, workbook, renames={old: new})
    assert plan.rename_categories == [(old, new)]
    assert plan.create_categories == [], "a rename must not create a second category"
    assert plan.link_terms == [] and plan.create_keywords == [], (
        "a rename must not re-link terms or re-create keywords")
    sync.apply_plan(conn, workbook, plan)
    conn.commit()

    assert category_id(conn, old) is None
    assert category_id(conn, new) == before, "the same row was renamed, not recreated"
    assert terms_of(conn, new) == [term]
    assert keywords_of(conn, new) == {phrase}


def test_a_category_missing_from_the_workbook_is_only_reported(conn):
    suffix = token()
    name = f"Zeta Keep {suffix}"
    path = build(workbook_path(),
                 {name: {"terms": [f"Alpha{suffix}"]}})
    sync_workbook(conn, path)

    empty = build(workbook_path(),
                  {f"Zeta Other {suffix}": {"terms": [f"Beta{suffix}"]}})
    workbook = rules.read(str(empty))
    plan = sync.build_plan(conn, workbook)
    assert name in [item for item in plan.missing_categories]
    assert plan.delete_categories == [], "deleting on absence needs --prune-categories"


def test_prune_categories_deletes_only_when_asked(conn):
    suffix = token()
    keep, drop = f"Zeta Prune Keep {suffix}", f"Zeta Prune Drop {suffix}"
    path = build(workbook_path(),
                 {keep: {"terms": [f"Alpha{suffix}"]},
                  drop: {"terms": [f"Beta{suffix}"]}})
    sync_workbook(conn, path)
    assert category_id(conn, drop) is not None

    smaller = build(workbook_path(),
                    {keep: {"terms": [f"Alpha{suffix}"]}})
    workbook = rules.read(str(smaller))
    plan = sync.build_plan(conn, workbook, prune_categories=True)
    assert plan.delete_categories == [drop]
    written = sync.apply_plan(conn, workbook, plan)
    conn.commit()
    assert written["categories_deleted"] == 1
    assert category_id(conn, drop) is None
    assert category_id(conn, keep) is not None


def test_a_word_used_by_two_categories_is_linked_to_both(conn):
    suffix = token()
    one, two = f"Zeta Link A {suffix}", f"Zeta Link B {suffix}"
    shared = f"Alpha{suffix}"
    path = build(workbook_path(),
                 {one: {"terms": [shared]}, two: {"terms": [shared]}})
    sync_workbook(conn, path)
    assert terms_of(conn, one) == [shared]
    assert terms_of(conn, two) == [shared]
    assert scalar(conn, "SELECT count(*) FROM words WHERE word = %s", (shared,)) == 1


def test_an_orphan_word_is_pruned_only_when_asked(conn):
    suffix = token()
    name = f"Zeta Orphan {suffix}"
    term = f"Alpha{suffix}"
    path = build(workbook_path(),
                 {name: {"terms": [term]}})
    sync_workbook(conn, path)

    empty = build(workbook_path(),
                  {name: {"terms": []}})
    workbook = rules.read(str(empty))
    plan = sync.build_plan(conn, workbook)
    assert sync.apply_plan(conn, workbook, plan)["words_pruned"] == 0
    assert scalar(conn, "SELECT count(*) FROM words WHERE word = %s", (term,)) == 1

    # once the link is gone the word is an orphan, and only then may it be pruned
    workbook = rules.read(str(empty))
    plan = sync.build_plan(conn, workbook, prune_words=True)
    assert term in plan.orphan_words
    assert sync.apply_plan(conn, workbook, plan, prune_words=True)["words_pruned"] >= 1
    conn.commit()
    assert scalar(conn, "SELECT count(*) FROM words WHERE word = %s", (term,)) == 0


def test_a_word_inside_a_keyword_phrase_is_not_pruned(conn):
    """Pruning must not rewrite another category's phrase.

    A keyword is stored as a list of word ids. A word that appears only inside
    a phrase still has a row; deleting it would silently corrupt the phrase.
    """
    suffix = token()
    name = f"Zeta Phrase Word {suffix}"
    only_in_phrase = f"omega{suffix}"
    path = build(workbook_path(),
                 {name: {"keywords": [f"Gamma {only_in_phrase} {suffix}"]}})
    sync_workbook(conn, path)

    empty = build(workbook_path(), {name: {}})
    workbook = rules.read(str(empty))
    plan = sync.build_plan(conn, workbook, prune_words=True)
    assert only_in_phrase not in plan.orphan_words
    sync.apply_plan(conn, workbook, plan, prune_words=True)
    conn.commit()
    assert scalar(conn, "SELECT count(*) FROM words WHERE word = %s", (only_in_phrase,)) == 1


def test_a_shared_phrase_is_stored_once_under_the_named_owner(conn):
    suffix = token()
    one, two = f"Zeta Shared A {suffix}", f"Zeta Shared B {suffix}"
    phrase = f"Gamma delta {suffix}"
    path = build(workbook_path(),
                 {one: {"keywords": [phrase]}, two: {"keywords": [phrase]}},
                 owners={phrase: two})
    workbook = rules.read(str(path))
    assert workbook.ok
    assert workbook.decisions and workbook.decisions[0].sheet == two

    plan = sync.build_plan(conn, workbook)
    sync.apply_plan(conn, workbook, plan)
    conn.commit()

    assert stored_keyword_count(conn, [one, two]) == 1
    assert keywords_of(conn, two) == {phrase}
    assert keywords_of(conn, one) == set()


def test_the_reported_counts_match_the_database(conn):
    """The dict the tool prints must equal what the tables gained."""
    suffix = token()
    name = f"Zeta Counts {suffix}"
    path = build(workbook_path(),
                 {name: {"terms": [f"Alpha{suffix}", f"Beta{suffix}"],
                         "keywords": [f"Gamma delta {suffix}"]}})
    before = {
        "words": scalar(conn, "SELECT count(*) FROM words"),
        "keywords": scalar(conn, "SELECT count(*) FROM keywords"),
        "links": scalar(conn, "SELECT count(*) FROM words_categorys"),
        "categories": scalar(conn, "SELECT count(*) FROM categorys"),
    }
    _, _, written = sync_workbook(conn, path)
    after = {
        "words": scalar(conn, "SELECT count(*) FROM words"),
        "keywords": scalar(conn, "SELECT count(*) FROM keywords"),
        "links": scalar(conn, "SELECT count(*) FROM words_categorys"),
        "categories": scalar(conn, "SELECT count(*) FROM categorys"),
    }
    assert after["categories"] - before["categories"] == written["categories_created"]
    assert after["links"] - before["links"] == written["terms_linked"] - written["terms_unlinked"]
    assert after["keywords"] - before["keywords"] == written["keywords_created"] - written["keywords_deleted"]
    # a new category contributes its own words (its name) plus the two terms
    assert after["words"] - before["words"] == written["words_created"]
    assert written["words_created"] >= 3


def test_a_lowercase_row_is_reused_rather_than_duplicated(conn):
    """The importer lowercases; the sync tool keeps the workbook's spelling.

    ``words.word`` is UNIQUE case-sensitively, so matching exactly would make a
    capitalised worksheet term insert a second row beside an existing lowercase
    one - and, because ``categorys.word_id`` is a different word id, a second
    category with the same name. The stored row is reused instead.
    """
    suffix = token()
    name = f"Zeta Case {suffix}"
    term = f"Alpha{suffix}"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES (%s)", (term.lower(),))
    conn.commit()

    path = build(workbook_path(), {name: {"terms": [term]}})
    _, plan, written = sync_workbook(conn, path)

    assert plan.create_words == [], "the plan already knew the word exists"
    assert scalar(conn, "SELECT count(*) FROM words WHERE lower(word) = lower(%s)",
                  (term,)) == 1
    # a category stores its whole name as one word row, so the only new word
    # is that name; the term was reused
    assert written["words_created"] == 1, "the term was inserted a second time"
    with conn.cursor() as cur:
        cur.execute("SELECT tw.word FROM words_categorys wc"
                    " JOIN categorys c ON c.id = wc.category_id"
                    " JOIN words cw ON cw.id = c.word_id"
                    " JOIN words tw ON tw.id = wc.word_id WHERE cw.word = %s", (name,))
        assert [row[0] for row in cur.fetchall()] == [term.lower()], (
            "the link points at the stored spelling")


def test_the_same_workbook_twice_creates_one_category_per_case(conn):
    """A category name in another casing must not become a second category."""
    suffix = token()
    path = build(workbook_path(), {f"Zeta Cased {suffix}": {"terms": [f"Alpha{suffix}"]}})
    sync_workbook(conn, path)

    lower = build(workbook_path(),
                  {f"zeta cased {suffix}": {"terms": [f"alpha{suffix}"]}})
    workbook = rules.read(str(lower))
    plan = sync.build_plan(conn, workbook)
    assert plan.create_categories == []
    assert plan.rename_categories == [], "a casing change is not a rename by itself"


# ---------------------------------------------------------------------------
# renames inferred from content, and the ability to undo a write
# ---------------------------------------------------------------------------
def test_a_renamed_sheet_with_identical_content_is_one_rename(conn):
    """A rename that the content proves is applied without being stated.

    Without this, an operator who renames a worksheet gets an empty new
    category and a report that the old one is missing - the content moves
    nowhere, and the database keeps a category the file no longer describes.
    """
    suffix = token()
    old, new = f"Zeta Was {suffix}", f"Zeta Now {suffix}"
    term, phrase = f"Alpha{suffix}", f"Gamma delta {suffix}"
    sync_workbook(conn, build(workbook_path(),
                              {old: {"terms": [term], "keywords": [phrase]}}))

    renamed = build(workbook_path(), {new: {"terms": [term], "keywords": [phrase]}})
    workbook = rules.read(str(renamed))
    assert sync.detect_renames(conn, workbook) == {old: new}

    plan = sync.build_plan(conn, workbook, auto_renames=True)
    assert plan.rename_categories == [(old, new)]
    assert plan.create_categories == [] and plan.link_terms == []


def test_a_rename_that_also_changes_content_is_left_to_the_operator(conn):
    """Content decides only when it is identical; otherwise it is a decision.

    A category that lost a term has a different content set, so "renamed" and
    "deleted, and a new one added" are both consistent with what is stored.
    Guessing here would move rows on a hunch.
    """
    suffix = token()
    old, new = f"Zeta Moved {suffix}", f"Zeta Elsewhere {suffix}"
    term = f"Alpha{suffix}"
    sync_workbook(conn, build(workbook_path(), {old: {"terms": [term]}}))

    changed = build(workbook_path(),
                    {new: {"terms": [term, f"Beta{suffix}"]}})
    workbook = rules.read(str(changed))
    assert sync.detect_renames(conn, workbook) == {}

    plan = sync.build_plan(conn, workbook, auto_renames=True)
    assert plan.rename_categories == []
    assert plan.create_categories == [new]
    assert plan.missing_categories == [old]


def test_two_worksheets_with_the_same_content_are_not_a_pairing(conn):
    """Ambiguity is not resolved by guessing: two candidates is a decision."""
    suffix = token()
    old = f"Zeta Single {suffix}"
    term = f"Alpha{suffix}"
    sync_workbook(conn, build(workbook_path(), {old: {"terms": [term]}}))

    copied = build(workbook_path(), {
        f"Zeta Copy One {suffix}": {"terms": [term]},
        f"Zeta Copy Two {suffix}": {"terms": [term]},
    })
    workbook = rules.read(str(copied))
    assert sync.detect_renames(conn, workbook) == {}


def test_an_empty_stored_category_is_never_matched_by_content(conn):
    """Every empty category has the same content, so it matches everything."""
    suffix = token()
    old, new = f"Zeta Empty {suffix}", f"Zeta Filled {suffix}"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (old,))
        word_id = cur.fetchone()[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s)", (word_id,))
    conn.commit()

    path = build(workbook_path(), {new: {"terms": [f"Alpha{suffix}"]}})
    workbook = rules.read(str(path))
    assert sync.detect_renames(conn, workbook) == {}


def test_an_applied_change_can_be_undone_from_its_snapshot(conn):
    """Automatic propagation has to be reversible, or it is a liability.

    The snapshot the tool takes before writing is put back here, and the two
    states are compared as data: same categories, same terms, same phrases.
    """
    suffix = token()
    keep, doomed = f"Zeta Keep {suffix}", f"Zeta Doomed {suffix}"
    term, phrase = f"Alpha{suffix}", f"Gamma delta {suffix}"
    first = build(workbook_path(), {
        keep: {"terms": [term], "keywords": [phrase]},
        doomed: {"terms": [f"Beta{suffix}"]},
    })
    sync_workbook(conn, first)
    before = sync.export_state(conn)

    second = build(workbook_path(), {keep: {"terms": [], "keywords": []}})
    workbook = rules.read(str(second))
    plan = sync.build_plan(conn, workbook, prune_categories=True)
    sync.apply_plan(conn, workbook, plan)
    conn.commit()
    after = sync.export_state(conn)
    assert after != before, "the edit did nothing, so the restore proves nothing"

    sync.restore_state(conn, before)
    conn.commit()
    restored = sync.export_state(conn)

    def shape(state):
        return {name: (sorted(content["terms"]),
                       sorted(item["text"] for item in content["keywords"]))
                for name, content in state["categories"].items()}

    assert shape(restored) == shape(before)


def test_the_single_entry_point_plans_without_writing_and_writes_when_asked(conn):
    suffix = token()
    name = f"Zeta Entry {suffix}"
    path = build(workbook_path(), {name: {"terms": [f"Alpha{suffix}"]}})

    planned = sync.sync_workbook(conn, str(path), apply=False)
    assert planned["ok"] and planned["applied"] is False
    assert planned["planned_changes"] > 0
    assert category_id(conn, name) is None, "a plan wrote to the database"

    written = sync.sync_workbook(conn, str(path), apply=True)
    assert written["applied"] is True and written["written"]["categories_created"] == 1
    assert category_id(conn, name) is not None
    assert "snapshot" in written, "an automatic write must be reversible"

    again = sync.sync_workbook(conn, str(path), apply=True)
    assert again["applied"] is False and again["reason"] == "nothing to do"


def test_a_workbook_that_breaks_a_rule_is_never_written(conn):
    path = build(workbook_path(), {"Broken": {"terms": ["Two words"]}})
    result = sync.sync_workbook(conn, str(path), apply=True)
    assert result["ok"] is False and result["applied"] is False
    assert result["errors"], "the refusal must say why"


def test_the_fingerprint_changes_only_when_the_file_does(conn, tmp_path):
    path = build(workbook_path(), {"Fingerprint": {"terms": ["Alpha"]}})
    first = sync.fingerprint(str(path))
    assert sync.fingerprint(str(path)) == first
    book = openpyxl.load_workbook(path)
    book["Fingerprint"].cell(row=3, column=1, value="Beta")
    book.save(path)
    assert sync.fingerprint(str(path)) != first
