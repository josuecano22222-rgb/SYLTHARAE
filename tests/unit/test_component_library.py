"""Unit: the shared components keep their contract.

The library is only worth having if the components stay predictable:

* every component says what it is for and which states it answers;
* the state vocabulary is complete - a state nobody implements is named as
  *not yet*, with the component that will own it, rather than implied;
* a component renders the stylesheets' own vocabulary and invents no styles;
* a component renders what it is given - no queries, no authorization, no
  deciding which state is true;
* an error state written for a reader never carries a stack trace, a path or
  a query (spec §38/§76);
* the document and the components cannot drift apart.

These are the rules from `docs/COMPONENT_LIBRARY.md`, as tests.
"""

from __future__ import annotations

import re

import jinja2
import pytest

from core.frontend.component_audit import (
    COMPONENTS,
    OWNED,
    PLANNED_STATES,
    PROJECT_ROOT,
    STATES,
    THIRD_PARTY,
    UNKNOWN,
    audit_block,
    class_ownership,
    classify_class,
    components,
    owned_classes,
    state_coverage,
    third_party_classes,
    undeclared_files,
    unknown_classes,
)

LIBRARY_DOC = PROJECT_ROOT / "docs/COMPONENT_LIBRARY.md"

#: Things a component must never do: read the database, take a decision the
#: server should take, or reach past its inputs.
FORBIDDEN = (
    ".query(", "db.session", "session[", "current_app", "requests.",
    "sqlalchemy", "get_interface(", "interface_registry(", "is_interface_enabled(",
    "has_permission", "check_permission",
)


def _component_texts():
    for path in sorted(COMPONENTS.glob("*.html")):
        yield path.name, path.read_text(errors="ignore")


def _renderable(text: str) -> str:
    """The component's markup, without its own prose.

    A component is allowed to describe a call it must not make - that is how
    the rule is written down in the file. Only what actually renders counts.
    """
    text = re.sub(r"\{#.*?#\}", " ", text, flags=re.DOTALL)
    return re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)


class TestEveryComponentDeclaresItself:
    def test_no_component_file_is_undeclared(self):
        assert undeclared_files() == [], (
            "a file in templates/components/ has no `{# component: … #}` "
            "declaration, so its purpose and states are unknown")

    def test_declared_states_are_from_the_vocabulary(self):
        for name, component in components().items():
            for state in component.states:
                assert state in STATES, f"{name} declares an unknown state: {state}"

    def test_declared_presentation_states_are_from_that_vocabulary(self):
        """A presentation state is not a component state, and vice versa."""
        from core.frontend.status_vocabulary import PRESENTATION_STATES

        for name, component in components().items():
            for state in component.presentation_states:
                assert state in PRESENTATION_STATES, (
                    f"{name} declares an unknown presentation state: {state}")
            for state in component.presentation_states:
                assert state not in STATES or state in {"success", "warning"}, (
                    f"{name}: {state} is being used as both a component state "
                    "and a presentation state")

    def test_every_component_says_what_it_is_for(self):
        for name, component in components().items():
            assert component.purpose, f"{name} does not say what it is for"


class TestTheStateVocabularyIsComplete:
    def test_the_vocabulary_is_the_thirteen_states(self):
        assert len(STATES) == 13, "the §63 vocabulary is thirteen states"

    def test_a_state_is_either_implemented_or_named_as_not_yet(self):
        covered = {state for state, owners in state_coverage().items() if owners}
        missing = set(STATES) - covered
        assert missing == set(PLANNED_STATES), (
            "the states no component implements must be exactly the ones this "
            "project has declared it still owes; if one has just been built, "
            "remove it from PLANNED_STATES; if one has gone missing, that is "
            "the regression. missing=" + repr(sorted(missing)))

    def test_each_planned_state_is_a_real_state(self):
        for state in PLANNED_STATES:
            assert state in STATES, state

    def test_the_document_calls_them_not_yet_rather_than_implying_coverage(self):
        text = LIBRARY_DOC.read_text()
        for state in PLANNED_STATES:
            assert f"`{state}`" in text
        assert "not yet" in text


class TestComponentsArePresentationOnly:
    def test_no_component_reads_the_database_or_decides_access(self):
        offenders = []
        for name, text in _component_texts():
            for needle in FORBIDDEN:
                if needle in _renderable(text):
                    offenders.append(f"{name}: {needle}")
        assert offenders == [], (
            "components render what they are given; these reach past their "
            "inputs:\n  " + "\n  ".join(offenders))

    def test_no_component_names_a_product_interface(self):
        from core.interfaces import REGISTRY

        offenders = []
        for name, text in _component_texts():
            for interface in REGISTRY:
                if f"'{interface.interface_id}'" in text:
                    offenders.append(f"{name}: {interface.interface_id}")
        assert offenders == [], offenders

    def test_the_error_state_never_renders_an_internal(self):
        text = (COMPONENTS / "states.html").read_text()
        for leak in ("str(e)", "str(exc)", "traceback", ".query", "sql",
                     "__class__", "repr("):
            assert leak not in text, (
                "the error state is written for a reader; it must not be able "
                f"to render internals ({leak})")

    def test_a_failure_is_announced_and_a_wait_is_polite(self):
        text = (COMPONENTS / "states.html").read_text()
        assert "role='alert'" in text or 'role="alert"' in text
        assert "role='status'" in text or 'role="status"' in text
        # error and unauthorized are `alert`; empty and loading are `status`.
        assert re.search(r"state_panel\('error'.*role='alert'", text, re.DOTALL)
        assert re.search(r"state_panel\('unauthorized'.*role='alert'", text, re.DOTALL)
        assert re.search(r"state_panel\('loading'.*role='status'", text, re.DOTALL)


class TestClassesHaveExactlyOneOwner:
    """OWNED, THIRD_PARTY or UNKNOWN - and UNKNOWN is a failure.

    The distinction matters: a class from Bootstrap or Bootstrap Icons is an
    expected dependency, not a finding, while a class nothing defines is how a
    component invents a style. Reporting both as "not defined by us" would
    make the guardrail noise, and a guardrail nobody trusts is worse than none.
    """

    def test_no_component_renders_a_class_nobody_owns(self):
        problems = unknown_classes()
        assert problems == {}, (
            "these classes are rendered by a component but nothing owns them - "
            "no SYLTHARAE stylesheet defines them, no bundled dependency does, "
            "and the component does not declare them:\n  " + repr(problems))

    def test_a_third_party_class_is_not_a_finding(self):
        assert "bi-search" in third_party_classes()
        assert "btn" in third_party_classes()
        assert "bi" in third_party_classes()
        assert classify_class("bi-search").ownership == THIRD_PARTY
        assert classify_class("table-hover").ownership == THIRD_PARTY

    def test_an_owned_class_is_recognised(self):
        assert classify_class("empty-state").ownership == OWNED
        assert classify_class("file-nav__button").ownership == OWNED

    def test_a_component_can_own_a_class_by_declaring_it(self):
        """Declaring a hook is a decision; the audit shows it, the test allows it.

        The example is derived rather than named: a hard-coded class stops
        exercising this path the moment a stylesheet starts styling it (which
        is what happened to `file-nav__text`, styled by the dock stylesheet to
        stay screen-reader-only on narrow screens - correctly reported as owned
        by that rule, not by the declaration).
        """
        declared = {name for component in components().values()
                    for name in component.classes}
        hooks = sorted(declared - owned_classes())
        assert hooks, "no component declares a class of its own any more"
        hook = hooks[0]
        entry = classify_class(hook, {hook})
        assert entry.ownership == OWNED
        assert entry.source == "component declaration"

    def test_an_invented_class_is_unknown(self):
        """The guardrail has to be able to fail, or it is decoration."""
        assert classify_class("some-class-nobody-defined").ownership == UNKNOWN
        assert classify_class("someOtherHook", {"someOtherHook"}).ownership == OWNED
        assert unknown_classes() == {}

    def test_a_class_built_from_a_variable_is_owned_by_its_prefix(self):
        """`file-nav--{{ variant }}` is a real class family, not a typo."""
        assert classify_class("file-nav--").ownership == OWNED

    def test_the_ownership_report_covers_every_rendered_class(self):
        report = class_ownership()
        assert report, "the ownership report is empty"
        for relative, entries in report.items():
            assert relative.startswith("templates/components/")
            for entry in entries:
                assert entry.ownership in {OWNED, THIRD_PARTY, UNKNOWN}
                assert entry.source

    def test_every_icon_a_component_names_exists(self):
        """An icon the bundle does not have renders as nothing, silently.

        `bi bi-person-check` was named in a component and on a page; Bootstrap
        Icons has no such icon, so the icon simply was not there and nothing
        said so. The bundle is the vocabulary, because it is what ships.
        """
        import re

        available = third_party_classes()
        offenders = []
        for path in sorted((PROJECT_ROOT / "templates").rglob("*.html")):
            text = path.read_text(errors="ignore")
            for icon in set(re.findall(r"\bbi-[a-z0-9-]+", text)):
                if icon.endswith("-"):
                    # Built from a variable: `bi-arrow-{{ up or down }}`.
                    if not any(name.startswith(icon) for name in available):
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {icon}*")
                elif icon not in available:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {icon}")
        assert offenders == [], (
            "these icons are named but the bundled Bootstrap Icons has no such "
            "icon, so they render as nothing:\n  " + "\n  ".join(sorted(offenders)))

    def test_every_declared_class_is_real(self):
        """A component may not declare a class it never renders."""
        for name, component in components().items():
            text = (PROJECT_ROOT / component.path).read_text()
            for declared in component.classes:
                if declared.startswith("("):
                    continue
                assert declared in text, (
                    f"{name} declares {declared} but its file never renders it")


class TestPagination:
    """Two models, one bar each, and no third renderer anywhere."""

    def test_no_template_writes_pagination_markup_by_hand(self):
        from core.frontend.component_audit import PATTERNS, users

        pattern = next(p for p in PATTERNS if p.key == "pagination")
        assert users(pattern) == [], (
            "pagination markup is written by hand again: " + repr(users(pattern)))

    def test_the_mounts_are_declared_and_not_confused_with_markup(self):
        from core.frontend.component_audit import PATTERNS, users

        mount = next(p for p in PATTERNS if p.key == "pagination_mount")
        assert users(mount), "the mount pattern matches nothing; it has drifted"
        for path in users(mount):
            text = (PROJECT_ROOT / path).read_text()
            assert "unified-pagination-item" not in text, path

    def test_the_two_models_stay_separate(self):
        """A cursor cannot jump to page 7, and numbered pages cannot be forced
        into a forward/backward API."""
        numbered = (PROJECT_ROOT / "templates/components/unified_pagination.html").read_text()
        cursor = (PROJECT_ROOT / "templates/components/cursor_pagination.html").read_text()
        assert "macro unified_pagination(page, total_pages" in numbered
        assert "macro cursor_pagination(next_cursor, prev_cursor" in cursor
        assert "next_cursor" not in numbered
        assert "total_pages" not in cursor, (
            "the cursor pagination is being given a page count it cannot have")

    def test_they_share_their_visual_conventions(self):
        """Different data semantics, one look."""
        for relative in ("templates/components/unified_pagination.html",
                         "templates/components/cursor_pagination.html"):
            text = (PROJECT_ROOT / relative).read_text()
            assert "pagination" in text, relative
            assert "aria-label" in text, relative
            assert "bi-chevron-left" in text and "bi-chevron-right" in text, relative

    def test_the_cursor_pager_needs_no_javascript(self):
        """Moving through a cursor-paged list is a URL, so it is a link. The
        component used to emit an onclick handler and a global function."""
        text = (PROJECT_ROOT / "templates/components/cursor_pagination.html").read_text()
        assert "<script" not in text
        assert "data-on-" not in text

    def test_cursor_pages_get_the_cursor_component(self):
        """The two pagination models stay honest about which is which.

        Sources and sides used to be cursor-paged with the numbered pager
        bolted on (a page number guessed a cursor); they are server-paged
        lists now and render the numbered pager. The cursor component stays
        for the lists that actually stream, and no list guesses a cursor
        from a page number any more.
        """
        for relative in ("templates/Side/sides_list.html",
                         "templates/Sources/sources_list.html"):
            text = (PROJECT_ROOT / relative).read_text()
            assert "components/unified_pagination.html" in text, relative
            assert "cursor_pagination.html" not in text, relative
            assert "unified-pagination-item" not in text, relative
            assert "estimatedCursor" not in text, relative

    def test_only_one_javascript_pager_exists(self):
        """The pages used to carry their own fallback renderers."""
        renderers = []
        for path in sorted((PROJECT_ROOT / "static/js").rglob("*.js")):
            text = path.read_text(errors="ignore")
            if "pagination-item" in text or "pagination-link" in text:
                renderers.append(str(path.relative_to(PROJECT_ROOT)))
        assert renderers == ["static/js/modules/rendering/unified-pagination.js"], (
            "more than one JavaScript module draws pagination markup: "
            + repr(renderers))

    def test_the_two_pagers_are_measured_as_one_family(self):
        """Adoption is per family: a cursor-paged list is not behind a
        numbered one, and a mount is how a page adopts the component, not a
        thing left to migrate."""
        from core.frontend.component_audit import adoption

        rows = {row["key"]: row for row in adoption()}
        assert "pagination" in rows
        assert "pagination_mount" not in rows
        assert rows["pagination"]["hand_written"] == 0
        assert rows["pagination"]["rate"] == 100

    def test_the_file_reader_uses_the_component(self):
        text = (PROJECT_ROOT / "templates/file/file_detail.html").read_text()
        assert "components/unified_pagination.html" in text
        assert "unified-pagination-item" not in text


class TestTheSearchBox:
    """One search box: same parts, same ARIA, same keyboard, everywhere."""

    @pytest.fixture()
    def render(self):
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")),
            autoescape=True)
        environment.globals["_"] = lambda text: text

        def render_source(source: str) -> str:
            return environment.from_string(source).render()

        return render_source

    def test_it_renders_the_parts_a_search_box_is_made_of(self, render):
        html = render("{% from 'components/search_input.html' import search_input %}"
                      "{{ search_input('findIt', label='Search Words',"
                      " placeholder='Search words...', on_clear='clearSearch()') }}")
        assert 'for="findIt"' in html                      # a real label
        assert 'id="findIt"' in html
        assert 'class="form-control search-input"' in html
        assert 'placeholder="Search words..."' in html
        assert 'class="btn-clear-search"' in html
        assert 'data-on-click="clearSearch()"' in html           # the page's handler
        assert 'aria-label="Clear search"' in html

    def test_it_can_be_labelled_without_a_visible_label(self, render):
        html = render("{% from 'components/search_input.html' import search_input %}"
                      "{{ search_input('findIt', aria_label='Find a file') }}")
        assert 'aria-label="Find a file"' in html
        assert "<label" not in html

    def test_it_can_be_disabled_and_busy(self, render):
        html = render("{% from 'components/search_input.html' import search_input %}"
                      "{{ search_input('findIt', label='S', disabled=true, loading=true) }}")
        assert " disabled" in html
        assert 'aria-disabled="true"' in html
        assert 'aria-busy="true"' in html
        assert "spinner-border" in html

    def test_it_can_be_typed_into_and_named(self, render):
        html = render("{% from 'components/search_input.html' import search_input %}"
                      "{{ search_input('findIt', label='S', value='already here', name='q') }}")
        assert 'value="already here"' in html
        assert 'name="q"' in html

    def test_the_escape_key_is_owned_by_one_module(self):
        """Keyboard behaviour is not scattered through the pages."""
        module = (PROJECT_ROOT / "static/js/modules/core/search-input.js").read_text()
        assert "Escape" in module
        assert "btn-clear-search" in module
        base = (PROJECT_ROOT / "templates/base.html").read_text()
        assert "modules/core/search-input.js" in base

    def test_the_pages_render_their_box_through_the_component(self):
        for relative, input_id in (
            ("templates/Word/Word_list.html", "searchWords"),
            ("templates/Side/sides_list.html", "sideSearch"),
            ("templates/Sources/sources_list.html", "sourceSearch"),
            ("templates/file/files_list.html", "smartSearch"),
            ("templates/Category/categories_list.html", "categorySearch"),
            ("templates/Category/category_words.html", "wordSearch"),
            ("templates/email_words/email_words.html", "searchInput"),
        ):
            text = (PROJECT_ROOT / relative).read_text()
            assert "search_group" in text or "search_input" in text, relative
            assert 'class="search-input-wrapper"' not in text, (
                relative + " still writes the box itself")
            assert input_id in text, relative


class TestTheDocumentCannotDrift:
    def test_the_generated_section_matches_the_components(self):
        text = LIBRARY_DOC.read_text()
        assert "<!-- BEGIN GENERATED COMPONENT AUDIT -->" in text
        start = text.index("<!-- BEGIN GENERATED COMPONENT AUDIT -->")
        end = text.index("<!-- END GENERATED COMPONENT AUDIT -->")
        generated = text[start:end].split("\n", 1)[1].strip()
        assert generated == audit_block().strip(), (
            "docs/COMPONENT_LIBRARY.md is out of date with the components; "
            "regenerate with `python3 -m core.frontend.component_audit "
            "docs/COMPONENT_LIBRARY.md`")

    def test_the_document_states_the_rules(self):
        text = LIBRARY_DOC.read_text()
        assert "Presentation only" in text
        assert "not a redesign" in text


class TestAdoption:
    """The library is used, and the hand-written count is the measure."""

    def test_the_migrated_pages_use_the_component(self):
        migrated = [
            "templates/Keyword/keywords_list.html",
            "templates/Word/Word_list.html",
            "templates/file/files_list.html",
        ]
        for relative in migrated:
            text = (PROJECT_ROOT / relative).read_text()
            assert 'class="empty-state"' not in text, relative

    def test_the_migrated_table_uses_the_frame(self):
        text = (PROJECT_ROOT / "templates/Word/Word_list.html").read_text()
        assert "components/table.html" in text
        assert "{% call record_table(" in text
        assert "<table" not in text, "the page writes its own table frame again"

    def test_the_filter_and_action_bar_page_uses_the_components(self):
        text = (PROJECT_ROOT / "templates/Keyword/keywords_list.html").read_text()
        assert "components/filter_bar.html" in text
        assert "components/action_toolbar.html" in text
        assert 'class="file-filters-section"' not in text
        assert 'class="action-bar"' not in text
        assert 'class="filter-group' not in text
        assert 'class="search-input-wrapper"' not in text

    def test_the_status_badge_migration_is_pinned(self):
        for relative in ("templates/Keyword/keywords_list.html",
                         "templates/Word/Word_list.html",
                         "templates/Word/Word_detail.html",
                         "templates/Settings/settings.html",
                         "templates/email_words/email_words.html",
                         "templates/Sources/sources_list.html"):
            text = (PROJECT_ROOT / relative).read_text()
            assert "status_badge" in text, relative

    def test_the_table_component_covers_the_states_a_list_has(self):
        table = components()["table"]
        assert {"normal", "empty", "filtered", "selected", "loading",
                "error"} <= set(table.states)

    #: What is still hand-written, checked one number at a time.
    #:
    #: Each entry carries the reason it is where it is, because a count with
    #: no inventory behind it is a number nobody can audit. The four family
    #: keys are the audit's pattern names; `status` and `chip` are badges.
    #:
    #: Every count here was re-derived from the templates, not copied from the
    #: previous run: `python3 -c "from core.frontend.component_audit import
    #: counts; print(counts())"` is the reproduction, and the per-template
    #: inventory for the two badge families is pinned in
    #: `HAND_WRITTEN_CHIPS` / `HAND_WRITTEN_SEARCH` below.
    HAND_WRITTEN = {
        # The seven list interfaces render through record_table now, and the
        # File Types dashboard's formats list has followed (its documents
        # panel embeds the shared file-library table); what is left
        # hand-writing table markup are the dashboards and screens the table
        # migration has not reached, named by the audit: analysis_batch,
        # dashboard, analyst_categorization, job_detail, jobs, settings,
        # translation_manager, auth/users and email_words.
        #
        # Not counted, and deliberately: `components/table.html` is the
        # component itself, and `components/operations_widget.html` renders a
        # `<table>` its script fills - a mount, like the pagination mounts the
        # audit counts separately. `users()` reports user-facing templates.
        #
        # It was twelve: monitoring (the scenarios list), reports (the
        # file-type table) and the translation selection panel still wrote the
        # frame by hand, and now call `data_table`. The assertion below was
        # already nine, so the audit had drifted *above* its own expectation
        # for four days without failing - the first mismatch it hit was this
        # one, which is why the numbers are now checked one at a time.
        "pattern_table": 9,
        "pattern_filter": 7,
        "pattern_toolbar": 2,
        "pattern_search": 12,
        "pattern_empty_state": 3,
        "badges_status": 0,
        "badges_chip": 39,
    }

    #: The two search boxes the audit counts that are not filter bars over a
    #: list, named rather than inferred: the category tree's finder and the
    #: Lexis cosmos finder. Both are page-local widgets - their own CSS file,
    #: their own key handling, a popup result listbox, and `flex` rules that
    #: target the `input` as a direct child of their own container
    #: (`.lexis-pbar input { flex: 1 }`, `.lexis-find-wrap input`,
    #: `.tree-search`). The shared box renders its input inside
    #: `.search-input-wrapper`, so adopting it would change those pages'
    #: layout with no test able to see the change. They stay hand-written and
    #: counted: the audit says twelve, not ten.
    HAND_WRITTEN_SEARCH = [
        "templates/Analysis/charts_dashboard.html",
        "templates/Analysis/comprehensive_dashboard.html",
        "templates/Analysis/dashboard.html",
        "templates/Category/categories_tree.html",
        "templates/Lexis/cosmos.html",
        "templates/Notifications/notifications.html",
        "templates/Operations/input.html",
        "templates/Search/search_advanced.html",
        "templates/Settings/settings.html",
        "templates/Settings/translation_manager.html",
        "templates/file/file_detail.html",
        "templates/map/map_viewer.html",
    ]

    #: Every hand-written count/id/method chip, by template, so the total is
    #: an inventory rather than an impression. `analysis_batch` alone carries
    #: nine, `settings` five. (It was 68: the id/count chips the sources and
    #: sides card grids rendered were deleted with the grids when both lists
    #: moved onto record_table - the markup moved, it was not migrated; the
    #: File Types formats table followed, and the search results moved onto
    #: the table with theirs.) Thirty-two was the last recorded total and 39
    #: is what the templates hold; the difference is `base.html` (three: the
    #: page identity badge and the two actor badges), `Reports/reports.html`
    #: (two: the paths-per-type counts), `Translation/_selection_panel.html`
    #: (one: the "machine translation, not verified" marker),
    #: `file/file_detail.html` (three), `file/files_list.html`,
    #: `file/full_content.html`, `Search/search.html`, `Search/saved_searches.html`
    #: and `Category/category_names.html`. Each is a number, an id or an
    #: HTTP method: migrating one would be busy-work dressed up as progress,
    #: which is what `badge_usage` says it will not count as work.
    HAND_WRITTEN_CHIPS = {
        "templates/Analysis/analysis_batch.html": 9,
        "templates/Category/category_names.html": 1,
        "templates/Keyword/keyword_detail.html": 2,
        "templates/Operations/import_center.html": 1,
        "templates/Reports/dashboard.html": 2,
        "templates/Reports/reports.html": 2,
        "templates/Search/saved_searches.html": 1,
        "templates/Search/search.html": 1,
        "templates/Settings/settings.html": 5,
        "templates/Translation/_selection_panel.html": 1,
        "templates/Word/Word_detail.html": 1,
        "templates/base.html": 3,
        "templates/email_words/email_words.html": 3,
        "templates/file/File_Management_Analysis_System.html": 2,
        "templates/file/file_detail.html": 3,
        "templates/file/files_list.html": 1,
        "templates/file/full_content.html": 1,
    }

    @pytest.mark.parametrize("name", sorted(HAND_WRITTEN))
    def test_the_audit_counts_the_hand_written_markup(self, name):
        """The measure of this phase is a number, not an impression.

        One number per case: the first mismatch used to abort the test and
        hide every count behind it, and two of them had drifted unnoticed.
        """
        from core.frontend.component_audit import counts

        expected = self.HAND_WRITTEN[name]
        assert counts()[name] == expected

    def test_the_empty_state_count_is_the_baseline_measure(self):
        from core.frontend.component_audit import counts, users

        pattern = next(p for p in __import__(
            "core.frontend.component_audit", fromlist=["PATTERNS"]).PATTERNS
            if p.key == "empty_state")
        assert counts()["pattern_empty_state"] == len(users(pattern))
        assert counts()["pattern_empty_state"] < 7, (
            "adoption has not started; the baseline was seven templates")
        assert counts()["pattern_empty_state"] == 3, (
            "the raw empty-state blocks the card grids carried went with the "
            "grids when sources and sides moved onto record_table; what is "
            "left are the three dashboards that have not been migrated")

    def test_the_search_count_names_every_box_it_counts(self):
        from core.frontend.component_audit import PATTERNS, users

        counted = users(next(p for p in PATTERNS if p.key == "search"))
        assert counted == self.HAND_WRITTEN_SEARCH

    def test_the_chip_count_names_every_template_it_counts(self):
        from core.frontend.component_audit import badge_usage, counts

        per_template = {}
        for path in sorted((PROJECT_ROOT / "templates").rglob("*.html")):
            if COMPONENTS in path.parents:
                continue
            chip = badge_usage(path.read_text(errors="ignore"))[1]
            if chip:
                per_template[str(path.relative_to(PROJECT_ROOT))] = chip
        assert per_template == self.HAND_WRITTEN_CHIPS
        assert sum(per_template.values()) == counts()["badges_chip"]
        assert counts()["badges_status"] == 0, (
            "a hand-written status badge is back; every one of them now "
            "renders through the component and the vocabulary")


class TestTheComponentsRenderWhatTheyPromised:
    """Rendered, not read: the rules above are only real if the output obeys."""

    @pytest.fixture()
    def render(self):
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(PROJECT_ROOT / "templates")),
            autoescape=True)
        environment.globals["_"] = lambda text: text

        def render_source(source: str) -> str:
            return environment.from_string(source).render()

        return render_source

    def test_a_bulk_action_without_a_selection_cannot_be_started(self, render):
        out = render("""{% from 'components/action_toolbar.html' import bulk_action_button %}
{{ bulk_action_button(_('Delete Selected'), onclick='bulkDelete()', noun=_('records')) }}""")
        assert "disabled" in out
        assert "data-on-click" not in out, "a bulk action ran without saying what it covers"
        assert "Select records first" in out
        assert 'aria-label="Delete Selected: Select records first"' in out

    def test_the_scope_element_carries_the_words_the_runtime_needs(self, render):
        """The server states the no-selection case; the runtime states the rest."""
        out = render("""{% from 'components/action_toolbar.html' import selection_summary %}
{{ selection_summary(27, _('records'), _('record')) }}""")
        assert "Select records first" in out
        assert 'role="status"' in out
        assert 'data-selected-count="0"' in out
        assert 'data-total="27"' in out
        assert 'data-noun="records"' in out
        assert 'data-noun-singular="record"' in out

    def test_the_empty_state_is_announced_politely_and_a_failure_is_not(self, render):
        empty = render("""{% from 'components/states.html' import empty_state %}
{{ empty_state(message='Nothing yet.') }}""")
        failure = render("""{% from 'components/states.html' import error_state %}
{{ error_state(message='We could not read that file.') }}""")
        assert 'role="status"' in empty and 'role="alert"' not in empty
        assert 'role="alert"' in failure
        assert "could not read that file" in failure

    def test_the_state_panel_marks_which_state_it_is(self, render):
        for state, macro in (("empty", "empty_state"), ("filtered", "filtered_state"),
                             ("loading", "loading_state"), ("error", "error_state"),
                             ("unauthorized", "unauthorized_state"),
                             ("unavailable", "unavailable_state"),
                             ("archived", "archived_state")):
            out = render("{% from 'components/states.html' import " + macro + " %}"
                         "{{ " + macro + "(message='x') }}")
            assert f'data-state="{state}"' in out, state

    def test_a_status_badge_takes_its_tone_rather_than_guessing_one(self, render):
        out = render("""{% from 'components/status_badge.html' import status_badge %}
{{ status_badge('Deprecated', tone='warning') }}{{ status_badge('') }}""")
        assert 'class="badge bg-warning text-dark"' in out
        assert out.count("<span") == 1, "an empty label renders no badge"

    def test_the_table_span_is_the_callers_not_a_guess(self, render):
        out = render("""{% from 'components/table.html' import table_empty_row %}
{{ table_empty_row(9, message='Nothing here.') }}""")
        assert 'colspan="9"' in out

    def test_a_filter_group_labels_its_control(self, render):
        out = render("""{% from 'components/filter_bar.html' import filter_group %}
{% call filter_group('statusFilter', _('Status'), 'bi-check-circle') %}
<select class="form-select" id="statusFilter"></select>
{% endcall %}""")
        assert 'class="filter-group"' in out
        assert 'for="statusFilter"' in out
        assert 'id="statusFilter"' in out

    def test_the_search_group_renders_the_clear_control(self, render):
        out = render("""{% from 'components/filter_bar.html' import search_group %}
{{ search_group('searchKeywords', _('Search Keywords'), _('Search keywords...'),
                onclear='clearSearch()') }}""")
        assert 'class="filter-group filter-group-search"' in out
        assert 'class="search-input-wrapper"' in out
        assert 'class="btn-clear-search"' in out
        assert 'data-on-click="clearSearch()"' in out
