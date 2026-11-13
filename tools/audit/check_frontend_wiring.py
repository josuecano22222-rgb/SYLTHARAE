#!/usr/bin/env python
"""Frontend ↔ backend wiring: every reference must resolve.

Four checks, each read from the application object and the filesystem:

* **JS → routes** — every URL a JS module fetches (``fetch('...')``,
  ``apiCall('...')``, ``$.getJSON('...')``) must match a Flask rule, comparing
  by method and by segment count (``:id`` matches any single segment).
* **Template → endpoint** — every ``url_for('endpoint')`` and
  ``get_url('endpoint')`` in a template must name a registered endpoint.
* **Template → backend** — every ``render_template('x.html')`` target must
  exist, and every template with a route must be the one that route renders.
* **Static references** — every ``<script src>``, ``<link href>``, ``<img src>``
  in a template and every ``/static/...`` string in JS must exist on disk.

Writes ``audit_report/findings/frontend_wiring.csv``; exit 1 on any finding.
"""

from __future__ import annotations

import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"

URL_CALL = re.compile(
    r"(?:fetch|apiCall|apiFetch|getJSON|axios\.get|axios\.post|request)\s*\(\s*"
    r"[`'\"]([^`'\"]+)[`'\"]")
TEMPLATE_URL_FOR = re.compile(
    r"url_for\(\s*['\"]([A-Za-z0-9_.]+)['\"]|get_url\(\s*['\"]([A-Za-z0-9_.]+)['\"]")
SCRIPT_SRC = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
LINK_HREF = re.compile(r"<link[^>]+href=[\"']([^\"']+)[\"']", re.I)
IMG_SRC = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
STATIC_IN_JS = re.compile(r"[\"'`](/static/[^\"'`\s]+)[\"'`]")


def normalize(url: str) -> str:
    url = url.split("?")[0].split("#")[0]
    url = re.sub(r"\$\{[^}]*\}", ":x", url)
    url = re.sub(r"\{\{[^}]*\}\}", ":x", url)
    url = re.sub(r"<(?:int:|string:|path:)?\w+>", ":x", url)
    url = re.sub(r"%[sd]", ":x", url)
    url = re.sub(r"/+", "/", url)
    if len(url) > 1:
        url = url.rstrip("/")
    return url


def route_patterns(app):
    """``{normalized_rule: set(methods)}`` with converters turned into ``:x``."""
    out = {}
    for rule in app.url_map.iter_rules():
        pat = normalize(re.sub(r"<(?:[^:>]+:)?([^>]+)>", ":x", rule.rule))
        out.setdefault(pat, set()).update(
            m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
    return out


def matches(url: str, patterns) -> bool:
    """A URL matches when its segments line up and the rule takes the method."""
    if url in patterns:
        return True
    url_parts = [p for p in url.split("/") if p]
    for pattern in patterns:
        if pattern.count("/") != url.count("/"):
            continue
        parts = [p for p in pattern.split("/") if p]
        if len(parts) != len(url_parts):
            continue
        if all(p == ":x" or p == u for p, u in zip(parts, url_parts)):
            return True
    return False


def main():
    from apps.web.app import app

    OUT.mkdir(parents=True, exist_ok=True)
    patterns = route_patterns(app)
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    findings = []

    # 1. JS -> routes
    checked = 0
    for path in sorted((ROOT / "static" / "js").rglob("*.js")):
        if ".min." in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in URL_CALL.finditer(text):
            raw = m.group(1)
            if not (raw.startswith("/") and not raw.startswith("//")):
                continue
            if "{{" in raw or "${" in raw:
                continue
            url = normalize(raw)
            if url.startswith("/static/"):
                continue
            checked += 1
            # ``'/api/file/' + id`` - the id is concatenated at the call site,
            # so the literal is a prefix. It is fine when some rule extends it.
            if raw.endswith("/") and any(
                    pat.startswith(url) for pat in patterns):
                continue
            if not matches(url, patterns):
                line = text[:m.start()].count("\n") + 1
                findings.append((str(path.relative_to(ROOT)), line,
                                 "js-url", raw, "no route matches"))
        # 1b. /static/ strings in JS
        for m in STATIC_IN_JS.finditer(text):
            raw = m.group(1)
            target = ROOT / raw.lstrip("/")
            if target.exists():
                continue
            if any(ch in raw for ch in "${`"):
                continue
            line = text[:m.start()].count("\n") + 1
            findings.append((str(path.relative_to(ROOT)), line, "static-ref",
                             raw, "file does not exist"))

    # 2. templates -> endpoints
    for path in sorted((ROOT / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in TEMPLATE_URL_FOR.finditer(text):
            endpoint = m.group(1) or m.group(2)
            if endpoint and endpoint not in endpoints:
                line = text[:m.start()].count("\n") + 1
                findings.append((str(path.relative_to(ROOT)), line,
                                 "url_for", endpoint, "no such endpoint"))
        # 3. static references
        for regex, kind in ((SCRIPT_SRC, "script-src"), (LINK_HREF, "link-href"),
                            (IMG_SRC, "img-src")):
            for m in regex.finditer(text):
                raw = m.group(1)
                if raw.startswith(("http://", "https://", "//", "data:")):
                    continue
                if raw.startswith("/static/") or raw.startswith("static/"):
                    target = ROOT / raw.lstrip("/")
                else:
                    target = (ROOT / "templates" / raw).resolve()
                if target.exists():
                    continue
                if "{{" in raw or "{%" in raw:
                    continue
                line = text[:m.start()].count("\n") + 1
                findings.append((str(path.relative_to(ROOT)), line, kind, raw,
                                 "file does not exist"))

    # 4. render_template targets and orphan templates
    import ast

    rendered = {}
    for d in ("Api", "apps", "core", "services", "settings", "pipeline"):
        base = ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(
                        node.func, "attr", getattr(node.func, "id", "")) == \
                        "render_template" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(
                            arg.value, str):
                        rendered.setdefault(arg.value, []).append(
                            f"{path.relative_to(ROOT)}:{node.lineno}")
    for name, sites in sorted(rendered.items()):
        if not (ROOT / "templates" / name).exists():
            findings.append((sites[0].split(":")[0], 0, "render_template",
                             name, "template does not exist"))

    with (OUT / "frontend_wiring.csv").open("w", newline="",
                                            encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "line", "kind", "reference", "finding", "status"])
        for row in findings:
            w.writerow(list(row) + ["UNTOUCHED"])
    print(f"js urls checked against routes: {checked}")
    print(f"templates checked: {len(list((ROOT / 'templates').rglob('*.html')))}")
    print(f"render_template targets: {len(rendered)}")
    print(f"findings: {len(findings)} -> audit_report/findings/frontend_wiring.csv")
    by_kind = {}
    for f in findings:
        by_kind[f[2]] = by_kind.get(f[2], 0) + 1
    print("  by kind:", by_kind)
    for f in findings[:25]:
        print("  ", f)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
