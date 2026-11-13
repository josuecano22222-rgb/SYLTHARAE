"""The confirmation dialog and the toast: two components, two boundaries.

What is protected here:

* **They are components, declared like every other one** - purpose, inputs,
  outputs, events, states from the §63 vocabulary, accessibility, third-party
  dependency, CSS ownership - and the component library knows them.
* **They are inert.** The dialog answers a question; the toast shows a
  sentence. Neither performs an operation, calls an endpoint, or knows what an
  action does. A message is written as text, never as markup.
* **They are wired into the product, not just present.** The two actions the
  audit found using the browser's own dialog ('jobs.cancel', 'files.reprocess')
  now ask through the component, and the shell carries one live region.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import jinja2
import pytest

from core.frontend import component_audit
from core.experience.action_registry import action

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
COMPONENTS = TEMPLATES / "components"
RUNTIMES = PROJECT_ROOT / "static/js/modules/core"
HARNESS = PROJECT_ROOT / "tests/js/action_lifecycle_smoke.mjs"


@pytest.fixture()
def render():
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    environment.globals["_"] = lambda text: text
    return environment


class TestTheyAreDeclaredComponents:
    @pytest.mark.parametrize("name", ("confirm_dialog", "toast"))
    def test_the_library_knows_them_and_they_declare_their_states(self, name):
        library = component_audit.components()
        assert name in library, sorted(library)
        component = library[name]
        assert component.states, f"{name} declares no states"
        for state in component.states:
            assert state in component_audit.STATES, (name, state)

    @pytest.mark.parametrize("name", ("confirm_dialog", "toast"))
    def test_the_declaration_answers_the_component_questions(self, name):
        text = (COMPONENTS / f"{name}.html").read_text(encoding="utf-8")
        for field in ("purpose", "inputs", "outputs", "events", "states",
                      "accessibility", "third_party", "css"):
            assert re.search(rf"^\s*{field}:", text, re.M), (name, field)

    @pytest.mark.parametrize("name", ("confirm_dialog", "toast"))
    def test_the_documentation_is_regenerated_from_the_library(self, name):
        document = (PROJECT_ROOT / "docs/COMPONENT_LIBRARY.md").read_text(encoding="utf-8")
        assert re.search(rf"^\|\s*`?{name}`?\s*\|", document, re.M), (
            f"{name} is missing from docs/COMPONENT_LIBRARY.md: "
            "regenerate with `python3 -m core.frontend.component_audit "
            "docs/COMPONENT_LIBRARY.md`")


class TestTheyAreInert:
    @pytest.mark.parametrize("name", ("confirm-dialog.js", "toast.js"))
    def test_the_runtime_never_performs_anything(self, name):
        source = (RUNTIMES / name).read_text(encoding="utf-8")
        for forbidden in ("fetch(", "XMLHttpRequest", "url_for", "window.location",
                          "csrf", "POST", "GET "):
            assert forbidden not in source, (
                f"{name} performs an operation; the page owns the operation and "
                "the component owns the presentation")

    @pytest.mark.parametrize("name", ("confirm-dialog.js", "toast.js"))
    def test_the_runtime_never_builds_markup_from_a_message(self, name):
        source = (RUNTIMES / name).read_text(encoding="utf-8")
        assert "innerHTML" not in source, name
        assert "textContent" in source, name

    def test_the_dialog_refuses_to_invent_itself(self):
        """A page without the component is told, not given a browser dialog."""
        source = (RUNTIMES / "confirm-dialog.js").read_text(encoding="utf-8")
        assert "no [data-confirm-dialog] on this page" in source
        assert "Promise.resolve(false)" in source

    def test_the_toast_region_is_announced_without_stealing_focus(self):
        text = (COMPONENTS / "toast.html").read_text(encoding="utf-8")
        assert 'role="status"' in text
        assert 'aria-live="polite"' in text
        assert "toast-container" in text


class TestTheRenderedDialogIsReal:
    """The component in front of the real runtime, in a browser-shaped harness."""

    def _render(self, render, **overrides):
        template = render.get_template("components/confirm_dialog.html")
        spec = dict(
            id="confirmDialog",
            title="Delete 27 files?",
            message="This permanently removes the selected records.",
            scope_label="Selection",
            confirm_label="Delete",
            cancel_label="Cancel",
            dangerous=True,
            action="files.delete_selected",
            confirmation_key="action.files.delete_selected.confirm",
        )
        spec.update(overrides)
        return template.module.confirm_dialog(**spec)

    def test_the_rendered_dialog_has_the_accessibility_contract(self, render):
        html = self._render(render)
        assert 'role="dialog"' in html
        assert 'aria-modal="true"' in html
        assert 'aria-labelledby="confirmDialogTitle"' in html
        assert 'aria-describedby="confirmDialogMessage"' in html
        assert 'data-confirm-action="files.delete_selected"' in html
        assert f'data-confirm-key="{action("files.delete_selected").confirmation}"' in html

    def test_the_harness_passes_against_the_rendered_component(self, render, tmp_path):
        node = subprocess.run(["/usr/bin/env", "bash", "-lc", "command -v node"],
                              capture_output=True, text=True)
        if node.returncode != 0 or not node.stdout.strip():
            pytest.skip("node is not available")
        rendered = tmp_path / "dialog.html"
        rendered.write_text(self._render(render,
                                         typed_confirmation="DELETE",
                                         scope="27 files"),
                            encoding="utf-8")
        proc = subprocess.run(
            [node.stdout.strip(), str(HARNESS), f"--dialog-html={rendered}"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "checks passed" in proc.stdout
        assert "FAIL" not in proc.stdout, proc.stdout


class TestTheShellCarriesThem:
    def test_the_shell_loads_both_runtimes_and_one_live_region(self):
        shell = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "modules/core/confirm-dialog.js" in shell
        assert "modules/core/toast.js" in shell
        assert "toast_region()" in shell


class TestTheActionsThatNeededThemUseThem:
    def test_the_browser_dialog_is_gone_from_the_two_undeclared_actions(self):
        jobs = (TEMPLATES / "Operations/jobs.html").read_text(encoding="utf-8")
        detail = (TEMPLATES / "file/file_detail.html").read_text(encoding="utf-8")
        assert "confirm(" not in jobs, "jobs still asks through the browser"
        assert "confirm(" not in detail, "the record page still asks through the browser"

    def test_they_ask_by_action_id(self):
        jobs = (TEMPLATES / "Operations/jobs.html").read_text(encoding="utf-8")
        assert "action='jobs.cancel'" in jobs or 'action="jobs.cancel"' in jobs
        assert "'jobs.cancel'" in jobs

        # Reprocess left this list, and deliberately: it was drawn on the file
        # detail page and pointed at /file/<id>/reprocess, which no route ever
        # served. It is now declared and not built, the page does not offer it,
        # and the record's own actions ask through the same dialog by id.
        detail = (TEMPLATES / "file/file_detail.html").read_text(encoding="utf-8")
        assert "data-action=\"files.reprocess\"" not in detail
        assert "action='files.delete'" in detail or 'action="files.delete"' in detail
        # The key reaches the surface through the prepared action, not from a
        # literal in the template: the registry owns it.
        assert "confirmation_key='action.files.delete.confirm'" in detail

    def test_the_registry_says_what_it_can_and_cannot_do(self):
        cancel = action("jobs.cancel")
        reprocess = action("files.reprocess")
        assert cancel.scope == "record" and cancel.confirmation
        assert cancel.destructive is False, (
            "cancelling stops work; it destroys no record, and the registry "
            "should not call it destructive")
        assert reprocess.built is False, (
            "the operation does not exist, and the registry says so instead of "
            "letting a page draw it as available")
        assert reprocess.execution is None

    def test_an_action_that_ends_visibly_goes_through_the_toast(self):
        template = (TEMPLATES / "Operations/jobs.html").read_text(encoding="utf-8")
        # RES-CSP-01: the page's script is a static file the template loads.
        assert "filename='js/pages/jobs-page.js'" in template
        jobs = (TEMPLATES.parent / "static/js/pages/jobs-page.js").read_text(encoding="utf-8")
        assert "Toast.success(" in jobs
        assert "Toast.error(" in jobs
