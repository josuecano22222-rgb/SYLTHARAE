"""A screen's actions, assembled from the registry plus that screen's bindings.

The Action Registry says what an action **is**: its id, its label key and
source, its icon, its scope, the permission domain it belongs to, whether it is
destructive, the confirmation key it asks with, how it loads, and the opaque
operation reference that will one day run it. It deliberately does not say what
any particular screen does with it.

A screen knows that part: the endpoint that performs the action *on this
record*, the question it asks in the reader's language, and whether the record
currently allows it - an original that is no longer on disk cannot be shown, an
operation nobody has built yet cannot be offered, and a request the server would
refuse should not be drawn.

This module is the join, and the only one. It:

* refuses an action that is not registered;
* refuses an action registered for another interface, or for another scope;
* refuses two bindings for one action, so one thing has one owner;
* refuses a state reason outside the declared vocabulary (``DISABLED_REASONS``,
  ``HIDDEN_REASONS``) - "nothing selected" and "no permission" never collapse
  into one grey button because a page typed a different word for one of them;
* refuses a destructive action whose question does not say what will happen;
* draws nothing for a registered action the screen has not bound, so a screen
  cannot show a button that does nothing;
* keeps the registry's execution reference out of the markup: what a screen
  carries is the endpoint *it* owns, never the operation name.

The state of each button is **derived**, not decided here: :class:`ActionState`
from ``model`` turns the facts the screen supplies (what the server said about
permission, how many records this action applies to, a named reason it cannot be
used) into the one state the vocabulary allows. This module only arranges the
result for a template, and it contains no endpoint of its own and imports
nothing that can perform anything.

What happens when a button is pressed is the page's business, and the server
authorises it independently of anything decided here - a hidden action is not a
secured one.

The words in a binding - titles, buttons, what is being acted on - are the
screen's own, already translated. The registry's contribution to the label is
its English source, which is still the catalog's key today; the migration to
semantic keys is measured by ``coverage`` rather than claimed here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from .action_registry import action as registered_action
from .action_registry import for_interface
from .model import (
    ActionState,
    ContractError,
    DISABLED_REASONS,
    HIDDEN_REASONS,
    LOADING_STYLES,
)

#: What a screen may say about one action. Everything here is presentation or
#: context; nothing here is a rule the server would honour. `endpoint` is the
#: screen's own address for the operation, not the registry's operation name.
BINDING_KEYS = frozenset({
    "href", "endpoint", "method", "target", "group", "permitted", "selected",
    "disabled_reason", "disabled_label", "hidden_reason", "confirmation",
    "success_message", "failure_message",
})

#: The question a destructive action asks, in the page's words.
CONFIRMATION_KEYS = frozenset({
    "title", "message", "confirm_label", "cancel_label", "typed",
})

#: Where a button is drawn. `overflow` is the menu a surface collapses into
#: when it has more actions than it shows; the screen decides which actions
#: belong there, because only the screen knows its own layout.
GROUPS: Tuple[str, ...] = ("primary", "secondary", "overflow")

#: The default method for a bound endpoint. Writes are the common case, and a
#: binding that means something else says so.
DEFAULT_METHOD = "POST"


class ActionBindingError(ContractError):
    """A screen asked for something the action layer cannot express."""


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _selected_for(definition, binding: Mapping[str, Any]) -> int:
    """How many records this action applies to on the screen that bound it.

    A record action is offered for one record - the one being read - unless the
    screen says otherwise; a selection or bulk action is offered for the
    selection, and a screen that has one of those says how many are selected.
    """
    if "selected" in binding:
        selected = binding.get("selected")
        if not isinstance(selected, int) or isinstance(selected, bool) or selected < 0:
            raise ActionBindingError(
                f"{definition.action_id}: selected must be a count, not "
                f"{selected!r}")
        return selected
    return 1 if definition.scope == "record" else 0


def _check_binding(definition, binding: Mapping[str, Any]) -> None:
    """Everything a screen is not allowed to get wrong, checked once."""
    unknown = sorted(set(binding) - BINDING_KEYS)
    if unknown:
        raise ActionBindingError(
            f"{definition.action_id}: unknown binding key(s) {unknown} - a "
            f"screen may say {sorted(BINDING_KEYS)}")

    group = binding.get("group", "secondary")
    if group not in GROUPS:
        raise ActionBindingError(
            f"{definition.action_id}: unknown group {group!r}; a button is one "
            f"of {list(GROUPS)}")

    reason = binding.get("disabled_reason")
    if reason is not None and reason not in DISABLED_REASONS:
        raise ActionBindingError(
            f"{definition.action_id}: unknown disabled reason {reason!r} - "
            f"the vocabulary is {list(DISABLED_REASONS)}")

    hidden = binding.get("hidden_reason")
    if hidden is not None and hidden not in HIDDEN_REASONS:
        raise ActionBindingError(
            f"{definition.action_id}: unknown hidden reason {hidden!r} - the "
            f"vocabulary is {list(HIDDEN_REASONS)}")

    if binding.get("disabled_label") and not reason:
        raise ActionBindingError(
            f"{definition.action_id}: a disabled label without a reason says "
            "nothing a screen-reader user can act on")

    drawn = (definition.built
             and binding.get("permitted", True) is not False
             and hidden is None)
    if not drawn:
        # Nothing is drawn, so nothing about how it would run has to be said -
        # and for an action the product has not built, there is nothing to say.
        return

    if not binding.get("href") and not binding.get("endpoint"):
        # A button with nothing to do is worse than no button: it teaches the
        # reader that pressing things does not work.
        raise ActionBindingError(
            f"{definition.action_id}: a binding must carry an href (a "
            "navigation) or an endpoint (an operation)")

    if binding.get("href") and binding.get("endpoint"):
        raise ActionBindingError(
            f"{definition.action_id}: a binding carries an href or an "
            "endpoint, not both - a link that also posts is a question nobody "
            "can answer")

    if binding.get("method") and not binding.get("endpoint"):
        raise ActionBindingError(
            f"{definition.action_id}: a method without an endpoint says "
            "nothing")

    confirmation = binding.get("confirmation")
    if confirmation is None:
        return
    if not isinstance(confirmation, Mapping):
        raise ActionBindingError(
            f"{definition.action_id}: confirmation must be a mapping")
    unknown = sorted(set(confirmation) - CONFIRMATION_KEYS)
    if unknown:
        raise ActionBindingError(
            f"{definition.action_id}: unknown confirmation key(s) {unknown}; "
            f"the question carries {sorted(CONFIRMATION_KEYS)}")
    if definition.destructive and not confirmation.get("message"):
        raise ActionBindingError(
            f"{definition.action_id} is destructive: the question has no "
            "message, so the reader is not told what will happen")
    if confirmation.get("typed") and not definition.destructive:
        raise ActionBindingError(
            f"{definition.action_id}: a typed confirmation is for destructive "
            "actions; this one is not destructive")


def state_for(definition, binding: Mapping[str, Any]) -> ActionState:
    """The one state this action is in, from the facts the screen supplied."""
    permitted = binding.get("permitted", True) is not False
    state = ActionState.derive(
        definition,
        selected=_selected_for(definition, binding),
        permitted=permitted,
        unavailable=_text(binding.get("disabled_reason")),
    )
    hidden = _text(binding.get("hidden_reason"))
    if hidden and state.state in ("available", "disabled"):
        # Not offered, and for a reason of its own - an operation nobody has
        # built yet, a screen this action does not apply to.
        return ActionState(
            action_id=definition.action_id, state="hidden",
            hidden_reason=hidden,
            confirmation_required=definition.confirmation_required)
    return state


def prepare(definition, binding: Mapping[str, Any]) -> Dict[str, Any]:
    """One action, as the surface draws it: registry facts + screen context."""
    _check_binding(definition, binding)
    state = state_for(definition, binding)
    confirmation = dict(binding.get("confirmation") or {})
    payload: Dict[str, Any] = {
        # From the registry - the screen does not restate any of this.
        "action_id": definition.action_id,
        "label": definition.source,
        "label_key": definition.label_key,
        "icon": definition.icon,
        "scope": definition.scope,
        "permission": definition.permission,
        "destructive": definition.destructive,
        "confirmation": definition.confirmation,
        "confirmation_required": definition.confirmation_required,
        "loading": definition.loading,
        "execution": definition.execution,
        # From the screen - what it does here, and what it knows about now.
        "group": binding.get("group", "secondary"),
        "href": _text(binding.get("href")),
        "endpoint": _text(binding.get("endpoint")),
        "method": (_text(binding.get("method"))
                   or (DEFAULT_METHOD if binding.get("endpoint") else None)),
        "target": _text(binding.get("target")),
        "state": state.state,
        "disabled_reason": state.disabled_reason,
        "disabled_label": _text(binding.get("disabled_label")),
        "hidden_reason": state.hidden_reason,
        "confirmation_title": _text(confirmation.get("title")),
        "confirmation_message": _text(confirmation.get("message")),
        "confirm_label": _text(confirmation.get("confirm_label")),
        "cancel_label": _text(confirmation.get("cancel_label")),
        "typed_confirmation": _text(confirmation.get("typed")),
        # What the reader is told when it ends. Words, never a technical detail.
        "success_message": _text(binding.get("success_message")),
        "failure_message": _text(binding.get("failure_message")),
    }
    # A disabled action keeps its place and says why; a hidden one is not drawn
    # at all. Neither is a security boundary - the endpoint decides on its own,
    # every time.
    payload["drawn"] = state.state != "hidden"
    payload["enabled"] = state.state == "available"
    return payload


def record_actions(interface_id: str,
                   bindings: Mapping[str, Mapping[str, Any]],
                   scope: str = "record") -> Dict[str, Any]:
    """The actions one screen offers for one scope, grouped for a surface.

    ``bindings`` maps action id -> what this screen does with it. An action the
    screen does not bind is left out and listed in ``skipped``; an action the
    screen hides is listed in ``hidden``. Both are reported rather than implied,
    because "not offered here" and "declared" are different facts and an audit
    that cannot tell them apart is not an audit.
    """
    known = {item.action_id: item for item in for_interface(interface_id)
             if item.scope == scope}

    unknown = sorted(set(bindings) - set(known))
    if unknown:
        raise ActionBindingError(
            f"{interface_id}: no {scope} action is registered with "
            f"{', '.join(unknown)}; a screen cannot invent an action, and an "
            "action registered for another screen is not this screen's to bind")

    groups: Dict[str, List[Dict[str, Any]]] = {group: [] for group in GROUPS}
    hidden: List[Dict[str, Any]] = []
    skipped: List[str] = []

    for action_id, definition in known.items():
        binding = bindings.get(action_id)
        if binding is None:
            skipped.append(action_id)
            continue
        payload = prepare(definition, binding)
        if not payload["drawn"]:
            hidden.append(payload)
            continue
        groups[payload["group"]].append(payload)

    return {
        "interface_id": interface_id,
        "scope": scope,
        "primary": groups["primary"],
        "secondary": groups["secondary"],
        "overflow": groups["overflow"],
        "hidden": hidden,
        "skipped": skipped,
        "drawn": sum(len(groups[group]) for group in GROUPS),
    }


def loading_styles() -> Tuple[str, ...]:
    """The loading styles a registry definition may declare."""
    return LOADING_STYLES


__all__ = [
    "ActionBindingError",
    "BINDING_KEYS",
    "CONFIRMATION_KEYS",
    "GROUPS",
    "loading_styles",
    "prepare",
    "record_actions",
    "registered_action",
    "state_for",
]
