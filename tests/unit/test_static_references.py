"""Every static file the pages ask for exists in the repository.

Field report (Windows run after step 13): four pages requested
``static/dist/{js,css}/select2.min.*``, the server answered 404 with an HTML
body, and the browser refused it ("MIME type ('text/html') is not a supported
stylesheet MIME type"). The files were listed in THIRD_PARTY_NOTICES.md but
had never been committed. The licensing test noticed the listing; nothing
checked the pages. This does.

Only literal references are checked (``url_for('static', filename='...')``
with a quoted string, and quoted ``/static/...`` paths in scripts and
templates); computed names cannot be resolved statically.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"

_URL_FOR = re.compile(r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]""")
_LITERAL = re.compile(r"""['"`](/static/[^'"`?#\s${}]+)""")


def _references():
    refs = {}
    for template in (ROOT / "templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8", errors="replace")
        for name in _URL_FOR.findall(text):
            refs.setdefault(name, set()).add(str(template.relative_to(ROOT)))
        for path in _LITERAL.findall(text):
            refs.setdefault(path[len("/static/"):], set()).add(str(template.relative_to(ROOT)))
    for script in STATIC.rglob("*.js"):
        if script.name.endswith(".min.js") or "dist" in script.parts:
            continue  # vendored bundles are not ours to scan
        text = script.read_text(encoding="utf-8", errors="replace")
        for path in _LITERAL.findall(text):
            refs.setdefault(path[len("/static/"):], set()).add(str(script.relative_to(ROOT)))
    return refs


REFERENCES = _references()


def test_the_scan_finds_references():
    assert len(REFERENCES) > 50, "guard: the scan found almost nothing"
    assert "dist/js/select2.min.js" in REFERENCES


@pytest.mark.parametrize("name", sorted(REFERENCES))
def test_referenced_static_file_exists(name):
    target = (STATIC / name).resolve()
    assert STATIC.resolve() in target.parents, f"{name} escapes static/"
    if name.endswith("/"):
        # A trailing slash names a directory, not a file: navigation-swap.js
        # tests a script's path against the ``/static/js/pages/`` prefix
        # (``path.indexOf(...)``), which requests nothing and 404s nothing.
        # The directory must exist; the stricter file check below is for
        # references that name a file.
        assert target.is_dir(), (
            f"static/{name} is referenced by {sorted(REFERENCES[name])} "
            f"as a directory but does not exist")
        return
    assert target.is_file(), (
        f"static/{name} is referenced by {sorted(REFERENCES[name])} but does not exist")


def test_the_server_serves_select2_with_script_and_style_types(app):
    client = app.test_client()
    js = client.get("/static/dist/js/select2.min.js")
    css = client.get("/static/dist/css/select2.min.css")
    assert js.status_code == 200 and "javascript" in js.content_type
    assert css.status_code == 200 and css.content_type.startswith("text/css")
    assert js.data.startswith(b"/*! Select2 4.0.13 ")
    js.close()
    css.close()
