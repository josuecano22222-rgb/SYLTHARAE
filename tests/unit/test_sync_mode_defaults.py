"""Unit: where `apply` is allowed to come from, and where `check` is the default.

The workbook is authoritative, so an edit that never reaches the database is a
lost edit; the same authority, wired to the wrong entry point, turns a truncated
file into a deletion. The rule this module pins:

    ``check`` is the default everywhere. Exactly one entry point opts in to
    ``apply`` - the served deployment - and it opts in with ``setdefault``, so an
    operator's environment always wins.

"Exactly one" is checked by parsing every Python file in the repository, not by
reading three modules and trusting them.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.importing import taxonomy_startup as startup  # noqa: E402

KEY = "SYLTHARAE_TAXONOMY_SYNC"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist",
             ".pytest_cache"}


def _python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _env_writes():
    """Every place that writes ``SYLTHARAE_TAXONOMY_SYNC``, and how."""
    found = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:                                 # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in (
                    "setdefault", "__setitem__"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value == KEY:
                found.append((str(path.relative_to(ROOT)), func.attr,
                              node.args[1].value if len(node.args) > 1
                              and isinstance(node.args[1], ast.Constant) else "?"))
    return found


def test_the_module_default_is_check(monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    assert startup.mode_from_env() == "check"


def test_the_served_entry_point_is_the_only_place_that_applies():
    writes = _env_writes()
    assert writes, "nothing sets the mode at all, so nothing would ever apply"
    appliers = [row for row in writes if row[2] in ("apply", "1", "yes", "true")]
    assert appliers == [("run_web.py", "setdefault", "apply")], appliers


def test_the_served_entry_point_uses_setdefault_so_the_operator_wins():
    writes = _env_writes()
    mode, mechanism, _value = writes[0]
    assert mode == "run_web.py"
    assert mechanism == "setdefault", (
        "an unconditional assignment would override the operator's environment")
    source = (ROOT / "run_web.py").read_text(encoding="utf-8")
    assert f"{KEY}'" in source or f'{KEY}"' in source


def test_an_operator_setting_check_wins_over_the_served_default():
    """`setdefault` semantics, stated as the property that matters."""
    import os

    saved = os.environ.get(KEY)
    try:
        os.environ[KEY] = "check"
        before = os.environ[KEY]
        os.environ.setdefault(KEY, "apply")      # what run_web.py does
        assert os.environ[KEY] == before == "check"
        assert startup.mode_from_env() == "check"
    finally:
        if saved is None:
            os.environ.pop(KEY, None)
        else:
            os.environ[KEY] = saved


def test_every_spelling_of_off_is_off_and_every_spelling_of_apply_is_apply():
    for spelling in ("0", "off", "no", "false", "OFF"):
        assert spelling.strip().lower() in startup.OFF_MODES, spelling
    for spelling in ("apply", "1", "yes", "true", "APPLY"):
        assert spelling.strip().lower() in startup.APPLY_MODES, spelling


def test_a_typo_in_the_mode_is_not_treated_as_apply(monkeypatch):
    """`checkk` must mean "tell me", never "write"."""
    monkeypatch.setenv(KEY, "checkk")
    assert startup.mode_from_env() not in startup.APPLY_MODES
