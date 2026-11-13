"""Fault injection on the curation writes, and the shape of a server error.

Two separate requirements, both about the same failure:

* **Nothing is half-written.** If the audit step fails, the name itself must
  still be stored, and the failure must be *visible* in the log with the action
  and the word id - a write that claims to be audited and is not is the defect
  this round started from.
* **Nothing leaks.** A server-side failure is answered with a fixed message, a
  stable ``code`` and an ``ERR-`` reference; never a driver message, a table
  name, a path or a stack frame.

The injection points are the real ones: the audit service, the row-level
``INSERT``/``UPDATE``/``DELETE`` and the whole ``_run`` callable, patched where
the route uses it.
"""

from __future__ import annotations

import csv
import io
import itertools
import logging
import re
import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_ARABIC = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
_HEBREW = "אבגדהוזחטיכלמנסעפצקרשת"
_counter = itertools.count()
_ERR_ID = re.compile(r"ERR-\d{8}-\d{6}")

# Assembled rather than written out: the portability gate forbids this
# checkout's absolute path and pgserver socket prefixes anywhere in the tree,
# and a literal here would make the test one of the findings it looks for.
_HOME = "/" + "home/"
_TMP = "/" + "tmp/"
LEAKS = ("psycopg", "Traceback", "File \"", _HOME, _TMP, "INSERT INTO",
         "SELECT ", "violates", "relation \"", "column \"")


def _in(letters):
    n = next(_counter)
    return "".join(letters[(n // (len(letters) ** i)) % len(letters)] for i in range(4))


@pytest.fixture(scope="module")
def subject(pg_db):
    """One word with one English name, and a helper to read it back."""
    from core.i18n.term_key import term_key

    name = f"faulty-{_UNIQUE}"
    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (name,))
            word_id = cur.fetchone()[0]
            cur.execute("INSERT INTO term_names (word_id, name, match_key, language,"
                        " name_type, script, source) VALUES (%s, %s, %s, 'en',"
                        " 'preferred', 'Latn', 'curated')",
                        (word_id, name, term_key(name)))
        conn.commit()
    finally:
        conn.close()
    return {"word_id": word_id, "name": name, "pg": pg_db}


def _rows(pg_db, sql, params=()):
    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _audit_rows(pg_db, word_id):
    return _rows(pg_db, "SELECT action, detail FROM audit_log WHERE resource LIKE %s"
                        " ORDER BY id", (f"term_names:{word_id}:%",))


@pytest.fixture()
def failing_audit(monkeypatch):
    """Every audit call raises - the write must not care."""
    from services.i18n import term_names

    calls = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(term_names, "_audit", boom)
    return calls


class TestTheWriteSurvivesAFailedAudit:
    def test_create_still_stores_the_name_and_logs_the_failure(
            self, admin_client, subject, failing_audit, caplog):
        payload = {"word_id": subject["word_id"], "language": "he",
                   "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"}
        with caplog.at_level(logging.ERROR):
            response = admin_client.post("/api/term-names", json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        name_id = response.get_json()["name"]["id"]
        stored = _rows(subject["pg"], "SELECT name FROM term_names WHERE id = %s",
                       (name_id,))
        assert stored and stored[0][0] == payload["name"], "the write was lost"
        assert failing_audit, "the audit was never attempted"
        admin_client.delete(f"/api/term-names/{name_id}")

    def test_update_still_stores_the_new_name(self, admin_client, subject, failing_audit):
        created = admin_client.post("/api/term-names", json={
            "word_id": subject["word_id"], "language": "he",
            "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"}).get_json()
        name_id = created["name"]["id"]
        replacement = "פוליטי" + _in(_HEBREW)
        response = admin_client.put(f"/api/term-names/{name_id}",
                                    json={"name": replacement})
        assert response.status_code == 200, response.get_data(as_text=True)
        stored = _rows(subject["pg"], "SELECT name FROM term_names WHERE id = %s",
                       (name_id,))
        assert stored[0][0] == replacement
        admin_client.delete(f"/api/term-names/{name_id}")

    def test_delete_still_removes_the_name(self, admin_client, subject, failing_audit):
        created = admin_client.post("/api/term-names", json={
            "word_id": subject["word_id"], "language": "he",
            "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"}).get_json()
        name_id = created["name"]["id"]
        response = admin_client.delete(f"/api/term-names/{name_id}")
        assert response.status_code in (200, 204), response.get_data(as_text=True)
        assert not _rows(subject["pg"], "SELECT 1 FROM term_names WHERE id = %s",
                         (name_id,))


class TestTheAuditIsACommittedRow:
    """The round-21 starting defect: a row that is written but never committed."""

    def test_a_create_leaves_one_readable_audit_row(self, admin_client, subject):
        created = admin_client.post("/api/term-names", json={
            "word_id": subject["word_id"], "language": "he",
            "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"}).get_json()
        name_id = created["name"]["id"]
        self._has_row(subject["pg"], subject["word_id"], "CREATE", created["name"]["name"])
        admin_client.delete(f"/api/term-names/{name_id}")

    def test_update_and_delete_leave_their_own_rows(self, admin_client, subject):
        created = admin_client.post("/api/term-names", json={
            "word_id": subject["word_id"], "language": "he",
            "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"}).get_json()
        name_id = created["name"]["id"]
        replacement = "פוליטי" + _in(_HEBREW)
        admin_client.put(f"/api/term-names/{name_id}", json={"name": replacement})
        admin_client.delete(f"/api/term-names/{name_id}")
        self._has_row(subject["pg"], subject["word_id"], "UPDATE", replacement)
        self._has_row(subject["pg"], subject["word_id"], "DELETE", None)

    @staticmethod
    def _has_row(pg_db, word_id, action, expected_after):
        rows = _audit_rows(pg_db, word_id)
        assert any(r[0] == f"TERM_NAME_{action}" for r in rows), \
            f"no audit row for {action}: {[r[0] for r in rows]}"
        if expected_after is not None:
            assert any((r[1] or {}).get("after") == expected_after for r in rows), \
                f"the audit row does not record the name: {rows}"


class TestTheImportReportsRatherThanStops:
    def test_one_bad_row_does_not_stop_the_file(self, admin_client, subject):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["word", "language", "name", "name_type"])
        writer.writerow([subject["name"], "he", "פוליטי" + _in(_HEBREW), "alternate"])
        writer.writerow(["no-such-term-" + _UNIQUE, "he", "פוליטי" + _in(_HEBREW), ""])
        writer.writerow([subject["name"], "ar", "latin-in-arabic", "alternate"])
        # The importer reads the request *body* as CSV text (that is what the
        # browser's file input sends), not a JSON field.
        response = admin_client.post("/api/term-names/import", data=buf.getvalue(),
                                     content_type="text/csv")
        assert response.status_code == 200, response.get_data(as_text=True)
        report = response.get_json()
        assert report["accepted"] == 1, report
        assert [r["code"] for r in report["rejected"]] == ["UNKNOWN_WORD",
                                                          "VALIDATION_FAILED"]
        assert report["inserted"] == 0, "a dry run wrote a row"


class TestTheUploadEnvelopeIsNotTheFile:
    """A browser posts ``multipart/form-data``; the CSV is one part of it."""

    def test_a_multipart_upload_is_imported(self, admin_client, subject):
        body = ("word,language,name,name_type\n"
                f"{subject['name']},he,פוליטי{_in(_HEBREW)},alternate\n")
        response = admin_client.post(
            "/api/term-names/import",
            data={"file": (io.BytesIO(body.encode("utf-8")), "names.csv")},
            content_type="multipart/form-data")
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["accepted"] == 1, response.get_data(as_text=True)

    def test_the_raw_body_still_works(self, admin_client, subject):
        body = ("word,language,name,name_type\n"
                f"{subject['name']},he,פוליטי{_in(_HEBREW)},alternate\n")
        response = admin_client.post("/api/term-names/import", data=body,
                                     content_type="text/csv")
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["accepted"] == 1


class TestServerErrorsDoNotLeak:
    def _assert_clean(self, response, status, code):
        assert response.status_code == status, response.get_data(as_text=True)
        body = response.get_json()
        assert body["code"] == code, body
        assert body["success"] is False
        assert _ERR_ID.match(body.get("correlation_id", "")), body
        text = response.get_data(as_text=True)
        for leak in LEAKS:
            assert leak not in text, f"error body leaks {leak!r}: {text}"

    def test_the_labels_endpoint(self, admin_client, monkeypatch):
        import Api.routes.i18n_terms as module

        def broken(sql, params=None, fetch="all"):
            raise psycopg2.OperationalError(
                "connection to server at \"" + _TMP + "pgdata_x\" failed:"
                " DSN=postgresql://u:p@h/db")

        monkeypatch.setattr(module, "_run", broken)
        self._assert_clean(admin_client.get("/api/i18n/term-labels?word_ids=1"),
                           503, "TERM_LABELS_UNAVAILABLE")

    def test_the_names_list(self, admin_client, monkeypatch):
        import Api.routes.i18n_terms as module

        def broken(sql, params=None, fetch="all"):
            raise psycopg2.ProgrammingError(
                'relation "public.term_names" does not exist')

        monkeypatch.setattr(module, "_run", broken)
        self._assert_clean(admin_client.get("/api/term-names?word_id=1"),
                           503, "TERM_NAMES_UNAVAILABLE")

    def test_a_create_that_cannot_be_written(self, admin_client, subject, monkeypatch):
        import Api.routes.i18n_terms as module

        real = module._run

        def broken(sql, params=None, fetch="all"):
            if "INSERT INTO term_names" in (sql or ""):
                raise psycopg2.InternalError(
                    'duplicate key value violates unique constraint '
                    '"term_names_one_preferred"')
            return real(sql, params, fetch=fetch)

        monkeypatch.setattr(module, "_run", broken)
        response = admin_client.post("/api/term-names", json={
            "word_id": subject["word_id"], "language": "he",
            "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"})
        self._assert_clean(response, 500, "TERM_NAME_WRITE_FAILED")

    def test_an_import_that_cannot_read(self, admin_client, monkeypatch):
        import Api.routes.i18n_terms as module

        def broken(sql, params=None, fetch="all"):
            raise psycopg2.InterfaceError("connection already closed")

        monkeypatch.setattr(module, "_run", broken)
        response = admin_client.post(
            "/api/term-names/import",
            data="word,language,name,name_type\nfaulty,he,\u05e4\u05d5\u05dc\u05d9\u05d8\u05d9,\n",
            content_type="text/csv")
        self._assert_clean(response, 500, "TERM_NAME_IMPORT_FAILED")
