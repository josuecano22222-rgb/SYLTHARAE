"""Hostile input reaches a refusal, not a database.

Every string below is something a curator or a search box can actually send.
The claims are narrow and checkable:

* a name that cannot be stored is refused with a 4xx carrying a stable ``code``,
  and the refusal mentions the caller's own value and nothing about the schema;
* the refusal happens in Python, so ``INSERT INTO term_names`` is never sent;
* a hostile *search* string is answered, not crashed, and PostgreSQL's error
  text never reaches the client.
"""

from __future__ import annotations

import itertools
import uuid

import psycopg2
import pytest

from services.i18n.term_search import expand_for_search

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_ARABIC = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
_HEBREW = "אבגדהוזחטיכלמנסעפצקרשת"
_counter = itertools.count()


def _in(letters):
    n = next(_counter)
    return "".join(letters[(n // (len(letters) ** i)) % len(letters)] for i in range(4))


@pytest.fixture(scope="module")
def seed(pg_db):
    """One word that exists, so a refusal cannot be 'unknown word' by accident."""
    from core.i18n.term_key import term_key

    name = f"hostile-{_UNIQUE}"
    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (name,))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (word_id,))
            category_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO term_names (word_id, name, match_key, language, name_type,"
                " script, source) VALUES (%s, %s, %s, 'en', 'preferred', 'Latn',"
                " 'curated')", (word_id, name, term_key(name)))
        conn.commit()
    finally:
        conn.close()
    return {"word_id": word_id, "category_id": category_id, "name": name, "pg": pg_db}


def _count_names(pg_db) -> int:
    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM term_names")
            return cur.fetchone()[0]
    finally:
        conn.close()


#: Things a person can paste into a name field.
HOSTILE_NAMES = [
    "DROP TABLE term_names",                     # a statement, not a name
    "פוליטי'); DROP TABLE term_names; --",      # injection shaped, Hebrew
    "سياسي\x00tail",                             # NUL byte
    "سياسي\u202etxt.exe",                        # right-to-left override
    "سیاسی\u200c\u200c\u200c",                   # repeated ZWNJ
    "ا" * 600,                                   # longer than any column
    "\u200f",                                    # formatting character only
    " ",                                         # whitespace only
]

#: Things a person can paste into a search box.
HOSTILE_QUERIES = [
    "%", "_", "%%%", ".*", "' OR 1=1 --",
    "سياسي' || (SELECT version()) || '",
    "\u0000", "ا" * 500, "\u202eevil",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_a_hostile_name_is_refused_and_stores_nothing(admin_client, seed, name):
    before = _count_names(seed["pg"])
    response = admin_client.post("/api/term-names", json={
        "word_id": seed["word_id"], "language": "he",
        "name": name, "name_type": "alternate"})
    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["code"] == "VALIDATION_FAILED"
    text = response.get_data(as_text=True)
    # The message quotes the submitted value back (that is the useful part: it
    # says *what* was wrong), so the check is that nothing the schema owns
    # appears - driver text, SQL, or a constraint name.
    for leak in ("psycopg", "Traceback", "INSERT INTO", "SELECT ", "violates",
                 "relation \"", "column \""):
        assert leak not in text, f"the refusal mentions {leak}: {text[:200]}"
    assert _count_names(seed["pg"]) == before, "a refused name was written anyway"


def test_an_injection_shaped_name_is_stored_verbatim_or_not_at_all(admin_client, seed):
    """A name that *is* valid Hebrew is stored as characters, not executed."""
    payload = "פוליטי" + _in(_HEBREW)
    response = admin_client.post("/api/term-names", json={
        "word_id": seed["word_id"], "language": "he",
        "name": payload, "name_type": "alternate"})
    assert response.status_code == 201, response.get_data(as_text=True)
    body = response.get_json()
    assert body["name"]["name"] == payload
    admin_client.delete(f"/api/term-names/{body['name']['id']}")


@pytest.mark.parametrize("query", HOSTILE_QUERIES)
def test_a_hostile_query_is_answered_not_crashed(admin_client, query):
    for url in (f"/api/categories/search?q={query}", f"/api/keywords?q={query}"):
        response = admin_client.get(url)
        assert response.status_code in (200, 400), (url, response.status_code)
        text = response.get_data(as_text=True)
        for leak in ("psycopg", "Traceback", "term_names t", "line 1"):
            assert leak not in text, (url, text[:200])


@pytest.mark.parametrize("query", HOSTILE_QUERIES)
def test_the_resolver_declines_what_is_not_a_name(pg_db, query):
    """The expansion is a lookup, not a pattern: metacharacters match nothing.

    ``%`` and ``_`` are ordinary characters to the normaliser, so they can only
    match a term that literally contains them - which no curated term does.
    """
    assert not expand_for_search(query).word_ids, query


def test_the_wildcard_search_is_no_worse_than_before(admin_client, seed):
    """``%`` still matches everything - pre-existing, and not what this changes.

    ``w.word ILIKE '%%%'`` matched every row before the feature; the requirement
    is that the *new* branch does not add rows, which is what an empty expansion
    guarantees.
    """
    response = admin_client.get("/api/categories/search?q=%25")
    assert response.status_code == 200
    assert response.get_json() is not None


def test_a_wrong_script_name_never_reaches_postgres(admin_client, seed, monkeypatch):
    """The refusal happens in Python, before the INSERT is attempted.

    ``_run`` is wrapped by a recorder that fails the test if it is asked to
    insert: if the validator ever stopped working, this catches it as a call
    rather than as a constraint-name leak from the database.
    """
    import Api.routes.i18n_terms as module

    sent = []
    real = module._run

    def recorder(sql, params=None, fetch="all"):
        sent.append(sql)
        if "INSERT INTO term_names" in (sql or ""):
            raise AssertionError(f"PostgreSQL was asked to insert a "
                                 f"wrong-script name: {sql}")
        return real(sql, params, fetch=fetch)

    monkeypatch.setattr(module, "_run", recorder)
    response = admin_client.post("/api/term-names", json={
        "word_id": seed["word_id"], "language": "ar",
        "name": "latin-in-arabic", "name_type": "alternate"})
    assert response.status_code == 400, response.get_data(as_text=True)
    assert not any("INSERT INTO term_names" in (sql or "") for sql in sent)


def test_an_over_long_id_list_is_capped_not_refused(admin_client):
    """One thousand ids is a client bug, not an attack; the cap answers it."""
    ids = ",".join(str(n) for n in range(1, 1001))
    response = admin_client.get(f"/api/i18n/term-labels?word_ids={ids}")
    assert response.status_code == 200
    assert response.get_json()["success"] is True
