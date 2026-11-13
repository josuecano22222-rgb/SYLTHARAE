"""The shared component library, and where pages still do the work by hand.

Two questions, both answered from the repository rather than from memory:

* what components exist, what states each one implements, and what classes it
  renders - read out of the component files themselves; and
* how many templates still hand-roll that markup - counted, not estimated.

The second number is the point. "Introduce shared components" is not
verifiable on its own; "eleven templates still write their own empty state,
it was fourteen" is. Nothing here is maintained by hand: the component list
comes from the `{# component: ... #}` declarations at the top of each file in
``templates/components``, and the usage counts come from scanning the
templates.

Usage::

    python3 -m core.frontend.component_audit                    # report
    python3 -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md
"""

from __future__ import annotations

import pathlib
import re
import sys
from functools import lru_cache
from typing import Dict, Iterable, List, NamedTuple

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TEMPLATES = PROJECT_ROOT / "templates"
COMPONENTS = TEMPLATES / "components"
STYLESHEETS = PROJECT_ROOT / "static/css"

BEGIN = "<!-- BEGIN GENERATED COMPONENT AUDIT -->"
END = "<!-- END GENERATED COMPONENT AUDIT -->"

#: Stylesheets SYLTHARAE owns. Anything defined here is the product's own
#: vocabulary, whatever file it lands in.
PROJECT_STYLESHEETS = ("static/css",)

#: Stylesheets somebody else owns, bundled with the application. A class found
#: here is a dependency, not a decision: it is expected to be defined outside
#: this repository and it is not a violation to use it.
THIRD_PARTY_STYLESHEETS = (
    "static/css/bootstrap.min.css",
    "static/icons/bootstrap-icons.css",
)

#: The three answers a rendered class can have.
OWNED = "OWNED"
THIRD_PARTY = "THIRD_PARTY"
UNKNOWN = "UNKNOWN"

#: The states a component has to have an answer for (spec §63). A component
#: implements the states that apply to it; every state must be implemented by
#: *something*, or the state does not exist in the product.
STATES: Dict[str, str] = {
    "loading": "Work is in progress; the reader is told what is being waited for.",
    "empty": "Nothing exists here yet, and the reader is told how to start.",
    "normal": "The ordinary case: content is present and usable.",
    "filtered": "Something exists, but not under the filters applied.",
    "selected": "A row or record is chosen; actions that need a choice appear.",
    "editing": "A value is being changed, and the change is not saved yet.",
    "saving": "A change is on its way to the server.",
    "success": "The last action worked.",
    "warning": "Something needs attention but is not broken.",
    "error": "Something failed, with a next step rather than a stack trace.",
    "unauthorized": "The server refused this for this account.",
    "unavailable": "It needs something this installation does not have.",
    "archived": "Kept and readable, but out of the working set.",
}

#: The declaration a component file opens with. Parsed, so a component cannot
#: exist without saying what it is for and which states it answers.
DECLARATION = re.compile(
    r"\{#\s*component:\s*(?P<name>[\w-]+)(?P<body>.*?)#\}", re.DOTALL)
FIELD = re.compile(r"^\s*(?P<key>\w+):\s*(?P<value>.*?)(?=\n\s*\w+:|\Z)",
                   re.DOTALL | re.MULTILINE)


#: States in the vocabulary that no component implements *yet*, and the
#: component that will implement each. This is a deliberate absence list, not
#: a wish: the audit reports them as missing, a test fails when one appears or
#: disappears without the list being updated, and nothing pretends otherwise.
PLANNED_STATES: Dict[str, str] = {}


class Pattern(NamedTuple):
    """A piece of markup that pages currently write for themselves."""

    key: str
    description: str
    regex: str
    component: str
    #: Two patterns can belong to one family when a component has more than
    #: one legitimate shape - pagination has a numbered and a cursor pager -
    #: and the family is what adoption is measured on.
    family: str = ""
    #: A mount is an empty box the shared renderer fills, not markup written
    #: by hand. It belongs to the family so it counts as adopted; it is not a
    #: thing to be migrated.
    mount: bool = False


#: Markup that a shared component exists (or should exist) for. The regular
#: expressions are deliberately narrow: they match the *repeating* markup, not
#: every mention of a word.
PATTERNS: List[Pattern] = [
    Pattern("empty_state", "Hand-written empty state", r'class="empty-state"', "states"),
    Pattern("loading", "Hand-written loading indicator",
            r'(spinner-border|skeleton-loader)', "states"),
    Pattern("error", "Hand-written inline error", r'(alert-danger|error-state)', "states"),
    Pattern("table", "Hand-written table", r"<table\b", "table"),
    # Pagination-specific classes only. `page-navigation-bar`, the browser
    # back/forward bar in the shell, has "navigation" in its name and nothing
    # to do with paging: matching it is how a metric starts lying.
    Pattern("pagination", "Hand-written pagination markup",
            # The parts that render a page. Wrappers (`pagination-controls`,
            # `analyst-pagination`) are page chrome around the component, not
            # the component's markup, and counting them would report work that
            # is already done as outstanding.
            r'class="[^"]*(unified-pagination-list|unified-pagination-item'
            r'|unified-pagination-link|pagination-list|pagination-item'
            r'|pagination-link|pagination-page-numbers|page-item|page-link)',
            "pagination", family="pagination"),
    # A mount is not hand-written markup: it is an empty box the shared
    # renderer fills, and it is counted separately so the two numbers mean
    # something.
    Pattern("pagination_mount", "Pagination mount (filled by the shared renderer)",
            r'id="pagination"|id="assignmentsPagination"|PaginationContainer', "pagination",
            family="pagination", mount=True),
    # Markup signals only: `search-input` inside a script src is a file name,
    # not a search box written by hand.
    Pattern("search", "Hand-written search input",
            r'(type="search"|role="search"|class="[^"]*search-input'
            r'|class="[^"]*search-input-wrapper|search-input-group)',
            "search_input"),
    Pattern("filter", "Hand-written filter control",
            r'(filter-bar|filter-group|filters-panel|filter-control)', "filter_bar"),
    Pattern("confirm", "Browser confirm() dialog", r"\bconfirm\(", "confirm_dialog"),
    # A status badge and a count chip are both `<span class="badge bg-…">`, so
    # counting them together would have said "22 status badges to migrate" when
    # most of them are numbers, ids and HTTP methods. They are counted apart,
    # by what the badge actually shows - see `badge_usage`.
    Pattern("status_badge", "Hand-written status badge", r'class="badge bg-[a-z]+', "status_badge"),
    Pattern("badge_chip", "Hand-written badge chip (count, id, method)",
            r'class="badge bg-[a-z]+', None),
    Pattern("toolbar", "Hand-written action bar",
            r'class="[^"]*(action-bar|action-toolbar|btn-toolbar)', "action_toolbar"),
]


#: Areas that legitimately do not use a shared component, with the reason and
#: who owns the decision. Without this list the two failure modes are a
#: duplicate component grown in secret, or a generic one overloaded until it
#: stops being usable - so an exception is written down, and the audit prints
#: it rather than counting it as debt.
EXCEPTIONS: Dict[str, Dict[str, str]] = {
    "analysis relationship matrix": {
        "reason": "A matrix of relationships between records is not a list of "
                  "records: its rows and columns are both entities, and its "
                  "cells are computed pairs. Forcing it into the table "
                  "component would give the component a second meaning.",
        "owner": "analysis workspace",
    },
}


class Component(NamedTuple):
    name: str
    path: str
    purpose: str
    states: List[str]
    classes: List[str]
    notes: str
    macros: List[str]
    #: The CSS classes the component says it owns but that appear in a
    #: stylesheet rather than in its own markup footprint - the `css:` line of
    #: the declaration. A component that declares `css: record-actions` has
    #: said the class is its own, and the Screen Inspector reads it that way.
    css: List[str] = []
    #: The presentation states a component accepts, where that differs from
    #: the component-state vocabulary (§63) it is described by. Keeping them
    #: as two fields is what stops "danger" being read as a §63 state.
    presentation_states: List[str] = []


def _parse_field(body: str, key: str) -> str:
    match = re.search(rf"^\s*{key}:\s*(?P<value>.*?)(?=\n\s*\w+:|\Z)",
                      body, re.DOTALL | re.MULTILINE)
    if not match:
        return ""
    lines = [line.strip() for line in match.group("value").strip().splitlines()]
    return " ".join(line for line in lines if line)


def _split(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def components() -> Dict[str, Component]:
    """Every component file, with what it declares about itself."""
    found: Dict[str, Component] = {}
    for path in sorted(COMPONENTS.glob("*.html")):
        match = DECLARATION.search(path.read_text(errors="ignore"))
        if not match:
            # Files that predate the declaration; reported as undeclared so
            # the gap is visible instead of implied.
            found[path.stem] = Component(path.stem, str(path.relative_to(PROJECT_ROOT)),
                                         "", [], [], "no declaration", [])
            continue
        body = match.group("body")
        # `{%- macro` is as much a macro as `{% macro`: requiring the space
        # after `{%` made half the library look macro-less.
        macros = re.findall(r"\{%-?\s*macro\s+(\w+)\s*\(",
                            path.read_text(errors="ignore"))
        found[match.group("name")] = Component(
            name=match.group("name"),
            path=str(path.relative_to(PROJECT_ROOT)),
            purpose=_parse_field(body, "purpose"),
            states=_split(_parse_field(body, "states")),
            classes=_split(_parse_field(body, "classes")),
            notes=_parse_field(body, "notes"),
            macros=macros,
            presentation_states=_split(_parse_field(body, "presentation_states")),
        )
        # `presentation_states` is not part of the tuple's positional order in
        # every caller, so it is set by replacement rather than position.
        entry = found[match.group("name")]
        found[match.group("name")] = entry._replace(
            css=_split(_parse_field(body, "css")))
    return found


def declared_classes(component: Component) -> List[str]:
    """Every class the component says is its own, from either declaration line."""
    combined: List[str] = []
    for name in list(component.classes) + list(component.css):
        # A `css:` line is prose at the end ("... - defined in static/css/…"),
        # so the class list is the part before the dash.
        name = name.split(" - ")[0].strip()
        if name and name not in combined:
            combined.append(name)
    return combined


#: The words a hand-written badge shows when it is showing a status. Taken from
#: the vocabulary rather than typed here, so a status added to the application
#: is counted without this file changing.
def _status_words() -> set:
    from core.frontend.status_vocabulary import VOCABULARY

    words = {label.lower() for _state, label in VOCABULARY.values()}
    words |= {state.lower().replace("_", " ") for state in VOCABULARY}
    return words


#: An empty container the shared renderer fills.
PAGINATION_MOUNT = re.compile(
    r'<div[^>]*id="(?:pagination|assignmentsPagination|paginationContainer)"[^>]*>\s*</div>')

BADGE_ELEMENT = re.compile(r'class="badge[^"]*"[^>]*>(.*?)</span>', re.DOTALL)


def badge_usage(template_text: str) -> tuple:
    """(status badges, other chips) written by hand in one template.

    A badge showing a status word or a `…status…` variable is a status badge;
    a badge showing a number, an id or an HTTP method is not, and migrating it
    would be busy-work dressed up as progress.
    """
    words = _status_words()
    status = chips = 0
    for inner in BADGE_ELEMENT.findall(template_text):
        text = re.sub(r"<[^>]+>", " ", inner)
        # Only the words: `{{ _('Active') }}` is the same badge as `Active`.
        words_in_badge = re.sub(r"[^a-z ]+", " ", text.lower()).split()
        if not words_in_badge:
            continue
        phrase = " ".join(words_in_badge)
        if "status" in words_in_badge or phrase in words:
            status += 1
        else:
            chips += 1
    return status, chips


#: Components that share a family with another component, because they do the
#: same job under different data semantics.
COMPONENT_FAMILY = {"pagination_cursor": "pagination"}

#: A macro that lives in one component's file but renders another component's
#: markup. Adoption is measured on who renders the markup, not on which file
#: the macro is defined in: a page that places a search box through the filter
#: bar has not migrated its filter controls.
MACRO_FAMILY = {"search_group": "search_input"}


def component_uses(component_name: str) -> set:
    """The other components a component renders.

    A component that delegates - the filter bar places the search box - has
    adopted the component it delegates to, and the page that uses the filter
    bar has therefore stopped writing a search box by hand. The import is the
    evidence, and it is read from the file rather than assumed.
    """
    path = component_file(component_name)
    if not path:
        return set()
    text = (PROJECT_ROOT / path).read_text(errors="ignore")
    used = set()
    for name in components():
        if name == component_name:
            continue
        if f"components/{component_file(name).split('components/')[-1]}" in text:
            used.add(name)
    return used


def family_of(pattern: Pattern) -> str:
    """The family a pattern belongs to - its component, unless it shares one."""
    return pattern.family or pattern.component or pattern.key


def component_file(component_name: str) -> str:
    """The file a component lives in - the name and the file differ for some."""
    entry = components().get(component_name)
    return entry.path if entry else ""


def family_macros(family: str) -> set:
    """The macros that render a family's markup, wherever they are defined.

    A macro defined in one component's file can render another component's
    markup - the filter bar's `search_group` places the search box - and who
    renders the markup is what adoption is about, not which file it lives in.
    """
    macros = set()
    for name, component in components().items():
        if COMPONENT_FAMILY.get(name, name) != family:
            continue
        macros |= {macro for macro in component.macros if not macro.startswith("_")}
    # A macro that renders another family's markup leaves this set, and one
    # that belongs here arrives, whether or not it is defined in this file.
    macros = {macro for macro in macros if MACRO_FAMILY.get(macro, family) == family}
    for macro, owner in MACRO_FAMILY.items():
        if owner == family:
            macros.add(macro)
    return macros


def standardized(pattern: Pattern) -> int:
    """How many templates render this family through the shared component.

    The other half of the adoption figure: the audit counts what is still
    written by hand, this counts what has moved, and together they give the
    completion criterion this phase is measured against.
    """
    if not pattern.component:
        return 0
    family = family_of(pattern)
    macros = family_macros(family)
    calls = (re.compile(r"\b(?:" + "|".join(sorted(re.escape(m) for m in macros))
                        + r")\s*\(") if macros else None)
    mount = [p for p in PATTERNS if p.mount and family_of(p) == family]
    found = set()
    for template in sorted(TEMPLATES.rglob("*.html")):
        if COMPONENTS in template.parents:
            continue
        text = template.read_text(errors="ignore")
        # A macro call is a page reading through the component; a mount is an
        # empty container the component's renderer fills. Both are adoption.
        if calls and calls.search(text):
            found.add(str(template.relative_to(PROJECT_ROOT)))
        elif any(re.search(p.regex, text) for p in mount):
            found.add(str(template.relative_to(PROJECT_ROOT)))
    return len(found)


def adoption() -> List[Dict[str, object]]:
    """Standardized versus hand-written, per pattern - the phase's measure."""
    rows = []
    seen = set()
    for pattern in PATTERNS:
        if pattern.key in ("badge_chip",):
            continue
        family = family_of(pattern)
        if family in seen:
            continue
        # The markup pattern of the family is the one that says what is still
        # written by hand; a mount is not.
        markup = next(p for p in PATTERNS
                      if family_of(p) == family and not p.mount
                      and p.key != "badge_chip")
        seen.add(family)
        hand = len(users(markup))
        done = standardized(markup)
        total = hand + done
        rows.append({
            "key": family,
            "description": markup.description,
            "component": markup.component,
            "standardized": done,
            "hand_written": hand,
            "total": total,
            "rate": round(100 * done / total) if total else None,
        })
    return rows


def badge_totals() -> Dict[str, int]:
    """Status badges and chips across every template, counted by what they show."""
    status = chips = 0
    for path in sorted(TEMPLATES.rglob("*.html")):
        if COMPONENTS in path.parents:
            continue
        found = badge_usage(path.read_text(errors="ignore"))
        status += found[0]
        chips += found[1]
    return {"status": status, "chip": chips}


def users(pattern: Pattern) -> List[str]:
    """Templates that still hand-roll this markup."""
    expression = re.compile(pattern.regex)
    hits = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if COMPONENTS in path.parents:
            continue
        text = path.read_text(errors="ignore")
        if pattern.key == "pagination":
            # An empty mount is not markup: remove the mounts and see what
            # pagination markup is left.
            text = PAGINATION_MOUNT.sub("", text)
        if pattern.key == "status_badge":
            # Counted by what the badge shows, not by its markup: see
            # `badge_usage`. A template full of count chips is not a template
            # that has a status badge left to migrate.
            if badge_usage(text)[0] == 0:
                continue
        elif pattern.key == "badge_chip":
            if badge_usage(text)[1] == 0:
                continue
        elif not expression.search(text):
            continue
        hits.append(str(path.relative_to(PROJECT_ROOT)))
    return hits


def state_coverage(library: Dict[str, Component] | None = None) -> Dict[str, List[str]]:
    """Which component answers for each state in the vocabulary."""
    library = library if library is not None else components()
    coverage: Dict[str, List[str]] = {state: [] for state in STATES}
    for component in library.values():
        for state in component.states:
            coverage.setdefault(state, []).append(component.name)
    return coverage


def counts() -> Dict[str, int]:
    library = components()
    return {
        "components": len(library),
        "declared": sum(1 for c in library.values() if c.states),
        "undeclared": sum(1 for c in library.values() if not c.states),
        "states": len(STATES),
        "states_covered": sum(1 for v in state_coverage(library).values() if v),
        "states_not_yet": len(PLANNED_STATES),
        **{f"pattern_{p.key}": len(users(p)) for p in PATTERNS},
        "badges_status": badge_totals()["status"],
        "badges_chip": badge_totals()["chip"],
    }


class RenderedClass(NamedTuple):
    """A class a component renders, and who owns it."""

    name: str
    ownership: str
    source: str


def _classes_in_files(library: Dict[str, Component] | None = None) -> Dict[str, List[str]]:
    """The class names each component file actually renders.

    Jinja expressions are removed first: `class="badge {{ tone }}"` renders a
    class the component does not name, and the identifiers inside the
    expression are not class names at all.
    """
    rendered: Dict[str, List[str]] = {}
    for path in sorted(COMPONENTS.glob("*.html")):
        text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", " ",
                      path.read_text(errors="ignore"), flags=re.DOTALL)
        names: set = set()
        for value in re.findall(r"""class=["']([^"']*)["']""", text):
            for token in re.split(r"\s+", value):
                token = token.strip("{}%'\"~ .")
                if token and re.fullmatch(r"[a-zA-Z][\w-]*", token):
                    names.add(token)
        rendered[str(path.relative_to(PROJECT_ROOT))] = sorted(names)
    return rendered


def stylesheet_classes(relative: str) -> set:
    """The class names one stylesheet defines."""
    path = PROJECT_ROOT / relative
    if path.is_dir():
        names: set = set()
        for child in path.glob("*.css"):
            names |= stylesheet_classes(str(child.relative_to(PROJECT_ROOT)))
        return names
    return set(re.findall(r"\.(-?[a-zA-Z_][\w-]*)", path.read_text(errors="ignore")))


@lru_cache(maxsize=None)
def owned_classes() -> frozenset:
    """Every class SYLTHARAE's own stylesheets define."""
    names: set = set()
    for relative in PROJECT_STYLESHEETS:
        names |= stylesheet_classes(relative)
    # The third-party bundles also ship classes the project uses incidentally;
    # those are still third-party, so they are removed from the owned set.
    return frozenset(names - third_party_classes())


@lru_cache(maxsize=None)
def third_party_classes() -> frozenset:
    """Every class the bundled third-party stylesheets define."""
    names: set = set()
    for relative in THIRD_PARTY_STYLESHEETS:
        names |= stylesheet_classes(relative)
    return frozenset(names)


def classify_class(name: str, declared: set | None = None) -> RenderedClass:
    """Owned, third-party, or unknown - and what says so.

    A class is OWNED when a stylesheet SYLTHARAE ships defines it, or when the
    component that renders it declares it in its own `classes:` header: a
    component saying "this class is mine" is a decision, and it is visible in
    the component's declaration rather than inferred. THIRD_PARTY is an
    expected dependency (Bootstrap, Bootstrap Icons). Anything else is
    UNKNOWN, and an unknown class is how a component invents a style nobody
    defined - which is what the guardrail exists to catch.
    """
    declared = declared or set()
    if name.endswith(("-", "_")):
        # A fragment built from a variable: `file-nav--{{ variant }}`.
        prefix = name
        return RenderedClass(
            name,
            OWNED if any(c.startswith(prefix) for c in owned_classes())
            or declared and any(c.startswith(prefix) for c in declared)
            else UNKNOWN,
            "project stylesheet (built from a variable)")
    if name in owned_classes():
        return RenderedClass(name, OWNED, "project stylesheet")
    if name in third_party_classes():
        return RenderedClass(name, THIRD_PARTY,
                             "Bootstrap" if name.startswith(("bi", "col", "row"))
                             else "third-party bundle")
    if name in declared:
        return RenderedClass(name, OWNED, "component declaration")
    return RenderedClass(name, UNKNOWN, "nothing defines it")


def class_ownership() -> Dict[str, List[RenderedClass]]:
    """Every class every component renders, classified."""
    library = components()
    declared_by_file = {component.path: set(component.classes)
                        for component in library.values()}
    report: Dict[str, List[RenderedClass]] = {}
    for relative, names in _classes_in_files(library).items():
        declared = declared_by_file.get(relative, set())
        report[relative] = [classify_class(name, declared) for name in names]
    return report


def unknown_classes() -> Dict[str, List[str]]:
    """Rendered classes with no owner: the violations."""
    problems: Dict[str, List[str]] = {}
    for relative, entries in class_ownership().items():
        missing = [entry.name for entry in entries if entry.ownership == UNKNOWN]
        if missing:
            problems[relative] = missing
    return problems


def undeclared_files() -> List[str]:
    return [c.path for c in components().values() if not c.states]


def audit_block() -> str:
    """The generated part of the component library document."""
    library = components()
    coverage = state_coverage(library)
    parts: List[str] = []

    parts.append("### Components\n")
    parts.append("Read from the `{# component: … #}` declaration at the top of "
                 "each file in `templates/components/`. A file without one has "
                 "no declared purpose or states, and is listed as such.\n")
    parts.append("| Component | File | States | Macros | Purpose |")
    parts.append("| --- | --- | --- | --- | --- |")
    for name, component in sorted(library.items()):
        states = ", ".join(f"`{s}`" for s in component.states) or "— none declared —"
        macros = ", ".join(f"`{m}`" for m in component.macros) or "—"
        parts.append(f"| `{name}` | `{component.path}` | {states} | {macros} | "
                     f"{component.purpose or '—'} |")
    parts.append("")

    parts.append("### States (§63)\n")
    parts.append("Every state in the vocabulary is answered by at least one "
                 "component; a state nobody implements does not exist in the "
                 "product, however often it is referred to.\n")
    parts.append("| State | Meaning | Implemented by |")
    parts.append("| --- | --- | --- |")
    for state, meaning in STATES.items():
        owners = ", ".join(f"`{c}`" for c in coverage.get(state, []))
        if not owners:
            planned = PLANNED_STATES.get(state)
            owners = (f"**not yet** — planned for `{planned}`" if planned
                      else "**nobody**")
        parts.append(f"| `{state}` | {meaning} | {owners} |")
    parts.append("")

    parts.append("### Markup pages still write by hand\n")
    parts.append("Counted by scanning `templates/**`. These are the places a "
                 "shared component has not reached yet - the number goes down "
                 "as components are adopted, and it is the measure of this "
                 "phase rather than an impression of it.\n")
    parts.append("| Markup | Component that replaces it | Templates |")
    parts.append("| --- | --- | --- |")
    for pattern in PATTERNS:
        if pattern.key == "badge_chip":
            hit_count = str(badge_totals()["chip"]) + " badges"
        elif pattern.key == "status_badge":
            hit_count = str(badge_totals()["status"]) + " badges, in " + \
                str(len(users(pattern))) + " templates"
        else:
            hit_count = str(len(users(pattern))) + " templates"
        owner = f"`{pattern.component}`" if pattern.component in library else "—"
        parts.append(f"| {pattern.description} | {owner} | {hit_count} |")
    parts.append("")

    # ---- adoption ------------------------------------------------------
    parts.append("### Adoption\n")
    parts.append("How much of the repeated markup has moved onto its "
                 "component. Standardized counts the templates that read "
                 "through the component; hand-written counts what is still "
                 "done by hand; the rate is the completion criterion for this "
                 "phase, not an impression of it.\n")
    parts.append("| Markup | Standardized | Hand-written | Adoption |")
    parts.append("| --- | --- | --- | --- |")
    for row in adoption():
        if row["total"] == 0:
            rate = "—"
        else:
            rate = f"{row['rate']}%"
        parts.append(f"| {row['description']} | {row['standardized']} | "
                     f"{row['hand_written']} | {rate} |")
    parts.append("")

    if EXCEPTIONS:
        parts.append("**Declared exceptions.** Not everything that looks "
                     "similar is the same thing, and overloaded components "
                     "stop being usable. An exception is a decision with an "
                     "owner:\n")
        parts.append("| Area | Reason | Owner |")
        parts.append("| --- | --- | --- |")
        for area, entry in EXCEPTIONS.items():
            parts.append(f"| {area} | {entry['reason']} | {entry['owner']} |")
        parts.append("")

    # ---- CSS ownership -------------------------------------------------
    ownership = class_ownership()
    totals = {OWNED: 0, THIRD_PARTY: 0, UNKNOWN: 0}
    declared_hooks: List[str] = []
    for relative, entries in sorted(ownership.items()):
        for entry in entries:
            totals[entry.ownership] += 1
            if entry.ownership == OWNED and entry.source == "component declaration":
                declared_hooks.append(f"`{entry.name}` ({relative.split('/')[-1]})")

    parts.append("### CSS ownership of the classes components render\n")
    parts.append("Every class a component renders has exactly one owner. "
                 "**OWNED** means an SYLTHARAE stylesheet defines it, or the "
                 "component declares it in its own `classes:` header. "
                 "**THIRD_PARTY** means a bundled dependency defines it - "
                 "Bootstrap and Bootstrap Icons are expected dependencies, and "
                 "using them is not a finding. **UNKNOWN** means nobody does, "
                 "and an unknown class is how a component invents a style: it "
                 "fails the guardrail test rather than being reported and "
                 "forgotten.\n")
    parts.append("| Ownership | Classes |")
    parts.append("| --- | --- |")
    parts.append(f"| OWNED (SYLTHARAE) | {totals[OWNED]} |")
    parts.append(f"| THIRD_PARTY (Bootstrap, Bootstrap Icons) | {totals[THIRD_PARTY]} |")
    parts.append(f"| UNKNOWN | {totals[UNKNOWN]} |")
    parts.append("")
    parts.append("Third-party stylesheets bundled with the application: " +
                 ", ".join(f"`{path}`" for path in THIRD_PARTY_STYLESHEETS) + ".")
    parts.append("")
    if declared_hooks:
        parts.append("Owned by declaration rather than by a stylesheet - the "
                     "component states these are its own hooks, and no rule "
                     "styles them (which is a decision, not an accident):\n")
        parts.append("* " + "\n* ".join(declared_hooks))
    else:
        parts.append("No component currently declares a class that no "
                     "stylesheet styles.")
    parts.append("")
    if totals[UNKNOWN]:
        parts.append("**Unknown classes (this fails the guardrail):**\n")
        for relative, names in sorted(unknown_classes().items()):
            parts.append(f"* `{relative}`: " + ", ".join(f"`{n}`" for n in names))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def splice(text: str, block: str) -> str:
    """Replace the generated section, leaving the hand-written part alone."""
    if BEGIN not in text or END not in text:
        raise SystemExit(f"the document has no generated section ({BEGIN} … {END})")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{block}\n{END}{tail}"


def main(argv: Iterable[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    block = audit_block()
    if not argv:
        print(block)
        return 0
    path = pathlib.Path(argv[0])
    path.write_text(splice(path.read_text(), block))
    print(f"regenerated the component audit in {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
