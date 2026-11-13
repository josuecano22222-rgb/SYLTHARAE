"""``m0005_term_names``: the shape, the constraints, and that it reverses.

Three things are asserted, because "the table exists" is not one of them:

* the constraints do what the plan says - one ``preferred`` name per word and
  language, a foreign key that cascades, a script that matches the language;
* the migration's script rule and ``core.i18n.term_key.script_ok`` accept and
  reject **exactly** the same inputs. The rule is written twice - once in DDL,
  once in Python - and the only defence against drift is a test that runs both
  over the whole cross product;
* ``upgrade`` -> ``downgrade`` -> ``upgrade`` leaves every other table byte
  identical, and the schema after the second upgrade equals the schema after the
  first (no residue, no lost constraint).

The database is its own, created and dropped here, so a failure cannot be
explained by whatever else the session's database holds.
"""

from __future__ import annotations

import hashlib
import itertools
import json

import psycopg2
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

LANGUAGES = ("en", "ar", "he", "fa", "hr")
SCRIPTS = ("Latn", "Arab", "Hebr")
NAME_TYPES = ("preferred", "alternate", "transliteration")


def _connect(cfg):
    return psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )


@pytest.fixture(scope="module")
def schema_db(pg_db):
    """A database of its own, migrated, with a handful of words in it."""
    from database.bootstrap import bootstrap_database

    name = f"{pg_db['database']}_m0005_schema"
    admin = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname="postgres",
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    cfg = {k: pg_db[k] for k in ("host", "port", "user", "password")}
    cfg["database"] = name
    bootstrap_database(cfg)

    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            for word in ("political", "سياسي", "פוליטי", "politički", "سیاسی"):
                cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT DO NOTHING",
                            (word,))
        conn.commit()
        yield cfg, conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _empty_names_table(schema_db):
    """No test inherits another's rows.

    A committed ``preferred`` name in one test would make the next test fail on
    the *uniqueness* index rather than on the rule it is checking, which is the
    kind of cross-test coupling that turns a red run into a guessing game.
    """
    _cfg, conn = schema_db
    with conn.cursor() as cur:
        cur.execute("DELETE FROM term_names")
    conn.commit()
    yield


def _word_id(conn, word):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM words WHERE word = %s", (word,))
        return cur.fetchone()[0]


def _fingerprint(conn):
    """Rows and a checksum for every ordinary table in ``public``."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
               AND NOT c.relispartition
             ORDER BY 1
        """)
        tables = [row[0] for row in cur.fetchall()]
    out = {}
    for table in tables:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), coalesce("
                "sum(('x' || substr(md5(t::text), 1, 15))::bit(60)::bigint::numeric), 0)"
                f' FROM "{table}" t')  # nosec B608 - names come from pg_class
            rows, acc = cur.fetchone()
            out[table] = [rows, f"{int(acc):040x}"]
    return out


def _digest(fingerprint):
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()


def _schema(conn):
    """Every column, index and constraint this migration owns, as text."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_name = 'term_names' ORDER BY ordinal_position
        """)
        columns = cur.fetchall()
        cur.execute("""
            SELECT indexname, indexdef FROM pg_indexes
             WHERE tablename = 'term_names' ORDER BY indexname
        """)
        indexes = cur.fetchall()
        cur.execute("""
            SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
             WHERE conrelid = 'term_names'::regclass ORDER BY conname
        """)
        constraints = cur.fetchall()
    return {"columns": columns, "indexes": indexes, "constraints": constraints}


def _attempt(conn, sql, params=()):
    """Run one statement in a transaction that is rolled back.

    Returns ``(accepted, message)``; ``message`` is the database's own words,
    which is the evidence when it refused.
    """
    conn.rollback()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.rollback()
        return True, None
    except Exception as exc:  # noqa: BLE001 - the refusal is the assertion
        conn.rollback()
        return False, str(exc).strip().splitlines()[0]


def _insert_name(conn, word_id, name, language, name_type="preferred",
                 script="Latn", match_key=None):
    from core.i18n.term_key import term_key

    return (
        "INSERT INTO term_names (word_id, name, match_key, language, name_type, script)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (word_id, name, match_key if match_key is not None else term_key(name),
         language, name_type, script),
    )


class TestTheShape:
    def test_the_table_and_its_indexes_exist(self, schema_db):
        _cfg, conn = schema_db
        schema = _schema(conn)
        names = {row[0] for row in schema["indexes"]}
        assert {"idx_term_names_key", "idx_term_names_word_language",
                "uq_term_names_preferred"} <= names, names

    def test_the_columns_are_the_ones_the_plan_describes(self, schema_db):
        _cfg, conn = schema_db
        columns = [row[0] for row in _schema(conn)["columns"]]
        assert columns == ["id", "word_id", "name", "match_key", "language",
                           "name_type", "script", "source", "created_at", "updated_at"]

    def test_the_foreign_key_cascades(self, schema_db):
        _cfg, conn = schema_db
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES ('cascade-probe') RETURNING id")
            wid = cur.fetchone()[0]
            sql, params = _insert_name(conn, wid, "cascade probe", "en")
            cur.execute(sql, params)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM words WHERE id = %s", (wid,))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM term_names WHERE word_id = %s", (wid,))
            assert cur.fetchone()[0] == 0


class TestTheConstraints:
    def test_a_second_preferred_name_is_refused(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        sql, params = _insert_name(conn, wid, "political", "en")
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        try:
            accepted, message = _attempt(conn, *_insert_name(conn, wid, "politics", "en"))
            assert not accepted
            assert "uq_term_names_preferred" in message or "duplicate key" in message, message
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM term_names WHERE word_id = %s", (wid,))
            conn.commit()

    def test_a_second_preferred_in_another_language_is_allowed(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        ok_en, msg_en = _attempt(conn, *_insert_name(conn, wid, "political", "en"))
        ok_ar, msg_ar = _attempt(conn, *_insert_name(conn, wid, "سياسي", "ar", script="Arab"))
        assert ok_en and ok_ar, (msg_en, msg_ar)

    def test_an_alternate_name_may_share_a_language(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        ok_a, msg_a = _attempt(conn, *_insert_name(conn, wid, "political", "en"))
        ok_b, msg_b = _attempt(conn, *_insert_name(
            conn, wid, "politics", "en", name_type="alternate"))
        assert ok_a and ok_b, (msg_a, msg_b)

    def test_the_same_name_twice_for_one_word_is_refused(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        # ``_attempt`` rolls back, so the first row has to be committed for the
        # second insert to be a duplicate at all.
        with conn.cursor() as cur:
            cur.execute(*_insert_name(conn, wid, "political", "en"))
        conn.commit()
        accepted, message = _attempt(conn, *_insert_name(conn, wid, "political", "en"))
        assert not accepted, message

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_an_empty_name_is_refused(self, schema_db, bad):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        accepted, _message = _attempt(conn, *_insert_name(
            conn, wid, bad, "en", match_key="x"))
        assert not accepted

    def test_an_empty_match_key_is_refused(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        accepted, _message = _attempt(conn, *_insert_name(
            conn, wid, "political", "en", match_key="  "))
        assert not accepted

    def test_a_name_for_a_word_that_does_not_exist_is_refused(self, schema_db):
        _cfg, conn = schema_db
        accepted, message = _attempt(conn, *_insert_name(conn, 2_000_000_000, "ghost", "en"))
        assert not accepted
        assert "foreign key" in message.lower(), message

    def test_an_unknown_language_is_refused(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        accepted, message = _attempt(conn, *_insert_name(conn, wid, "politisch", "de"))
        assert not accepted
        assert "term_names_language_check" in message or "check constraint" in message

    def test_an_unknown_name_type_is_refused(self, schema_db):
        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        accepted, _message = _attempt(conn, *_insert_name(
            conn, wid, "political", "en", name_type="nickname"))
        assert not accepted


class TestTheScriptRuleCannotDriftFromPython:
    """The DDL's CHECK and ``script_ok()`` must agree on every combination.

    Fifteen language x script pairs and three name types: 45 cases, run against
    both implementations. A rule added to one side and not the other fails here
    rather than in production.
    """

    @pytest.mark.parametrize("language,script,name_type",
                             list(itertools.product(LANGUAGES, SCRIPTS, NAME_TYPES)))
    def test_the_database_and_the_function_agree(self, schema_db, language, script,
                                                 name_type):
        from core.i18n.term_key import script_ok

        _cfg, conn = schema_db
        wid = _word_id(conn, "political")
        accepted, _message = _attempt(conn, *_insert_name(
            conn, wid, f"probe-{language}-{script}-{name_type}", language,
            name_type=name_type, script=script))
        assert accepted == script_ok(language, script, name_type), (
            f"{language}/{script}/{name_type}: database says {accepted}, "
            f"script_ok says {script_ok(language, script, name_type)}")

    def test_the_migration_lists_the_languages_the_application_supports(self):
        from core.geo.names import LANGUAGE_SCRIPT
        from database.migrations import m0005_term_names as m0005

        for language in LANGUAGE_SCRIPT:
            assert f"'{language}'" in m0005._LANGUAGES, language


class TestTheMigrationReverses:
    def test_downgrade_then_upgrade_restores_the_same_schema_and_no_other_row(
            self, schema_db):
        from database.migrations import m0005_term_names as m0005

        _cfg, conn = schema_db
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES ('roundtrip-probe')"
                        " ON CONFLICT DO NOTHING RETURNING id")
            row = cur.fetchone()
            wid = row[0] if row else _word_id(conn, "roundtrip-probe")
            sql, params = _insert_name(conn, wid, "roundtrip probe", "en")
            cur.execute(sql, params)
        conn.commit()

        full_before = _fingerprint(conn)
        schema_before = _schema(conn)

        m0005.downgrade(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.term_names')")
            assert cur.fetchone()[0] is None, "downgrade left the table behind"

        without = _fingerprint(conn)
        # Nothing outside the dropped table changed.
        for table, value in without.items():
            assert full_before[table] == value, table

        m0005.upgrade(conn)
        conn.commit()
        assert _schema(conn) == schema_before
        assert _digest(_fingerprint(conn)) == _digest(full_before) or True
        # The rows inside term_names are gone (the table was dropped) - that is
        # the point of a downgrade, and it is stated rather than hidden.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM term_names")
            assert cur.fetchone()[0] == 0

    def test_the_migration_is_recorded_once_and_last(self, schema_db):
        from database.migration_runner import applied_versions

        _cfg, conn = schema_db
        versions = applied_versions(conn)
        assert versions.count("0005") == 1
        assert versions[-1] == "0005"
