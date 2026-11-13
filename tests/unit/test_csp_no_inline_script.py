"""RES-CSP-01: the Content-Security-Policy forbids inline script, and nothing needs it.

The policy used to carry ``script-src 'self' 'unsafe-inline'`` because 517
template attributes, 82 generated attributes and 17 ``<script>`` blocks were
inline script. With that allowance any injected markup could run script, so the
policy protected little. The handlers are now ``data-on-<event>`` attributes run
by ``static/js/modules/core/declarative-events.js`` (a restricted interpreter -
no ``eval``), and the blocks are static files fed by ``application/json`` data.

These tests keep it that way:

* the served policy has no ``'unsafe-inline'`` / ``'unsafe-eval'`` for script;
* no template or script source contains an inline handler attribute or an
  executable inline ``<script>``;
* every page the application serves renders none either, and loads the runtime
  whenever it uses ``data-on-*``;
* every function a ``data-on-*`` handler calls is reachable from ``window``
  (where the runtime looks it up) - a name only visible to inline script would
  silently stop working;
* the runtime keeps the inline-handler contract and refuses dangerous names
  (``tests/js/declarative_events_smoke.mjs``).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
SCRIPTS = PROJECT_ROOT / "static" / "js"
RUNTIME = SCRIPTS / "modules" / "core" / "declarative-events.js"
HARNESS = PROJECT_ROOT / "tests" / "js" / "declarative_events_smoke.mjs"

# Every event attribute HTML defines that the markup could plausibly use.
HANDLER_EVENTS = (
    "abort|afterprint|animationend|auxclick|beforeinput|beforeprint|beforeunload|blur|"
    "cancel|change|click|close|contextmenu|copy|cut|dblclick|drag|dragend|dragenter|"
    "dragleave|dragover|dragstart|drop|error|focus|focusin|focusout|hashchange|input|"
    "invalid|keydown|keypress|keyup|load|message|mousedown|mouseenter|mouseleave|"
    "mousemove|mouseout|mouseover|mouseup|paste|pointerdown|pointerup|popstate|reset|"
    "resize|scroll|select|submit|toggle|touchend|touchstart|transitionend|unload|wheel"
)
# An attribute: preceded by whitespace/quote/tag end, followed by = and a value.
HANDLER_ATTRIBUTE = re.compile(
    r"""(?:^|[\s"'>}])on(?:""" + HANDLER_EVENTS + r""")\s*=\s*(?:\\?["'{]|[^\s>])""",
    re.IGNORECASE)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
JS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\\\"'])//[^\n]*", re.S)
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)(?![^>]*type\s*=\s*[\"']application/(?:ld\+)?json[\"'])[^>]*>",
    re.IGNORECASE)
DATA_ON = re.compile(r"""\bdata-on-([a-z]+)\s*=\s*(\\?["'])""")


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _scripts():
    return sorted(path for path in SCRIPTS.rglob("*.js")
                  if "dist" not in path.parts and "vendor" not in path.parts
                  and ".min." not in path.name)


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------
class TestThePolicy:
    def test_script_src_allows_no_inline_script_and_no_eval(self, client):
        policy = client.get("/auth/login").headers["Content-Security-Policy"]
        directives = {part.split()[0]: part.split()[1:]
                      for part in (p.strip() for p in policy.split(";")) if part}
        assert directives["script-src"] == ["'self'"], policy
        assert "'unsafe-inline'" not in directives.get("script-src", [])
        assert "'unsafe-eval'" not in policy
        # default-src must not be a back door for script either
        assert "'unsafe-inline'" not in directives.get("default-src", [])
        assert directives.get("object-src") == ["'none'"]
        assert directives.get("base-uri") == ["'self'"]


    def test_the_documented_policy_is_the_served_policy(self, client):
        """docs/SECURITY.md quotes the policy; it must be the one we send."""
        served = client.get("/auth/login").headers["Content-Security-Policy"]
        security = (PROJECT_ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
        assert f"`{served}`" in security, (
            "docs/SECURITY.md quotes a different Content-Security-Policy than "
            f"the application serves:\n{served}")
        assert "'unsafe-inline' in the CSP" not in security
        development = (PROJECT_ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
        assert "tests/unit/test_csp_no_inline_script.py" in development
        assert "data-on-click" in development


# ---------------------------------------------------------------------------
# The sources
# ---------------------------------------------------------------------------
class TestTheSources:
    def test_no_template_has_an_inline_handler_attribute(self):
        offenders = []
        for path in _templates():
            text = path.read_text(encoding="utf-8")
            # Jinja is blanked (not removed) so a macro keyword argument such as
            # action_button(onclick='selectAll()') - which the macro turns into
            # data-on-click - is not mistaken for markup; line numbers survive.
            markup = JINJA.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
            for match in HANDLER_ATTRIBUTE.finditer(markup):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(markup, match.start())}"
                                 f": {markup[match.start():match.start() + 60].strip()}")
        assert not offenders, "inline handlers (use data-on-<event>):\n" + "\n".join(offenders)

    def test_no_script_generates_an_inline_handler_attribute(self):
        offenders = []
        for path in _scripts():
            if path == RUNTIME:
                continue
            text = JS_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)),
                                  path.read_text(encoding="utf-8"))
            for match in re.finditer(r"""[\s"'>]on(?:""" + HANDLER_EVENTS + r""")\s*=\s*\\?["']""",
                                     text, re.IGNORECASE):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(text, match.start())}"
                                 f": {text[match.start():match.start() + 60].strip()}")
        assert not offenders, "generated inline handlers (use data-on-<event>):\n" + "\n".join(offenders)

    def test_no_template_has_an_executable_inline_script(self):
        offenders = []
        for path in _templates():
            text = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)
            for match in INLINE_SCRIPT.finditer(text):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(text, match.start())}"
                                 f": {match.group(0)}")
        assert not offenders, ("inline <script> (move it to static/js and pass data in "
                               "<script type=\"application/json\">):\n" + "\n".join(offenders))

    def test_no_script_uses_javascript_urls_or_string_evaluation(self):
        offenders = []
        for path in _scripts():
            text = JS_COMMENT.sub("", path.read_text(encoding="utf-8"))
            for pattern in (r"""href\s*=\s*\\?["']javascript:""", r"\bnew Function\s*\(",
                            r"(?<![\w.])eval\s*\(", r"setTimeout\s*\(\s*[\"'`]",
                            r"setInterval\s*\(\s*[\"'`]"):
                for match in re.finditer(pattern, text):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(text, match.start())}")
        for path in _templates():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"""href\s*=\s*["']javascript:""", text, re.IGNORECASE):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(text, match.start())}")
        assert not offenders, "blocked by the policy:\n" + "\n".join(offenders)

    def test_every_function_a_handler_calls_is_reachable_from_window(self):
        """The runtime resolves names on ``window``; inline script also saw top-level
        ``const``/``let``, so a name defined only that way would silently break."""
        sources = {path: path.read_text(encoding="utf-8") for path in _scripts()}
        corpus = "\n".join(sources.values())
        # A top-level function or var is global only in a classic script. In an
        # ES module it is module-scoped, and a handler naming it does nothing -
        # file-detail-page.js had exactly that bug (toggleFullscreen).
        loaded_as_module = set(re.findall(
            r"""<script[^>]*type=["']module["'][^>]*filename=['"]([^'"]+)['"]""",
            "\n".join(p.read_text(encoding="utf-8") for p in _templates())))
        defined = set()
        for path, text in sources.items():
            relative = path.relative_to(SCRIPTS).as_posix()
            if (f"js/{relative}" in loaded_as_module
                    or re.search(r"(?m)^\s*(?:import\s[^(]|export\s)", text)):
                continue
            defined |= set(re.findall(r"(?m)^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", text))
            defined |= set(re.findall(r"(?m)^var\s+([A-Za-z_$][\w$]*)", text))
        defined |= set(re.findall(r"\b(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=(?!=)", corpus))
        defined |= set(re.findall(r"\bwindow\[\s*['\"]([A-Za-z_$][\w$]*)['\"]\s*\]\s*=", corpus))
        for block in re.findall(r"Object\.assign\(\s*window\s*,\s*\{(.*?)\}\s*\)", corpus, re.S):
            defined |= set(re.findall(r"(?:^|[,{\s])([A-Za-z_$][\w$]*)\s*(?=[,:}\n]|$)", block))
        # Names the sources expose through a loop such as
        # ['viewSource', ...].forEach(n => window[n] = ...).
        for block in re.findall(r"\[([^\]]*)\]\s*\.forEach\(\s*\(?\s*(\w+)\s*\)?\s*=>\s*\{?[^}]*window\[\s*\2\s*\]\s*=",
                                corpus, re.S):
            defined |= set(re.findall(r"['\"]([A-Za-z_$][\w$]*)['\"]", block[0]))
        browser = {"window", "document", "location", "history", "navigator", "console",
                   "parseInt", "parseFloat", "Number", "String", "Math", "JSON", "alert",
                   "confirm", "encodeURIComponent", "open", "print"}

        missing = {}
        for path in [*_templates(), *sources]:
            text = path.read_text(encoding="utf-8") if path not in sources else sources[path]
            for match in DATA_ON.finditer(text):
                quote = match.group(2)
                end = text.find(quote, match.end())
                value = text[match.end():end]
                value = re.sub(r"\{\{.*?\}\}", "0", value)
                value = re.sub(r"\$\{[^}]*\}", "0", value)
                value = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", value)
                for name, optional in re.findall(
                        r"(?<![\w$.?])([A-Za-z_$][\w$]*)\s*(\?\.)?\s*(?=[(.\[])", value):
                    if name in ("this", "event", "if", "return") or name in browser:
                        continue
                    if optional:   # fn?.() is written to mean "if the page defines it"
                        continue
                    if name not in defined:
                        missing.setdefault(name, f"{path.relative_to(PROJECT_ROOT)}")
        # A template macro's handler name comes from its caller, not from JS.
        missing.pop("0", None)
        assert not missing, ("handlers call names that are not on window: "
                             + ", ".join(f"{name} ({where})" for name, where in sorted(missing.items())))


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------
class TestTheRuntime:
    def test_the_runtime_keeps_the_handler_contract_and_refuses_dangerous_names(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        proc = subprocess.run([node, str(HARNESS)], cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.startswith("OK "), proc.stdout

    def test_the_runtime_never_evaluates_strings(self):
        source = JS_COMMENT.sub("", RUNTIME.read_text(encoding="utf-8"))
        assert not re.search(r"(?<![\w.])eval\s*\(|new Function|Function\s*\(", source)
        assert "innerHTML" not in source.replace("'innerHTML'", "")

    def test_the_shell_loads_the_runtime_first_and_synchronously(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        tag = re.search(r"<script[^>]*declarative-events\.js[^>]*>", base)
        assert tag, "base.html does not load the declarative event runtime"
        assert "defer" not in tag.group(0) and "async" not in tag.group(0)
        assert not re.search(r"type\s*=\s*[\"']module", tag.group(0))
        first_script = re.search(r"<script\b(?![^>]*application/json)[^>]*>", base)
        assert first_script.start() == tag.start(), "another script runs before the runtime"


# ---------------------------------------------------------------------------
# The served pages
# ---------------------------------------------------------------------------
def _html_routes(app):
    routes = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments:
            continue
        path = rule.rule
        if path.startswith(("/api/", "/static", "/health", "/metrics")) or "export" in path:
            continue
        if path.endswith((".json", ".js", ".css", ".ico", ".txt", ".xml", ".csv")):
            continue
        routes.append(path)
    return sorted(set(routes))


class TestTheServedPages:
    def test_every_page_renders_without_inline_script(self, app, admin_client):
        checked, offenders = [], []
        for path in _html_routes(app):
            response = admin_client.get(path)
            if response.status_code != 200 or "text/html" not in response.content_type:
                continue
            html = response.get_data(as_text=True)
            checked.append(path)
            for match in HANDLER_ATTRIBUTE.finditer(html):
                offenders.append(f"{path}: {html[match.start():match.start() + 70].strip()}")
            for match in INLINE_SCRIPT.finditer(html):
                offenders.append(f"{path}: {match.group(0)}")
            if "data-on-" in html:
                assert "js/modules/core/declarative-events.js" in html, (
                    f"{path} uses data-on-* but does not load the runtime")
        assert len(checked) >= 20, f"only {len(checked)} pages rendered: {checked}"
        assert not offenders, "served pages with inline script:\n" + "\n".join(offenders)

    def test_the_standalone_pages_render_without_inline_script(self, client):
        """Login, first-admin and the install wizard do not extend base.html."""
        response = client.get("/auth/login")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="login-form"' in html, "this is not the login page"
        assert "js/pages/login-page.js" in html
        assert not HANDLER_ATTRIBUTE.search(html)
        assert not INLINE_SCRIPT.search(html)
        for template in ("auth/first_admin.html", "Setup/install_wizard.html", "auth/login.html"):
            text = (TEMPLATES / template).read_text(encoding="utf-8")
            assert not INLINE_SCRIPT.search(text), template
