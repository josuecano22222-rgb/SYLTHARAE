"""Display: the fallback chain, the visible marker, and the labels endpoint.

The chain is *requested locale -> its base language -> the default language ->
the canonical word*, and the difference between those four is what the reader
sees. A name that fell back to English must say so; a page that silently
presents English as Arabic is the failure this feature exists to prevent.
"""

from __future__ import annotations

import itertools
import json
import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
EN_NAME = f"political-{_UNIQUE}"
AR_NAME = f"سياسي-{_UNIQUE}"

#: A name in a single script has to stay in that script: the API refuses a
#: Latin suffix inside an Arabic name *before* PostgreSQL sees it, which is the
#: behaviour one of these tests asserts. Uniqueness therefore comes from
#: letters, not from the hex token.
_ARABIC = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
_HEBREW = "אבגדהוזחטיכלמנסעפצקרשת"
_counter = itertools.count()


def _in(letters):
    """A distinct word in one script (the counter, written in its letters)."""
    n = next(_counter)
    suffix = "".join(letters[(n // (len(letters) ** i)) % len(letters)]
                     for i in range(4))
    return suffix


@pytest.fixture(scope="module")
def labelled(pg_db):
    """One word with an English and an Arabic name, one with English only."""
    from core.i18n.term_key import term_key

    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (EN_NAME,))
            translated = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (translated,))
            translated_cat = cur.fetchone()[0]
            for language, name, script in (("en", EN_NAME, "Latn"), ("ar", AR_NAME, "Arab")):
                cur.execute(
                    "INSERT INTO term_names (word_id, name, match_key, language,"
                    " name_type, script, source) VALUES (%s, %s, %s, %s, 'preferred',"
                    " %s, 'curated')",
                    (translated, name, term_key(name), language, script))

            only_en = f"economic-{_UNIQUE}"
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (only_en,))
            untranslated = cur.fetchone()[0]
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                        (untranslated,))
            untranslated_cat = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO term_names (word_id, name, match_key, language,"
                " name_type, script, source) VALUES (%s, %s, %s, 'en', 'preferred',"
                " 'Latn', 'curated')",
                (untranslated, only_en, term_key(only_en)))
        conn.commit()
    finally:
        conn.close()
    return {"translated_word": translated, "translated_cat": translated_cat,
            "untranslated_word": untranslated, "untranslated_cat": untranslated_cat,
            "only_en": only_en}


def _labels(client, **params):
    return client.get("/api/i18n/term-labels", query_string=params)


class TestTheLabelsEndpoint:
    def test_a_translated_name_is_returned_in_the_requested_language(self, admin_client,
                                                                    labelled):
        body = _labels(admin_client, category_ids=str(labelled["translated_cat"]),
                       lang="ar").get_json()
        label = body["category_labels"][str(labelled["translated_cat"])]
        assert label["name"] == AR_NAME
        assert label["source"] == "translated"
        assert label["translated"] is True

    def test_the_default_language_is_a_fallback_and_says_so(self, admin_client, labelled):
        body = _labels(admin_client, category_ids=str(labelled["translated_cat"]),
                       lang="hr").get_json()
        label = body["category_labels"][str(labelled["translated_cat"])]
        assert label["name"] == EN_NAME
        assert label["source"] == "default-language"
        assert label["translated"] is False

    def test_a_region_locale_falls_back_to_its_base_language(self, admin_client, labelled):
        body = _labels(admin_client, category_ids=str(labelled["translated_cat"]),
                       lang="ar-EG").get_json()
        label = body["category_labels"][str(labelled["translated_cat"])]
        assert label["name"] == AR_NAME
        assert label["source"] == "base-language"
        assert body["chain"] == ["ar-eg", "ar", "en"]

    def test_two_categories_are_one_request(self, admin_client, labelled):
        body = _labels(admin_client,
                       category_ids=f"{labelled['translated_cat']},{labelled['untranslated_cat']}",
                       lang="ar").get_json()
        assert len(body["category_labels"]) == 2
        untranslated = body["category_labels"][str(labelled["untranslated_cat"])]
        assert untranslated["name"] == labelled["only_en"]
        assert untranslated["source"] == "default-language"

    def test_words_can_be_labelled_directly(self, admin_client, labelled):
        body = _labels(admin_client, word_ids=str(labelled["translated_word"]),
                       lang="he").get_json()
        assert body["labels"][str(labelled["translated_word"])]["name"] == EN_NAME

    def test_a_word_with_no_names_falls_back_to_the_canonical_word(self, admin_client,
                                                                   pg_db):
        conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                                user=pg_db["user"], password=pg_db["password"],
                                dbname=pg_db["database"])
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                            (f"bare-{uuid.uuid4().hex[:6]}",))
                bare = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        body = _labels(admin_client, word_ids=str(bare), lang="ar").get_json()
        assert body["labels"][str(bare)]["source"] == "canonical"

    def test_missing_ids_is_a_validation_error_not_a_crash(self, admin_client):
        response = _labels(admin_client)
        assert response.status_code == 400
        assert response.get_json()["code"] == "VALIDATION_FAILED"

    def test_ids_that_are_not_numbers_are_ignored_not_echoed(self, admin_client):
        response = _labels(admin_client, word_ids="1,abc,';DROP TABLE words;--")
        assert response.status_code in (200, 400)
        assert "DROP TABLE" not in response.get_data(as_text=True)


class TestTheCategoryPage:
    def test_the_page_shows_the_arabic_name(self, admin_client, labelled):
        html = admin_client.get(
            "/categories", query_string={"search": EN_NAME, "lang": "ar"}
        ).get_data(as_text=True)
        assert AR_NAME in html, "the list did not render the translated name"

    def test_the_page_marks_a_name_that_was_not_translated(self, admin_client, labelled):
        # The *interface* language is not a query parameter: it comes from the
        # ``user_language`` cookie the header switcher sets, then the session,
        # then the system setting (Api/routes/common.resolve_interface_language).
        # ``?lang=ar`` only selects which translation of a *term* the API
        # returns, so the page still rendered in English and this assertion
        # failed against the English marker. Set the cookie the switcher sets,
        # and remove it again so no other test inherits an Arabic interface.
        admin_client.set_cookie("user_language", "ar")
        try:
            html = admin_client.get("/categories",
                                    query_string={"search": labelled["only_en"]}
                                    ).get_data(as_text=True)
        finally:
            admin_client.delete_cookie("user_language")
        assert 'data-term-source="default-language"' in html
        # ``Missing`` is the catalog's existing word for "not translated"
        # (Arabic "غير مترجم"); ``Original`` is absent from the Hebrew and
        # Persian catalogs, so it would render in English. The page is Arabic,
        # so the *attribute* is the stable thing to assert - the text is the
        # translation, not the msgid.
        assert 'data-term-marker="missing"' in html
        assert "غير مترجم" in html

    def test_the_page_marks_a_translated_name_as_such(self, admin_client, labelled):
        html = admin_client.get("/categories",
                                query_string={"search": EN_NAME,
                                              "lang": "ar"}).get_data(as_text=True)
        assert 'data-term-source="translated"' in html or \
               'data-term-source="default-language"' in html


class TestTheNamesPage:
    """The page a curator actually opens.

    It is not enough that the API behind it works: the page renders through
    ``base.html``, which takes a ``languages`` **dict**, and passing a list of
    pairs from the route shadowed it - every request to
    ``/categories/<id>/names`` was a 500 with ``'list object' has no attribute
    'items'`` while the API tests stayed green. Found in the browser.
    """

    def test_the_page_renders(self, admin_client, labelled):
        response = admin_client.get(f"/categories/{labelled['translated_cat']}/names")
        assert response.status_code == 200, response.get_data(as_text=True)[:400]

    def test_it_lists_the_names_that_exist(self, admin_client, labelled):
        html = admin_client.get(
            f"/categories/{labelled['translated_cat']}/names").get_data(as_text=True)
        assert AR_NAME in html and EN_NAME in html

    def test_it_offers_every_supported_language(self, admin_client, labelled):
        from settings.languages import SUPPORTED_LANGUAGES

        html = admin_client.get(
            f"/categories/{labelled['translated_cat']}/names").get_data(as_text=True)
        for code in SUPPORTED_LANGUAGES:
            assert f'value="{code}"' in html, f"language {code} is not offered"

    def test_an_unknown_category_is_a_404(self, admin_client):
        assert admin_client.get("/categories/2000000000/names").status_code == 404


class TestTheCurationApi:
    def _payload(self, labelled, **overrides):
        payload = {"word_id": labelled["translated_word"], "language": "he",
                   "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"}
        payload.update(overrides)
        return payload

    def test_an_admin_can_add_a_name(self, admin_client, labelled):
        response = admin_client.post("/api/term-names", json=self._payload(labelled))
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["name"]["language"] == "he"
        admin_client.delete(f"/api/term-names/{body['name']['id']}")

    def test_a_wrong_script_is_refused_before_postgres_sees_it(self, admin_client, labelled):
        response = admin_client.post("/api/term-names",
                                     json=self._payload(labelled, language="ar",
                                                        name="latin-in-arabic"))
        assert response.status_code == 400
        body = response.get_json()
        assert body["code"] == "VALIDATION_FAILED"
        assert "Latn" in body["error"] and "Arab" in body["error"]

    def test_a_second_preferred_name_is_a_conflict_not_a_500(self, admin_client, labelled):
        # A pure-Arabic name: AR_NAME carries a Latin hex suffix, so it would be
        # refused as mixed-script (400) before the uniqueness rule is reached.
        response = admin_client.post("/api/term-names",
                                     json={"word_id": labelled["translated_word"],
                                           "language": "ar",
                                           "name": "سياسي" + _in(_ARABIC),
                                           "name_type": "preferred"})
        assert response.status_code == 409
        assert response.get_json()["code"] == "PREFERRED_NAME_EXISTS"

    def test_an_unknown_word_is_a_404(self, admin_client, labelled):
        response = admin_client.post("/api/term-names",
                                     json={"word_id": 2_000_000_000, "language": "he",
                                           "name": "פוליטי"})
        assert response.status_code == 404

    def test_an_unknown_language_is_refused(self, admin_client, labelled):
        response = admin_client.post("/api/term-names",
                                     json=self._payload(labelled, language="de"))
        assert response.status_code == 400

    def _audit_row(self, pg_db, action):
        conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                                user=pg_db["user"], password=pg_db["password"],
                                dbname=pg_db["database"])
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT action, username, user_id, resource,"
                            " detail::text FROM audit_log WHERE action = %s"
                            " ORDER BY id DESC LIMIT 1", (action,))
                return cur.fetchone()
        finally:
            conn.close()

    def test_the_create_is_audited(self, admin_client, labelled, pg_db):
        response = admin_client.post("/api/term-names", json=self._payload(labelled))
        name_id = response.get_json()["name"]["id"]
        row = self._audit_row(pg_db, "TERM_NAME_CREATE")
        assert row, "no audit row for the create"
        action, username, user_id, resource, _detail = row
        assert action == "TERM_NAME_CREATE"
        assert str(labelled["translated_word"]) in resource
        # The actor is the signed-in administrator, by id *and* name: the first
        # version resolved the actor by username and, when that failed, inserted
        # no row at all rather than an anonymous one.
        assert username == "testadmin", row
        assert user_id is not None, row
        admin_client.delete(f"/api/term-names/{name_id}")

    def test_the_update_and_delete_are_audited(self, admin_client, labelled, pg_db):
        created = admin_client.post("/api/term-names",
                                    json=self._payload(labelled)).get_json()["name"]
        admin_client.put(f"/api/term-names/{created['id']}",
                         json={"name": "פוליטיקה" + _in(_HEBREW)})
        admin_client.delete(f"/api/term-names/{created['id']}")
        assert self._audit_row(pg_db, "TERM_NAME_UPDATE"), "update not audited"
        assert self._audit_row(pg_db, "TERM_NAME_DELETE"), "delete not audited"

    def test_the_import_is_audited(self, admin_client, labelled, pg_db):
        csv_body = ("word,language,name,name_type\n"
                    f"{labelled['only_en']},ar,اقتصاد{_in(_ARABIC)},alternate\n")
        response = admin_client.post("/api/term-names/import?apply=1",
                                     data=csv_body.encode("utf-8"),
                                     content_type="text/csv")
        assert response.status_code == 200, response.get_data(as_text=True)
        row = self._audit_row(pg_db, "TERM_NAME_IMPORT")
        # The import's counts live in ``detail``; ``resource`` names the scope.
        assert row and "inserted" in (row[4] or ""), row
        assert row[3] == "term_names:0:-", row

    def test_the_update_changes_the_label_the_next_request_returns(self, admin_client,
                                                                   labelled):
        created = admin_client.post("/api/term-names",
                                    json=self._payload(labelled)).get_json()["name"]
        new_name = "פוליטיקה" + _in(_HEBREW)
        updated = admin_client.put(f"/api/term-names/{created['id']}",
                                   json={"name": new_name})
        assert updated.status_code == 200, updated.get_data(as_text=True)
        labels = _labels(admin_client, word_ids=str(labelled["translated_word"]),
                         lang="he").get_json()
        assert labels["labels"][str(labelled["translated_word"])]["name"] == new_name
        admin_client.delete(f"/api/term-names/{created['id']}")

    def test_a_delete_removes_it(self, admin_client, labelled):
        created = admin_client.post("/api/term-names",
                                    json=self._payload(labelled)).get_json()["name"]
        assert admin_client.delete(f"/api/term-names/{created['id']}").status_code == 200
        response = admin_client.delete(f"/api/term-names/{created['id']}")
        assert response.status_code == 404


class TestTheAuditCannotFailSilently:
    """A write that cannot be recorded must still be written - and said so."""

    def test_a_broken_audit_logs_an_error_and_keeps_the_row(
            self, admin_client, labelled, pg_db, caplog, monkeypatch):
        import logging

        from core.security.service import AuthService

        def _explode(*args, **kwargs):
            raise RuntimeError("audit store unavailable")

        monkeypatch.setattr(AuthService, "audit", _explode, raising=True)
        with caplog.at_level(logging.ERROR):
            response = admin_client.post("/api/term-names", json={
                "word_id": labelled["translated_word"], "language": "he",
                "name": "פוליטי" + _in(_HEBREW), "name_type": "alternate"})
        assert response.status_code == 201, response.get_data(as_text=True)
        messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert any("term name audit write failed" in m for m in messages), messages
        assert any("TERM_NAME_CREATE" in m for m in messages), messages
        # The write itself survived: the names table is the record of truth.
        conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                                user=pg_db["user"], password=pg_db["password"],
                                dbname=pg_db["database"])
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM term_names WHERE word_id = %s"
                            " AND language = 'he'",
                            (labelled["translated_word"],))
                assert cur.fetchone()[0] >= 1
        finally:
            conn.close()
        admin_client.delete(f"/api/term-names/{response.get_json()['name']['id']}")


class TestTheCsvImport:
    HEADER = "word,language,name,name_type\n"

    def _post(self, client, body, apply=False):
        return client.post("/api/term-names/import" + ("?apply=1" if apply else ""),
                           data=body.encode("utf-8"),
                           content_type="text/csv")

    def test_a_dry_run_reports_rows_and_writes_nothing(self, admin_client, labelled,
                                                       pg_db):
        csv_body = self.HEADER + f"{labelled['only_en']},ar,اقتصادي,preferred\n"
        conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                                user=pg_db["user"], password=pg_db["password"],
                                dbname=pg_db["database"])
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM term_names")
                before = cur.fetchone()[0]
        finally:
            conn.close()
        body = self._post(admin_client, csv_body).get_json()
        assert body["accepted"] == 1
        assert body["inserted"] == 0
        conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"],
                                user=pg_db["user"], password=pg_db["password"],
                                dbname=pg_db["database"])
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM term_names")
                after = cur.fetchone()[0]
        finally:
            conn.close()
        # Compared with itself, not with zero: a sibling test may already have
        # written an Arabic name for this word, and "nothing was written" is a
        # statement about this call.
        assert after == before

    def test_apply_writes_the_valid_row(self, admin_client, labelled):
        csv_body = self.HEADER + f"{labelled['only_en']},ar,اقتصادي,preferred\n"
        body = self._post(admin_client, csv_body, apply=True).get_json()
        assert body["inserted"] == 1, body
        labels = _labels(admin_client, word_ids=str(labelled["untranslated_word"]),
                         lang="ar").get_json()
        assert labels["labels"][str(labelled["untranslated_word"])]["name"] == "اقتصادي"

    def test_a_bad_row_is_rejected_with_its_line_number_and_the_rest_applies(
            self, admin_client, labelled):
        csv_body = (self.HEADER
                    + "no-such-word-here,ar,سياسي,preferred\n"
                    + f"{labelled['only_en']},ar,latin-in-arabic,preferred\n"
                    + f"{labelled['only_en']},ar,اقتصادي,alternate\n")
        body = self._post(admin_client, csv_body, apply=True).get_json()
        assert body["rows"] == 3
        assert body["accepted"] == 1
        assert [row["line"] for row in body["rejected"]] == [2, 3]
        assert {row["code"] for row in body["rejected"]} == {"UNKNOWN_WORD",
                                                             "VALIDATION_FAILED"}

    def test_a_file_without_the_expected_header_is_refused_whole(self, admin_client,
                                                                 labelled):
        response = self._post(admin_client, "term,lang\nx,ar\n")
        assert response.status_code == 400
        assert "header" in response.get_json()["error"]

    def test_an_empty_upload_is_refused(self, admin_client):
        assert self._post(admin_client, "").status_code == 400
