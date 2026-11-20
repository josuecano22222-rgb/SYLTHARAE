"""No handler may hand a client the text of an unexpected exception.

AUDIT-LEAK-01/02: five list pages flashed the raw exception to the user
(``flash(f"Error loading sources: {str(e)}")``) - SQL fragments, column names and
file paths reach a screenshot that way - and two more put it in a rendered page
and in a JSON ``message``.

The rule this test enforces, precisely: inside ``except Exception`` /
``except BaseException`` (or a bare ``except``), the arguments of a response
writer - ``jsonify``, ``render_template``, ``flash``, ``make_response`` - may not
interpolate the caught exception (``str(e)``, ``{e}``, ``{exc}``, ...).

``client_error`` / ``client_safe_message`` are how such a handler reports an
unexpected failure: they log the detail and answer with a fixed sentence plus a
correlation id. Branches that catch a *specific* class (``except ValueError``, a
domain error) are exempt: their text is authored for the reader and is the
reason the request failed.
"""

from __future__ import annotations

import ast
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SKIP_PARTS = {"build", "dist", ".venv", "node_modules", "test-doc", "tests",
               "__pycache__", ".git", "audit_report", "tools"}

_RESPONSE_WRITERS = {"jsonify", "render_template", "flash", "make_response"}
_BROAD = {"", "Exception", "BaseException"}
_LEAK_MARKERS = ("str(e", "str(exc", "str(error", "str(err", "{e}", "{exc}", "{error}")


def _leaks():
    for path in PROJECT_ROOT.rglob("*.py"):
        if _SKIP_PARTS & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            caught = ast.unparse(handler.type) if handler.type else ""
            if caught not in _BROAD:
                continue
            for call in ast.walk(handler):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in _RESPONSE_WRITERS):
                    continue
                source = ast.unparse(call)
                if any(marker in source for marker in _LEAK_MARKERS):
                    yield (path.relative_to(PROJECT_ROOT), call.lineno, caught or "bare",
                           source[:110])


def test_no_response_writer_interpolates_an_unexpected_exception():
    leaks = list(_leaks())
    assert not leaks, "an exception's own text reaches the client:\n" + "\n".join(
        f"  {path}:{line} (except {caught}) -> {tail}" for path, line, caught, tail in leaks)


def test_the_flash_sites_report_a_sentence_instead_of_the_exception():
    """The five pages that leaked, pinned individually."""
    for relative in ("Api/routes/sources.py", "Api/routes/keywords.py",
                     "Api/routes/words.py", "Api/blueprints/files.py"):
        for line in (PROJECT_ROOT / relative).read_text(encoding="utf-8").splitlines():
            if "flash(" in line:
                assert "{e}" not in line and "str(e)" not in line, f"{relative}: {line.strip()}"
