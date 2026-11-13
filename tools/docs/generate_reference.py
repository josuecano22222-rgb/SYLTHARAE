#!/usr/bin/env python3
"""Generate the source-code and HTTP reference under ``docs/reference/``.

Two outputs, both derived from the code so they cannot drift:

* ``docs/reference/python/<package>.md`` - every module of a first-party
  package with its docstring summary, classes (with methods) and public
  functions, signatures included. Read with :mod:`ast`; nothing is imported,
  so optional dependencies do not need to be installed.
* ``docs/reference/HTTP_ROUTES.md`` - every rule in the Flask URL map with
  its methods, endpoint, view function location and access class. This part
  imports the application (no database connection is opened).

Usage::

    python3 tools/docs/generate_reference.py            # write both
    python3 tools/docs/generate_reference.py --check    # exit 1 if stale

``--check`` is what CI and ``tests/unit/test_reference_docs.py`` use.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reference"

#: First-party packages, in the order a reader meets them (entry points first).
PACKAGES: Tuple[str, ...] = (
    "apps", "Api", "core", "database", "pipeline", "reader_file", "services",
    "settings", "concurrency", "Hdg_Err_Ex_Log", "scripts", "tools",
)
#: Top-level scripts documented on the entry-points page.
ENTRY_POINTS: Tuple[str, ...] = (
    "run_web.py", "run_cli.py", "run_import.py", "install.py",
    "verify_readiness.py", "version.py",
)
SKIP_DIRS = {"__pycache__", "tests", "node_modules", ".venv", "venv"}


def _summary(node) -> str:
    doc = ast.get_docstring(node) or ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line.replace("|", "\\|")
    return ""


def _annotation(node: Optional[ast.AST]) -> str:
    return "" if node is None else ast.unparse(node)


def _signature(fn: ast.AST) -> str:
    args = fn.args
    parts: List[str] = []
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for i, (arg, default) in enumerate(zip(positional, defaults)):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {_annotation(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        parts.append(text)
        if args.posonlyargs and i == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {_annotation(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        parts.append(text)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    ret = f" -> {_annotation(fn.returns)}" if fn.returns is not None else ""
    prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    sig = f"{prefix}{fn.name}({', '.join(parts)}){ret}"
    return sig if len(sig) <= 160 else sig[:157] + "..."


def _iter_modules(package: str) -> Iterable[Path]:
    base = ROOT / package
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield path


def _decorators(node) -> List[str]:
    return [ast.unparse(d) for d in getattr(node, "decorator_list", [])]


def _module_section(path: Path) -> List[str]:
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # reported, not hidden
        return [f"### `{rel}`", "", f"> Could not be parsed: {exc.msg} (line {exc.lineno}).", ""]
    lines = [f"### `{rel}`", ""]
    summary = _summary(tree)
    lines.append(summary or "_No module docstring._")
    lines.append("")
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for cls in classes:
        bases = ", ".join(ast.unparse(b) for b in cls.bases)
        lines.append(f"- **class `{cls.name}`**{f'({bases})' if bases else ''}"
                     f" - {_summary(cls) or '_undocumented_'}")
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("_") and item.name != "__init__":
                    continue
                tag = ""
                decos = _decorators(item)
                if "staticmethod" in decos:
                    tag = " *(static)*"
                elif "classmethod" in decos:
                    tag = " *(class)*"
                elif "property" in decos:
                    tag = " *(property)*"
                doc = _summary(item)
                lines.append(f"  - `{_signature(item)}`{tag}{f' - {doc}' if doc else ''}")
    for fn in functions:
        routes = [d for d in _decorators(fn) if ".route(" in d or d.startswith(("bp.", "app."))]
        route = f" <sub>{routes[0]}</sub>" if routes else ""
        doc = _summary(fn)
        private = fn.name.startswith("_")
        if private and not routes:
            continue
        lines.append(f"- `{_signature(fn)}`{route}{f' - {doc}' if doc else ''}")
    lines.append("")
    return lines


def python_reference(package: str) -> str:
    modules = list(_iter_modules(package))
    out = [f"# `{package}` - Python reference", "",
           "_Generated by `tools/docs/generate_reference.py`; do not edit by hand._", "",
           f"{len(modules)} modules. Private helpers (leading "
           "underscore) are omitted unless they are routes.", ""]
    for path in modules:
        out.extend(_module_section(path))
    return "\n".join(out).rstrip() + "\n"


def entry_points_reference() -> str:
    out = ["# Entry points - Python reference", "",
           "_Generated by `tools/docs/generate_reference.py`; do not edit by hand._", ""]
    for name in ENTRY_POINTS:
        path = ROOT / name
        if path.exists():
            out.extend(_module_section(path))
    return "\n".join(out).rstrip() + "\n"


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: The installation wizard is public only until the database is initialised;
#: each endpoint then closes in its own way (``Api/routes/setup.py``).
SETUP_WINDOW: Dict[str, Tuple[str, str]] = {
    "setup.setup_page": ("public until installed, then redirects", "-"),
    "setup.check_setup_status": ("public (reports installed yes/no)", "-"),
    "setup.system_check": ("public until installed, then admin", "-"),
    "setup.test_database": ("-", "public until installed, then admin"),
    "setup.run_installation": ("-", "public until installed, then 409"),
}


def _baseline_access(app, endpoint: str, methods: List[str]) -> Tuple[str, str]:
    """The (read, write) policy ``core.security.flask_ext`` applies before any
    view-level decorator. Decorators can tighten it, never loosen it."""
    cfg = app.config
    if endpoint in SETUP_WINDOW:
        return SETUP_WINDOW[endpoint]
    if endpoint in cfg.get("AUTH_PUBLIC_ENDPOINTS", ()) or endpoint.startswith("health."):
        return "public", "public"
    blueprint = endpoint.split(".")[0] if "." in endpoint else ""
    admin_read = (blueprint in cfg.get("AUTH_ADMIN_READ_BLUEPRINTS", ())
                  or endpoint in cfg.get("AUTH_ADMIN_READ_ENDPOINTS", ())
                  or blueprint in ("settings", "settings_api"))
    read = "admin" if admin_read else "any user"
    write = "admin" if blueprint in cfg.get("AUTH_ADMIN_BLUEPRINTS", ()) else "analyst+"
    has_read = any(m in ("GET", "HEAD") for m in methods)
    has_write = any(m in WRITE_METHODS for m in methods)
    return (read if has_read else "-"), (write if has_write else "-")


def _view_location(app, endpoint: str) -> str:
    view = app.view_functions.get(endpoint)
    if view is None:
        return ""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    code = getattr(view, "__code__", None)
    if code is None:
        return ""
    try:
        rel = Path(code.co_filename).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return ""
    return rel


def http_reference(app) -> str:
    rules = sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint))
    rows = []
    for rule in rules:
        if rule.endpoint == "static" or rule.endpoint.endswith(".static"):
            continue
        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        read, write = _baseline_access(app, rule.endpoint, methods)
        rows.append((rule.rule, ", ".join(methods), rule.endpoint, read, write,
                     _view_location(app, rule.endpoint)))
    groups: Dict[str, int] = {}
    for row in rows:
        key = "/" + row[0].strip("/").split("/")[0] if row[0] != "/" else "/"
        if key == "/api":
            parts = row[0].strip("/").split("/")
            key = "/api/" + (parts[1] if len(parts) > 1 else "")
        groups[key] = groups.get(key, 0) + 1
    out = ["# HTTP routes", "",
           "_Generated by `tools/docs/generate_reference.py` from the live Flask URL map; "
           "do not edit by hand._", "",
           f"{len(rows)} routes (static files excluded).", "",
           "**Access** is the baseline policy enforced for every request by "
           "`core/security/flask_ext.py` (default deny):", "",
           "- `public` - reachable without a session (the explicit allow-list).",
           "- `any user` - any authenticated role (viewer, analyst, admin).",
           "- `analyst+` - analyst or admin (every mutating method by default).",
           "- `admin` - administrators only.", "",
           "A view may tighten this with `@roles_required`/`@admin_required`, never "
           "loosen it. The setup probes are additionally closed after installation "
           "(`Api/routes/setup.py`). Every mutating request also needs a CSRF token.", "",
           "## Route groups", "", "| Prefix | Routes |", "| --- | --- |"]
    out += [f"| `{k}` | {v} |" for k, v in sorted(groups.items())]
    out += ["", "## All routes", "",
            "| Rule | Methods | Endpoint | Read | Write | View |",
            "| --- | --- | --- | --- | --- | --- |"]
    for rule, methods, endpoint, read, write, loc in rows:
        out.append(f"| `{rule}` | {methods} | `{endpoint}` | {read} | {write} | "
                   f"{f'`{loc}`' if loc else ''} |")
    return "\n".join(out) + "\n"


def index_page(packages: List[str]) -> str:
    out = ["# Source reference", "",
           "_Generated by `tools/docs/generate_reference.py`; do not edit by hand._", "",
           "- [HTTP routes](HTTP_ROUTES.md) - every URL the web application serves.",
           "- [Database schema](DATABASE_SCHEMA.md) - every table, column, key and index "
           "(generated by `tools/docs/generate_schema.py`).",
           "- [Entry points](python/entry_points.md) - the top-level scripts."]
    for package in packages:
        count = len(list(_iter_modules(package)))
        out.append(f"- [`{package}`](python/{package}.md) - {count} modules.")
    return "\n".join(out) + "\n"


def build(include_http: bool = True) -> Dict[Path, str]:
    packages = [p for p in PACKAGES if (ROOT / p).is_dir()]
    files: Dict[Path, str] = {OUT / "README.md": index_page(packages),
                              OUT / "python" / "entry_points.md": entry_points_reference()}
    for package in packages:
        files[OUT / "python" / f"{package}.md"] = python_reference(package)
    if include_http:
        os.environ.setdefault("APP_DATA_DIR", str(ROOT / ".docs-appdata"))
        sys.path.insert(0, str(ROOT))
        from apps.web.app import app  # noqa: E402 - the URL map is the source

        files[OUT / "HTTP_ROUTES.md"] = http_reference(app)
    return files


def main(argv: List[str]) -> int:
    check = "--check" in argv
    files = build(include_http="--no-http" not in argv)
    stale = []
    for path, text in files.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        stale.append(path.relative_to(ROOT).as_posix())
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if check:
        if stale:
            print("stale reference docs (run python3 tools/docs/generate_reference.py):")
            print("\n".join(f"  {p}" for p in stale))
            return 1
        print("reference docs are up to date")
        return 0
    print(f"wrote {len(stale)} of {len(files)} reference files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
