"""The schema reference must survive a write/read round trip.

``pg_get_constraintdef`` hands back the CHECK expression verbatim. Migration
``m0005`` compares two columns against an explicit whitespace class, so its
constraint text contains a real tab, CR, FF and VT. Written into markdown as-is
they split the line, and reading the file back (text mode, universal newlines)
turns the CR into an LF - so ``TARGET.read_text() == generate(conn)`` could
never hold and ``tests/integration/test_schema_reference.py`` failed even
against a file the generator had just written. Found in round 21.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.docs.generate_schema import render  # noqa: E402

WHITESPACE_CLASS = "CHECK ((btrim((name)::text, ' \t\n\r\f\v'::text) <> ''::text))"


def _rendered():
    columns = [("term_names", "name", "character varying", "NO", None, 255, 1, "NO")]
    constraints = [("term_names", "term_names_name_check", "c", WHITESPACE_CLASS)]
    return render(columns, constraints, [], [("0005", "term_names")])


def test_a_constraint_with_control_characters_stays_on_one_line():
    text = _rendered()
    line = next(ln for ln in text.splitlines() if "term_names_name_check" in ln)
    assert "\t" not in line and "\r" not in line and "\f" not in line
    assert "\\t" in line and "\\r" in line and "\\f" in line


def test_the_generated_text_round_trips_through_a_file(tmp_path):
    text = _rendered()
    target = tmp_path / "schema.md"
    target.write_text(text, encoding="utf-8")
    assert target.read_text(encoding="utf-8") == text
