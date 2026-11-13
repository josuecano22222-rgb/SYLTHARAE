"""Static guard (unit level, no database): the shared table macro contract.

Every template that calls ``record_table(...)`` imports it from
``components/table.html``. A missing import renders as a 500 at request
time (``UndefinedError: 'record_table' is undefined`` - field defect
ERR-20260929-000002), which the integration render tests
(``test_list_pages_render.py``) catch for the registered routes; this guard
catches any *new* caller the moment it is written.
"""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"
DEFINITION = "components/table.html"

IMPORT_RE = re.compile(
    r"\{%-?\s*from\s+'components/table\.html'\s+import[^%]*?\brecord_table\b",
    re.S)
CALL_RE = re.compile(r"\brecord_table\s*\(")


def test_every_record_table_caller_imports_it():
    offenders = []
    checked = 0
    for path in TEMPLATES.rglob("*.html"):
        rel = str(path.relative_to(TEMPLATES)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        if rel == DEFINITION or "macro record_table(" in text:
            continue
        if CALL_RE.search(text):
            checked += 1
            if not IMPORT_RE.search(text):
                offenders.append(rel)
    assert checked > 0, "no caller found: the guard is pointed at nothing"
    assert offenders == [], (
        "these templates call record_table without importing it from "
        f"{DEFINITION}: {offenders}")
