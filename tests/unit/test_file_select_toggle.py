"""The files list has ONE select/deselect control, and it works.

The action bar used to carry two buttons ("Select All", "Select None"),
which cost space and made the selection state easy to miss. They are one
standout toggle now (``#selectToggleBtn``): it selects everything when
nothing is selected, clears the selection otherwise, and its label, icon,
``aria-pressed`` flag and count badge always describe the selection. These
tests pin the rendered markup, the wiring, the style, and the behaviour
(the behaviour through node against the stub DOM, like the other front-end
contracts).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS = PROJECT_ROOT / "tests" / "js" / "file_select_toggle_smoke.mjs"
PAGE_MODULE = PROJECT_ROOT / "static" / "js" / "pages" / "files-list-page.js"
FUNCTION_MANAGER = PROJECT_ROOT / "static" / "js" / "managers" / "function-manager.js"
PAGE_CSS = PROJECT_ROOT / "static" / "css" / "file-manger.css"


def _files_page_html(admin_client):
    resp = admin_client.get("/files")
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


class TestTheRenderedToggle:
    def test_the_action_bar_has_one_toggle_and_no_button_pair(self, admin_client):
        html = _files_page_html(admin_client)
        selection_group = re.search(
            r'<div class="action-group">\s*<span class="action-label">[^<]*</span>'
            r'\s*<button.*?</button>',
            html, re.S)
        assert selection_group, "selection group not found"
        markup = selection_group.group(0)
        assert 'id="selectToggleBtn"' in markup
        assert 'data-on-click="toggleAllFilesSelection()"' in markup
        assert 'aria-pressed="false"' in markup, "the toggle must expose its state"
        assert markup.count("<button") == 1, "exactly one selection button"
        # The old pair is gone from the whole page.
        assert 'data-on-click="selectAllFiles()"' not in html
        assert 'data-on-click="deselectAllFiles()"' not in html
        assert 'id="selectAllBtn"' not in html
        assert 'id="selectNoneBtn"' not in html

    def test_the_toggle_carries_label_icon_and_count(self, admin_client):
        html = _files_page_html(admin_client)
        assert "select-toggle-label" in html
        assert "select-toggle-icon" in html
        assert 'id="selectToggleCount"' in html
        assert "bi-check-square-fill" in html

    def test_the_translated_phrases_travel_with_the_page(self, admin_client):
        html = _files_page_html(admin_client)
        block = re.search(
            r'<script type="application/json" id="files-list-page-data">(.*?)</script>',
            html, re.S)
        assert block, "page translations block missing"
        assert '"selectAll"' in block.group(1)
        assert '"deselectAll"' in block.group(1)


class TestTheWiring:
    def test_the_page_exposes_the_toggle_to_the_declarative_runtime(self):
        source = PAGE_MODULE.read_text(encoding="utf-8")
        assert "window.toggleAllFilesSelection" in source
        assert "FileManagement.toggleAllFilesSelection" in source

    def test_the_function_manager_exposes_the_module_function(self):
        # Round 14 gave every action name one owner per page: the files list page
        # publishes ``toggleAllFilesSelection`` itself (asserted above), so the
        # shared module export carries a suffix. The guard below is the ownership
        # rule itself - the manager must NOT take the name the page owns - which
        # is what the old assertion was really protecting, and it cannot be
        # satisfied by an assignment that the loader order makes ambiguous.
        source = FUNCTION_MANAGER.read_text(encoding="utf-8")
        assert ("window.toggleAllFilesSelectionShared = "
                "fileSelection.toggleAllFilesSelection;") in source
        assert "window.toggleAllFilesSelection = " not in source, (
            "the shared export must not claim the name the files page publishes")
        # And the control that uses the shared copy names it explicitly, so the
        # suffix cannot quietly turn into a dead button.
        view = (PROJECT_ROOT / "static" / "js" / "modules" / "views"
                / "file-view.js").read_text(encoding="utf-8")
        assert 'data-on-click="toggleAllFilesSelectionShared?.()"' in view

    def test_the_style_exists_and_covers_both_states(self):
        css = PAGE_CSS.read_text(encoding="utf-8")
        assert ".btn-action.btn-select-toggle" in css
        assert ".btn-action.btn-select-toggle.is-selected" in css
        assert ".select-toggle-count" in css

    def test_every_selection_mutation_updates_the_toggle(self):
        source = (PROJECT_ROOT / "static" / "js" / "modules" / "file-operations"
                  / "file-selection.js").read_text(encoding="utf-8")
        # The funnel: the count update runs after select-all, deselect-all,
        # filtering and a checkbox change - and the toggle rides on it.
        assert "updateSelectToggle();" in source
        funnel = re.search(r"function updateSelectedCount\(\).*?\n}", source, re.S)
        assert funnel and "updateSelectToggle();" in funnel.group(0)


class TestTheBehaviour:
    def test_the_module_drives_the_toggle_against_the_stub_dom(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        proc = subprocess.run(
            [node, str(HARNESS)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "checks passed" in proc.stdout
        assert "FAIL" not in proc.stdout, proc.stdout
