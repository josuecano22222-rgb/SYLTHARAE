"""Building the Experience Contract from the sources of truth that exist.

Nothing here is a second registry. The registry says what an interface is: its
id, name, description, domain, route, lifecycle, role, dependencies, help topic
and shortcut. The declarations say what somebody has described about how it is
presented. This module joins them and states plainly which of the two a given
part of a screen came from.

Where nothing is declared, the contract still answers - the shell needs a title
and a navigation entry for every screen - but it answers with the registry's own
words and marks the screen ``declared = False``, so the difference between
"described" and "derived" is visible in the audit rather than smoothed over.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.interfaces import REGISTRY, Interface, InterfaceKind
from core.interfaces.lifecycle import policy

from . import declarations
from .model import (
    ExperienceContract,
    HelpDefinition,
    LayoutDefinition,
    NavigationDefinition,
    ScreenConfiguration,
    ShortcutDefinition,
    TranslationDefinition,
)


def _translation(key: str, source: str, screen: str, context: str,
                 description: Optional[str] = None) -> TranslationDefinition:
    from .model import placeholders

    return TranslationDefinition(
        key=key, source=source, context=context, screen=screen,
        description=description, placeholders=placeholders(source))


def _navigation_order() -> Dict[str, int]:
    """Position of each interface inside its domain, from registry order."""
    positions: Dict[str, int] = {}
    counters: Dict[str, int] = {}
    for interface in REGISTRY:
        if not interface.navigable:
            continue
        domain = str(interface.domain)
        counters[domain] = counters.get(domain, 0) + 1
        positions[interface.interface_id] = counters[domain]
    return positions


def build_contract(interface: Interface,
                   order: Optional[Dict[str, int]] = None) -> ExperienceContract:
    """The contract for one interface."""
    interface_id = interface.interface_id
    order = order if order is not None else _navigation_order()

    declared_title = declarations.title(interface_id)
    title_source = declared_title or interface.name
    title_key = f"screen.{interface_id}.title"

    description_key = (f"screen.{interface_id}.description"
                       if interface.description else None)

    screen = ScreenConfiguration(
        interface_id=interface_id,
        title_key=title_key,
        source=title_source,
        description_key=description_key,
        icon=interface.icon,
        navigation=NavigationDefinition(
            domain=str(interface.domain),
            label_key=f"nav.{interface_id}.label",
            source=interface.name,
            icon=interface.icon,
            order=order.get(interface_id, 0),
            visible=interface.navigable,
            required_role=interface.required_role,
            feature=interface.feature_flag,
        ),
        actions=declarations.actions(interface_id),
        fields=declarations.fields(interface_id),
        columns=declarations.columns(interface_id),
        filters=declarations.filters(interface_id),
        states=declarations.states(interface_id, prefix=interface_id),
        help=_help(interface, interface_id),
        shortcuts=_shortcuts(interface, interface_id),
        layout=declarations.layout(interface_id),
        declared=interface_id in declarations.DECLARED_SCREENS,
    )

    screen = _with_translations(screen, interface)

    return ExperienceContract(
        interface_id=interface_id,
        name=interface.name,
        description=interface.description,
        domain=str(interface.domain),
        kind=str(interface.kind),
        status=str(interface.status),
        navigable=interface.navigable,
        route=interface.route,
        endpoints=interface.endpoints,
        required_role=interface.required_role,
        dependencies=interface.dependencies,
        settings=interface.settings,
        feature_flag=interface.feature_flag,
        screen=screen,
    )


def _help(interface: Interface, interface_id: str) -> Optional[HelpDefinition]:
    """Declared help first, then the registry's own help topic."""
    declared = declarations.help_definition(interface_id)
    if declared is not None:
        return declared
    if not interface.help_topic:
        return None
    return HelpDefinition(
        topic=interface.help_topic,
        title_key=f"help.{interface_id}.title",
        source=interface.name,
        summary_key=f"help.{interface_id}.summary")


def _shortcuts(interface: Interface,
               interface_id: str) -> Tuple[ShortcutDefinition, ...]:
    """The registry's declared shortcut, pointed at opening the screen."""
    declared = declarations.shortcuts(interface_id)
    if declared:
        return declared
    if not interface.keyboard_shortcut:
        return ()
    return (ShortcutDefinition(shortcut=interface.keyboard_shortcut,
                               action_key=f"nav.{interface_id}.label"),)


def _with_translations(screen: ScreenConfiguration,
                       interface: Interface) -> ScreenConfiguration:
    """Every string a screen shows, with the key a translator edits it by."""
    from dataclasses import replace

    entries: List[TranslationDefinition] = []

    def add(key: str, source: str, context: str, description: Optional[str] = None):
        if not source:
            return
        if any(entry.key == key for entry in entries):
            return
        entries.append(_translation(key, source, interface.interface_id,
                                    context, description))

    add(screen.title_key, screen.source, "title")
    if screen.description_key:
        add(screen.description_key, interface.description, "description")
    if screen.navigation:
        add(screen.navigation.label_key, screen.navigation.source, "navigation")
    for action in screen.actions:
        add(action.label_key, action.source, "action")
        if action.confirmation:
            add(action.confirmation,
                f"{action.source}?", "action",
                description=f"Confirmation shown before: {action.source}")
    for column in screen.columns:
        add(column.label_key, column.source, "column")
    for definition in screen.filters:
        add(definition.label_key, definition.source, "filter")
    for definition in screen.fields:
        add(definition.label_key, definition.source, "field")
    for state in screen.states:
        add(state.title_key, state.source, f"state:{state.state}")
        if state.message_key:
            add(state.message_key, state.source, f"state:{state.state}")
    if screen.help:
        add(screen.help.title_key, screen.help.source, "help")
        if screen.help.summary_key:
            add(screen.help.summary_key, screen.help.source, "help")
    for shortcut in screen.shortcuts:
        add(shortcut.action_key,
            f"Open {interface.name}", "shortcut")

    return replace(screen, translations=tuple(entries))


def contracts() -> Tuple[ExperienceContract, ...]:
    """Every registered interface, in registry order."""
    order = _navigation_order()
    return tuple(build_contract(interface, order) for interface in REGISTRY)


def contract(interface_id: str) -> Optional[ExperienceContract]:
    for candidate in contracts():
        if candidate.interface_id == interface_id:
            return candidate
    return None


def translation_index() -> Dict[str, TranslationDefinition]:
    """Every key every screen needs, keyed by key.

    Two screens needing the same key is normal; needing it with two different
    source strings is not, and ``validation`` reports that as a conflict.
    """
    index: Dict[str, TranslationDefinition] = {}
    for item in contracts():
        for entry in item.screen.translations:
            index.setdefault(entry.key, entry)
    return index


def screens_without_experience() -> Tuple[str, ...]:
    """Registered page interfaces nobody has described yet.

    The honest gap: these have a derived contract, so they work, but no
    columns, actions or help have been decided for them.
    """
    return tuple(
        item.interface_id for item in contracts()
        if not item.screen.declared
        and item.kind == InterfaceKind.PAGE.value
        and item.navigable
        and policy_status(item) != "RETIRED"
    )


def policy_status(item: ExperienceContract) -> str:
    return str(policy(item.status).status if hasattr(policy(item.status), "status")
               else item.status)


def counts() -> Dict[str, int]:
    """Machine-generated counts, for the API, the doc and the studio."""
    all_contracts = contracts()
    declared = [c for c in all_contracts if c.screen.declared]
    return {
        "contracts": len(all_contracts),
        "declared_screens": len(declared),
        "derived_screens": len(all_contracts) - len(declared),
        "actions": sum(len(c.screen.actions) for c in all_contracts),
        "columns": sum(len(c.screen.columns) for c in all_contracts),
        "filters": sum(len(c.screen.filters) for c in all_contracts),
        "fields": sum(len(c.screen.fields) for c in all_contracts),
        "states": sum(len(c.screen.states) for c in all_contracts),
        "translations": len(translation_index()),
        "with_help": sum(1 for c in all_contracts if c.screen.help),
        "with_shortcut": sum(1 for c in all_contracts if c.screen.shortcuts),
        "with_navigation": sum(1 for c in all_contracts if c.screen.navigation),
    }


def contracts_json() -> List[Dict]:
    return [item.to_dict() for item in contracts()]


__all__ = [
    "build_contract",
    "contract",
    "contracts",
    "contracts_json",
    "counts",
    "policy_status",
    "screens_without_experience",
    "translation_index",
]
