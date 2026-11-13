"""Regression: the job scheduler's dependency must be in the manifest.

Found by the round-10 clean-install drill. A fresh venv built from
``requirements.txt`` could not start the scheduler:

    WARNING apps.web.app: Job scheduler not started: ModuleNotFoundError

because ``services/scheduling/scheduler.py`` imports ``apscheduler`` and no
manifest declares it - not ``requirements.txt``, not
``offline-bundle/requirements.txt``. apps/web/app.py swallows the failure by
design (a scheduler that cannot start must not stop the web app), so a
production install silently never fired a due schedule: report runs, rule and
scenario evaluations simply did not happen, with one warning line as the only
evidence.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEDULER = PROJECT_ROOT / "services" / "scheduling" / "scheduler.py"

#: Modules that ship with Python; never declared in requirements.txt.
_STDLIB = set(sys.stdlib_module_names) | {"__future__", "typing"}


def _declared_requirements() -> set[str]:
    import re

    declared = set()
    for raw in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[><=!~\[; ]", line, maxsplit=1)[0].strip().lower()
        if name:
            declared.add(name)
    return declared


def _third_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                modules.add(node.module.split(".")[0])
    local = {p.name for p in PROJECT_ROOT.iterdir() if p.is_dir()}
    return {m for m in modules if m not in _STDLIB and m not in local}


def test_every_third_party_import_of_the_scheduler_is_declared():
    imports = _third_party_imports(SCHEDULER)
    assert "apscheduler" in imports, imports  # the drill's finding, pinned
    declared = _declared_requirements()
    undeclared = {name for name in imports if name.lower() not in declared}
    assert not undeclared, (
        f"{SCHEDULER.relative_to(PROJECT_ROOT)} imports {sorted(undeclared)} "
        f"but requirements.txt does not declare them"
    )


def test_a_scheduler_that_cannot_start_says_which_module_is_missing(caplog, monkeypatch):
    """The warning has to name the module, not only the exception class."""
    import builtins

    real_import = builtins.__import__

    def _refuse(name, *args, **kwargs):
        if name == "services.scheduling.scheduler":
            raise ModuleNotFoundError("No module named 'apscheduler'", name="apscheduler")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "services.scheduling.scheduler", None)
    monkeypatch.setattr(builtins, "__import__", _refuse)

    from apps.web import app as app_module

    with caplog.at_level(logging.WARNING):
        app_module._start_scheduler()

    messages = [record.getMessage() for record in caplog.records]
    assert any("scheduler" in message.lower() for message in messages), messages
    assert any("apscheduler" in message for message in messages), (
        "the warning hides the missing module name: " + repr(messages)
    )
