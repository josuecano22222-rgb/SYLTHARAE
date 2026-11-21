"""A ratchet on code shape: two measured defects may not get worse.

AUDIT-SIZE-01 measured, over this repository:

    11,453 functions;  122 longer than 150 lines;  27 longer than 300
    worst: Api/routes/api.py::register_api_routes 1,862 lines
           Api/routes/keywords.py::register_keywords_routes 1,646
           Api/routes/archives.py::register_archives_routes 1,400

AUDIT-N1-01 measured 91 query calls nested inside a loop (the classic N+1
shape: one round trip per row) using its own hand-checked rule. The AST rule
below is broader - it counts every ``execute``/``fetch*``/``query`` call
anywhere inside a loop body - and finds 171. The ceiling is this rule's number,
so the test is self-consistent; the audit's 91 was the hand-verified subset of
real N+1s.

Splitting 122 functions is a refactor with its own risk profile and does not
belong in the same change as a correctness fix. What does belong here is a
*ratchet*: the numbers below are the current ceiling. A change that makes any
of them worse fails this test and has to justify itself; a change that improves
them lowers the ceiling in the same commit. The defect cannot grow silently
while the refactor is pending.

The numbers are counts, not budgets: the test does not reward making a function
longer than 300 lines shorter, and it never fails for code that is merely new
within the limits.
"""

from __future__ import annotations

import ast
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Directories that are not this project's source.
_SKIP_PARTS = {"build", "dist", ".venv", "node_modules", "test-doc", "tests",
               "__pycache__", ".git", "audit_report"}

#: Ceilings, measured on the commit that introduced this test. Lower them when
#: you split a function or remove a nested query; never raise them by more than
#: the diff that caused it.
MAX_FUNCTIONS_OVER_150 = 122
MAX_FUNCTIONS_OVER_300 = 27
MAX_LONGEST_FUNCTION = 1862
MAX_LOOP_NESTED_QUERIES = 171

#: Query calls that fetch or execute inside a loop are the N+1 shape.
_QUERY_ATTRS = {"execute", "fetchone", "fetchall", "fetchmany", "query"}


def _parse_all():
    """Parse every source file once; the three checks share the result."""
    parsed = []
    for path in PROJECT_ROOT.rglob("*.py"):
        if _SKIP_PARTS & set(path.parts):
            continue
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8", errors="replace"))))
        except SyntaxError:  # a broken file is another test's job
            continue
    return parsed


def _sources():
    return _PARSED


def _functions():
    for path, tree in _PARSED:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                yield path, node, length


def test_no_function_got_longer_than_the_measured_worst():
    longest = max(((length, str(path), node.name) for path, node, length in _functions()),
                  default=(0, "-", "-"))
    assert longest[0] <= MAX_LONGEST_FUNCTION, (
        f"{longest[1]}::{longest[2]} is {longest[0]} lines; the ceiling is "
        f"{MAX_LONGEST_FUNCTION}. Split it instead of raising this number.")


def test_the_number_of_long_functions_did_not_grow():
    lengths = [length for _, _, length in _functions()]
    over_150 = sum(1 for n in lengths if n > 150)
    over_300 = sum(1 for n in lengths if n > 300)
    assert over_150 <= MAX_FUNCTIONS_OVER_150, (
        f"{over_150} functions exceed 150 lines (ceiling {MAX_FUNCTIONS_OVER_150})")
    assert over_300 <= MAX_FUNCTIONS_OVER_300, (
        f"{over_300} functions exceed 300 lines (ceiling {MAX_FUNCTIONS_OVER_300})")


def _loop_nested_query_count() -> int:
    """Calls on a database cursor that sit inside a loop in the same function."""
    total = 0
    for _, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, (ast.For, ast.AsyncFor, ast.While)):
                    continue
                for call in ast.walk(inner):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr in _QUERY_ATTRS):
                        total += 1
    return total


def test_no_new_query_inside_a_loop():
    count = _loop_nested_query_count()
    assert count <= MAX_LOOP_NESTED_QUERIES, (
        f"{count} query calls are nested in loops (ceiling "
        f"{MAX_LOOP_NESTED_QUERIES}); batch them into one statement")


# Parsed once, after every helper above is defined.
_PARSED = _parse_all()
