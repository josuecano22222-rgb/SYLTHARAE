"""NARR-02: translation calls must not turn code identifiers into catalog entries.

``core/analytics/narrative.py`` renders a stored sentence through
``gettext``/``ngettext``. The sentence is *data* - read out of the record the run
stored - so the call has to read it into locals first. Passing the lookups
straight in offered the dict keys to the extractor, which walks the arguments of
a translation call for string constants:

    msgid "msgid"
    msgid_plural "msgid_plural"

Every catalog gained that pair; ``msgmerge`` then fuzzy-matched it onto the
existing "Side" entry and carried "side" into the Arabic, Hebrew, Persian and
Croatian catalogs, and no runtime path can ever look it up.

The tests pin the mechanism rather than the incident: a translation call may
take a literal (that is what extraction is *for*) but not a key it looks up at
runtime, and no catalog may carry such an entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from babel.messages.extract import extract_from_file
from babel.messages.frontend import ExtractMessages
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[2]

#: The extraction keywords this repository configures (``babel.cfg`` with the
#: keyword list in ``scripts/initialize_translations.py``), widened with the
#: ``pgettext``/``npgettext`` forms Babel knows: a superset can only *find*
#: more, so a clean result here is clean under the configured set too.
KEYWORDS = {"_": None, "gettext": None, "ngettext": (1, 2), "lazy_gettext": None,
            "pgettext": ((1, "c"), 2), "npgettext": ((1, "c"), 2, 3)}

#: Called as ``_(...)``, ``gettext(...)`` or ``self._t.ngettext(...)``, with
#: the argument positions that carry a *message* (``ngettext``'s third argument
#: is the count, which is allowed to be a lookup).
TRANSLATION_FUNCTIONS = {
    "_": (0,), "gettext": (0,), "lazy_gettext": (0,), "ugettext": (0,),
    "ngettext": (0, 1), "ungettext": (0, 1),
}

FIRST_PARTY = ("core", "services", "Api", "apps", "database", "reader_file",
               "settings", "pipeline", "tools", "scripts")

ENGINE = ROOT / "core" / "analytics" / "narrative.py"
CATALOGS = ("en", "ar", "he", "fa", "hr")


def _python_files():
    for package in FIRST_PARTY:
        root = ROOT / package
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _lookup_keys_in_translation_calls(path: Path):
    """``(line, text)`` for every string key handed to a translation call.

    The message arguments are searched for a subscript by a string constant -
    ``record["msgid"]``, ``row["title"]`` - which a message extractor reads as
    the message text. A literal argument, and an f-string or ``%``-formatted
    template, are the normal ways to write a message and are not reported.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in TRANSLATION_FUNCTIONS:
            continue
        for position in TRANSLATION_FUNCTIONS[name]:
            if position >= len(node.args):
                continue
            argument = node.args[position]
            for inner in ast.walk(argument):
                if not isinstance(inner, ast.Subscript):
                    continue
                key = inner.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((node.lineno, key.value))
    return found


def _extract(destination: Path, *input_dirs: str) -> Path:
    """Run the repository's own extraction into ``destination``."""
    command = ExtractMessages()
    command.initialize_options()
    command.mapping_file = str(ROOT / "babel.cfg")
    command.keywords = ["_", "gettext", "ngettext", "lazy_gettext"]
    command.output_file = str(destination)
    command.input_dirs = [str(ROOT / name) for name in input_dirs] or [str(ROOT)]
    command.ignore_dirs = ("/.venv", "/.git", "/node_modules", "/__pycache__")
    command.finalize_options()
    command.run()
    return destination


def _shape(path: Path):
    with path.open("rb") as handle:
        catalog = read_po(handle, locale="en")
    return sorted((message.id if isinstance(message.id, str) else tuple(message.id),
                   getattr(message, "id_plural", "") or "")
                  for message in catalog if message.id)


class TestNoTranslationCallPassesALookupKey:
    def test_the_narrative_engine_offers_no_message_of_its_own(self):
        """Its sentences come from the registry, not from a literal here."""
        assert extract_from_file("python", str(ENGINE), keywords=KEYWORDS) == []

    def test_no_translation_call_is_given_a_key_it_looks_up_at_runtime(self):
        offenders = []
        for path in _python_files():
            for line, text in _lookup_keys_in_translation_calls(path):
                offenders.append(f"{path.relative_to(ROOT)}:{line} {text!r}")
        assert offenders == [], (
            "a translation call reads its message out of a lookup, so the "
            f"extractor will offer the key as a message: {offenders[:6]}")


class TestTheExtractionIsDeterministic:
    def test_two_runs_describe_the_same_messages(self, tmp_path):
        first = _extract(tmp_path / "one.pot", "core", "services")
        second = _extract(tmp_path / "two.pot", "core", "services")
        shape = _shape(first)
        assert len(shape) > 150, "the extraction found almost nothing to compare"
        assert shape == _shape(second)


class TestTheCatalogsCarryNoArtifact:
    @pytest.mark.parametrize("lang", CATALOGS)
    def test_no_entry_is_an_extraction_argument(self, lang):
        path = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
        with path.open("rb") as handle:
            catalog = read_po(handle, locale=lang)
        offenders = []
        for message in catalog:
            if not message.id:
                continue
            singular = message.id if isinstance(message.id, str) else message.id[0]
            plural = getattr(message, "id_plural", "") or (
                message.id[1] if isinstance(message.id, tuple) and len(message.id) > 1 else "")
            if singular in ("msgid", "msgid_plural") or plural in ("msgid", "msgid_plural"):
                offenders.append((singular, plural))
        assert offenders == [], f"[{lang}] identifier pair kept as a message: {offenders}"
