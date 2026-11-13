"""First-run language selection in the setup wizard.

The wizard is the first screen a new install shows, so it must work in every
language the application offers before any account exists. These tests pin:

1. **Coverage.** Every string the wizard shows (template ``_()`` calls, the
   script's ``T()`` keys, and the backend validation messages) has a real,
   non-fuzzy translation in Arabic, Hebrew and Persian. The list is derived
   from the sources, so a new string without a translation fails here.
2. **Rendering.** ``/setup`` renders in the selected language with the right
   ``lang`` and ``dir``, the selector offers exactly the supported languages,
   and the script receives translated strings.
3. **Persistence.** Choosing a language calls ``/set_language``, which stores
   ``system.language``. The wizard is still uninstalled at that point, so the
   setup gate must let that one request through.
4. **Fallback.** A string with no translation shows its English source, never
   an empty label or a raw key, on both the server and the script side.
5. **Layout.** The wizard CSS uses logical (start/end) properties so RTL
   layout follows ``dir`` instead of fixed left/right.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

TEMPLATE = PROJECT_ROOT / "templates" / "Setup" / "install_wizard.html"
SCRIPT = PROJECT_ROOT / "static" / "js" / "pages" / "install-wizard-page.js"
SETUP_ROUTES = PROJECT_ROOT / "Api" / "routes" / "setup.py"
TRANSLATIONS = PROJECT_ROOT / "translations"

RTL_LOCALES = ("ar", "he", "fa")
#: Entries whose translation is intentionally the same text in every language.
IDENTITY_ALLOWED = {"PostgreSQL", "Python"}

_STR = r"((?:[^'\\]|\\.)*)"
_DSTR = r'((?:[^"\\]|\\.)*)'


def _unescape(s: str) -> str:
    return s.replace("\\'", "'").replace('\\"', '"')


def _template_msgids() -> set[str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"_\('" + _STR + r"'", text))


def _script_keys() -> set[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    keys = {_unescape(k) for k in re.findall(r"\bT\('" + _STR + r"'", text)}
    # Step names are shown through T(name) but keyed by their English label.
    keys |= {"Write configuration", "Database & schema", "Administrator",
             "Runtime directories", "Finalize"}
    return keys


def _backend_msgids() -> set[str]:
    text = SETUP_ROUTES.read_text(encoding="utf-8")
    ids = {_unescape(m) for m in re.findall(r'_\("' + _DSTR + r'"', text)}
    # Field labels are passed as data and translated when a message is built.
    ids |= set(re.findall(r'_(?:text|integer)_field\(\s*data,\s*"\w+",\s*"([^"]+)"', text))
    return ids


def _required_msgids() -> set[str]:
    return _template_msgids() | _script_keys() | _backend_msgids()


def _catalog_entries(lang: str):
    from babel.messages.pofile import read_po

    with (TRANSLATIONS / lang / "LC_MESSAGES" / "messages.po").open("rb") as fh:
        return read_po(fh)


def _mo(lang: str):
    from babel.support import Translations

    return Translations.load(str(TRANSLATIONS), [lang])


# ---------------------------------------------------------------------------
# 1. Coverage
# ---------------------------------------------------------------------------

def test_the_wizard_script_key_list_matches_its_t_calls():
    """The server ships exactly the strings the script asks for."""
    from Api.routes.setup import WIZARD_SCRIPT_STRING_KEYS

    assert set(WIZARD_SCRIPT_STRING_KEYS) == _script_keys()


def test_the_required_string_list_is_non_trivial():
    # Guards against the extraction silently finding nothing.
    required = _required_msgids()
    assert len(required) > 100
    assert "Create the initial administrator account and configure security policies." in required
    assert "Admin password must be at least %(n)s characters." in required
    assert "%(label)s is required." in required


@pytest.mark.parametrize("lang", RTL_LOCALES)
def test_every_wizard_string_has_a_real_translation(lang):
    """No required string is missing, fuzzy, empty, or left in English."""
    entries = {m.id: m for m in _catalog_entries(lang) if m.id}
    mo = _mo(lang)
    problems = []
    for msgid in sorted(_required_msgids()):
        entry = entries.get(msgid)
        if entry is None:
            problems.append(f"missing from .po: {msgid!r}")
            continue
        if entry.fuzzy or not entry.string:
            problems.append(f"fuzzy or empty in .po: {msgid!r}")
            continue
        translated = mo.gettext(msgid)
        if translated == msgid and msgid not in IDENTITY_ALLOWED:
            problems.append(f"still English in compiled .mo: {msgid!r}")
        if set(re.findall(r"%\(\w+\)s", translated)) != set(re.findall(r"%\(\w+\)s", msgid)):
            problems.append(f"placeholders changed: {msgid!r}")
    assert not problems, f"[{lang}] {len(problems)} problems, e.g. {problems[:8]}"


def test_english_catalog_identity_covers_every_wizard_string():
    entries = {m.id for m in _catalog_entries("en") if m.id}
    missing = sorted(_required_msgids() - entries)
    assert not missing, f"English catalog lacks {missing[:8]}"


# ---------------------------------------------------------------------------
# 2. Rendering
# ---------------------------------------------------------------------------

@pytest.fixture()
def uninstalled(monkeypatch):
    """Present the wizard as a fresh install (no initialized system)."""
    import Api.routes.setup as setup_module

    monkeypatch.setattr(setup_module, "_is_initialized", lambda: False)
    return setup_module


@pytest.mark.parametrize("lang", ("en",) + RTL_LOCALES)
def test_wizard_renders_in_the_selected_language(app, uninstalled, lang, monkeypatch):
    client = app.test_client()
    client.set_cookie("user_language", lang)
    resp = client.get("/setup")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert f'<html lang="{lang}"' in html
    if lang in RTL_LOCALES:
        assert 'dir="rtl"' in html
    else:
        assert 'dir="ltr"' in html

    if lang != "en":
        catalog = _mo(lang)
        for english in (
            "Create the initial administrator account and configure security policies.",
            "Affects security defaults, logging, and rate limits",
            "Checking that your system meets the requirements…",
        ):
            translated = catalog.gettext(english)
            assert translated in html, f"[{lang}] {english!r} not rendered as {translated!r}"
            assert english not in html, f"[{lang}] English {english!r} still on the page"


@pytest.mark.parametrize("lang", RTL_LOCALES)
def test_wizard_script_strings_are_translated_in_the_page(app, uninstalled, lang):
    client = app.test_client()
    client.set_cookie("user_language", lang)
    html = client.get("/setup").get_data(as_text=True)
    m = re.search(
        r'<script type="application/json" id="install-wizard-strings">(.*?)</script>', html, re.S
    )
    assert m, "the script string block is missing"
    shipped = json.loads(m.group(1).replace("\\u0026", "&").replace("\\u003c", "<")
                         .replace("\\u003e", ">").replace("\\u0027", "'"))
    mo = _mo(lang)
    assert set(shipped) == set(WIZARD_KEYS())
    for key in ("Next", "Install", "Checking…", "Step %(current)s of %(total)s"):
        assert shipped[key] == mo.gettext(key) != key


def WIZARD_KEYS():
    from Api.routes.setup import WIZARD_SCRIPT_STRING_KEYS

    return WIZARD_SCRIPT_STRING_KEYS


def test_language_selector_offers_the_supported_languages(app, uninstalled):
    from settings.languages import SUPPORTED_LANGUAGES

    client = app.test_client()
    client.set_cookie("user_language", "fa")
    html = client.get("/setup").get_data(as_text=True)

    options = re.findall(r'<option value="(\w+)" lang="\w+"( selected)?>', html)
    assert [code for code, _ in options] == list(SUPPORTED_LANGUAGES)
    assert [code for code, sel in options if sel] == ["fa"]
    assert 'id="setup-language"' in html


# ---------------------------------------------------------------------------
# 3. Persistence before installation
# ---------------------------------------------------------------------------

class _RecordingSettings:
    def __init__(self):
        self.calls = []

    def set_setting(self, key, value):
        self.calls.append((key, value))


@pytest.fixture()
def recorded_settings(monkeypatch):
    import settings as settings_pkg

    recorder = _RecordingSettings()
    monkeypatch.setattr(settings_pkg, "get_settings", lambda: recorder)
    return recorder


@pytest.mark.parametrize("lang", RTL_LOCALES)
def test_choosing_a_language_persists_system_language_before_install(
    app, uninstalled, recorded_settings, lang
):
    client = app.test_client()
    resp = client.post("/api/setup/language", json={"language": lang})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"ok": True, "language": lang, "persisted": True}
    assert ("system.language", lang) in recorded_settings.calls
    assert f"user_language={lang}" in resp.headers.get("Set-Cookie", "")


def test_an_unsupported_language_is_rejected_and_not_persisted(app, uninstalled, recorded_settings):
    client = app.test_client()
    resp = client.post("/api/setup/language", json={"language": "xx"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    assert not recorded_settings.calls


def test_the_language_endpoint_is_closed_once_the_system_is_initialized(
    app, monkeypatch, recorded_settings
):
    import Api.routes.setup as setup_module

    monkeypatch.setattr(setup_module, "_is_initialized", lambda: True)
    client = app.test_client()
    resp = client.post("/api/setup/language", json={"language": "ar"})
    assert resp.status_code >= 400
    assert not recorded_settings.calls


def test_a_failed_settings_write_still_reports_honestly(app, uninstalled, monkeypatch):
    import settings as settings_pkg

    class _Broken:
        def set_setting(self, key, value):
            raise OSError("disk is read-only")

    monkeypatch.setattr(settings_pkg, "get_settings", lambda: _Broken())
    client = app.test_client()
    resp = client.post("/api/setup/language", json={"language": "he"})
    assert resp.status_code == 200
    assert resp.get_json()["persisted"] is False


# ---------------------------------------------------------------------------
# 4. Fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang", RTL_LOCALES)
def test_an_untranslated_string_falls_back_to_english(lang):
    unknown = "A sentence that no catalog has ever contained."
    assert _mo(lang).gettext(unknown) == unknown


def test_backend_validation_messages_are_translated_and_fall_back(app):
    import Api.routes.setup as setup_module
    from flask_babel import force_locale

    with app.test_request_context():
        with force_locale("ar"):
            with pytest.raises(ValueError) as exc:
                setup_module._text_field({}, "host", "Database host", required=True)
        expected = _mo("ar").gettext("%(label)s is required.") % {
            "label": _mo("ar").gettext("Database host")
        }
        assert str(exc.value) == expected
        assert "Database host" not in str(exc.value)

        with force_locale("en"):
            with pytest.raises(ValueError) as exc:
                setup_module._text_field({}, "host", "Database host", required=True)
        assert str(exc.value) == "Database host is required."


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node is not installed")
def test_the_script_t_helper_substitutes_and_falls_back():
    source = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"function T\(key, vars\) \{.*?\n\}\n", source, re.S)
    assert m, "T() helper not found in the wizard script"
    harness = (
        "var INSTALL_WIZARD_STRINGS = {"
        "'Step %(current)s of %(total)s': 'الخطوة %(current)s من %(total)s', "
        "'Minimum %(n)s characters': 'حداقل %(n)s نویسه', "
        "'Empty': ''};\n"
        + m.group(0)
        + "\nconsole.log(JSON.stringify([\n"
        "  T('Step %(current)s of %(total)s', {current: 2, total: 5}),\n"
        "  T('Minimum %(n)s characters', {n: 12}),\n"
        "  T('Not translated yet'),\n"
        "  T('Empty'),\n"
        "  T('Unknown %(x)s', {}),\n"
        "]));\n"
    )
    out = subprocess.run(
        [_node(), "-e", harness], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    assert json.loads(out) == [
        "الخطوة 2 من 5",
        "حداقل 12 نویسه",
        "Not translated yet",
        "Empty",
        "Unknown %(x)s",
    ]


# ---------------------------------------------------------------------------
# 5. Layout
# ---------------------------------------------------------------------------

def test_the_wizard_css_uses_logical_properties_for_rtl():
    text = TEMPLATE.read_text(encoding="utf-8")
    style = re.search(r"<style>(.*?)</style>", text, re.S).group(1)
    physical = re.findall(
        r"(?:margin|padding|border)-(?:left|right)(?:-color)?\s*:"
        r"|text-align\s*:\s*(?:left|right)\b"
        r"|(?<![-\w])(?:left|right)\s*:",
        style,
    )
    assert not physical, f"physical left/right properties block RTL layout: {physical[:6]}"
