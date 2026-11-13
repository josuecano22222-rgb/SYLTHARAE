"""The Experience Contract: the declarative description of how a screen behaves.

The registry answers *what exists* — an interface, its domain, its lifecycle, its
dependencies. This package answers the next question: *how is it presented and
what does it offer the person using it*. Navigation, actions, columns, filters,
states, help, shortcuts, layout and the translation keys each screen uses.

Three rules hold everywhere in this package, and the tests enforce them:

* **Declarative only.** A definition names things — an id, a translation key, an
  icon, a permission, the shape of a confirmation. It never contains SQL, an
  import, an expression to evaluate, or an authorisation decision. Executing
  anything stays where it always was: in the service layer and the route.
* **Keys, not sentences.** Every human-readable string is a *translation key*
  with its English source recorded beside it. A screen cannot grow an English
  string that no translator can find.
* **Nothing is invented.** A definition exists because a declaration or a
  registry entry says so. Where nothing is declared, the contract says
  ``declared: false`` and the audit reports it as outstanding rather than
  filling the gap with a plausible-looking default.

The model is inert: dataclasses that describe, validate their own shape, and
serialise. Reading the registry, the catalogs and the templates is the job of
``contract.py`` and ``coverage.py``; rendering is the job of the shell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

#: A semantic translation key: `screen.files.title`, `action.files.export.label`.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")

#: An execution reference is one lowercase word. A route, a URL, a template, a
#: file path and a Python callable all fail this pattern, which is the point:
#: the definition names the operation, and the service layer owns how it is
#: reached.
_EXECUTION_REFERENCE = re.compile(r"^[a-z][a-z0-9_]*$")

#: A stable id inside a screen: `name`, `usage_count`, `bulk_update`.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

#: Placeholders inside a translatable string: `{count}`, `{name}`.
PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")

#: The §63 state vocabulary, in one place for the whole product.
SCREEN_STATES: Tuple[str, ...] = (
    "loading", "empty", "normal", "filtered", "selected", "editing", "saving",
    "success", "warning", "error", "unauthorized", "unavailable", "archived",
)

#: What an action applies to. A bulk action must show its scope; a dangerous
#: action must be able to ask for confirmation (see `confirmation`).
ACTION_SCOPES: Tuple[str, ...] = ("page", "selection", "bulk", "record")

#: The resource an action acts on, and the first half of every action id.
#: Deliberately a resource (`files`, `keywords`, `search`) and not a page: an
#: action that appears on a second screen keeps its id, so nothing has to be
#: renamed, and two pages cannot both register `delete_selected` and mean
#: different things.
ACTION_NAMESPACES: Tuple[str, ...] = (
    "files", "sources", "sides", "keywords", "words", "categories", "search",
    "viewer", "export", "jobs", "analysis",
)

#: How a selection is turned into work. ``all`` acts on every selected record.
#: ``one`` acts on a single member of the selection - which member is the
#: operation's own rule, stated by the page that performs it; a component never
#: infers it, and the toolbar never needs to know. This is what keeps "edits the
#: first of three selected rows" expressible without inventing a fifth scope.
SELECTION_RULES: Tuple[str, ...] = ("all", "one")

#: What an action can be doing right now. The definition describes the action;
#: this is its state at a moment, which is a runtime fact, not a declaration.
ACTION_STATES: Tuple[str, ...] = (
    "available", "disabled", "hidden", "running", "success", "failed",
)

#: Why an action is present but unusable. Never mixed with the reasons an
#: action is not shown: "nothing is selected" and "you may not do this" are
#: different situations and must not collapse into one grey button.
DISABLED_REASONS: Tuple[str, ...] = (
    "no_selection", "single_selection_required", "not_built", "unavailable",
    "record_archived", "original_missing",
)

#: Why an action is not shown at all. Decided by the server and passed in; a
#: hidden action is not a secured one, and this vocabulary decides nothing.
HIDDEN_REASONS: Tuple[str, ...] = (
    "no_permission", "not_applicable", "not_built",
)

#: How an action's progress is shown while it runs.
LOADING_STYLES: Tuple[str, ...] = ("inline", "button", "region", "toast")

#: Whether the product actually offers the action.
#:
#: ``offered`` is everything the product presents today. ``not_built`` is a
#: capability that has been declared - because an interface is expected to have
#: it, because a control once existed for it - and that **no operation performs
#: yet**. A surface may not draw one as available, and the audit reports it by
#: name; declaring it is honest, hiding the absence is not. An action that is
#: not built carries no execution reference, because there is nothing to
#: reference.
ACTION_VISIBILITIES: Tuple[str, ...] = ("offered", "not_built")

#: What kind of value a field holds. Not a validation engine: a hint the form
#: component uses to pick a control, and the server keeps its own rules.
FIELD_TYPES: Tuple[str, ...] = (
    "text", "textarea", "number", "boolean", "select", "multiselect", "date",
    "datetime", "file", "password", "hidden",
)

#: How a column's value is rendered. Formatting stays a presentation decision.
COLUMN_FORMATS: Tuple[str, ...] = (
    "text", "number", "date", "datetime", "duration", "bytes", "status",
    "badge", "link", "boolean",
)

#: The control a filter uses.
FILTER_CONTROLS: Tuple[str, ...] = ("select", "multiselect", "text", "date", "range", "toggle")

#: Density a screen can be read at.
DENSITIES: Tuple[str, ...] = ("compact", "comfortable", "spacious")


class ContractError(ValueError):
    """A definition that cannot be part of a contract."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _key(value: str, what: str) -> str:
    _check(bool(KEY_PATTERN.match(value or "")),
           f"{what} must be a semantic translation key, not a sentence: {value!r}")
    return value


def _ident(value: str, what: str) -> str:
    _check(bool(ID_PATTERN.match(value or "")),
           f"{what} must be a stable lowercase id: {value!r}")
    return value


def placeholders(text: str) -> Tuple[str, ...]:
    """The placeholders a string requires, in order, deduplicated."""
    seen = []
    for name in PLACEHOLDER_PATTERN.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def check_placeholders(source: str, translation: str) -> Tuple[str, ...]:
    """Placeholder problems between a source string and its translation.

    This is the check that stops an administrator saving a translation that has
    dropped ``{count}`` - the failure that reaches a reader as "Processing
    files" with the number missing. It returns the problems, in the reader's
    terms, so a screen can show them beside the editor.
    """
    problems = []
    wanted = placeholders(source)
    found = placeholders(translation)
    for name in wanted:
        if name not in found:
            problems.append(f"missing placeholder {{{name}}}")
    for name in found:
        if name not in wanted:
            problems.append(f"unexpected placeholder {{{name}}}")
    # An unbalanced brace usually means a broken placeholder rather than
    # deliberate text: `{count` or `count}`.
    opened, closed = translation.count("{"), translation.count("}")
    if opened != closed:
        problems.append("unbalanced braces")
    # A literal `{}` with no name cannot be filled by anything.
    if "{}" in translation:
        problems.append("empty placeholder")
    return tuple(problems)


# --------------------------------------------------------------------------
# The definitions a screen is made of
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionDefinition:
    """Something a person can do on a screen.

    It describes the action; it does not perform it. Five separations are
    deliberate, and each one is enforced below:

    * **Execution is a reference, not a route.** ``execution`` names an
      operation in a plain lowercase token (``download_original``). It is not a
      URL, not a Flask endpoint, and not something this layer can resolve: the
      service layer owns what the operation does, and the Action Registry must
      never become a second router.
    * **Permission is a name, not a decision.** ``permission`` says which
      permission domain the action belongs to; the server decides whether this
      request may run it, exactly as it does for every other request. A hidden
      action is not therefore protected, and nothing here authorises anything.
    * **Confirmation is required of anything destructive.** A destructive
      action with no confirmation key is refused at definition time.
    * **A bulk action requires a selection and acts on all of it.** Anything
      that acts on one member of a selection says so with ``selection_rule``
      instead of pretending to be bulk.
    * **The id is namespaced by resource**, so the same action keeps one name
      when it appears on a second screen.
    """

    action_id: str
    label_key: str
    source: str
    icon: str = "bi-lightning"
    interfaces: Tuple[str, ...] = ()
    scope: str = "page"
    permission: Optional[str] = None
    destructive: bool = False
    confirmation: Optional[str] = None      # translation key, never a sentence
    shortcut: Optional[str] = None
    requires_selection: bool = False
    selection_rule: Optional[str] = None
    loading: str = "inline"
    visibility: str = "offered"
    execution: Optional[str] = None          # opaque operation reference

    def __post_init__(self) -> None:
        from .permissions import ACTION_PERMISSIONS

        namespace, _, name = self.action_id.partition(".")
        _check(bool(name), f"action {self.action_id}: an id is <resource>.<name>")
        _ident(namespace, f"action {self.action_id} namespace")
        _ident(name, f"action {self.action_id} name")
        _check(namespace in ACTION_NAMESPACES,
               f"action {self.action_id}: unknown namespace {namespace!r}")
        _key(self.label_key, f"action {self.action_id} label_key")
        _check(self.scope in ACTION_SCOPES,
               f"action {self.action_id}: unknown scope {self.scope!r}")
        _check(self.loading in LOADING_STYLES,
               f"action {self.action_id}: unknown loading style {self.loading!r}")
        _check(self.visibility in ACTION_VISIBILITIES,
               f"action {self.action_id}: unknown visibility {self.visibility!r}")
        _check(bool(self.interfaces),
               f"action {self.action_id}: no interface can show it")
        _check(len(set(self.interfaces)) == len(self.interfaces),
               f"action {self.action_id}: an interface is listed twice")
        for interface_id in self.interfaces:
            _ident(interface_id, f"action {self.action_id} interface")
        if self.permission is not None:
            _check(self.permission in ACTION_PERMISSIONS,
                   f"action {self.action_id}: unknown permission "
                   f"{self.permission!r} - permissions are a declared "
                   "vocabulary, not free text")
        if self.confirmation:
            _key(self.confirmation, f"action {self.action_id} confirmation")
        if self.shortcut:
            _check(isinstance(self.shortcut, str) and self.shortcut.strip(),
                   f"action {self.action_id}: empty shortcut")
        if self.visibility == "not_built":
            # A capability nobody has built cannot name the operation that
            # performs it: naming one would be a claim, not a reference.
            _check(self.execution is None,
                   f"action {self.action_id} is not built and yet names "
                   f"execution {self.execution!r}")
        if self.execution is not None:
            _check(bool(_EXECUTION_REFERENCE.match(self.execution)),
                   f"action {self.action_id}: execution {self.execution!r} is "
                   "not an opaque operation reference - no route, no URL and "
                   "no path belongs here")
        if self.destructive:
            # A destructive action that cannot ask for confirmation is how a
            # product loses data by accident.
            _check(bool(self.confirmation),
                   f"action {self.action_id} is destructive and declares no confirmation")
        if self.selection_rule is not None:
            _check(self.selection_rule in SELECTION_RULES,
                   f"action {self.action_id}: unknown selection rule "
                   f"{self.selection_rule!r}")
        if self.scope == "bulk":
            _check(self.requires_selection,
                   f"action {self.action_id} is a bulk action and must require a selection")
            _check(self.selection_rule == "all",
                   f"action {self.action_id} is bulk, so it acts on the whole "
                   "selection; an action that acts on one member is a "
                   "selection action with selection_rule='one'")
        if self.scope == "selection":
            _check(self.requires_selection,
                   f"action {self.action_id} acts on a selection and must "
                   "require one")
            _check(self.selection_rule in SELECTION_RULES,
                   f"action {self.action_id} acts on a selection and must say "
                   "how (selection_rule='all' or 'one')")
        if self.scope == "page":
            _check(self.selection_rule is None,
                   f"action {self.action_id} is a page action and cannot act "
                   "on a selection")

    # -- derived facts ----------------------------------------------------
    @property
    def namespace(self) -> str:
        """The resource this action acts on - the first half of its id."""
        return self.action_id.split(".", 1)[0]

    @property
    def confirmation_required(self) -> bool:
        """Whether running it has to be confirmed before it happens."""
        return bool(self.confirmation)

    @property
    def built(self) -> bool:
        """Whether the product offers this action at all."""
        return self.visibility == "offered"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "namespace": self.namespace,
            "interfaces": list(self.interfaces),
            "label_key": self.label_key,
            "source": self.source,
            "icon": self.icon,
            "scope": self.scope,
            "permission": self.permission,
            "destructive": self.destructive,
            "confirmation": self.confirmation,
            "confirmation_required": self.confirmation_required,
            "shortcut": self.shortcut,
            "requires_selection": self.requires_selection,
            "selection_rule": self.selection_rule,
            "loading": self.loading,
            "visibility": self.visibility,
            "built": self.built,
            "execution": self.execution,
        }


@dataclass(frozen=True)
class ActionState:
    """What an action is doing at this moment.

    A runtime fact about one action, not a declaration: the definition says
    what can be done, this says how it is presented right now, and it carries
    its reason. The reasons are kept apart on purpose - an action nobody has
    selected anything for is disabled, an action this person may not use is
    hidden, and a product that shows both as the same grey button has lost the
    information the operator needed.

    ``derive`` takes facts the server has already established (whether this
    request is permitted, how many records are selected). It decides nothing:
    it arranges what it was told.
    """

    action_id: str
    state: str = "available"
    disabled_reason: Optional[str] = None
    hidden_reason: Optional[str] = None
    confirmation_required: bool = False
    message_key: Optional[str] = None      # why, in the reader's language

    def __post_init__(self) -> None:
        _ident(self.action_id.replace(".", "_"),
               f"action state {self.action_id}")
        _check(self.state in ACTION_STATES,
               f"action {self.action_id}: unknown state {self.state!r}")
        if self.state == "disabled":
            _check(self.disabled_reason in DISABLED_REASONS,
                   f"action {self.action_id}: disabled without a reason from "
                   f"{DISABLED_REASONS}")
        else:
            _check(self.disabled_reason is None,
                   f"action {self.action_id}: {self.state} cannot carry a "
                   "disabled reason")
        if self.state == "hidden":
            _check(self.hidden_reason in HIDDEN_REASONS,
                   f"action {self.action_id}: hidden without a reason from "
                   f"{HIDDEN_REASONS}")
        else:
            _check(self.hidden_reason is None,
                   f"action {self.action_id}: {self.state} cannot carry a "
                   "hidden reason")

    @classmethod
    def derive(cls, definition: ActionDefinition, *, selected: int = 0,
               permitted: bool = True, running: bool = False,
               unavailable: Optional[str] = None) -> "ActionState":
        """Present one action from facts somebody else established.

        ``permitted`` must come from the server's own authorisation - this
        method never evaluates a permission, it only reflects the answer. The
        other inputs are the same: how many records are selected, whether the
        operation is already running, and a named reason the action cannot be
        used at all (a missing original file, an archived record).
        """
        _check(selected >= 0, "selected cannot be negative")
        if not definition.built:
            # The honest answer, whatever else is true: there is nothing here
            # to run, so the surface must not offer it.
            return cls(action_id=definition.action_id, state="hidden",
                       hidden_reason="not_built")
        if not permitted:
            return cls(action_id=definition.action_id, state="hidden",
                       hidden_reason="no_permission")
        if running:
            return cls(action_id=definition.action_id, state="running")
        if unavailable is not None:
            return cls(action_id=definition.action_id, state="disabled",
                       disabled_reason=unavailable,
                       confirmation_required=definition.confirmation_required)
        if definition.requires_selection and not selected:
            return cls(action_id=definition.action_id, state="disabled",
                       disabled_reason="no_selection",
                       confirmation_required=definition.confirmation_required)
        if (definition.selection_rule == "one" and selected > 1
                and definition.selection_rule != "all"):
            # The action can still run: it acts on one member of the selection.
            # It stays available and the operation decides which member.
            return cls(action_id=definition.action_id, state="available",
                       confirmation_required=definition.confirmation_required)
        return cls(action_id=definition.action_id, state="available",
                   confirmation_required=definition.confirmation_required)


@dataclass(frozen=True)
class FieldDefinition:
    """A value a form shows or collects."""

    field_id: str
    label_key: str
    source: str
    help_key: Optional[str] = None
    data_type: str = "text"
    required: bool = False
    visible: bool = True
    editable: bool = True
    permission: Optional[str] = None
    order: int = 0

    def __post_init__(self) -> None:
        _ident(self.field_id, "field_id")
        _key(self.label_key, f"field {self.field_id} label_key")
        if self.help_key:
            _key(self.help_key, f"field {self.field_id} help_key")
        _check(self.data_type in FIELD_TYPES,
               f"field {self.field_id}: unknown type {self.data_type!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_id": self.field_id,
            "label_key": self.label_key,
            "source": self.source,
            "help_key": self.help_key,
            "data_type": self.data_type,
            "required": self.required,
            "visible": self.visible,
            "editable": self.editable,
            "permission": self.permission,
            "order": self.order,
        }


@dataclass(frozen=True)
class ColumnDefinition:
    """A column of a table: what it is called, how it renders, where it sits."""

    column_id: str
    label_key: str
    source: str
    width: Optional[str] = None
    visible: bool = True
    sortable: bool = False
    filterable: bool = False
    align: str = "start"
    render: str = "text"

    def __post_init__(self) -> None:
        _ident(self.column_id, "column_id")
        _key(self.label_key, f"column {self.column_id} label_key")
        _check(self.render in COLUMN_FORMATS,
               f"column {self.column_id}: unknown format {self.render!r}")
        _check(self.align in ("start", "center", "end"),
               f"column {self.column_id}: unknown alignment {self.align!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column_id": self.column_id,
            "label_key": self.label_key,
            "source": self.source,
            "width": self.width,
            "visible": self.visible,
            "sortable": self.sortable,
            "filterable": self.filterable,
            "align": self.align,
            "render": self.render,
        }


@dataclass(frozen=True)
class FilterDefinition:
    """A control that narrows a list, and what it starts at."""

    filter_id: str
    label_key: str
    source: str
    control: str = "select"
    default: Any = None
    options: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ident(self.filter_id, "filter_id")
        _key(self.label_key, f"filter {self.filter_id} label_key")
        _check(self.control in FILTER_CONTROLS,
               f"filter {self.filter_id}: unknown control {self.control!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filter_id": self.filter_id,
            "label_key": self.label_key,
            "source": self.source,
            "control": self.control,
            "default": self.default,
            "options": list(self.options),
        }


@dataclass(frozen=True)
class StateDefinition:
    """One of the §63 states, as this screen presents it."""

    state: str
    title_key: str
    source: str
    message_key: Optional[str] = None
    action_label_key: Optional[str] = None

    def __post_init__(self) -> None:
        _check(self.state in SCREEN_STATES,
               f"unknown screen state {self.state!r}")
        _key(self.title_key, f"state {self.state} title_key")
        if self.message_key:
            _key(self.message_key, f"state {self.state} message_key")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "title_key": self.title_key,
            "source": self.source,
            "message_key": self.message_key,
            "action_label_key": self.action_label_key,
        }


@dataclass(frozen=True)
class HelpDefinition:
    """Where a screen's help lives and what it is about."""

    topic: str
    title_key: str
    source: str
    summary_key: Optional[str] = None
    related: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check(bool(self.topic), "help topic is required")
        _key(self.title_key, "help title_key")
        if self.summary_key:
            _key(self.summary_key, "help summary_key")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "title_key": self.title_key,
            "source": self.source,
            "summary_key": self.summary_key,
            "related": list(self.related),
        }


@dataclass(frozen=True)
class TranslationDefinition:
    """A string this screen shows, and the key a translator edits it by."""

    key: str
    source: str
    context: str = ""
    screen: str = ""
    description: Optional[str] = None
    placeholders: Tuple[str, ...] = ()
    plural: bool = False

    def __post_init__(self) -> None:
        _key(self.key, "translation key")
        _check(bool(self.source), f"translation {self.key} has no source string")
        # The recorded placeholders must be the ones the source actually uses.
        _check(tuple(self.placeholders) == placeholders(self.source),
               f"translation {self.key}: recorded placeholders "
               f"{list(self.placeholders)} do not match the source "
               f"{list(placeholders(self.source))}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source,
            "context": self.context,
            "screen": self.screen,
            "description": self.description,
            "placeholders": list(self.placeholders),
            "plural": self.plural,
        }


@dataclass(frozen=True)
class ShortcutDefinition:
    """A keyboard sequence and the action it triggers."""

    shortcut: str
    action_key: str

    def __post_init__(self) -> None:
        _key(self.action_key, "shortcut action_key")
        _check(bool(self.shortcut.strip()), "a shortcut with no keys is not a shortcut")

    def to_dict(self) -> Dict[str, Any]:
        return {"shortcut": self.shortcut, "action_key": self.action_key}


@dataclass(frozen=True)
class NavigationDefinition:
    """How this interface appears in navigation, if at all."""

    domain: str
    label_key: str
    source: str
    icon: str = "bi-square"
    order: int = 0
    group: Optional[str] = None
    visible: bool = True
    required_role: Optional[str] = None
    feature: Optional[str] = None

    def __post_init__(self) -> None:
        _check(bool(self.domain), "a navigation entry belongs to a domain")
        _key(self.label_key, "navigation label_key")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "label_key": self.label_key,
            "source": self.source,
            "icon": self.icon,
            "order": self.order,
            "group": self.group,
            "visible": self.visible,
            "required_role": self.required_role,
            "feature": self.feature,
        }


@dataclass(frozen=True)
class LayoutDefinition:
    """The regions a screen is composed of.

    Conceptual, not a fixed three-pane requirement: a screen may show the
    inspector inline, docked, or not at all, and says so here.
    """

    navigator: str = "global"      # global | contextual | none
    inspector: str = "docked"      # docked | overlay | inline | none
    workspace: str = "standard"    # standard | split | full
    density: str = "comfortable"
    responsive: bool = True
    regions: Tuple[str, ...] = ("context", "toolbar", "content", "state")

    def __post_init__(self) -> None:
        _check(self.navigator in ("global", "contextual", "none"),
               f"unknown navigator {self.navigator!r}")
        _check(self.inspector in ("docked", "overlay", "inline", "none"),
               f"unknown inspector {self.inspector!r}")
        _check(self.workspace in ("standard", "split", "full"),
               f"unknown workspace {self.workspace!r}")
        _check(self.density in DENSITIES, f"unknown density {self.density!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "navigator": self.navigator,
            "inspector": self.inspector,
            "workspace": self.workspace,
            "density": self.density,
            "responsive": self.responsive,
            "regions": list(self.regions),
        }


@dataclass(frozen=True)
class ScreenConfiguration:
    """Everything about how one registered interface is presented.

    `declared` is the honest part: `False` means the screen is still running on
    derived defaults and nobody has configured it yet. The audit counts the
    difference instead of implying every screen has been designed.
    """

    interface_id: str
    title_key: str
    source: str
    description_key: Optional[str] = None
    icon: Optional[str] = None
    navigation: Optional[NavigationDefinition] = None
    actions: Tuple[ActionDefinition, ...] = ()
    fields: Tuple[FieldDefinition, ...] = ()
    columns: Tuple[ColumnDefinition, ...] = ()
    filters: Tuple[FilterDefinition, ...] = ()
    states: Tuple[StateDefinition, ...] = ()
    help: Optional[HelpDefinition] = None
    translations: Tuple[TranslationDefinition, ...] = ()
    shortcuts: Tuple[ShortcutDefinition, ...] = ()
    layout: LayoutDefinition = field(default_factory=LayoutDefinition)
    declared: bool = False

    def __post_init__(self) -> None:
        _ident(self.interface_id, "interface_id")
        _key(self.title_key, f"screen {self.interface_id} title_key")
        if self.description_key:
            _key(self.description_key, f"screen {self.interface_id} description_key")
        seen: Dict[str, set] = {}
        for name, items in (("action", self.actions), ("field", self.fields),
                            ("column", self.columns), ("filter", self.filters)):
            for item in items:
                ident = getattr(item, f"{name}_id")
                if ident in seen.setdefault(name, set()):
                    raise ContractError(
                        f"screen {self.interface_id}: duplicate {name} id {ident!r}")
                seen[name].add(ident)

    # -- lookups the shell and the studio use -----------------------------
    def action(self, action_id: str) -> Optional[ActionDefinition]:
        return next((a for a in self.actions if a.action_id == action_id), None)

    def state(self, state: str) -> Optional[StateDefinition]:
        return next((s for s in self.states if s.state == state), None)

    @property
    def translation_keys(self) -> Tuple[str, ...]:
        """Every key this screen needs translated, in a stable order."""
        keys = [self.title_key]
        if self.description_key:
            keys.append(self.description_key)
        keys += [a.label_key for a in self.actions]
        keys += [a.confirmation for a in self.actions if a.confirmation]
        keys += [f.label_key for f in self.fields]
        keys += [c.label_key for c in self.columns]
        keys += [f.label_key for f in self.filters]
        keys += [s.title_key for s in self.states]
        keys += [s.message_key for s in self.states if s.message_key]
        keys += [s.action_label_key for s in self.states if s.action_label_key]
        if self.help:
            keys.append(self.help.title_key)
            if self.help.summary_key:
                keys.append(self.help.summary_key)
        if self.navigation:
            keys.append(self.navigation.label_key)
        keys += [s.action_key for s in self.shortcuts]
        return tuple(dict.fromkeys(keys))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interface_id": self.interface_id,
            "title_key": self.title_key,
            "source": self.source,
            "description_key": self.description_key,
            "icon": self.icon,
            "declared": self.declared,
            "navigation": self.navigation.to_dict() if self.navigation else None,
            "actions": [a.to_dict() for a in self.actions],
            "fields": [f.to_dict() for f in self.fields],
            "columns": [c.to_dict() for c in self.columns],
            "filters": [f.to_dict() for f in self.filters],
            "states": [s.to_dict() for s in self.states],
            "help": self.help.to_dict() if self.help else None,
            "translations": [t.to_dict() for t in self.translations],
            "shortcuts": [s.to_dict() for s in self.shortcuts],
            "layout": self.layout.to_dict(),
            "translation_keys": list(self.translation_keys),
        }


@dataclass(frozen=True)
class ExperienceContract:
    """A screen: what the registry says it is, and how it presents itself."""

    interface_id: str
    name: str
    description: str
    domain: str
    kind: str
    status: str
    navigable: bool
    route: Optional[str]
    endpoints: Tuple[str, ...]
    required_role: Optional[str]
    dependencies: Tuple[str, ...]
    settings: Tuple[str, ...]
    feature_flag: Optional[str]
    screen: ScreenConfiguration

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interface_id": self.interface_id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "kind": self.kind,
            "status": self.status,
            "navigable": self.navigable,
            "route": self.route,
            "endpoints": list(self.endpoints),
            "required_role": self.required_role,
            "dependencies": list(self.dependencies),
            "settings": list(self.settings),
            "feature_flag": self.feature_flag,
            "screen": self.screen.to_dict(),
            # The immutable half, stated so the studio can show it as
            # read-only: where the code lives is not configurable.
            "implementation": {
                "route": self.route,
                "endpoints": list(self.endpoints),
                "editable_from_frontend": False,
            },
        }


__all__ = [
    "ACTION_SCOPES",
    "ActionDefinition",
    "COLUMN_FORMATS",
    "ColumnDefinition",
    "ContractError",
    "DENSITIES",
    "ExperienceContract",
    "FIELD_TYPES",
    "FieldDefinition",
    "FILTER_CONTROLS",
    "FilterDefinition",
    "HelpDefinition",
    "LAYOUT_REGIONS",
    "LayoutDefinition",
    "LOADING_STYLES",
    "NavigationDefinition",
    "SCREEN_STATES",
    "ScreenConfiguration",
    "ShortcutDefinition",
    "StateDefinition",
    "TranslationDefinition",
    "check_placeholders",
    "placeholders",
]

#: Named for the doc and tests: the conceptual regions a screen composes.
LAYOUT_REGIONS: Tuple[str, ...] = ("navigator", "workspace", "inspector")
