"""The Interface model.

An *interface* is a capability the product offers to a person - the file
library, the jobs list, the interface manager. It is not a URL, not a template
and not a route function: those are ways an interface happens to be delivered
today. This separation is what lets the product rename "Batch Analysis" to
"Batch Analysis" without touching a single stored setting, and lets one
interface be served by several endpoints.

The model is deliberately inert. It describes; it does not decide. Authorisation
stays in ``core/security``, values stay in the settings engine, and the URL map
stays in Flask - see ``docs/INTERFACE_REGISTRY.md`` for why that boundary is
enforced by tests rather than convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .domains import Domain, coerce_domain

#: Interface ids: lowercase, machine-safe, no URL or label in them.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

#: Help topics are slugs of the help tree (``analysis/batch``).
HELP_TOPIC_PATTERN = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")

#: Bootstrap icon names.
ICON_PATTERN = re.compile(r"^bi-[a-z0-9-]+$")

#: Settings references are ``category.key`` paths (``processing.max_workers``).
SETTING_REF_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class InterfaceStatus(str, Enum):
    """Whether an interface exists, and how it should be treated.

    This is the answer to "does the product have this?" - a different question
    from "is it switched on for this installation", which is stored state. The
    two are kept apart on purpose: a retired interface can still have a
    historical value in a settings file, and that value must not resurrect it.
    """

    ACTIVE = "ACTIVE"
    EXPERIMENTAL = "EXPERIMENTAL"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"

    def __str__(self) -> str:
        return self.value


class InterfaceKind(str, Enum):
    """How the interface is presented."""

    PAGE = "PAGE"            # has its own page; may appear in navigation
    SECTION = "SECTION"      # lives inside another page (route is None)
    INTERNAL = "INTERNAL"    # a real page, deliberately not advertised
    FEATURE = "FEATURE"      # a behaviour switch with no page of its own

    def __str__(self) -> str:
        return self.value


#: Lifecycle states that mean "this interface is still part of the product".
LIVE_STATUSES = frozenset({InterfaceStatus.ACTIVE, InterfaceStatus.EXPERIMENTAL,
                           InterfaceStatus.DEPRECATED})

#: Kinds an operator can expect to find in navigation.
NAVIGABLE_KINDS = frozenset({InterfaceKind.PAGE})


@dataclass(frozen=True)
class Interface:
    """One capability of the product.

    Every field has a job to do; nothing is here for decoration:

    ``interface_id``
        The stable key. Persisted settings use it, code refers to it, and it
        must never follow a display label or a URL.
    ``name`` / ``description``
        What the operator is shown. Free to change at any time.
    ``domain``
        The one domain this belongs to (see ``domains.py``).
    ``route``
        The Flask endpoint that serves it, or ``None`` for sections and
        features. An endpoint name, not a URL: the URL is the application's
        business.
    ``aliases``
        Additional endpoints that belong to this same interface - a second way
        in, a sub-page, a redirect kept for old bookmarks, or an action that
        only makes sense on this page. Declaring them here is what stops two
        registry entries quietly owning the same page.
    ``icon``
        Bootstrap icon class, for navigation.
    ``default_enabled``
        The one definition of the default. The registry owns this; no second
        list of defaults exists anywhere else.
    ``required_role``
        Visibility only: who should *see* this. Never authorisation - the
        request is still authorised server-side by ``core/security``.
    ``dependencies``
        Interfaces that must be available for this one to make sense. Used to
        refuse a broken switch-off and to explain why.
    ``settings``
        ``category.key`` references to settings this interface consumes. A
        reference, never a copy of the value.
    ``feature_flag``
        Gate for work that is part of the product model but not yet released.
    ``help_topic``
        Where this interface's help lives. ``None`` is a deliberate decision
        that there is no topic yet.
    ``keyboard_shortcut``
        The key sequence for later shortcut and command-palette support.
    ``kind`` / ``status``
        How it is presented, and whether it still exists.
    """

    interface_id: str
    name: str
    description: str
    domain: Domain
    route: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    icon: str = "bi-square"
    default_enabled: bool = True
    required_role: Optional[str] = None
    dependencies: Tuple[str, ...] = ()
    settings: Tuple[str, ...] = ()
    feature_flag: Optional[str] = None
    help_topic: Optional[str] = None
    keyboard_shortcut: Optional[str] = None
    kind: InterfaceKind = InterfaceKind.PAGE
    status: InterfaceStatus = InterfaceStatus.ACTIVE

    def __post_init__(self) -> None:
        # ``domain`` may be given as a string in declarative entries; normalise
        # once here so callers never see a mixture of str and Domain.
        object.__setattr__(self, "domain", coerce_domain(self.domain))
        if isinstance(self.kind, str):
            object.__setattr__(self, "kind", InterfaceKind(self.kind))
        if isinstance(self.status, str):
            object.__setattr__(self, "status", InterfaceStatus(self.status))

    # -- derived facts ----------------------------------------------------
    @property
    def endpoints(self) -> Tuple[str, ...]:
        """Every endpoint this interface owns: the route first, then aliases."""
        return tuple(e for e in (self.route,) + tuple(self.aliases) if e)

    @property
    def live(self) -> bool:
        """Still part of the product (whether or not it is switched on)."""
        return self.status in LIVE_STATUSES

    @property
    def navigable(self) -> bool:
        """Could appear in navigation at all.

        Whether a status is navigable is the lifecycle policy's answer
        (``core/interfaces/lifecycle.py``), not this model's own opinion: the
        policy is what navigation, settings, the API and the documentation all
        read, so there is one meaning of DEPRECATED rather than four.
        """
        from .lifecycle import policy

        return (self.kind in NAVIGABLE_KINDS
                and self.route is not None
                and policy(self.status).navigable)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interface_id": self.interface_id,
            "name": self.name,
            "description": self.description,
            "domain": str(self.domain),
            "route": self.route,
            "aliases": list(self.aliases),
            "endpoints": list(self.endpoints),
            "icon": self.icon,
            "default_enabled": self.default_enabled,
            "required_role": self.required_role,
            "dependencies": list(self.dependencies),
            "settings": list(self.settings),
            "feature_flag": self.feature_flag,
            "help_topic": self.help_topic,
            "keyboard_shortcut": self.keyboard_shortcut,
            "kind": str(self.kind),
            "status": str(self.status),
            "navigable": self.navigable,
        }


@dataclass(frozen=True)
class Feature:
    """A cross-cutting behaviour switch that is not an interface.

    ``page_tips`` is the case that created this: it turns explanatory tips on
    and off across every page, has no page of its own and no navigation entry,
    and yet it is referenced by name in templates. It is not a page pretending
    to be one - it is a feature, and it is declared as one so that the
    visibility service can answer for it and the registry can still be the
    single list of ids that are allowed to be switched on.
    """

    feature_id: str
    name: str
    description: str
    domain: Domain = Domain.INTERNAL
    default_enabled: bool = True
    settings: Tuple[str, ...] = ()
    status: InterfaceStatus = InterfaceStatus.ACTIVE

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", coerce_domain(self.domain))
        if isinstance(self.status, str):
            object.__setattr__(self, "status", InterfaceStatus(self.status))

    @property
    def live(self) -> bool:
        return self.status in LIVE_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interface_id": self.feature_id,  # same key space: it is switched by id
            "name": self.name,
            "description": self.description,
            "domain": str(self.domain),
            "default_enabled": self.default_enabled,
            "settings": list(self.settings),
            "kind": str(InterfaceKind.FEATURE),
            "status": str(self.status),
            "navigable": False,
            "route": None,
            "aliases": [],
            "endpoints": [],
            "icon": None,
            "required_role": None,
            "dependencies": [],
            "feature_flag": None,
            "help_topic": None,
            "keyboard_shortcut": None,
        }
