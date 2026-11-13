"""The licence decision (AGPL-3.0-or-later) is applied everywhere it must be.

* LICENSE is the AGPL-3.0 text and the package metadata declares it.
* Every vendored static file is listed in THIRD_PARTY_NOTICES.md, and every
  bundled font ships its OFL text.
* The Python dependency closure contains nothing incompatible or unreviewed
  (tools/licenses/check_licenses.py), and the checker classifies correctly.
* Every page offers the source (AGPL section 13) and shows the running
  version - the sidebar used to print a hard-coded "2.0.0".
"""
import fnmatch
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

import version

ROOT = Path(__file__).resolve().parents[2]
SPDX = "AGPL-3.0-or-later"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_licenses", ROOT / "tools" / "licenses" / "check_licenses.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_licenses"] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# ---------------------------------------------------------------------------
# Licence file and package metadata
# ---------------------------------------------------------------------------
def test_license_file_is_the_agpl_v3():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
    assert "Version 3, 19 November 2007" in text
    assert "13. Remote Network Interaction; Use with the GNU General Public License." in text


def test_pyproject_declares_the_licence():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^license\s*=\s*"AGPL-3\.0-or-later"\s*$', toml, re.M)
    files = re.search(r"^license-files\s*=\s*\[([^\]]*)\]", toml, re.M).group(1)
    listed = re.findall(r'"([^"]+)"', files)
    assert {"LICENSE", "THIRD_PARTY_NOTICES.md"} <= set(listed)
    assert all((ROOT / f).is_file() for f in listed)
    assert "Proprietary" not in toml
    # PEP 639 licence expressions need setuptools 77.
    assert re.search(r'"setuptools>=(7[7-9]|[89]\d)', toml)


def test_readme_and_docs_state_the_licence():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "GNU Affero General Public License" in readme and "(LICENSE)" in readme
    licensing = (ROOT / "docs" / "LICENSING.md").read_text(encoding="utf-8")
    assert f"`{SPDX}`" in licensing and "SOURCE_CODE_URL" in licensing
    assert "tools/licenses/check_licenses.py" in licensing
    assert "SOURCE_CODE_URL" in (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "`SOURCE_CODE_URL`" in (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Third-party files
# ---------------------------------------------------------------------------
VENDORED = ("*.min.js", "*.min.css", "*.umd.js", "*.ttf", "*.otf", "*.woff", "*.woff2")


def _tracked_static_files():
    out = subprocess.run(["git", "ls-files", "static"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [p for p in out if any(fnmatch.fnmatch(Path(p).name, pat) for pat in VENDORED)]


def test_every_vendored_file_is_in_the_notices():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    patterns = re.findall(r"`(static/[^`]+)`", notices)
    files = _tracked_static_files()
    assert len(files) > 40, "the vendored-file scan found almost nothing"
    unlisted = [f for f in files if not any(fnmatch.fnmatch(f, p) for p in patterns)]
    assert not unlisted, f"add these to THIRD_PARTY_NOTICES.md: {unlisted}"
    # ...and the notices list nothing that does not exist.
    stale = [p for p in patterns if not any(fnmatch.fnmatch(f, p) for f in files)
             and not (ROOT / p).exists()]
    assert not stale, f"THIRD_PARTY_NOTICES.md lists missing files: {stale}"


@pytest.mark.parametrize("family, licence", [
    ("inter-*.ttf", "OFL-Inter.txt"),
    ("NotoSansArabic*.ttf", "OFL-NotoSansArabic.txt"),
    ("NotoSansHebrew*.ttf", "OFL-NotoSansHebrew.txt"),
])
def test_every_font_ships_its_ofl_text(family, licence):
    fonts = ROOT / "static" / "fonts"
    assert list(fonts.glob(family)), family
    text = (fonts / licence).read_text(encoding="utf-8")
    assert text.startswith("Copyright ")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text


# ---------------------------------------------------------------------------
# Dependency licences
# ---------------------------------------------------------------------------
def test_installed_dependencies_are_agpl_compatible():
    findings = checker.check(include_extras=True)
    assert len([f for f in findings if f.verdict == "compatible"]) > 20
    problems = [f"{f.name} {f.version}: {f.expression or '?'} ({f.detail})"
                for f in findings if f.verdict in ("incompatible", "unknown")]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("expression, expected", [
    ("MIT", "compatible"),
    ("GPL-3.0-only", "compatible"),
    ("GPL-2.0-or-later", "compatible"),
    ("GPL-2.0-only", "incompatible"),
    ("MIT OR GPL-2.0-only", "compatible"),        # a choice: take MIT
    ("MIT AND GPL-2.0-only", "incompatible"),     # both apply
    ("Apache-2.0 AND Made-Up-1.0", "unknown"),
    ("", "unknown"),
])
def test_checker_classifies_expressions(expression, expected):
    assert checker.verdict(expression)[0] == expected


class _FakeDistribution:
    def __init__(self, **fields):
        self._fields = fields

    @property
    def metadata(self):
        fields = self._fields

        class _Meta:
            def __getitem__(self, key):
                return fields[key]

            def get(self, key, default=None):
                return fields.get(key, default)

            def get_all(self, key):
                return fields.get(key + "s")
        return _Meta()


def test_checker_reads_expression_then_field_then_classifiers():
    read = checker.declared_licence
    assert read(_FakeDistribution(Name="a", **{"License-Expression": "BSD-3-Clause"})) == \
        ("BSD-3-Clause", "License-Expression")
    assert read(_FakeDistribution(Name="b", License="MIT License")) == ("MIT", "License")
    assert read(_FakeDistribution(Name="c", Classifiers=[
        "License :: OSI Approved :: MIT License"])) == ("MIT", "Classifier")
    # A copyright line is not a licence: unknown unless reviewed.
    expression, source = read(_FakeDistribution(Name="d", License="Copyright 2020 Someone"))
    assert expression == "" and source.startswith("unrecognised")
    assert checker.verdict(expression)[0] == "unknown"


def test_checker_cli_exits_zero_on_this_environment():
    result = subprocess.run([sys.executable, "tools/licenses/check_licenses.py", "--quiet"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 incompatible, 0 unknown" in result.stdout


# ---------------------------------------------------------------------------
# Section 13 source offer and the displayed version
# ---------------------------------------------------------------------------
@pytest.fixture
def web_app():
    from apps.web.app import app
    return app


def _render_base_page(app):
    from flask import render_template
    with app.test_request_context("/files"):
        app.preprocess_request()
        return render_template("404.html")  # extends base.html


def test_every_page_links_to_the_source_and_shows_the_real_version(web_app, monkeypatch):
    from apps.web.app import DEFAULT_SOURCE_CODE_URL
    monkeypatch.delenv("SOURCE_CODE_URL", raising=False)
    page = _render_base_page(web_app)
    assert f'href="{DEFAULT_SOURCE_CODE_URL}"' in page
    assert f"Version {version.__version__}" in " ".join(page.split())
    assert "2.0.0" not in page

    # A first-run install redirects every request to the setup wizard
    # (Api/routes/setup.py), so the client has to follow the redirect a browser
    # follows - otherwise this asserts against the empty 302 body and the
    # source offer is "checked" nowhere. The assertions themselves are
    # unchanged: the page the user ends up on must carry both.
    login = web_app.test_client().get("/auth/login", follow_redirects=True)
    assert login.status_code == 200
    page = login.get_data(as_text=True)
    assert f'href="{DEFAULT_SOURCE_CODE_URL}"' in page
    assert version.__version__ in page


def _setup_page_context():
    """The variables ``Api.routes.setup.setup_page`` passes to the template.

    Rendering the template without them makes ``|tojson`` receive an Undefined
    and the page raises before any assertion runs. Built from the route's own
    helpers so this stays in step with what a fresh install really renders.
    """
    from Api.routes.setup import _wizard_language_options, _wizard_script_strings

    return {
        "wizard_script_strings": _wizard_script_strings(),
        "wizard_language_options": _wizard_language_options(),
    }


def test_the_first_run_setup_page_offers_the_source_and_the_version(web_app, monkeypatch):
    """AGPL section 13 must reach the only page a fresh install shows.

    Until this was repaired, `templates/Setup/install_wizard.html` rendered
    neither the source link nor the running version, and the setup gate sends
    every request there until the system is initialized - so an operator
    evaluating the software was offered no source at all, on the one screen
    they could reach. Rendered directly rather than through the redirect so the
    assertion is the template's own behaviour in any initialization state.
    """
    from flask import render_template

    from apps.web.app import DEFAULT_SOURCE_CODE_URL

    monkeypatch.delenv("SOURCE_CODE_URL", raising=False)
    with web_app.test_request_context("/setup"):
        web_app.preprocess_request()
        page = render_template("Setup/install_wizard.html", **_setup_page_context())
    assert f'href="{DEFAULT_SOURCE_CODE_URL}"' in page
    assert f"Version {version.__version__}" in " ".join(page.split())
    assert "2.0.0" not in page


def test_the_setup_page_refuses_a_non_http_source_url(web_app, monkeypatch):
    """The setup page cannot be turned into a javascript: link either."""
    from flask import render_template

    from apps.web.app import DEFAULT_SOURCE_CODE_URL

    monkeypatch.setenv("SOURCE_CODE_URL", "javascript:alert(1)")
    with web_app.test_request_context("/setup"):
        web_app.preprocess_request()
        page = render_template("Setup/install_wizard.html", **_setup_page_context())
    assert "javascript:" not in page
    assert f'href="{DEFAULT_SOURCE_CODE_URL}"' in page


def test_source_code_url_is_configurable_and_http_only(web_app, monkeypatch):
    monkeypatch.setenv("SOURCE_CODE_URL", "https://git.example.org/fork/syltharae")
    assert 'href="https://git.example.org/fork/syltharae"' in _render_base_page(web_app)

    from apps.web.app import DEFAULT_SOURCE_CODE_URL
    monkeypatch.setenv("SOURCE_CODE_URL", "javascript:alert(1)")
    page = _render_base_page(web_app)
    assert "javascript:" not in page
    assert f'href="{DEFAULT_SOURCE_CODE_URL}"' in page
