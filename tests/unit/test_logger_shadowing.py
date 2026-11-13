"""No function may use a module-level ``logger`` before binding its own.

This is a repo-wide guard for the class of defect that cost the legacy
PowerPoint reader every file it was given:

    def read_ppt_file(self, filepath):
        if logger is None:                      # UnboundLocalError
            logger = logging.getLogger(__name__)

A name bound anywhere in a function body is local to that function for its
entire execution, so the condition above cannot even be evaluated. The failure
mode is nasty out of proportion to its size:

* it fires on the *first* statement, so no work is attempted at all;
* the message mentions ``logger``, not the file or the feature, so it reads
  like an infrastructure problem;
* it is silent in review, because the assignment is visible right there next to
  the use.

A local assignment that happens *before* any use (a logging call inside an
``except`` block, a debug helper) is redundant rather than broken - the module
logger is already available - so only the use-before-bind order is an error
here. That keeps the guard focused on the defect and does not force a
repo-wide style change.
"""

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
    ".arena", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


def _modules():
    for path in sorted(Path(PROJECT_ROOT).rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _binds_module_logger(module: ast.Module) -> bool:
    """True when the module defines ``logger`` at import time."""
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "logger"
            for target in node.targets
        ):
            return True
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "logger":
                return True
    return False


def _functions(module: ast.Module):
    """Every method/function in the module, at any nesting depth."""
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _logger_uses(function: ast.AST):
    """(lines where ``logger`` is read, lines where it is bound)."""
    reads, binds = [], []
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id == "logger":
            (binds if isinstance(node.ctx, ast.Store) else reads).append(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == "logger":
                    binds.append(node.lineno)
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and "logger" in node.names:
            binds.append(node.lineno)
    return reads, binds


def _violations():
    found = []
    for path in _modules():
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            module = ast.parse(source)
        except (SyntaxError, OSError):  # pragma: no cover - unparsable file
            continue
        if not _binds_module_logger(module):
            continue
        for function in _functions(module):
            reads, binds = _logger_uses(function)
            if reads and binds and min(reads) < min(binds):
                found.append((
                    str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    function.name,
                    min(reads),
                    min(binds),
                ))
    return found


def test_no_function_reads_logger_before_binding_it():
    violations = _violations()

    assert not violations, (
        "these functions read `logger` on a line before they bind it locally,"
        " which makes the read an UnboundLocalError at runtime: "
        + "; ".join(f"{path}:{line} {name}()" for path, name, line, _ in violations)
    )


def test_the_guard_detects_the_shape_it_is_meant_to_catch():
    """A guard that cannot fail is not a guard."""
    broken = ast.parse(
        "def read(f):\n"
        "    if logger is None:\n"
        "        logger = logging.getLogger(__name__)\n"
        "    return logger\n"
    )
    function = next(_functions(broken))
    reads, binds = _logger_uses(function)

    assert reads and binds and min(reads) < min(binds)


def test_the_guard_accepts_a_local_logger_bound_before_use():
    """Redundant-but-safe assignments (an `except` block binding its own
    logger) must not be reported: they are common in this codebase and are not
    the defect."""
    safe = ast.parse(
        "def load():\n"
        "    logger = logging.getLogger(__name__)\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception as exc:\n"
        "        logger.debug('failed: %s', exc)\n"
        "        raise\n"
    )
    function = next(_functions(safe))
    reads, binds = _logger_uses(function)

    assert reads and binds and min(reads) > min(binds)
