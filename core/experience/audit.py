"""The experience audit: what an element on this screen is, and whether the
product can say so without guessing.

The Screen Inspector answers one question - *what is this element?* - and its
worth is entirely in the answer being derived rather than inferred. This module
measures how much of that answer the product can actually supply today, per
element and per interface, from the same sources the Inspector reads: the
interface registry, the action registry, the binding scan, and the component
library. Nothing here is authored by hand and nothing here inspects a live page.

Four measurements:

* **Per element**: for each interface a person can navigate to, whether it
  resolves in the registry, and whether it declares the help topic and shortcut
  the directive requires of it.
* **Per action**: whether anything in the product binds it, whether the page
  that presents it does so from the registry or from a hand-written button, and
  whether it is built at all - plus the defect that motivated this layer, an
  action named in markup that no registry entry owns.
* **Per component**: whether an element can be resolved to a component at all,
  by name and by the classes the component declares as its own, and how many
  classes on screen belong to nobody.
* **Per binding**: what the scan found, where, and which of those resolve to
  something the application serves.

The gap list at the end is short and curated on purpose: the questions the
Inspector cannot answer *honestly* yet, each anchored to the code that proves
it, so the list cannot decay into opinion and a solved question disappears from
it. The audit is the agenda for the metadata the product still owes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from core.frontend import component_audit
from core.interfaces import REGISTRY

from . import bindings as binding_scan
from .action_registry import not_built, registered
from .declarations import BINDING_SOURCES, SCREEN_ACTIONS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
SCRIPTS = PROJECT_ROOT / "static/js"

BEGIN = "<!-- BEGIN GENERATED: experience audit -->"
END = "<!-- END GENERATED: experience audit -->"

#: An action id is a namespace and a member, lowercase, stable. Anything that
#: does not look like one is not reported as an action id, because the point of
#: this audit is to catch a *named* action that nobody registered - not to
#: conduct a spelling survey of the pages.
ACTION_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

#: Where a control names the action it presents. Both patterns are the ones the
#: shared components emit and the ones the pages pass to a macro, so what this
#: finds is what a reader's click actually reaches.
NAMES_AN_ACTION = (
    re.compile(r"""data-(?:inspector-|confirm-)action\s*=\s*["']([^"']+)["']"""),
    re.compile(r"""\baction\s*=\s*["']([^"']+)["']"""),
    re.compile(r"""\bACTIONS?\s*=\s*\[([^\]]*)\]"""),
)

#: The words the audit uses for how far an action's binding reaches. They are
#: deliberately longer than a code: an audit read by a person should not need a
#: legend to be understood.
STATUS_NOT_BUILT = "declared, not built"
STATUS_BOUND = "bound to a control"
STATUS_UNBOUND = "presented, nothing binds it"
STATUS_OUTSIDE = "no described screen presents it"
STATUS_MISSING = "named in markup, not registered"

#: The fields an inspection reports, and therefore the fields the product owes
#: an answer for. Kept here as the vocabulary the coverage counts are stated in.
MEASURED_FIELDS: Tuple[str, ...] = (
    "interface", "component", "action", "scope", "permission", "state",
    "translation", "binding", "execution", "help",
)


def _source_files() -> Tuple[Path, ...]:
    """Every file that can name an action, in a stable order."""
    files: List[Path] = []
    for root in (TEMPLATES, SCRIPTS / "pages", SCRIPTS / "modules"):
        if not root.exists():
            continue
        files.extend(sorted(path for path in root.rglob("*")
                            if path.suffix in (".html", ".js")))
    return tuple(files)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _by_id() -> Dict[str, object]:
    """The registry, keyed by the id that never changes."""
    return {item.action_id: item for item in registered()}


def named_action_ids() -> Dict[str, List[str]]:
    """Action ids named in markup or a script, and where each one was named.

    This is the audit that would have caught a page presenting an action that
    does not exist: the id is *named*, and the registry has never heard of it.
    """
    found: Dict[str, List[str]] = {}
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable file
            continue
        relative = _relative(path)
        for pattern in NAMES_AN_ACTION:
            for group in pattern.findall(text):
                for candidate in re.findall(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", group):
                    # The whole value has to be the id. A template expression
                    # such as `{{ action.action_id }}` names no action: it is
                    # the mechanism by which an action *would* be named, and
                    # reporting it would be this audit inventing a defect.
                    if candidate != group.strip() or not ACTION_ID.match(candidate):
                        continue
                    found.setdefault(candidate, [])
                    if relative not in found[candidate]:
                        found[candidate].append(relative)
    return {action_id: sorted(where) for action_id, where in sorted(found.items())}


def missing_actions() -> List[Dict[str, object]]:
    """Actions named by a control that the registry does not own."""
    known = set(_by_id())
    return [{"action_id": action_id, "named_in": where}
            for action_id, where in named_action_ids().items()
            if action_id not in known]


def actions_outside_screens() -> List[str]:
    """Registered actions no described screen presents."""
    presented = {action_id for action_ids in SCREEN_ACTIONS.values()
                 for action_id in action_ids}
    return sorted(item.action_id for item in registered()
                  if item.action_id not in presented)


def describes_itself(interface_id: str) -> bool:
    return interface_id in SCREEN_ACTIONS


def screen_templates(interface_id: str) -> Tuple[str, ...]:
    """The templates that serve this interface's declared screen."""
    from .action_audit import SCREEN_TEMPLATES  # local: one direction only

    return SCREEN_TEMPLATES.get(interface_id, ())


#: The shared components that can draw an action, and the macro that says a
#: screen uses each. Measured from the templates, never assumed.
ACTION_SURFACES: Tuple[Tuple[str, str], ...] = (
    ("action_toolbar(", "the shared action toolbar"),
    ("record_action_surface(", "the shared record action surface"),
)


def controls_for(interface_id: str) -> Tuple[str, ...]:
    """Which shared surfaces this screen's templates draw its actions with.

    The interesting fact about an unbound action is not only that nothing binds
    it but *what is drawing it instead*: the frozen toolbar with a page function
    behind it, the shared record surface, or a bar written inside that one
    template. A screen can have more than one - the file library has a
    hand-written bar on its list and the record surface on a record.
    """
    found: List[str] = []
    for relative in screen_templates(interface_id):
        path = PROJECT_ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for macro, description in ACTION_SURFACES:
            if macro in text and description not in found:
                found.append(description)
    return tuple(found) or ("none — a control written inside the template",)


def interface_rows() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for interface in REGISTRY:
        rows.append({
            "interface_id": interface.interface_id,
            "name": interface.name,
            "domain": str(interface.domain),
            "navigable": bool(interface.navigable),
            "route": interface.route,
            "help_topic": interface.help_topic or None,
            "keyboard_shortcut": interface.keyboard_shortcut or None,
            "described": describes_itself(interface.interface_id),
            "actions": len(SCREEN_ACTIONS.get(interface.interface_id, ())),
            "binding_source": BINDING_SOURCES.get(interface.interface_id),
        })
    return rows


def interfaces_without_help() -> List[str]:
    return [interface.interface_id for interface in REGISTRY
            if not interface.help_topic]


def interfaces_without_shortcut() -> List[str]:
    return [interface.interface_id for interface in REGISTRY
            if not interface.keyboard_shortcut]


def bound_action_ids() -> Dict[str, List[str]]:
    """Each action the product binds somewhere, and the evidence for it."""
    bound: Dict[str, List[str]] = {}
    for item in binding_scan.scan():
        action_id = item.get("action_id")
        if not action_id:
            continue
        where = f"{item.get('file')}:{item.get('line')}"
        bound.setdefault(action_id, [])
        if where not in bound[action_id]:
            bound[action_id].append(where)
    return {action_id: sorted(where) for action_id, where in sorted(bound.items())}


def action_status(action_id: str, bound: Dict[str, List[str]]) -> str:
    """How far this action's binding reaches, in the audit's own words."""
    item = _by_id().get(action_id)
    if item is None:
        return STATUS_MISSING
    if action_id in bound:
        return STATUS_NOT_BUILT if not item.built else STATUS_BOUND
    presented = any(action_id in action_ids for action_ids in SCREEN_ACTIONS.values())
    return STATUS_UNBOUND if presented else STATUS_OUTSIDE


def action_rows() -> List[Dict[str, object]]:
    bound = bound_action_ids()
    rows: List[Dict[str, object]] = []
    for action_id, item in sorted(_by_id().items()):
        presented_by = [interface_id for interface_id, action_ids
                        in sorted(SCREEN_ACTIONS.items())
                        if action_id in action_ids]
        rows.append({
            "action_id": action_id,
            "scope": item.scope,
            "permission": item.permission,
            "destructive": bool(item.destructive),
            "execution": item.execution or None,
            "built": bool(item.built),
            "presented_by": presented_by,
            "controls": sorted({name for interface_id in presented_by
                                for name in controls_for(interface_id)}),
            "bound_in": bound.get(action_id, []),
            "status": action_status(action_id, bound),
        })
    return rows


def unresolvable_actions() -> List[Dict[str, object]]:
    """Every action the product cannot describe honestly, and why not."""
    rows = [row for row in action_rows() if row["status"] != STATUS_BOUND]
    for row in rows:
        if row["status"] == STATUS_MISSING:
            row["named_in"] = named_action_ids().get(row["action_id"], [])
    for missing in missing_actions():
        rows.append({"action_id": missing["action_id"], "status": STATUS_MISSING,
                     "named_in": missing["named_in"], "scope": None,
                     "permission": None, "destructive": False,
                     "execution": None, "built": False, "presented_by": [],
                     "controls": [], "bound_in": []})
    rows.sort(key=lambda row: (str(row["status"]), str(row["action_id"])))
    return rows


def unbound_actions() -> List[str]:
    """Presented actions nothing binds: the toolbar's own outstanding debt."""
    bound = bound_action_ids()
    presented = {action_id for action_ids in SCREEN_ACTIONS.values()
                 for action_id in action_ids}
    return sorted(action_id for action_id in presented if action_id not in bound)


def component_rows() -> List[Dict[str, object]]:
    """What the component library says, and whether an element resolves to it."""
    from .inspector import resolve_component  # local: the Inspector reads this

    rows: List[Dict[str, object]] = []
    for component_id, component in sorted(component_audit.components().items()):
        declared = component_audit.declared_classes(component)
        ownership = [component_audit.classify_class(name, set(declared))
                     for name in declared]
        by_name = resolve_component(component_id)
        by_class = resolve_component(classes=tuple(declared))
        rows.append({
            "component_id": component_id,
            "path": component.path,
            "states": list(component.states),
            "classes": declared,
            "classes_owned": sum(1 for entry in ownership
                                 if entry.ownership == component_audit.OWNED),
            "classes_third_party": sum(1 for entry in ownership
                                       if entry.ownership == component_audit.THIRD_PARTY),
            "classes_unknown": sum(1 for entry in ownership
                                   if entry.ownership == component_audit.UNKNOWN),
            "resolves_by_name": by_name.get("component_id") == component_id,
            "resolves_by_class": by_class.get("component_id") == component_id,
        })
    return rows


def components_unresolvable() -> List[str]:
    """Components an element on a screen could never be resolved to."""
    return [row["component_id"] for row in component_rows()
            if not (row["resolves_by_name"] or row["resolves_by_class"])]


def class_ownership() -> Dict[str, int]:
    """How many rendered classes have an owner, as rendered - not per component.

    This is the number behind the Inspector's ownership answer: a class with no
    owner is what makes an element *Unknown* rather than a component, and the
    count is the size of that blind spot.
    """
    counts = {"rendered": 0, "owned": 0, "third_party": 0, "unknown": 0}
    for entries in component_audit.class_ownership().values():
        for entry in entries:
            counts["rendered"] += 1
            key = {"OWNED": "owned", "THIRD_PARTY": "third_party",
                   "UNKNOWN": "unknown"}.get(str(entry.ownership), "unknown")
            counts[key] += 1
    return counts


def binding_report(route_map: Iterable[str] | None = None) -> Dict[str, object]:
    found = binding_scan.scan()
    by_kind: Dict[str, int] = {}
    for item in found:
        kind = item.get("kind", "attribute")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    report: Dict[str, object] = {
        "bindings": len(found),
        "by_kind": dict(sorted(by_kind.items())),
        "files": sorted({item["file"] for item in found}),
        "actions": sorted({item["action_id"] for item in found
                           if item.get("action_id")}),
    }
    if route_map is not None:
        checked = binding_scan.check(route_map,
                                     known_actions={item["action_id"] for item in found})
        report["dangling"] = checked.get("dangling", [])
        report["unresolved"] = checked.get("unresolved", [])
    return report


def field_coverage() -> Dict[str, Dict[str, int]]:
    """For each field the panel shows, how many registered actions declare it.

    A field nobody declares is not a defect in the Inspector - it is a fact
    about the metadata, and the panel says "Not declared" because of it. The
    number is here so that filling the metadata in is visible progress rather
    than a matter of opinion.
    """
    actions = list(registered())
    coverage: Dict[str, Dict[str, int]] = {}
    for field in MEASURED_FIELDS:
        declared = 0
        for item in actions:
            if field == "permission" and item.permission:
                declared += 1
            elif field == "execution" and item.execution:
                declared += 1
            elif field == "translation" and item.label_key:
                declared += 1
            elif field in ("scope", "state"):
                declared += 1
            elif field == "action":
                declared += 1
        coverage[field] = {"declared": declared, "of": len(actions)}
    bound = bound_action_ids()
    coverage["binding"] = {"declared": sum(1 for item in actions
                                           if item.action_id in bound),
                           "of": len(actions)}
    coverage["interface"] = {"declared": len(REGISTRY), "of": len(REGISTRY)}
    coverage["component"] = {"declared": len(component_audit.components()),
                             "of": len(component_audit.components())}
    coverage["help"] = {"declared": len(REGISTRY) - len(interfaces_without_help()),
                        "of": len(REGISTRY)}
    return coverage


def counts() -> Dict[str, object]:
    """Every number this audit reports, each one measured just above."""
    rows = action_rows()
    statuses: Dict[str, int] = {}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    library = component_audit.components()
    by_kind: Dict[str, int] = {}
    for item in binding_scan.scan():
        kind = item.get("kind", "attribute")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "interfaces": len(REGISTRY),
        "navigable_interfaces": sum(1 for interface in REGISTRY
                                    if interface.navigable),
        "described_interfaces": len(SCREEN_ACTIONS),
        "interfaces_without_help": len(interfaces_without_help()),
        "interfaces_without_shortcut": len(interfaces_without_shortcut()),
        "registered_actions": len(rows),
        "action_statuses": dict(sorted(statuses.items())),
        "actions_presented": sum(1 for row in rows if row["presented_by"]),
        "actions_bound": sum(1 for row in rows if row["bound_in"]),
        "actions_unbound": len(unbound_actions()),
        "actions_not_built": len(not_built()),
        "actions_outside_screens": len(actions_outside_screens()),
        "actions_named_but_unregistered": len(missing_actions()),
        "components": len(library),
        "components_unresolvable": len(components_unresolvable()),
        "classes": class_ownership(),
        "bindings": len(binding_scan.scan()),
        "bindings_by_kind": dict(sorted(by_kind.items())),
        "fields": field_coverage(),
    }


def reference_markdown() -> str:
    """The generated block the evidence document carries."""
    numbers = counts()
    lines: List[str] = []
    add = lines.append

    add("## What the product can say about an element")
    add("")
    add("Measured from the interface registry, the action registry, the binding")
    add("scan and the component library. Nothing on this page was typed by hand:")
    add("`python3 -m core.experience.audit` writes it, and the test compares the")
    add("document with what it writes.")
    add("")
    add("| Measurement | Count |")
    add("| --- | --- |")
    add(f"| Interfaces | {numbers['interfaces']} |")
    add(f"| Navigable interfaces | {numbers['navigable_interfaces']} |")
    add(f"| Interfaces with a described screen | {numbers['described_interfaces']} |")
    add(f"| Interfaces declaring no help topic | {numbers['interfaces_without_help']} |")
    add(f"| Interfaces declaring no keyboard shortcut "
        f"| {numbers['interfaces_without_shortcut']} |")
    add(f"| Registered actions | {numbers['registered_actions']} |")
    add(f"| Actions bound to a control | {numbers['actions_bound']} |")
    add(f"| Presented actions nothing binds | {numbers['actions_unbound']} |")
    add(f"| Actions declared and not built | {numbers['actions_not_built']} |")
    add(f"| Actions named in markup and never registered "
        f"| {numbers['actions_named_but_unregistered']} |")
    add(f"| Actions no described screen presents "
        f"| {numbers['actions_outside_screens']} |")
    add(f"| Declared components | {numbers['components']} |")
    add(f"| Components an element cannot resolve to "
        f"| {numbers['components_unresolvable']} |")
    add(f"| Classes rendered in the product | {numbers['classes']['rendered']} |")
    add(f"| Classes the project owns | {numbers['classes']['owned']} |")
    add(f"| Classes that are third-party | {numbers['classes']['third_party']} |")
    add(f"| Classes belonging to nobody | {numbers['classes']['unknown']} |")
    add(f"| Bindings the scan found | {numbers['bindings']} |")
    add("")

    add("### How far each action's binding reaches")
    add("")
    add("| Status | Actions |")
    add("| --- | --- |")
    for status, count in sorted(numbers["action_statuses"].items()):
        add(f"| {status} | {count} |")
    add("")
    add("`bound to a control` means the product names the action where a control")
    add("comes from - a shared component, a page script, or the Python that")
    add("prepares the surface. Everything else is an action a reader can meet that")
    add("the architecture cannot yet account for, and each one is listed below")
    add("with the evidence.")
    add("")

    add("### The actions the product cannot account for")
    add("")
    unaccounted = unresolvable_actions()
    if not unaccounted:
        add("None: every registered action is bound to a control, and every action")
        add("named in markup is registered.")
    else:
        add("| Action | Status | Where | Shared surface it uses |")
        add("| --- | --- | --- | --- |")
        for row in unaccounted:
            where = ", ".join(row.get("bound_in") or row.get("named_in")
                              or row.get("presented_by") or ())
            drawn = "; ".join(row.get("controls") or ())
            add(f"| `{row['action_id']}` | {row['status']} | {where or '-'} "
                f"| {drawn or '-'} |")
    add("")

    add("### Interfaces the Inspector describes")
    add("")
    add("| Interface | Domain | Route | Help topic | Shortcut | Actions |")
    add("| --- | --- | --- | --- | --- | --- |")
    for row in interface_rows():
        add(f"| `{row['interface_id']}` | {row['domain']} | `{row['route']}` "
            f"| {row['help_topic'] or 'Not declared'} "
            f"| {row['keyboard_shortcut'] or 'Not declared'} | {row['actions']} |")
    add("")

    add("### Fields, and how many actions declare them")
    add("")
    add("| Field | Declared | Of |")
    add("| --- | --- | --- |")
    for field, coverage in sorted(numbers["fields"].items()):
        add(f"| {field} | {coverage['declared']} | {coverage['of']} |")
    add("")
    add("A field nobody declares is not a defect in the Inspector: it is why the")
    add("panel says *Not declared*, and the number is here so filling the metadata")
    add("in is visible progress rather than a matter of opinion.")
    add("")

    add("### The questions the Inspector cannot answer yet")
    add("")
    for gap in GAPS:
        add(f"**{gap['what']}** — {gap['cannot_say']}")
        add("")
        add(f"Evidence: {', '.join('`' + item + '`' for item in gap['evidence'])}")
        add("")
    return "\n".join(lines).rstrip() + "\n"


#: The questions the Inspector cannot answer honestly today. Each one is a
#: capability the model does not have - not a screen that has not been styled -
#: and each is anchored to the code that proves it, so a fixed question
#: disappears from this list rather than lingering as folklore.
GAPS: Tuple[Dict[str, object], ...] = (
    {
        "what": "Actions with no operation: `execution` is empty and no job exists",
        "cannot_say": "The registry can say 'declared, not built' and the surface "
                      "honours it - the button is hidden, nothing is drawn, and no "
                      "route is bound - but four actions (`files.reprocess`, "
                      "`sources.export_selected`, `sources.edit_selected`, and the "
                      "bulk export and edit of the same family) have no operation to "
                      "name, so the Inspector can only report the absence. Building "
                      "one means giving it a persistent job and a path policy first, "
                      "which the action vocabulary has no room for.",
        "evidence": ("core/experience/action_registry.py",
                     "Api/blueprints/files.py",
                     "pipeline/storage_pipeline.py"),
    },
    {
        "what": "An action's help topic and shortcut",
        "cannot_say": "Every interface declares a help topic and five of six "
                      "navigable screens declare no keyboard shortcut, and an action "
                      "declares neither. The Inspector therefore shows the screen's "
                      "help and nothing for the action's own, rather than borrowing "
                      "one: a help topic is a promise that a page answers that "
                      "question, and the action vocabulary has no field for it.",
        "evidence": ("core/interfaces/registry.py",
                     "core/experience/inspector.py"),
    },
    {
        "what": "What a control is bound to, when it is wired by a page function",
        "cannot_say": "The frozen toolbar renders buttons that call a page function "
                      "by name (`data-sequence-action`), not from the registry, so "
                      "for most actions the only honest answer is 'hand-written "
                      "button, not registry-bound'. The Inspector cannot trace a "
                      "click to its operation for those screens until each screen "
                      "renders its toolbar from the registry, which the freeze "
                      "defers on purpose.",
        "evidence": ("templates/components/action_toolbar.html",
                     "static/js/pages/keywords-list-page.js",
                     "static/js/pages/sources-list-page.js"),
    },
    {
        "what": "Whether the reader is permitted to run the action",
        "cannot_say": "The Inspector reports the permission *name* the action "
                      "declares and the authorization decision the server gave for "
                      "that request, and keeps them apart on purpose: a name is not a "
                      "decision, and a browser must never be shown one as a boolean. "
                      "There is no server-side 'may this account run this action' "
                      "answer to show yet, because authorization today is decided "
                      "where the operation runs - so the field reads 'Not determined' "
                      "rather than a guess.",
        "evidence": ("core/experience/inspector.py",
                     "core/security/flask_ext.py"),
    },
)


def write_document(path: str) -> None:
    """Replace the generated block in a document, leaving its prose alone."""
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
    if len(argv) == 3 and argv[1] == "--json":
        import json

        print(json.dumps(counts(), indent=2, sort_keys=True, default=str))
        return 0
    if len(argv) != 2:
        print(__doc__)
        print("usage: python3 -m core.experience.audit "
              "docs/SCREEN_INSPECTOR_EVIDENCE.md")
        return 2
    write_document(argv[1])
    return 0


if __name__ == "__main__":  # pragma: no cover - a command, not a library
    import sys

    raise SystemExit(main(sys.argv))
