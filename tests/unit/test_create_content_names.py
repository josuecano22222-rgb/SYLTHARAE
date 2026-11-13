"""Guards the NameError class of defect in ``contents_db_service``.

The runtime log showed::

    File ".../contents_db_service.py", line 534, in create_content
        f"{len(words):,}", path_id,
    NameError: name 'path_id' is not defined

``create_content`` takes ``hash_id`` but its body referenced ``path_id``; it
only fired for documents with >100 000 words (the ``len(words) > 100000``
logging branch), which is why 14 of 15 files stored and ``ss.xlsx`` failed.

This test uses :mod:`symtable` (same machinery as the interpreter's scope
resolution) to prove every name referenced inside the protected functions is
bound somewhere: a parameter, a local, a module-level name, or a builtin.
Any reintroduction of a body/parameter name mismatch fails here before it
can fail in production - and unlike pyflakes, this runs with the standard
library alone.
"""

from __future__ import annotations

import ast
import builtins
import symtable
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_SERVICE = PROJECT_ROOT / "database" / "services" / "contents_db_service.py"

PROTECTED_FUNCTIONS = (
    "create_content",
    "create_content_from_symbols",
    "process_full_document",
)


def _iter_tables(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _iter_tables(child)


def _find_function_tables(module_table: symtable.SymbolTable, name: str) -> list[symtable.SymbolTable]:
    return [
        t
        for t in _iter_tables(module_table)
        if t.get_name() == name and t.get_type() in ("function", "lambda")
    ]


def _module_and_builtin_names(module_table: symtable.SymbolTable) -> set[str]:
    names = {sym.get_name() for sym in module_table.get_symbols()}
    names |= set(dir(builtins))
    return names


def test_protected_functions_have_no_unbound_references():
    source = DB_SERVICE.read_text(encoding="utf-8")
    module_table = symtable.symtable(source, str(DB_SERVICE), "exec")
    known = _module_and_builtin_names(module_table)

    checked = 0
    for fn_name in PROTECTED_FUNCTIONS:
        fn_tables = _find_function_tables(module_table, fn_name)
        assert fn_tables, f"function {fn_name} not found in contents_db_service"
        for fn_table in fn_tables:
            checked += 1
            unbound = []
            for sym in fn_table.get_symbols():
                # A name that resolves to the global scope without ever
                # being a parameter/import/assignment in this scope is a
                # runtime NameError waiting to happen.
                if not sym.is_global():
                    continue
                if sym.is_parameter() or sym.is_assigned() or sym.is_imported():
                    continue
                if sym.get_name() not in known:
                    unbound.append(sym.get_name())
            assert not unbound, (
                f"{fn_name} references name(s) {sorted(set(unbound))} that are "
                "not parameters, locals, module-level names or builtins - "
                "this is a NameError at runtime (the ss.xlsx failure class)"
            )
    assert checked >= 3


def test_create_content_parameters_cover_body_usage():
    """Explicit AST check: ``path_id``/``hash_id`` naming stays consistent."""
    tree = ast.parse(DB_SERVICE.read_text(encoding="utf-8"))

    funcs = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_content"
    ]
    assert funcs, "create_content not found"
    for func in funcs:
        args = {a.arg for a in func.args.args + func.args.kwonlyargs}
        if func.args.vararg:
            args.add(func.args.vararg.arg)
        if func.args.kwarg:
            args.add(func.args.kwarg.arg)
        # The signature must take exactly one of the two id names - and the
        # body must agree with whichever it takes.
        id_names = args & {"path_id", "hash_id"}
        assert len(id_names) == 1, (
            f"create_content must take exactly one of path_id/hash_id, got {sorted(id_names)}"
        )
        other = ({"path_id", "hash_id"} - id_names).pop()
        used = {
            n.id for n in ast.walk(func)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        assert other not in used, (
            f"create_content takes {id_names.pop()} but its body reads {other}"
        )
