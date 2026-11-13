"""What the product knows about one element on one screen.

The Screen Inspector answers a developer's question - *what is this control, and
who owns it?* - from the architecture that already exists. It is a read-only
join of five sources, and it invents nothing:

* the **interface registry** owns which screen you are on (identity comes from
  the registry, never from parsing the URL);
* the **experience contract** owns what the screen declares - its help topic,
  its shortcut, the keys its strings need;
* the **action registry** owns what an action is: scope, permission name,
  destructiveness, the confirmation key, the operation reference, and whether
  the product has built it at all;
* the **binding layer** (``bindings``) owns where that action is bound, and
  whether the address it carries resolves;
* the **component library** (``core.frontend.component_audit``) owns which
  component renders an element, and whether a class belongs to SYLTHARAE, to a
  third party, or to nobody.

Two rules shape every answer here, and both of them are about honesty rather
than about interface:

**Permission is a name, not a decision.** An action may declare
``permission="files.delete"``; that says which vocabulary the action belongs to.
It does not say the account in front of the screen may delete anything, and this
module never turns one into the other. Where a server-side authorisation answer
is available it is carried separately, as an input, exactly as it was given.

**Unknown is a value.** A screen with no declared help topic says "Not declared";
an element with no component metadata says so; a class nobody defines is
UNKNOWN. The Inspector does not brighten a gap with a plausible-looking guess -
a button whose text is "Delete Selected" is never turned into an action id by
reading the words.

Nothing here performs anything, writes anything, or decides anything: it is a
report about the interface, generated from the same sources the interface itself
is generated from.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.frontend import component_audit
from core.interfaces import REGISTRY

from . import bindings as binding_layer
from .action_registry import action as registered_action
from .model import ActionState

# ---------------------------------------------------------------------------
# The vocabulary of an unresolved field
# ---------------------------------------------------------------------------
#: Nothing in the product declares this. The field is real (the element could
#: carry it), and its absence is the answer.
NOT_DECLARED = "not_declared"
#: The concept does not apply here - an action-less element has no scope.
NOT_APPLICABLE = "not_applicable"
#: Something should supply this and did not: it is reported, not filled in.
UNAVAILABLE = "unavailable"
#: Something was named that the architecture does not know.
UNKNOWN = "unknown"
#: A thing that resolved to a definition.
RESOLVED = "resolved"
#: A binding that names an address nothing serves, or an action nobody declares.
UNRESOLVED = "unresolved"
#: The question was not asked - no route map was available to answer it.
NOT_CHECKED = "not_checked"

#: What an unresolved field means, in words, for the panel and for the tests.
FIELD_STATUSES: Tuple[str, ...] = (
    NOT_DECLARED, NOT_APPLICABLE, UNAVAILABLE, UNKNOWN,
    RESOLVED, UNRESOLVED, NOT_CHECKED,
)

#: The statuses a *value* may resolve to.
RESOLUTION_STATUSES: Tuple[str, ...] = (RESOLVED, UNRESOLVED, UNKNOWN, NOT_DECLARED)

#: Why a binding is unresolved. Named rather than described, so a report can
#: count them.
BINDING_PROBLEMS: Tuple[str, ...] = (
    "unknown_action", "no_matching_endpoint", "not_bound", "interface_mismatch",
    "endpoint_not_named",
)

#: The attributes the shared components emit, and the only ones the Inspector
#: needs: which screen, which component, which action, and what kind of element
#: it is. Nothing here is trusted as a fact about authorisation: they say
#: *which* thing was selected, and the server looks the meaning up itself.
DOM_ATTRIBUTES: Dict[str, str] = {
    "interface": "data-inspector-interface",
    "component": "data-inspector-component",
    "action": "data-inspector-action",
    "role": "data-inspector-role",
}

#: Hints a page may add when a binding lives somewhere the scan cannot see. They
#: are read, never required, and never believed: the scan still decides.
DOM_ATTRIBUTE_HINTS: Dict[str, str] = {
    "binding_kind": "data-inspector-binding",
    "binding_source": "data-inspector-binding-source",
}

#: The screen-level roles an element can declare about itself. Diagnostics
#: only: "control" does not mean "authorised", and "region" does not mean
#: "not a control".
ELEMENT_ROLES: Tuple[str, ...] = (
    "control", "region", "record_header", "toolbar", "panel", "link", "screen",
)

#: Longest a piece of observed text may be before it is truncated for the panel.
OBSERVED_TEXT_LIMIT = 200

#: Text that looks like a filesystem path is not carried, whoever sent it. The
#: Inspector shows labels, ids and keys; a path is neither, and section 46 of the
#: directive is explicit that one must not appear as an execution target or as
#: anything else.
_PATH_LIKE = re.compile(
    r"^(?:[a-z]:[\\/]|\\\\|/(?:home|users|var|tmp|mnt|opt|media|srv|root)/)",
    re.IGNORECASE)


class InspectorError(Exception):
    """A question the Inspector refuses to answer with a guess.

    Raising is the answer: a claim naming an interface or an action that the
    registries do not know is reported as such, never resolved into something
    else that happens to be nearby.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _clean(value: Optional[str], limit: int = 120) -> Optional[str]:
    """A string from outside, trimmed and capped. Never trusted, only shown."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


def _as_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


# ---------------------------------------------------------------------------
# What was selected
# ---------------------------------------------------------------------------
def redact_observed(text: Optional[str]) -> Optional[str]:
    """Observed text, with anything path-shaped removed rather than echoed."""
    cleaned = _clean(text, OBSERVED_TEXT_LIMIT)
    if cleaned and _PATH_LIKE.match(cleaned):
        return None
    return cleaned


@dataclass(frozen=True)
class Selection:
    """The element the reader pointed at, as the browser described it.

    Every field is a claim from the client and is treated as one: the ids are
    looked up rather than trusted, the counts are presentation facts, and the
    text is quoted, never parsed.
    """

    interface_id: Optional[str] = None
    component_id: Optional[str] = None
    action_id: Optional[str] = None
    binding_kind: Optional[str] = None
    binding_source: Optional[str] = None
    role: Optional[str] = None
    classes: Tuple[str, ...] = ()
    observed_text: Optional[str] = None
    selected: Optional[int] = None
    total: Optional[int] = None
    running: bool = False
    outcome: Optional[str] = None
    job_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Redaction belongs to the type, not to one factory: whoever builds a
        # Selection - a request, a panel, a test - the path-shaped text is
        # dropped before the object exists.
        object.__setattr__(self, "observed_text",
                           redact_observed(self.observed_text))

    @classmethod
    def from_request(cls, values: Dict[str, Any]) -> "Selection":
        """Build a selection from request parameters, ignoring rather than
        guessing at anything the vocabulary does not have."""
        classes = values.get("classes") or ()
        if isinstance(classes, str):
            classes = tuple(part.strip() for part in classes.split(",") if part.strip())
        role = _clean(values.get("role"), 40)
        outcome = _clean(values.get("outcome"), 20)
        return cls(
            interface_id=_clean(values.get("interface"), 80),
            component_id=_clean(values.get("component"), 80),
            action_id=_clean(values.get("action"), 120),
            binding_kind=_clean(values.get("binding_kind"), 40),
            binding_source=_clean(values.get("binding_source"), 200),
            role=role if role in ELEMENT_ROLES else None,
            classes=tuple(classes)[:40],
            observed_text=redact_observed(values.get("text")),
            selected=_as_int(values.get("selected")),
            total=_as_int(values.get("total")),
            running=bool(values.get("running")),
            outcome=outcome if outcome in ("success", "failed") else None,
            job_id=_clean(values.get("job"), 60),
            correlation_id=_clean(values.get("correlation_id"), 60),
        )


# ---------------------------------------------------------------------------
# What the Inspector found
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScreenInspection:
    """One element, joined with the architecture that owns it.

    ``None`` means "this element genuinely has no such concept"; a declared-but-
    absent value carries one of ``FIELD_STATUSES`` instead, so the difference
    between "an action-less element" and "an action nobody registered" survives
    all the way to the panel.
    """

    # The element, as asked about.
    role: Optional[str] = None
    classes: Tuple[str, ...] = ()

    # The screen.
    interface_id: Optional[str] = None
    interface_name: Optional[str] = None
    interface_domain: Optional[str] = None
    interface_status: Optional[str] = None
    interface_kind: Optional[str] = None
    interface_enabled: Optional[bool] = None
    interface_route: Optional[str] = None
    interface_help_topic: Optional[str] = None
    interface_help_status: str = NOT_DECLARED
    interface_shortcut: Optional[str] = None
    interface_shortcut_status: str = NOT_DECLARED
    interface_status_note: str = RESOLVED

    # The component that renders it.
    component_id: Optional[str] = None
    component_path: Optional[str] = None
    component_ownership: str = UNKNOWN
    component_status: str = NOT_DECLARED
    component_source: Optional[str] = None

    # The action.
    action_id: Optional[str] = None
    action_status: str = NOT_APPLICABLE
    action_scope: Optional[str] = None
    action_permission: Optional[str] = None
    action_destructive: Optional[bool] = None
    action_confirmation_required: Optional[bool] = None
    action_confirmation_key: Optional[str] = None
    action_execution: Optional[str] = None
    action_execution_status: str = NOT_DECLARED
    action_visibility: Optional[str] = None

    # Its state, from the one state model the product uses.
    state: Optional[str] = None
    disabled_reason: Optional[str] = None
    hidden_reason: Optional[str] = None

    # The words.
    translation_key: Optional[str] = None
    translation_source: Optional[str] = None
    translation_locale: Optional[str] = None
    translation_rendered: Optional[str] = None
    translation_status: str = NOT_DECLARED
    translation_note: Optional[str] = None

    # Where it is bound, and where that binding goes.
    binding_kind: Optional[str] = None
    binding_source: Optional[str] = None
    binding_status: str = NOT_DECLARED
    binding_problem: Optional[str] = None
    binding_observed: Optional[bool] = None

    # Authorisation, if the server said so. Never computed from a permission.
    authorization: Optional[str] = None
    authorization_roles: Tuple[str, ...] = ()
    authorization_note: str = (
        "The Action Registry names a permission domain; it does not decide "
        "whether this account may use the action. Only the endpoint does that."
    )

    # Runtime facts the page observed about itself.
    selected: Optional[int] = None
    total: Optional[int] = None
    selection_state: Optional[str] = None
    running: bool = False
    outcome: Optional[str] = None
    job_id: Optional[str] = None
    correlation_id: Optional[str] = None
    observed_text: Optional[str] = None

    # Honest notes: what could not be answered, and why.
    problems: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """The payload, field by field, with nothing computed at read time."""
        payload = asdict(self)
        payload["classes"] = list(self.classes)
        payload["authorization_roles"] = list(self.authorization_roles)
        payload["problems"] = list(self.problems)
        # What "unresolved" may say, carried with every answer so a client never
        # has to invent a wording for a value nobody declared.
        payload["field_statuses"] = list(FIELD_STATUSES)
        return payload


# ---------------------------------------------------------------------------
# Resolution: registry, contract, catalog, components, bindings
# ---------------------------------------------------------------------------
def interface_by_id(interface_id: Optional[str]):
    if not interface_id:
        return None
    for interface in REGISTRY:
        if interface.interface_id == interface_id:
            return interface
    return None


def interface_for_endpoint(endpoint: Optional[str]):
    """The interface that owns a Flask endpoint, from the registry.

    Identity comes from the registry: a URL is evidence about which endpoint
    served a request, and the endpoint is evidence about which interface owns
    it. Nothing here parses a path to guess at a screen name.
    """
    if not endpoint:
        return None
    for interface in REGISTRY:
        if interface.route == endpoint or endpoint in interface.aliases:
            return interface
    return None


def _fold_component_name(name: str) -> str:
    """A component name, compared by letters and digits alone.

    `ActionToolbar`, `action-toolbar` and `action_toolbar` are one name written
    three ways; the library keys the third. Folding is not matching: a name that
    folds to nothing in the library is still reported as unknown.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_component(component_id: Optional[str] = None,
                      classes: Sequence[str] = ()) -> Dict[str, Any]:
    """Which component renders an element - declared, third-party, or unknown.

    The name is matched against the component library's declarations (its
    component names and the macros they export), and - only when no name was
    given - against the classes a component declares as its own. A class that
    no component declares and no SYLTHARAE stylesheet defines is UNKNOWN, and
    saying so is the answer; Bootstrap stays third-party.
    """
    library = component_audit.components()
    wanted = (component_id or "").strip()
    if wanted:
        # A page may name a component the way a person reads it ("ActionToolbar")
        # rather than the way the library keys it ("action_toolbar"). That is a
        # spelling of the same name, not a different component, so it is folded
        # before the lookup - and a name that still matches nothing stays
        # Unknown rather than being bent into the nearest thing.
        folded = _fold_component_name(wanted)
        for name, component in library.items():
            if folded in (_fold_component_name(name), f"{_fold_component_name(name)}.html"):
                return {
                    "component_id": name,
                    "component_path": component.path,
                    "component_ownership": component_audit.OWNED,
                    "component_status": RESOLVED,
                    "component_source": f"component declaration ({component.path})",
                }
            if wanted in component.macros or folded in component.macros:
                return {
                    "component_id": name,
                    "component_path": component.path,
                    "component_ownership": component_audit.OWNED,
                    "component_status": RESOLVED,
                    "component_source": (f"macro {wanted} in {component.path}"),
                }
        return {
            "component_id": wanted,
            "component_path": None,
            "component_ownership": component_audit.UNKNOWN,
            "component_status": UNKNOWN,
            "component_source": "no component declares that name",
        }

    if not classes:
        return {
            "component_id": None, "component_path": None,
            "component_ownership": component_audit.UNKNOWN,
            "component_status": NOT_DECLARED,
            "component_source": "the element carries no component metadata",
        }

    # Which component declares these classes *as its own*. A class the bundled
    # third-party stylesheets own (`btn`, `col-md-6`) is a dependency every
    # component shares, so it is never the evidence; and the best match wins,
    # because the longest name a component declares is the most specific thing
    # it said about itself.
    best: Optional[Tuple[int, int, str, List[str]]] = None
    for name, component in library.items():
        declared_here = set(component_audit.declared_classes(component))
        owned = [c for c in set(classes) & declared_here
                 if component_audit.classify_class(c, declared_here
                                                   ).ownership == component_audit.OWNED
                 and c not in component_audit.third_party_classes()]
        if not owned:
            continue
        score = (len(owned), max(len(c) for c in owned))
        if best is None or score > best[0:2]:
            best = (score[0], score[1], name, sorted(owned))
    if best is not None:
        name = best[2]
        component = library[name]
        return {
            "component_id": name,
            "component_path": component.path,
            "component_ownership": component_audit.OWNED,
            "component_status": RESOLVED,
            "component_source": (f"{', '.join(best[3])} is declared by "
                                 f"{component.path}"),
        }

    classified = [component_audit.classify_class(name) for name in classes]
    unknown = [entry.name for entry in classified
               if entry.ownership == component_audit.UNKNOWN]
    if unknown:
        return {
            "component_id": None, "component_path": None,
            "component_ownership": component_audit.UNKNOWN,
            "component_status": UNKNOWN,
            "component_source": ("nothing defines " + ", ".join(sorted(unknown))),
        }
    third_party = sorted({entry.source for entry in classified
                          if entry.ownership == component_audit.THIRD_PARTY})
    return {
        "component_id": None,
        "component_path": None,
        "component_ownership": component_audit.THIRD_PARTY,
        "component_status": NOT_APPLICABLE,
        "component_source": ("no SYLTHARAE component; " + ", ".join(third_party)
                             if third_party else "no SYLTHARAE component owns it"),
    }


def _translation(action, interface_id: Optional[str],
                 locale: Optional[str]) -> Dict[str, Any]:
    """What the label is, where it comes from, and whether it is translated."""
    from .coverage import catalog, hardcoded_source_strings

    key = action.label_key
    source = action.source
    if not locale:
        return {"translation_key": key, "translation_source": source,
                "translation_locale": None, "translation_rendered": None,
                "translation_status": NOT_DECLARED,
                "translation_note": "no locale was asked about"}

    catalogue = catalog(locale)
    rendered = catalogue.translation(key, source)
    if locale == "en":
        status = "source_language"
        note = "English is the language the source strings are written in"
    elif rendered is None:
        status = UNAVAILABLE
        note = "the catalog has neither the key nor the source string"
    elif rendered.strip() == source.strip():
        status = "fallback"
        note = "the catalog carries the English source, untranslated"
    elif key in catalogue.messages:
        status = RESOLVED
        note = "translated by semantic key"
    else:
        status = "resolved_by_source"
        note = "translated by English source string; the key is not in the catalog yet"

    if interface_id and source in hardcoded_source_strings(interface_id):
        note = (note + "; the contract still keys this string by its source text")
    return {"translation_key": key, "translation_source": source,
            "translation_locale": locale, "translation_rendered": rendered,
            "translation_status": status, "translation_note": note}


def _binding(action_id: str, interface_id: Optional[str],
             routes: Any) -> Dict[str, Any]:
    """Where the action is bound, and whether that binding resolves."""
    bound = binding_layer.by_action()
    items = bound.get(action_id, ())
    observed_kind = None
    observed_source = None
    if items:
        first = items[0]
        observed_kind = {"attribute": "markup", "literal": "page_script",
                         "prepared": "prepared_in_python"}.get(
                             first.get("kind", "attribute"), first.get("kind"))
        observed_source = first["file"]
        if len(items) > 1:
            observed_source = "{} (+{} more)".format(observed_source, len(items) - 1)

    problem = None
    status = RESOLVED
    if items and routes is not None:
        report = binding_layer.check(routes, list(items), known_actions=[action_id])
        if report["dangling"]:
            problem = "no_matching_endpoint"
            status = UNRESOLVED
    elif items and routes is None:
        status = NOT_CHECKED

    if not items:
        if interface_id is None:
            return {"binding_kind": None, "binding_source": None,
                    "binding_status": NOT_APPLICABLE, "binding_problem": None,
                    "binding_observed": None}
        from .declarations import SCREEN_ACTIONS

        presented = action_id in SCREEN_ACTIONS.get(interface_id, ())
        return {
            "binding_kind": None, "binding_source": None,
            "binding_status": UNRESOLVED if presented else NOT_APPLICABLE,
            "binding_problem": "not_bound" if presented else None,
            "binding_observed": None,
        }
    return {"binding_kind": observed_kind, "binding_source": observed_source,
            "binding_status": status, "binding_problem": problem,
            "binding_observed": True}


def inspect(selection: Selection,
            *,
            endpoint: Optional[str] = None,
            routes: Any = None,
            locale: Optional[str] = None,
            authorization: Optional[str] = None,
            roles: Iterable[str] = (),
            ) -> ScreenInspection:
    """Join one element with the architecture that owns it.

    ``endpoint`` is the Flask endpoint that served the request, used only when
    the element did not name an interface. ``routes`` is
    ``app.url_map.iter_rules()`` when a checker is available. ``authorization``
    is an answer the server's own authorisation already gave - it is carried,
    never computed here.
    """
    problems: List[str] = []

    # ---- the screen -------------------------------------------------------
    interface = interface_by_id(selection.interface_id)
    if interface is None and selection.interface_id:
        problems.append(
            f"{selection.interface_id!r} is not an interface the registry knows")
    if interface is None:
        interface = interface_for_endpoint(endpoint)
    if interface is None and endpoint:
        problems.append(
            f"the endpoint {endpoint!r} belongs to no registered interface")
    if interface is None:
        raise InspectorError(
            "interface_unknown",
            "No interface could be resolved for this element: the element named "
            "no interface, and the request did not come from a registered route.")

    from .contract import contract

    screen = contract(interface.interface_id)
    help_topic = interface.help_topic
    help_status = RESOLVED if help_topic else NOT_DECLARED
    shortcut = interface.keyboard_shortcut
    shortcut_status = RESOLVED if shortcut else NOT_DECLARED
    if not help_topic:
        problems.append(f"{interface.interface_id} declares no help topic")
    if not shortcut:
        problems.append(f"{interface.interface_id} declares no keyboard shortcut")

    # ---- the component ----------------------------------------------------
    component = resolve_component(selection.component_id, selection.classes)

    # ---- the action -------------------------------------------------------
    action = registered_action(selection.action_id) if selection.action_id else None
    if selection.action_id and action is None:
        problems.append(
            f"{selection.action_id!r} is not in the Action Registry")
    action_status = NOT_APPLICABLE
    if selection.action_id:
        action_status = RESOLVED if action else UNKNOWN

    scope = permission = destructive = confirmation_key = None
    execution = None
    execution_status = NOT_APPLICABLE
    confirmation_required = None
    visibility = None
    translation: Dict[str, Any] = {
        "translation_key": None, "translation_source": None,
        "translation_locale": locale, "translation_rendered": None,
        "translation_status": NOT_APPLICABLE if not selection.action_id else NOT_DECLARED,
        "translation_note": None,
    }
    binding: Dict[str, Any] = {
        "binding_kind": selection.binding_kind, "binding_source": selection.binding_source,
        "binding_status": NOT_APPLICABLE if not selection.action_id else NOT_DECLARED,
        "binding_problem": None, "binding_observed": None,
    }
    state = disabled_reason = hidden_reason = None

    if action is not None:
        scope = action.scope
        permission = action.permission
        destructive = action.destructive
        confirmation_required = action.confirmation_required
        confirmation_key = action.confirmation
        visibility = action.visibility
        execution = action.execution
        execution_status = RESOLVED if action.execution else (
            "not_built" if not action.built else NOT_DECLARED)
        if action.permission is None:
            problems.append(f"{action.action_id} declares no permission domain")
        if not action.built:
            problems.append(
                f"{action.action_id} is declared and not built: the registry "
                "names no operation for it")
        translation = _translation(action, interface.interface_id, locale)
        if selection.binding_kind or selection.binding_source:
            # The element said where its binding lives; that is reported as
            # observed and still checked against the scan.
            observed = _binding(action.action_id, interface.interface_id, routes)
            binding = dict(observed)
            binding["binding_observed"] = True
        else:
            binding = _binding(action.action_id, interface.interface_id, routes)
        if binding["binding_problem"] == "not_bound":
            problems.append(
                f"{action.action_id} is presented by {interface.interface_id} and "
                "nothing in the product binds it")
        elif binding["binding_problem"] == "no_matching_endpoint":
            problems.append(
                f"{action.action_id} is bound to an address the application does "
                "not serve")

        # The one state model, asked the questions the element can answer. No
        # permission decision is invented: `permitted` stays at the registry's
        # own default (nothing has asserted a denial), and the authorisation
        # answer the server gave is reported separately below.
        if action.built:
            derived = ActionState.derive(
                action,
                selected=selection.selected or 0,
                permitted=True,
                running=bool(selection.running),
                unavailable=None,
            )
            state = derived.state
            disabled_reason = derived.disabled_reason
            hidden_reason = derived.hidden_reason
        else:
            derived = ActionState.derive(action, selected=selection.selected or 0)
            state = derived.state
            disabled_reason = derived.disabled_reason
            hidden_reason = derived.hidden_reason

        if selection.outcome == "success":
            state = "success"
        elif selection.outcome == "failed":
            state = "failed"

    # ---- selection --------------------------------------------------------
    selection_state = None
    if selection.selected is not None or selection.total is not None:
        if not selection.selected:
            selection_state = "none"
        elif selection.total is None:
            selection_state = "partial" if selection.selected else "none"
        elif selection.selected >= selection.total:
            selection_state = "all"
        else:
            selection_state = "partial"

    return ScreenInspection(
        role=selection.role,
        classes=selection.classes,
        interface_id=interface.interface_id,
        interface_name=interface.name,
        interface_domain=str(interface.domain),
        interface_status=str(interface.status),
        interface_kind=str(interface.kind),
        interface_enabled=interface.default_enabled,
        interface_route=interface.route,
        interface_help_topic=help_topic,
        interface_help_status=help_status,
        interface_shortcut=shortcut,
        interface_shortcut_status=shortcut_status,
        interface_status_note=(RESOLVED if screen and screen.screen.declared
                              else "derived"),
        component_id=component["component_id"],
        component_path=component["component_path"],
        component_ownership=component["component_ownership"],
        component_status=component["component_status"],
        component_source=component["component_source"],
        action_id=action.action_id if action else selection.action_id,
        action_status=action_status,
        action_scope=scope,
        action_permission=permission,
        action_destructive=destructive,
        action_confirmation_required=confirmation_required,
        action_confirmation_key=confirmation_key,
        action_execution=execution,
        action_execution_status=execution_status,
        action_visibility=visibility,
        state=state,
        disabled_reason=disabled_reason,
        hidden_reason=hidden_reason,
        translation_key=translation["translation_key"],
        translation_source=translation["translation_source"],
        translation_locale=translation["translation_locale"],
        translation_rendered=translation["translation_rendered"],
        translation_status=translation["translation_status"],
        translation_note=translation["translation_note"],
        binding_kind=binding["binding_kind"],
        binding_source=binding["binding_source"],
        binding_status=binding["binding_status"],
        binding_problem=binding["binding_problem"],
        binding_observed=binding["binding_observed"],
        authorization=authorization,
        authorization_roles=tuple(sorted(roles or ())),
        selected=selection.selected,
        total=selection.total,
        selection_state=selection_state,
        running=bool(selection.running),
        outcome=selection.outcome,
        job_id=selection.job_id,
        correlation_id=selection.correlation_id,
        observed_text=selection.observed_text,
        problems=tuple(problems),
    )


# ---------------------------------------------------------------------------
# What the Inspector can be asked about (the evidence, measured)
# ---------------------------------------------------------------------------
def catalogue() -> Dict[str, Any]:
    """The Inspectable: what exists to inspect, counted from the sources.

    This is what the evidence document reports, and the reason it can report it
    is that every one of these numbers comes from a registry, a library or a
    scan rather than from an intention.
    """
    from .action_registry import not_built, registered

    library = component_audit.components()
    scanned = binding_layer.scan()
    kinds: Dict[str, int] = {}
    for item in scanned:
        kind = item.get("kind", "attribute")
        kinds[kind] = kinds.get(kind, 0) + 1

    undeclared_help = [i.interface_id for i in REGISTRY if not i.help_topic]
    undeclared_shortcut = [i.interface_id for i in REGISTRY
                           if not i.keyboard_shortcut]
    from .declarations import SCREEN_ACTIONS

    presented = {action_id for ids in SCREEN_ACTIONS.values() for action_id in ids}
    bound = set(binding_layer.by_action())
    unbound = sorted(action_id for action_id in presented if action_id not in bound)

    return {
        "interfaces": len([i for i in REGISTRY]),
        "interfaces_navigable": len([i for i in REGISTRY if i.navigable]),
        "interfaces_without_help": sorted(undeclared_help),
        "interfaces_without_shortcut": sorted(undeclared_shortcut),
        "components": len(library),
        "components_declared": sum(1 for c in library.values() if c.states),
        "components_undeclared": sorted(component_audit.undeclared_files()),
        # A class no SYLTHARAE stylesheet defines and no component declares:
        # the one ownership question with a definite answer.
        "unknown_classes": component_audit.unknown_classes(),
        "actions": len(registered()),
        "actions_not_built": [item.action_id for item in not_built()],
        "actions_without_permission": sorted(
            item.action_id for item in registered() if not item.permission),
        "actions_presented": len(presented),
        "actions_bound": len(bound),
        "actions_presented_but_unbound": unbound,
        "bindings": len(scanned),
        "bindings_by_kind": kinds,
        "dom_attributes": dict(DOM_ATTRIBUTES),
        "dom_attribute_hints": dict(DOM_ATTRIBUTE_HINTS),
        "field_statuses": list(FIELD_STATUSES),
    }


# ---------------------------------------------------------------------------
# The panel: the same answer, in the order a person reads it
# ---------------------------------------------------------------------------
#: The fields the panel shows, in order, and the key each one answers to. The
#: server fills them from one inspection; a field whose value nobody declared
#: carries its status instead of a plausible-looking guess.
PANEL_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("interface", "interface_id"),
    ("interface_name", "interface_name"),
    ("component", "component_id"),
    ("action", "action_id"),
    ("role", "role"),
    ("scope", "action_scope"),
    ("permission", "action_permission"),
    ("authorization", "authorization"),
    ("state", "state"),
    ("disabled_reason", "disabled_reason"),
    ("hidden_reason", "hidden_reason"),
    ("confirmation", "action_confirmation_key"),
    ("translation_key", "translation_key"),
    ("source_text", "translation_source"),
    ("rendered_text", "translation_rendered"),
    ("help", "interface_help_topic"),
    ("shortcut", "interface_shortcut"),
    ("binding_kind", "binding_kind"),
    ("binding_source", "binding_source"),
    ("binding", "binding_status"),
    ("execution", "action_execution"),
    ("selection", "selection_state"),
    ("job", "job_id"),
    ("correlation_id", "correlation_id"),
)

#: Which field carries the status of which value, when the value is absent -
#: the reason a reader is looking at a blank: nothing declares it, it does not
#: apply here, it is unavailable, or the product has not built it.
PANEL_STATUS_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("interface_status", "interface_status_note"),
    ("component_status", "component_status"),
    ("action_status", "action_status"),
    ("translation_status", "translation_status"),
    ("binding_status", "binding_status"),
    ("execution_status", "action_execution_status"),
)


def _field(key: str, label: Optional[str], value: Any, status: str,
           status_labels: Dict[str, str], note: Optional[str] = None) -> Dict[str, Any]:
    """One row of the panel: a value, or the reason there is not one."""
    if value is None:
        return {"key": key, "label": label or key, "value": None,
                "status": status, "status_text": status_labels.get(status, status),
                "note": note}
    return {"key": key, "label": label or key, "value": value, "status": status,
            "status_text": None, "note": note}


def panel(inspection: ScreenInspection,
          labels: Dict[str, str],
          status_labels: Dict[str, str],
          reason_labels: Optional[Dict[str, str]] = None,
          problem_labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The inspection as fields a panel can draw.

    ``labels`` and ``status_labels`` are supplied by the caller, which is the
    layer that has a locale to translate them in: this module describes an
    interface, and a description that knows how to speak has stopped being one.

    A field whose value nobody declares carries its *status* in the value's
    place - "Not declared", "Unknown", "Not determined" - so the panel never
    shows a blank where the answer is "nothing here says".
    """
    values = inspection.to_dict()
    reason_labels = reason_labels or {}
    problem_labels = problem_labels or {}
    fields: List[Dict[str, Any]] = []

    def status_for(key: str, value: Any) -> str:
        if key in ("interface", "interface_name"):
            return inspection.interface_status_note
        if key == "component":
            return inspection.component_status if value is None else RESOLVED
        if key == "action":
            return inspection.action_status
        if key == "binding":
            return (UNRESOLVED if inspection.binding_problem
                    else inspection.binding_status)
        if key == "execution":
            return inspection.action_execution_status
        if key == "translation_key" or key == "source_text" or key == "rendered_text":
            return (translation_status_of(inspection)
                    if inspection.action_id else NOT_APPLICABLE)
        if key == "help":
            return inspection.interface_help_status
        if key == "shortcut":
            return inspection.interface_shortcut_status
        if key == "authorization":
            return RESOLVED if value else NOT_CHECKED
        if key == "state":
            return inspection.action_status if inspection.action_id else NOT_APPLICABLE
        if key in ("scope", "permission", "confirmation", "disabled_reason",
                   "hidden_reason", "job", "correlation_id"):
            if not inspection.action_id:
                return NOT_APPLICABLE
            if key in ("disabled_reason", "hidden_reason"):
                return RESOLVED if value else NOT_APPLICABLE
        return RESOLVED if value is not None else NOT_DECLARED

    notes = {
        "interface": inspection.interface_name,
        "component": inspection.component_source,
        "authorization": inspection.authorization_note,
        "rendered_text": (inspection.translation_note
                          if inspection.action_id else None),
        # The reason the binding is not resolved, in the reader's words - a
        # token like `not_bound` is the report's vocabulary, not the panel's.
        "binding": (problem_labels.get(inspection.binding_problem,
                                       inspection.binding_problem)
                    if inspection.binding_problem else None),
        "state": None,
    }

    for key, attribute in PANEL_FIELDS:
        if key not in labels:
            continue
        value = values.get(attribute)
        status = status_for(key, value)
        if key in ("disabled_reason", "hidden_reason") and value:
            # The vocabulary's own word for the reason, not its key.
            value = reason_labels.get(value, value)
        if key == "binding":
            # The row answers one question - did this binding resolve - and the
            # kind and source are their own fields.
            value = status_labels.get(inspection.binding_status,
                                      inspection.binding_status)
        if key == "state" and value:
            notes["state"] = reason_labels.get(
                inspection.disabled_reason or inspection.hidden_reason or "",
                inspection.disabled_reason or inspection.hidden_reason)
        if key == "authorization" and value is None:
            value = status_labels.get(NOT_CHECKED, NOT_CHECKED)
            status = NOT_CHECKED
        fields.append(_field(key, labels.get(key, key), value, status,
                             status_labels, notes.get(key)))

    for key, attribute in PANEL_STATUS_FIELDS:
        if key not in labels:
            continue
        fields.append(_field(key, labels[key], status_labels.get(values.get(attribute), values.get(attribute)),
                             values.get(attribute) or NOT_CHECKED, status_labels))

    return {
        "title": labels.get("_title", "Screen Inspector"),
        "fields": fields,
        "problems": list(inspection.problems),
        "status_labels": dict(status_labels),
    }


def translation_status_of(inspection: ScreenInspection) -> str:
    """The translation status, or why there is none to report."""
    if not inspection.action_id:
        return NOT_APPLICABLE
    return inspection.translation_status


__all__ = [
    "BINDING_PROBLEMS",
    "DOM_ATTRIBUTES",
    "DOM_ATTRIBUTE_HINTS",
    "ELEMENT_ROLES",
    "FIELD_STATUSES",
    "InspectorError",
    "NOT_APPLICABLE",
    "NOT_CHECKED",
    "NOT_DECLARED",
    "RESOLVED",
    "RESOLUTION_STATUSES",
    "ScreenInspection",
    "Selection",
    "UNAVAILABLE",
    "UNKNOWN",
    "UNRESOLVED",
    "PANEL_FIELDS",
    "catalogue",
    "inspect",
    "panel",
    "interface_by_id",
    "interface_for_endpoint",
    "redact_observed",
    "resolve_component",
]
