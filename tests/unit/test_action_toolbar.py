"""The action toolbar's contract: rendered, translated, run, and obeyed.

The toolbar is a shared component, and the risk in a shared component is that
its boundary moves one page at a time until it knows what a "file" is and what
"export" does. So the boundary is pinned here instead:

* the component renders groups, buttons, icons, states and accessibility, and
  the no-selection state of a bulk action;
* the runtime (`static/js/modules/core/action-toolbar.js`) keeps the scope on
  screen as the reader selects, and knows nothing else - no action name, no
  endpoint, no listener;
* the page owns selection calculation, business logic, API calls and the
  outcome;
* the phrases are the catalogs', not the runtime's, so a bulk action's scope is
  said in the reader's language.

`Sources` and `Sides` are the reference pair: same component, different page
data, same runtime contract, different business behaviour. They are asserted
against the *served* pages, not against the templates, and the runtime is
driven through node against the bar the server really rendered.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import jinja2
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TEMPLATES = PROJECT_ROOT / "templates"
RUNTIME = PROJECT_ROOT / "static/js/modules/core/action-toolbar.js"
HARNESS = PROJECT_ROOT / "tests/js/action_toolbar_smoke.mjs"

#: A row must exist for "one binding per row" to mean anything: the pages are
#: empty on a fresh database, so the pair is seeded the way the product seeds
#: itself - through the API, not through the templates.
def _seed(admin_client):
    admin_client.post("/api/input/sources",
                      json={"name": "toolbar-reference-source", "job": "analyst",
                            "country": "NL"})
    admin_client.post("/api/input/sides", json={"name": "toolbar-reference-side"})
    admin_client.post("/api/words", json={"word": "toolbarreferenceword"})
    admin_client.post("/api/words", json={"word": "toolbar@reference.example"})


#: Every page the toolbar has been migrated onto. Keywords and Words are here
#: because a contract that only fits the two pages it was written against is
#: not a contract: the same primitives have to express a second and third
#: screen with different data, different actions and different business
#: behaviour.
#:
#: url, bar id, plural noun, singular noun, selection-scoped buttons, page
#: module, checkbox class, and how that page renders its rows.
SELECTION_PAGES = (
    ("/sources", "sourcesActionBar", "sources", "source",
     ("bulkExportBtn", "bulkUpdateBtn"), "sources-list-page.js",
     "source-checkbox", "server"),
    ("/sides", "sidesActionBar", "sides", "side",
     ("bulkExportBtn", "bulkUpdateBtn"), "sides-list-page.js",
     "side-checkbox", "server"),
    ("/keywords", "keywordsActionBar", "keywords", "keyword",
     ("bulkUpdateBtn", "bulkDeleteBtn"), "keywords-list-page.js",
     "keyword-checkbox", "script"),
    ("/words", "wordsActionBar", "words", "word",
     ("bulkUpdateBtn", "bulkDeleteBtn"), "words-list-page.js",
     "word-checkbox", "server"),
    ("/email-words", "emailWordsActionBar", "emails", "email",
     ("bulkExportEmailBtn", "bulkCopyEmailBtn"), "email-words-page.js",
     "email-checkbox", "server"),
)

#: Phrases the runtime composes. They must exist, translated, in every
#: maintained catalog: a scope sentence that only exists in English is how a
#: bulk action ends up saying something the reader cannot read.
PHRASES = ("Select {noun} first", "{count} {noun} selected",
           "{count} of {total} {noun} selected")
NOUNS = ("source", "side", "keyword", "word", "email")
MAINTAINED = ("ar", "he", "fa", "hr")


@pytest.fixture()
def render():
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    environment.globals["_"] = lambda text: text

    def render_source(source: str) -> str:
        return environment.from_string(source).render()

    return render_source


def bar(render, noun="sources", singular="source", total=27):
    """The bar as `Sources`/`Sides` render it, with both bulk actions."""
    return render(
        "{% from 'components/action_toolbar.html' import action_toolbar,"
        " action_group, bulk_action_button, selection_summary, select_toggle_button %}"
        "{% call action_toolbar(id='sourcesActionBar', label=_('Actions')) %}"
        "{% call action_group(_('Selection:')) %}"
        "{{ select_toggle_button(onclick='toggleAllSelection()', id='sourcesSelectToggleBtn') }}"
        "{% endcall %}"
        "{% call action_group(_('Bulk Actions:')) %}"
        f"{{{{ selection_summary({total}, _({noun!r}), _({singular!r})) }}}}"
        "{{ bulk_action_button(_('Export Selected'), icon='bi-download',"
        " onclick='bulkExport()', tone='outline-primary', id='bulkExportBtn',"
        f" noun=_({noun!r})) }}}}"
        "{{ bulk_action_button(_('Edit Selected'), icon='bi-pencil',"
        " onclick='bulkUpdate()', tone='outline-warning', id='bulkUpdateBtn',"
        f" noun=_({noun!r})) }}}}"
        "{% endcall %}{% endcall %}")


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
class TestTheComponentRendersTheContract:
    def test_the_bar_names_itself_and_says_what_it_is(self, render):
        out = bar(render)
        assert 'role="toolbar"' in out
        assert 'aria-label="Actions"' in out
        assert 'id="sourcesActionBar"' in out
        assert 'data-action-toolbar' in out

    def test_selection_is_one_toggle_not_a_button_pair(self, render):
        out = bar(render)
        assert 'data-select-toggle' in out
        assert 'id="sourcesSelectToggleBtn"' in out
        assert 'data-on-click="toggleAllSelection()"' in out
        assert out.count('data-on-click="selectAll()"') == 0
        assert out.count('data-on-click="selectNone()"') == 0
        # Exactly one button in the Selection group.
        selection = re.search(
            r'data-action-group[^>]*>.*?</div>\s*<div class="action-group"',
            out, re.S)
        assert selection, "selection group not found"
        assert selection.group(0).count("<button") == 1

    def test_a_bulk_action_without_a_selection_is_disabled_and_says_why(self, render):
        out = bar(render)
        export = re.search(r"<button[^>]*id=\"bulkExportBtn\"[^>]*>", out, re.S).group(0)
        assert " disabled" in export
        assert 'aria-disabled="true"' in export
        assert "data-on-click" not in export, "a bulk action ran before a scope was known"
        assert 'aria-label="Export Selected: Select sources first"' in export

    def test_the_scope_is_a_live_region_carrying_the_words_the_runtime_needs(self, render):
        out = bar(render)
        summary = re.search(r"<span[^>]*data-selection-summary[^>]*>.*?</span>", out, re.S).group(0)
        assert 'role="status"' in summary
        assert 'aria-live="polite"' in summary
        assert 'data-noun="sources"' in summary
        assert 'data-noun-singular="source"' in summary
        assert 'data-total="27"' in summary
        assert "Select sources first" in summary
        # The phrases travel with the bar as data: the runtime fills them in
        # rather than composing English of its own.
        assert 'data-text-select-first="Select {noun} first"' in out
        assert 'data-text-selected="{count} {noun} selected"' in out
        assert 'data-text-all-selected="{count} of {total} {noun} selected"' in out

    def test_a_bulk_action_carries_its_classification_for_the_runtime(self, render):
        out = bar(render)
        for button in re.findall(r"<button[^>]*data-bulk-action[^>]*>", out, re.S):
            assert 'data-action-label="' in button
        assert out.count("data-bulk-action") == 2

    def test_a_page_action_is_not_a_bulk_action(self, render):
        """`Apply Filters` is a page action: never enabled or disabled by a count."""
        out = render(
            "{% from 'components/action_toolbar.html' import action_button %}"
            "{{ action_button(_('Apply Filters'), icon='bi-funnel', submit=True, tone='primary') }}"
            "{{ action_button(_('Reset'), icon='bi-arrow-counterclockwise', href='/reset') }}")
        assert 'type="submit"' in out
        assert 'href="/reset"' in out
        assert "data-bulk-action" not in out


class TestThePhrasesAreTheCatalogs:
    def test_every_phrase_the_toolbar_composes_is_translated_everywhere(self):
        """No English invented in the runtime, and none left behind either."""
        import re as _re

        missing = []
        for language in MAINTAINED:
            text = (PROJECT_ROOT / "translations" / language
                    / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
            for phrase in PHRASES + NOUNS:
                block = _re.search(
                    r'^msgid "' + _re.escape(phrase) + r'"\nmsgstr "(.*)"$',
                    text, _re.M)
                if block is None or not block.group(1).strip():
                    missing.append(f"{language}: {phrase}")
        assert not missing, f"the toolbar would fall back to English: {missing}"

    def test_the_toolbar_names_no_action_of_its_own(self):
        """Every action name in the component comes from the page's call."""
        source = RUNTIME.read_text(encoding="utf-8")
        for name in ("delete", "export", "edit", "upload", "archive"):
            assert f"'{name}" not in source.lower().replace("selected", ""), name


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------
class TestTheRuntimeStaysSmallAndInert:
    def test_it_exposes_one_method_and_that_method_takes_numbers_and_words(self):
        source = RUNTIME.read_text(encoding="utf-8")
        assert "window.ActionToolbar = { sync: sync }" in source
        assert "function sync(root, spec)" in source

    def test_it_performs_nothing_and_binds_nothing(self):
        """No network, no storage, no listeners, no endpoints, no actions."""
        source = RUNTIME.read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        for forbidden in ("fetch(", "XMLHttpRequest", "addEventListener",
                          "localStorage", "sessionStorage", "/api/", "onclick=", "data-on-"):
            assert forbidden not in code, (
                f"the toolbar runtime reached for {forbidden!r}: the boundary "
                "between presentation and business behaviour has moved")

    @pytest.mark.parametrize("url,bar_id,noun,singular,bulk_ids,module,checkbox,rows",
                             SELECTION_PAGES)
    def test_the_page_no_longer_decides_how_the_scope_looks(
            self, url, bar_id, noun, singular, bulk_ids, module, checkbox, rows):
        """The page reports the numbers; the toolbar decides what that looks like."""
        source = (PROJECT_ROOT / "static/js/pages" / module).read_text(encoding="utf-8")
        assert f"ActionToolbar.sync('{bar_id}'" in source, (
            f"{module} renders a scope but does not keep it in step")
        assert ".disabled = !" not in source, (
            f"{module} still enables or disables an action from the selection: "
            "that is the toolbar's decision, and doing it in two places is how "
            "the two drift apart")
        assert "document.addEventListener('change'" not in source, (
            "a second listener for the same change would update the toolbar "
            "twice per click")

    def test_the_shell_loads_the_runtime_the_way_it_loads_the_others(self):
        shell = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "js/modules/core/action-toolbar.js" in shell


# ---------------------------------------------------------------------------
# The runtime against the bar the server rendered
# ---------------------------------------------------------------------------
class TestTheRuntimeObeysTheRenderedBar:
    def test_the_runtime_turns_the_served_bar_into_the_contract(self, render, tmp_path):
        """Node runs the shipped module against the shipped markup."""
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")

        html = bar(render)
        opening = re.search(r'<div class="action-bar[^>]*>', html, re.S).group(0)
        attributes = dict(re.findall(r'(data-[\w-]+)="([^"]*)"', opening))
        labels = {}
        for button in re.findall(r"<button[^>]*data-bulk-action[^>]*>", html, re.S):
            label = re.search(r'data-action-label="([^"]*)"', button)
            name = re.search(r'aria-label="([^"]*)"', button)
            if label and name:
                labels[label.group(1)] = name.group(1)
        fixture = tmp_path / "bar.json"
        fixture.write_text(json.dumps({"attributes": attributes,
                                       "bulk_labels": labels}), encoding="utf-8")

        proc = subprocess.run(
            [node, str(HARNESS), f"--bar-json={fixture}"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "checks passed" in proc.stdout
        assert "FAIL" not in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# The reference pair, as served
# ---------------------------------------------------------------------------
class TestTheMigratedPagesRenderTheContract:
    @pytest.mark.parametrize("url,bar_id,noun,singular,bulk_ids,module,checkbox,rows",
                             SELECTION_PAGES)
    def test_the_served_page_renders_the_no_selection_state(
            self, app, admin_client, url, bar_id, noun, singular, bulk_ids,
            module, checkbox, rows):
        resp = admin_client.get(url)
        assert resp.status_code == 200, url
        html = resp.get_data(as_text=True)

        assert f'id="{bar_id}"' in html
        assert 'role="toolbar"' in html
        summary = re.search(r"<span[^>]*data-selection-summary[^>]*>.*?</span>", html, re.S)
        assert summary, f"{url} renders a bar with no scope element"
        assert f'data-noun="{noun}"' in summary.group(0)
        assert f'data-noun-singular="{singular}"' in summary.group(0)
        assert 'role="status"' in summary.group(0)
        assert f"Select {noun} first" in summary.group(0), (
            f"{url}: the scope does not say what has to happen first")

        for button_id in bulk_ids:
            button = re.search(rf'<button[^>]*id="{button_id}"[^>]*>', html, re.S)
            assert button, f"{url} has no {button_id}"
            markup = button.group(0)
            assert " disabled" in markup, (
                f"{url}: {button_id} was startable with nothing selected")
            assert "aria-disabled=\"true\"" in markup
            assert "data-on-click" not in markup, f"{url}: {button_id} ran without a scope"
            assert f"Select {noun} first" in markup, (
                f"{url}: {button_id} does not say why it cannot be used")

        # One select/deselect toggle replaces the former Select All / Select None pair.
        assert 'data-select-toggle' in html, f"{url}: missing select toggle"
        assert 'data-on-click="toggleAllSelection()"' in html
        assert html.count('data-on-click="selectAll()"') == 0, f"{url}: Select All button still present"
        assert html.count('data-on-click="selectNone()"') == 0, f"{url}: Select None button still present"

    @pytest.mark.parametrize("url,bar_id,noun,singular,bulk_ids,module,checkbox,rows",
                             SELECTION_PAGES)
    def test_every_row_reaches_the_toolbar_exactly_once(
            self, app, admin_client, url, bar_id, noun, singular, bulk_ids,
            module, checkbox, rows):
        """One binding per row, and no second, document-wide listener beside it."""
        path = PROJECT_ROOT / "static/js/pages" / module
        source = path.read_text(encoding="utf-8")

        if rows == "server":
            _seed(admin_client)
            html = admin_client.get(url).get_data(as_text=True)
            changes = html.count('data-on-change="updateBulkButtons()"')
            rendered = len(re.findall(rf'class="[^"]*{re.escape(checkbox)}', html))
            assert rendered > 0, f"{url} renders no rows to select"
            assert changes == rendered, (
                f"{url}: {rendered} rows but {changes} change handlers - a row "
                "would not reach the toolbar, or would reach it twice")
        else:
            # The keyword page builds rows in JavaScript. It binds one delegated
            # change listener to the stable table body and filters to the row
            # checkbox, rather than generating an inline handler per row.
            if module == "keywords-list-page.js":
                assert source.count("tableBody?.addEventListener('change'") == 1
                assert f"event.target.matches('.{checkbox}')" in source
                assert re.search(
                    rf'<input[^>]*class="[^"]*{re.escape(checkbox)}', source
                ), f"{module} no longer renders the selection checkbox"
                assert re.search(
                    rf"event\.target\.matches\('\.{re.escape(checkbox)}'\)"
                    r"\)\s*updateBulkButtons\(\)", source
                ), f"{module}: the delegated checkbox change does not reach the page handler"
            else:
                # A different dynamic list may use one handler in its row
                # template; in that shape, assert the single binding is local.
                handlers = source.count('data-on-change="updateBulkButtons()"')
                assert handlers == 1, (
                    f"{module} renders {handlers} change bindings; a row template "
                    "needs exactly one")
                lines = source.splitlines()
                at = next(i for i, line in enumerate(lines)
                          if 'data-on-change="updateBulkButtons()"' in line)
                window = "\n".join(lines[max(0, at - 8):at + 1])
                assert checkbox in window, (
                    f"{module}: the handler is not on the row's checkbox - a change "
                    "would reach the toolbar from something else")

        assert "document.addEventListener('change'" not in source, (
            f"{module} both binds each row and listens document-wide: one "
            "change would update the toolbar twice")

    def test_email_words_keeps_page_actions_alongside_selection(self, app, admin_client):
        """Email words has both selection bulk actions and page-scoped export/copy."""
        resp = admin_client.get("/email-words")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="emailWordsActionBar"' in html
        assert "data-selection-summary" in html
        assert 'data-select-toggle' in html
        assert 'id="bulkExportEmailBtn"' in html
        assert 'id="bulkCopyEmailBtn"' in html
        assert 'type="submit"' in html, "Apply Filters submits the filter form"
        assert 'href="/email-words"' in html, "Reset is a link, not a handler"
        module = (PROJECT_ROOT / "static/js/pages/email-words-page.js").read_text(
            encoding="utf-8")
        export_btn = re.search(r'<button[^>]*id="exportEmailWordsButton"[^>]*>', html, re.S)
        assert export_btn, "email_words lost its exportEmailWordsButton control"
        assert "data-on-click" not in export_btn.group(0), (
            "page actions are bound by the module, not inline handlers")
        assert 'id="emailWordsExportMenu"' in html, (
            "export must offer a column picker before CSV download")
        assert 'data-export-column="email"' in html
        assert 'data-export-column="usage_count"' in html
        assert 'data-email-export-format="csv"' in html
        assert "getElementById('emailWordsExportMenu')" in module
        assert "exportData(" in module
        assert "ActionToolbar.sync('emailWordsActionBar'" in module
        assert "exportSelectedEmails" in module
        assert "copySelectedEmails" in module

        copy_btn = re.search(r'<button[^>]*id="copyEmailWordsButton"[^>]*>', html, re.S)
        assert copy_btn, "email_words lost its copyEmailWordsButton control"
        assert "data-on-click" not in copy_btn.group(0), (
            "page actions are bound by the module, not inline handlers")
        assert "getElementById('copyEmailWordsButton')?.addEventListener('click'" in module
        assert "copyToClipboard(event.currentTarget)" in module