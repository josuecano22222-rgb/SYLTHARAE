"""Integration: the runtime translation overlay is per request, not per process.

``apply_runtime_translation_overrides`` puts the editor's saved edits into the
Babel catalog for the request being rendered. The catalog object is cached by
flask-babel for the whole process and shared by every request in a locale, so
the first version of this code welded an override in permanently: the editor's
"reset" deleted the row and every page kept rendering the old text until the
service restarted. That was found as a module-order failure and fixed by
merging into a copy; these tests keep it fixed:

* a reset is visible on the next request (the failing test);
* two requests at once - one rendering an override, one reading the base
  string - never see a torn catalog, and neither sees the other's state as its
  own;
* an Arabic override never appears in a Hebrew page (no cross-locale leakage);
* the remembered pristine catalogs do not grow across a thousand requests;
* the overlay is applied per request, so a request without overrides after one
  with them renders the checked-in catalog exactly.
"""

from __future__ import annotations

import threading

import pytest

from babel.messages.pofile import read_po

pytestmark = pytest.mark.integration

PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
MSGID = "Translation Management"


def _po_catalog(locale: str):
    path = PROJECT_ROOT / "translations" / locale / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as handle:
        return read_po(handle, locale=locale)


def _po_string(locale: str) -> str:
    return _po_catalog(locale).get(MSGID).string


def _csrf(client) -> dict:
    response = client.get("/api/csrf-token")
    assert response.status_code == 200
    return {"X-CSRFToken": response.get_json()["csrf_token"]}


def _entry(client, locale: str) -> dict:
    body = client.get(
        f"/api/translations/manage/catalog?locale={locale}&q=Translation%20Management"
        "&per_page=10").get_json()
    assert body["success"] is True, body
    return next(row for row in body["items"] if row["msgid"] == MSGID)


def _render(client, path: str = "/translations/manage") -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.fixture()
def arabic(admin_client):
    """The admin client in Arabic, with the checked-in override cleared."""
    headers = _csrf(admin_client)
    entry = _entry(admin_client, "ar")
    reset = {"locale": "ar", "translations": [{"id": entry["id"], "reset": True}]}
    with admin_client.session_transaction() as session:
        session["language"] = "ar"
    admin_client.put("/api/translations/manage/catalog", json=reset, headers=headers)
    yield {"client": admin_client, "headers": headers, "entry": entry, "reset": reset}
    admin_client.put("/api/translations/manage/catalog", json=reset, headers=headers)
    with admin_client.session_transaction() as session:
        session["language"] = "en"


def _save(admin_client, headers, entry, value: str):
    response = admin_client.put(
        "/api/translations/manage/catalog",
        json={"locale": "ar",
              "translations": [{"id": entry["id"], "translation": value}]},
        headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)


def test_a_reset_stops_rendering_on_the_very_next_request(arabic):
    client, headers, entry = arabic["client"], arabic["headers"], arabic["entry"]
    base = _po_string("ar")
    marker = "ترجمة مؤقتة لإعادة الضبط"
    assert marker != base and base in _render(client)

    _save(client, headers, entry, marker)
    assert marker in _render(client)

    client.put("/api/translations/manage/catalog", json=arabic["reset"], headers=headers)
    rendered = _render(client)
    assert marker not in rendered, "the deleted override is still rendering"
    assert base in rendered


def test_the_pristine_catalog_is_the_checked_in_catalog():
    """The remembered copy must come from the files, not from an earlier overlay."""
    from Api.services.translation_management import _PRISTINE_CATALOGS

    for locale, catalog in _PRISTINE_CATALOGS.items():
        if locale == "en":
            continue                      # msgid == translation for the source locale
        reference = _po_catalog(locale)
        for msgid in (MSGID, "Save changes", "Translation filters"):
            expected = reference.get(msgid).string
            if not expected:
                continue
            assert catalog.get(msgid) == expected, (locale, msgid, catalog.get(msgid))
    from settings.languages import SUPPORTED_LANGUAGES

    assert set(_PRISTINE_CATALOGS) <= set(SUPPORTED_LANGUAGES) | {"en"}


def test_a_concurrent_override_and_base_read_never_tear(arabic):
    """One thread saves and resets an override; another renders the page.

    Every render must show *the base string or the override*, never an empty
    title, an English fallback, or a half-applied catalog. The overlay is a
    per-request copy precisely so that a reader racing a writer gets one of the
    two consistent states.
    """
    client, headers, entry = arabic["client"], arabic["headers"], arabic["entry"]
    base = _po_string("ar")
    override = "ترجمة أثناء التزامن"
    seen, errors = [], []
    stop = threading.Event()

    def writer():
        try:
            for index in range(40):
                _save(client, headers, entry, override)
                if index % 2:
                    client.put("/api/translations/manage/catalog",
                               json=arabic["reset"], headers=headers)
        except Exception as exc:                       # pragma: no cover - reported
            errors.append(f"writer: {exc}")
        finally:
            stop.set()

    def reader():
        try:
            while not stop.is_set():
                html = _render(client)
                title = html.split("<title>", 1)[1].split("</title>", 1)[0]
                seen.append(title)
                if title not in (base, override):
                    errors.append(f"torn title: {title!r}")
                if MSGID in html:
                    errors.append("the English source rendered in an Arabic page")
        except Exception as exc:                       # pragma: no cover - reported
            errors.append(f"reader: {exc}")

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    assert not errors, errors[:3]
    assert seen, "the reader never rendered a page while the writer worked"


def test_an_arabic_override_does_not_leak_into_another_locale(arabic):
    client, headers, entry = arabic["client"], arabic["headers"], arabic["entry"]
    marker = "تسريب محتمل بين اللغات"
    _save(client, headers, entry, marker)
    assert marker in _render(client), "the override did not apply at all"

    for locale in ("he", "fa"):
        with client.session_transaction() as session:
            session["language"] = locale
        html = _render(client)
        assert marker not in html, f"the Arabic override rendered in a {locale} page"
        assert _po_string(locale) in html, f"the {locale} catalog did not render"
    with client.session_transaction() as session:
        session["language"] = "ar"


def test_the_pristine_catalogue_does_not_grow_across_a_thousand_requests(arabic):
    from Api.services.translation_management import _PRISTINE_CATALOGS

    client = arabic["client"]
    before = len(_PRISTINE_CATALOGS)
    # A light route that still goes through the before_request hook which
    # applies the overlay (a static asset would skip it and prove nothing).
    for _ in range(1000):
        assert client.get("/api/i18n/term-labels?word_ids=1").status_code == 200
    assert len(_PRISTINE_CATALOGS) == before, (
        f"the pristine catalog map grew from {before} to {len(_PRISTINE_CATALOGS)}")
    assert len(_PRISTINE_CATALOGS) <= 8, "more locales remembered than exist"
