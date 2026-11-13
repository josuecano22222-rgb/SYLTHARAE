"""The action surface audit: what a person can do, and where it is done today.

Read-only and generated, like the rest of ``core/experience``. It answers the
question the Action Registry has to answer before it can exist: *which actions
does this product actually offer, what does each apply to, and which of them
cannot be described honestly by the model we have?*

Four measurements, none of them typed by hand:

* **The registry** (``action_registry.py``): every registered action, with the
  scope, permission, confirmation and operation reference it declares.
* **The surfaces the templates still carry** - shared toolbar, hand-written
  bars, record-row actions, browser ``confirm()`` dialogs, viewer controls,
  page-local button state. Every scan is stated once and used for both the
  count and the file list, so the number and the evidence cannot disagree.
* **What the registry itself is missing** - actions with no operation, and
  screens nobody has described - counted, not glossed over.
* **The gaps**: the short, curated list of actions whose behaviour the current
  model cannot say. Each entry names the code that proves it, and the test
  checks that the anchor still exists - so the list cannot quietly turn into
  opinion, and a fixed gap disappears from it instead of lingering.

Nothing here performs an action or changes a screen. The audit is the agenda
for the next layer, and it says out loud which screens nobody has described
yet instead of implying every screen has been designed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.frontend import component_audit
from core.interfaces import REGISTRY
from core.interfaces.model import NAVIGABLE_KINDS

from . import bindings as binding_scan
from .action_registry import not_built, registered, without_operation
from .declarations import BINDING_SOURCES, SCREEN_ACTIONS
from .model import ACTION_SCOPES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
PAGES = PROJECT_ROOT / "static/js/pages"

BEGIN = "<!-- BEGIN GENERATED: action surfaces -->"
END = "<!-- END GENERATED: action surfaces -->"

#: The screens whose experience is described, and the templates that serve
#: each. An interface can have more than one screen - the file library has its
#: list, a record page and the reader - and a declaration may name a string
#: that appears on any of them. The contract tests use the same mapping, which
#: is what stops a declaration from drifting away from the screen it claims to
#: describe.
SCREEN_TEMPLATES: Dict[str, Tuple[str, ...]] = {
    "file_library": ("templates/file/files_list.html",
                     "templates/file/file_detail.html",
                     "templates/file/full_content.html",
                     # The library's rows render through the one shared
                     # table component; the dashboard's documents panel
                     # embeds the same component.
                     "templates/components/file_library_table.html",
                     "templates/file/file_types.html"),
    "keywords": ("templates/Keyword/keywords_list.html",),
    "words": ("templates/Word/Word_list.html",),
    "sources": ("templates/Sources/sources_list.html",),
    "sides": ("templates/Side/sides_list.html",),
}

#: Repeated markup that means "there is an action here". Each entry is
#: (key, what it is, pattern, where to look).
SURFACES: Tuple[Tuple[str, str, str, Tuple[Path, ...]], ...] = (
    ("toolbar_component", "Shared action toolbar",
     r"action_toolbar\(", (TEMPLATES,)),
    ("hand_written_bar", "Hand-written action bar",
     r'class="[^"]*action-bar', (TEMPLATES,)),
    ("record_row_actions", "Record actions drawn by hand",
     r'btn-action-icon|data-on-click="editWord\(|data-on-click="deleteWord\(',
     (TEMPLATES,)),
    ("filter_submit", "Filter form submitted from a bar",
     r"submit=True", (TEMPLATES,)),
    ("viewer_controls", "Document viewer controls",
     r'id="btn(Copy|Download|Print|Wrap|FontInc|FontDec)"', (TEMPLATES,)),
    ("browser_confirm", "Browser confirm() dialog",
     r"confirm\(", (TEMPLATES, PAGES)),
    ("page_local_button_state", "Button state decided by the page",
     r"\.disabled = true", (PAGES,)),
)

#: Actions the current model cannot describe honestly, each anchored to code.
#: The point of the audit is not to be reassuring: an action that does not fit
#: is how the next abstraction gets designed, and it is cheaper to name it here
#: than to widen a component for it.
#:
#: Two gaps left this list when the Action Definition grew: "Edit Selected"
#: used to have no way to say it acts on *one member* of a selection, which is
#: now ``selection_rule="one"``; and the file library's bulk buttons had no
#: vocabulary for "this work is running", which is now the ``running`` state.
GAPS: Tuple[Dict[str, object], ...] = (
    {
        "what": "files.reprocess",
        "where": "file_library",
        "cannot_say": "The registry can say 'declared, not built' and the "
                      "surface honours it - the action is hidden, nothing is "
                      "drawn, and no route is bound - but the model still "
                      "cannot describe the work: no persistent job type exists "
                      "for re-extraction, and the pipeline refuses a duplicate "
                      "hash whose path is already stored. Registering an "
                      "operation for it means giving it a job and a path "
                      "policy first, which the action vocabulary has no room "
                      "for.",
        "evidence": ("core/experience/action_registry.py",
                     "Api/blueprints/files.py",
                     "pipeline/storage_pipeline.py"),
    },
    {
        "what": "merge_duplicates",
        "where": "keywords",
        "cannot_say": "A page action whose confirmation carries runtime "
                      "numbers - how many duplicates, what will be merged. "
                      "`confirmation` is a translation key and nothing else, so "
                      "the dialog cannot be handed values.",
        "evidence": ("static/js/pages/keywords-list-page.js",),
    },
    {
        "what": "export_selected, edit_selected",
        "where": "sources, sides",
        "cannot_say": "Registered with no operation: the controls exist, the "
                      "registry says so by leaving `execution` empty, and the "
                      "audit counts them. What the model lacks is a way to say "
                      "'shown, not built' that the interface can honour - today "
                      "the button is simply there, enabled when rows are "
                      "selected, and does nothing useful.",
        "evidence": ("static/js/pages/sources-list-page.js",
                     "static/js/pages/sides-list-page.js"),
    },
    {
        "what": "select_all / select_none",
        "where": "keywords, words, sources, sides, file_library",
        "cannot_say": "Selection *controls*, registered as page actions "
                      "because that is the only vocabulary available. They "
                      "produce the scope the other actions consume; the model "
                      "has one word for both roles, so the pattern cannot be "
                      "required of the next screen.",
        "evidence": ("templates/Sources/sources_list.html",
                     "templates/Side/sides_list.html"),
    },
    {
        "what": "apply_filters",
        "where": "email_words",
        "cannot_say": "A page action that is really a filter surface's control "
                      "(it submits the filter form). Registering it as an "
                      "action would give one control two owners - the filter "
                      "definition and the action definition.",
        "evidence": ("templates/email_words/email_words.html",),
    },
    {
        "what": "open / edit / delete (record actions)",
        "where": "file_library, words",
        "cannot_say": "Registered with the right scope, but rendered inside "
                      "the row by hand: no component owns where a record's "
                      "actions are drawn. That is the Record Action Surface, "
                      "which does not exist yet.",
        "evidence": ("templates/Word/Word_list.html",
                     "templates/file/files_list.html"),
    },
    {
        "what": "analyze_selected, export_selected, upload",
        "where": "file_library",
        "cannot_say": "Registered, and the model can now say an action is "
                      "running - but not what it started. There is no job "
                      "reference on an action, so 'Analysis queued. Job "
                      "AN-4921' cannot be produced from the definition.",
        "evidence": ("templates/file/files_list.html",
                     "static/js/modules/file-operations/file-management.js"),
    },
    {
        "what": "view_original, download_original",
        "where": "file_library",
        "cannot_say": "The two operations are registered and distinct, and the "
                      "service behind them is real (inline vs attachment, range "
                      "requests, path from the database). What the model cannot "
                      "say is where they are drawn: today they are two "
                      "hand-written links in the reader, labelled 'Original "
                      "File' and 'Original' - one of which is a download and "
                      "does not say so.",
        "evidence": ("templates/file/full_content.html",
                     "Api/services/original_file.py"),
    },
    {
        "what": "export_data (email_words)",
        "where": "email_words",
        "cannot_say": "Loading is implemented by the page (button disabled, "
                      "label swapped) although the model has a running state, "
                      "because nothing tells the page which action it belongs "
                      "to; and the export acts on the filtered set, which is "
                      "neither page nor selection scope as currently worded.",
        "evidence": ("static/js/pages/email-words-page.js",),
    },
    {
        "what": "viewer controls",
        "where": "file_library",
        "cannot_say": "Copy, Download, Print, Wrap, Smaller/Larger, Dark: "
                      "document controls, not record actions, and deliberately "
                      "not toolbar buttons. They need a viewer surface, and "
                      "the audit records them so nobody 'fixes' them into one.",
        "evidence": ("templates/file/full_content.html",),
    },
    {
        "what": "permission",
        "where": "every registered action",
        "cannot_say": "Every action now names a permission domain, and nothing "
                      "enforces one: the vocabulary "
                      "(core/experience/permissions.py) is a naming scheme for "
                      "visibility, while the server still authorises by role at "
                      "the route. Until operations are bound to actions, a "
                      "permission is a name the inspector can show - not a "
                      "boundary, and not a reason to hide a control that the "
                      "server would refuse anyway.",
        "evidence": ("core/experience/permissions.py",),
    },
    {
        "what": "cancel_job, reprocess",
        "where": "jobs, file_library",
        "cannot_say": "Registered as record actions with a confirmation, but "
                      "the screens they live on have no experience contract, "
                      "and the confirmation is still a browser dialog written "
                      "in the template or the page's JavaScript. They are the "
                      "first two actions the shared Confirmation Dialog has to "
                      "take over.",
        "evidence": ("templates/Operations/jobs.html",
                     "templates/file/file_detail.html"),
    },
)


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8", errors="ignore")


def _scan(pattern: str, roots: Sequence[Path], skip_components: bool) -> List[str]:
    """Files under ``roots`` matching ``pattern``, relative to the repository."""
    expression = re.compile(pattern)
    found = []
    for root in roots:
        for path in sorted(root.rglob("*.html" if root == TEMPLATES else "*.js")):
            if skip_components and component_audit.COMPONENTS in path.parents:
                continue
            if expression.search(path.read_text(encoding="utf-8", errors="ignore")):
                found.append(str(path.relative_to(PROJECT_ROOT)))
    return sorted(set(found))


def surfaces() -> Dict[str, List[str]]:
    """Every scanned surface, as the files that carry it."""
    return {
        key: _scan(pattern, roots,
                   skip_components=bool(roots) and roots[0] == TEMPLATES)
        for key, _what, pattern, roots in SURFACES}


def library_toolbar() -> Dict[str, int]:
    """The component library's own count for the action bar family.

    The two reports measure the same bars from different directions - this
    audit by what the bar *is* (an action surface or a viewer surface), the
    library by what the markup *looks like*. The numbers are reconciled in the
    document and a test fails if they stop agreeing, which is the only way two
    measurements stay useful.
    """
    row = next(item for item in component_audit.adoption()
               if item["key"] == "action_toolbar")
    return {"standardized": row["standardized"], "hand_written": row["hand_written"]}


def action_status() -> Dict[str, str]:
    """How each action stands, measured rather than assumed.

    Five facts, and they are not the same fact:

    * **registered** - it is in the category (every row here is);
    * **presented** - a screen declaration says that screen offers it
      (``declarations.SCREEN_ACTIONS``);
    * **bound** - markup in the product names it, so a reader can press it;
    * **built** - the product performs it: ``visibility`` is ``offered``;
    * **unbuilt** - declared, and no operation exists. Named, not hidden.

    An action can be registered and presented without being built (the pages
    did that for months), and presented without being bound (the declaration is
    ahead of the screen). Each combination is a different piece of work, and
    collapsing them into one "done" column is how a dead control survives.
    """
    bound = binding_scan.by_action(binding_scan.scan())
    presented = {action_id
                 for ids in SCREEN_ACTIONS.values() for action_id in ids}
    status: Dict[str, str] = {}
    for item in registered():
        if not item.built:
            status[item.action_id] = ("declared, not built; a page still "
                                      "prepares it" if item.action_id in bound
                                      else "declared, not built")
        elif item.action_id in bound:
            status[item.action_id] = "bound"
        elif item.action_id in presented:
            status[item.action_id] = "presented by declaration; no control names it"
        elif item.execution:
            status[item.action_id] = "built, no screen presents it"
        else:
            status[item.action_id] = "no operation named"
    return status


def status_counts(rows: Optional[Sequence[Dict[str, object]]] = None
                  ) -> Dict[str, int]:
    """How many actions are in each condition. The agenda, as numbers."""
    counted: Dict[str, int] = {}
    for row in (rows if rows is not None else declared_actions()):
        counted[row["status"]] = counted.get(row["status"], 0) + 1
    return counted


def bound_evidence() -> Dict[str, List[str]]:
    """Where each bound action is written, as `file:line`."""
    found: Dict[str, List[str]] = {}
    for item in binding_scan.scan():
        found.setdefault(item["action_id"], []).append(
            f"{item['file']}:{item['line']}")
    return found


def declared_actions() -> List[Dict[str, object]]:
    """Every registered action, with what the product does about it today."""
    status = action_status()
    bound = bound_evidence()
    presented = {action_id
                 for ids in SCREEN_ACTIONS.values() for action_id in ids}
    rows: List[Dict[str, object]] = []
    for action in registered():
        interface_id = action.interfaces[0]
        rows.append({
            "interface": interface_id,
            "also_on": ", ".join(action.interfaces[1:]),
            "action": action.action_id,
            "label": action.source,
            "scope": action.scope,
            "permission": action.permission,
            "destructive": action.destructive,
            "confirmation": action.confirmation,
            "requires_selection": action.requires_selection,
            "selection_rule": action.selection_rule,
            "execution": action.execution,
            "visibility": action.visibility,
            "presented": action.action_id in presented,
            "bound": ", ".join(bound.get(action.action_id, ())) or "-",
            "status": status[action.action_id],
            "component": _component_for(interface_id, action.scope),
        })
    return rows


def _component_for(interface_id: str, scope: str = "page") -> str:
    templates = SCREEN_TEMPLATES.get(interface_id)
    if templates is None:
        return "no described screen"
    text = "\n".join(_read(relative) for relative in templates)
    if scope == "record":
        return "record row" if "action_toolbar(" not in text else "record + toolbar"
    if "action_toolbar(" in text:
        return "ActionToolbar"
    if re.search(r'class="[^"]*action-bar', text):
        return "hand-written bar"
    return "page"


def described_interfaces() -> Tuple[str, ...]:
    return tuple(sorted(set(SCREEN_TEMPLATES)))


def undescribed_interfaces() -> Tuple[str, ...]:
    return tuple(sorted({i.interface_id for i in REGISTRY
                         if i.kind in NAVIGABLE_KINDS
                         and i.interface_id not in SCREEN_TEMPLATES}))


def counts() -> Dict[str, object]:
    """Everything the report claims, computed here and nowhere else."""
    rows = declared_actions()
    scanned = surfaces()
    navigable = [i for i in REGISTRY if i.kind in NAVIGABLE_KINDS]
    without = without_operation()
    return {
        "interfaces": len(REGISTRY),
        "navigable_interfaces": len(navigable),
        "described_interfaces": len(described_interfaces()),
        "undescribed_interfaces": len(undescribed_interfaces()),
        "registered_actions": len(rows),
        "by_scope": {scope: sum(1 for row in rows if row["scope"] == scope)
                     for scope in ACTION_SCOPES},
        "destructive": sum(1 for row in rows if row["destructive"]),
        "with_confirmation": sum(1 for row in rows if row["confirmation"]),
        "with_permission": sum(1 for row in rows if row["permission"]),
        "with_execution": sum(1 for row in rows if row["execution"]),
        "without_execution": len(without),
        "without_execution_names": ", ".join(a.action_id for a in without),
        "toolbar_component": len(scanned["toolbar_component"]),
        "hand_written_bars": len(scanned["hand_written_bar"]),
        "record_action_files": len(scanned["record_row_actions"]),
        "filter_submit_files": len(scanned["filter_submit"]),
        "viewer_control_files": len(scanned["viewer_controls"]),
        "confirm_files": len(scanned["browser_confirm"]),
        "page_local_state_files": len(scanned["page_local_button_state"]),
        "library_toolbar": library_toolbar(),
        "not_built": len(not_built()),
        "not_built_names": ", ".join(item.action_id for item in not_built()),
        "presented_actions": sum(1 for row in rows if row["presented"]),
        "bound_actions": sum(1 for row in rows if row["bound"] != "-"),
        "status_counts": status_counts(rows),
        "bindings": binding_scan.counts()["bindings"],
        "bindings_by_kind": {kind: binding_scan.counts()[kind]
                             for kind in ("attributes", "literals", "prepared")},
        "binding_files": binding_scan.counts()["files"],
        "gaps": len(GAPS),
    }


def _files(count: int) -> str:
    return f"{count} template" if count == 1 else f"{count} templates"


def _table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> List[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join("" if cell is None else str(cell)
                                       for cell in row) + " |")
    return lines


def reference_markdown() -> str:
    """The generated block. Every figure in it comes from ``counts()``."""
    values = counts()
    scanned = surfaces()
    lines: List[str] = []

    lines += ["### What was measured", "",
              "Four sources, none of them typed by hand:", "",
              "* the **Action Registry** - every action the product offers, "
              "with the scope, permission, confirmation and operation reference "
              "it declares;",
              "* the **surfaces the templates carry** - the shared toolbar, the "
              "bars still written by hand, record-row actions, browser "
              "confirmation dialogs, viewer controls and page-local button "
              "state, each with the files that prove it;",
              "* **what the registry is missing** - actions with no operation "
              "behind them, and screens nobody has described;",
              "* the **gaps** - actions whose behaviour the current model "
              "cannot say, each anchored to code. A gap that closes leaves this "
              "list; two already have.", "",
              "This is a measurement of the product as it is, not a target.", ""]

    lines += _table(
        ("Measure", "Value"),
        (("Registered interfaces", values["interfaces"]),
         ("Navigable screens", values["navigable_interfaces"]),
         ("Screens with a described experience", values["described_interfaces"]),
         ("Screens nobody has described yet", values["undescribed_interfaces"]),
         ("Registered actions", values["registered_actions"]),
         ("- page / record / selection / bulk",
          "{} / {} / {} / {}".format(values["by_scope"]["page"],
                                     values["by_scope"]["record"],
                                     values["by_scope"]["selection"],
                                     values["by_scope"]["bulk"])),
         ("- destructive", values["destructive"]),
         ("- naming a confirmation", values["with_confirmation"]),
         ("- naming a permission", values["with_permission"]),
         ("- naming the operation that owns them", values["with_execution"]),
         ("- declared with no operation behind them",
          values["without_execution"]),
         ("Templates rendering the shared ActionToolbar", values["toolbar_component"]),
         ("Hand-written action bars", values["hand_written_bars"]),
         ("Templates with record actions drawn by hand", values["record_action_files"]),
         ("Templates with a filter submit inside a bar", values["filter_submit_files"]),
         ("Templates with document viewer controls", values["viewer_control_files"]),
         ("Files still calling the browser confirm()", values["confirm_files"]),
         ("Files deciding button state by hand", values["page_local_state_files"]),
         ("Gaps: actions the model cannot describe", values["gaps"])))
    lines.append("")

    lines += ["### Registered actions", "",
              "Every action names a permission domain and either an operation "
              "or nothing at all. The empty operation column is the honest "
              "part: {} of them are words on a screen that no service owns "
              "yet.".format(values["without_execution"]), ""]
    if values["without_execution"]:
        lines += [f"Declared with no operation: `{values['without_execution_names']}`.",
                  ""]
    lines += _table(
        ("Action", "Interface", "Scope", "Selection", "Permission",
         "Destructive", "Confirmation", "Operation", "Component"),
        [(row["action"], row["interface"]
          + (f" (+{row['also_on']})" if row["also_on"] else ""),
          row["scope"], row["selection_rule"] or "-", row["permission"] or "-",
          "yes" if row["destructive"] else "-", row["confirmation"] or "-",
          row["execution"] or "**none**", row["component"])
         for row in declared_actions()])
    lines.append("")

    lines += ["### How each action stands", "",
              "Four conditions, and they are not the same condition:", "",
              "* **registered** - the catalog has a row for it;",
              "* **presented** - a screen declaration says that screen offers "
              "it (`SCREEN_ACTIONS`);",
              "* **bound** - something in the product names it: an attribute in "
              "markup, a macro argument, a string in the page script that drives "
              "the control, or an id written in the file a screen declares as its "
              "binding source (`BINDING_SOURCES`);",
              "* **built** - the registry names the operation that performs it.",
              "",
              "A control a reader can press without the operation existing is "
              "exactly the defect this audit was written after, so an action "
              "that is presented and not built is a finding, not a style note.",
              ""]
    lines += _table(
        ("Condition", "Actions"),
        sorted((status, count)
               for status, count in values["status_counts"].items())
        if values["status_counts"] else (("none", 0),))
    lines.append("")
    kinds = values["bindings_by_kind"]
    lines += [
        "The scan found {} bindings in {} files: {} in markup attributes, {} "
        "named as values (a macro argument or a page script), and {} in the "
        "files the screens declare as their binding sources. The attribute "
        "count is the one that falls as this layer lands: an action drawn from "
        "prepared data needs no id typed into a template.".format(
            values["bindings"], values["binding_files"],
            kinds["attributes"], kinds["literals"], kinds["prepared"]),
        "",
        "Every one of those bindings is then resolved against the "
        "application's own URL map (`core.experience.bindings.check`), so a "
        "control pointing at a route nobody serves is a test failure rather "
        "than a 404 a reader finds. That check is what retired the Reprocess "
        "link.",
        ""]

    lines += ["### Surfaces the model does not own yet", "",
              "Each row is a scan from `SURFACES`, so the count and the files "
              "come from one statement.", ""]
    lines += _table(
        ("Surface", "Files", "Where"),
        [(_what, len(scanned[key]), ", ".join(f"`{f}`" for f in scanned[key]) or "-")
         for key, _what, _pattern, _roots in SURFACES])
    lines.append("")
    library = values["library_toolbar"]
    lines += [
        "The component library counts {} standardised and {} hand-written "
        "action bars. Those are the same bars seen from two directions: the {} "
        "templates rendering the shared toolbar are listed above, and the "
        "hand-written ones split into the file list's action bar and the "
        "full-content viewer's bar - which this audit counts as a viewer "
        "control, because that is what it is, not as an action bar to "
        "migrate.".format(library["standardized"], library["hand_written"],
                          values["toolbar_component"]),
        "",
        "The scripts are scanned as well as the templates, which is why the "
        "browser `confirm()` and page-local button state rows are larger here "
        "than in the component library: a dialog written in a page's "
        "JavaScript is still a dialog nobody owns.",
        ""]

    lines += ["### Candidate surfaces for the next layer", "",
              "Derived from the table above, not from taste:", "",
              "* **Record action surface** - the {} registered record-scope "
              "actions, plus the {} that draw record actions themselves.".format(
                  values["by_scope"]["record"],
                  _files(values["record_action_files"])),
              "* **Specialized composites** - a viewer surface ({}) for the "
              "document controls, and a filter surface ({}) for the bar that "
              "submits a filter form.".format(
                  _files(values["viewer_control_files"]),
                  _files(values["filter_submit_files"])),
              "* **Confirmation dialog** - {} registered actions require a "
              "confirmation, and {} files still open the browser's own dialog "
              "to get one.".format(values["with_confirmation"],
                                   values["confirm_files"]),
              ""
              ]

    lines += ["### Actions that do not fit the current abstractions", "",
              "This is the agenda for the next layer: each row is something the "
              "model cannot say today, and the code that proves it.", ""]
    lines += _table(("Action", "Where", "What the model cannot say", "Evidence"),
                    [(gap["what"], gap["where"], gap["cannot_say"],
                      ", ".join(f"`{f}`" for f in gap["evidence"]))
                     for gap in GAPS])
    return "\n".join(lines) + "\n"


def write_document(path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        document = handle.read()
    if BEGIN not in document or END not in document:
        raise SystemExit(
            "the document is missing the generated block markers "
            f"({BEGIN} / {END})")
    head, rest = document.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{head}{BEGIN}\n{reference_markdown()}{END}{tail}")


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        print("usage: python3 -m core.experience.action_audit "
              "docs/ACTION_SURFACE_AUDIT.md")
        return 2
    write_document(argv[1])
    return 0


if __name__ == "__main__":  # pragma: no cover - a command, not a library
    import sys

    raise SystemExit(main(sys.argv))
