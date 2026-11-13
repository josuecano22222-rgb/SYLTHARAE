"""The application's navigation and page identity, prepared from the registry.

The rule this module exists to enforce:

    The template must not know interface IDs.

Before it, `base.html` carried two hand-written endpoint-label dictionaries and
the sidebar decided enabled/role/route questions in markup - so the product map
existed in the registry, in the template, and in the request gate, and a rename
left a dead entry behind. Now the application layer asks this module for a
prepared structure and the template renders it:

    navigation = build_navigation(user, state, current_endpoint, url_for)
    page = present_page(current_endpoint, state, user, url_for)

A template may know *label, icon, url, active, children*. It may not know
*is it enabled, who owns it, what role does it need, what does it depend on*.

Everything here is derived from `core.interfaces` and `InterfaceState`; nothing
in this module grants access. `required_role` decides what is *shown*; the
authorization decision stays in `core/security` (`accessible`, below, reports
the product's conditions and says so).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from core.i18n import translate_ui

from .domains import DOMAIN_LABELS, DOMAIN_ORDER, domain_label
from .lifecycle import StatusPolicy, policy
from .model import Interface, InterfaceKind
from .registry import REGISTRY, get_interface, get_interface_for_endpoint


@dataclass(frozen=True)
class NavigationEntry:
    """One navigable interface, ready to render."""

    interface_id: str
    #: The Flask endpoint the entry navigates to. Kept on the entry because
    #: the navigation script matches `data-endpoint` against the current
    #: endpoint to decide the active link, and the interface id is not a route.
    route: str
    label: str
    url: str
    icon: str
    active: bool
    domain: str
    description: str = ""
    shortcut: Optional[str] = None
    badge: Optional[str] = None
    note: Optional[str] = None
    deprecated: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "interface_id": self.interface_id,
            "route": self.route,
            "label": self.label,
            "url": self.url,
            "icon": self.icon,
            "active": self.active,
            "domain": self.domain,
            "description": self.description,
            "shortcut": self.shortcut,
            "badge": self.badge,
            "note": self.note,
            "deprecated": self.deprecated,
        }


@dataclass(frozen=True)
class NavigationGroup:
    """A domain and the entries it holds, in declared order."""

    domain: str
    label: str
    entries: Tuple[NavigationEntry, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.entries)


@dataclass(frozen=True)
class Crumb:
    label: str
    url: Optional[str] = None
    current: bool = False
    icon: Optional[str] = None


@dataclass(frozen=True)
class InterfaceConditions:
    """The four questions, kept apart.

    `exists` is the product's answer (the registry declares it), `enabled` is
    this installation's answer (the switch, its dependencies and its feature
    flag), `visible` adds the visibility role, and `accessible` reports whether
    the interface's own product conditions are met. Authorization is *not*
    decided here: `core/security` remains the only place that may allow or
    refuse a request.
    """

    interface_id: str
    exists: bool
    enabled: bool
    visible: bool
    accessible: bool
    status: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "interface_id": self.interface_id,
            "exists": self.exists,
            "enabled": self.enabled,
            "visible": self.visible,
            "accessible": self.accessible,
            "status": self.status,
            "reason": self.reason,
            "authorization": "core/security",
        }


@dataclass(frozen=True)
class PagePresentation:
    """What a page says about itself, taken from the registry."""

    interface_id: Optional[str]
    label: str
    title: str
    description: str
    icon: Optional[str]
    domain: Optional[str]
    domain_label: Optional[str]
    help_topic: Optional[str]
    shortcut: Optional[str]
    status: Optional[str]
    badge: Optional[str]
    breadcrumbs: Tuple[Crumb, ...] = ()
    conditions: Optional[InterfaceConditions] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "interface_id": self.interface_id,
            "route": self.route,
            "label": self.label,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "domain": self.domain,
            "help_topic": self.help_topic,
            "shortcut": self.shortcut,
            "status": self.status,
            "badge": self.badge,
            "breadcrumbs": [
                {"label": c.label, "url": c.url, "current": c.current} for c in self.breadcrumbs
            ],
            "conditions": self.conditions.to_dict() if self.conditions else None,
        }


def _humanise(endpoint: Optional[str]) -> str:
    """A readable fallback for an endpoint the registry does not own."""
    if not endpoint:
        return ""
    tail = endpoint.split(".")[-1]
    return tail.replace("_", " ").strip().title()


def interface_conditions(interface_id: str, state, user) -> InterfaceConditions:
    """Answer exists / enabled / visible / accessible, without collapsing them."""
    interface = get_interface(interface_id)
    exists = interface is not None
    if not exists:
        return InterfaceConditions(
            interface_id=interface_id, exists=False, enabled=False,
            visible=False, accessible=False,
            reason="No interface of this name is declared.")

    enabled = bool(state.is_enabled(interface_id))
    visible = bool(state.is_visible(interface_id, user))
    role = interface.required_role

    if not enabled:
        # Say *why* it is off: switched off, waiting on a dependency, or an
        # unfinished interface whose feature flag has not been turned on.
        flag_off = bool(interface.feature_flag) and not state.feature_on(interface.feature_flag)
        missing = state.missing_dependencies(interface_id) if hasattr(
            state, "missing_dependencies") else ()
        if flag_off:
            reason = (f"Waiting on the feature flag {interface.feature_flag}.")
        elif missing:
            reason = ("It is switched on, but " + ", ".join(missing) + " is switched off.")
        else:
            reason = "It is switched off."
    elif not visible and role:
        reason = f"It requires the {role} role; your account does not have it."
    elif not visible:
        reason = "You are not signed in."
    else:
        reason = None

    return InterfaceConditions(
        interface_id=interface_id,
        exists=True,
        enabled=enabled,
        visible=visible,
        accessible=enabled and visible,
        status=str(interface.status),
        reason=reason,
    )


def _entry_for(interface: Interface, state, user, current_endpoint: Optional[str],
               url_for: Callable[..., str]) -> Optional[NavigationEntry]:
    status_policy = policy(interface.status)
    if not status_policy.navigable or not interface.route:
        return None
    try:
        url = url_for(interface.route)
    except Exception:
        # A route the application does not serve cannot be rendered. The
        # registry validation test is what fails loudly about that; navigation
        # simply does not offer a broken link.
        return None
    active = bool(current_endpoint) and (
        current_endpoint == interface.route or current_endpoint in interface.aliases)
    return NavigationEntry(
        interface_id=interface.interface_id,
        route=interface.route,
        label=translate_ui(interface.name),
        url=url,
        icon=interface.icon,
        active=active,
        domain=str(interface.domain),
        description=translate_ui(interface.description or ""),
        shortcut=interface.keyboard_shortcut,
        badge=status_policy.badge,
        note=status_policy.note,
        deprecated=interface.status.value == "DEPRECATED"
        if hasattr(interface.status, "value") else str(interface.status) == "DEPRECATED",
    )


def build_navigation(state, user, current_endpoint: Optional[str] = None,
                     url_for: Optional[Callable[..., str]] = None,
                     prefs: Optional[Mapping[str, Mapping[str, object]]] = None) -> Tuple[NavigationGroup, ...]:
    """The sidebar: domains in declared order, each holding what may be shown.

    Visibility comes from `InterfaceState.is_visible` (enabled, dependencies,
    feature flag, role); ordering comes from `DOMAIN_ORDER`; the label comes
    from the domain vocabulary. Nothing here decides security.

    ``prefs`` (optional, the user's own `user_navigation_prefs` rows) narrows
    what the registry already allows: a ``hidden`` entry is dropped and an
    explicit ``position`` places an entry inside its domain ahead of the
    entries that keep the declared order. Preferences can never widen
    visibility: they are applied after `is_visible` said yes.
    """
    if url_for is None:  # pragma: no cover - callers in a request pass url_for
        from flask import url_for as _url_for
        url_for = _url_for

    if user is None or not getattr(user, "is_authenticated", False):
        return ()

    visible_by_domain = state.get_visible_by_domain(user)

    groups = []
    for domain in DOMAIN_ORDER:
        interfaces = visible_by_domain.get(domain) or visible_by_domain.get(str(domain)) or ()
        prepared = []
        for interface in interfaces:
            if interface.kind is InterfaceKind.PAGE or interface.kind == InterfaceKind.PAGE:
                entry = _entry_for(interface, state, user, current_endpoint, url_for)
                if entry is not None:
                    prepared.append(entry)
        if prefs:
            shown = []
            for entry in prepared:
                pref = prefs.get(entry.interface_id)
                if pref and pref.get("hidden"):
                    continue
                shown.append((pref.get("position") if pref else None, entry))
            shown.sort(key=lambda pair: (pair[0] is None, pair[0] or 0))
            prepared_entries = [entry for _, entry in shown]
        else:
            prepared_entries = prepared
        entries = tuple(prepared_entries)
        if entries:
            groups.append(NavigationGroup(domain=str(domain), label=domain_label(domain), entries=entries))
    return tuple(groups)


def breadcrumbs_for(current_endpoint: Optional[str], label: str, state=None, user=None,
                    url_for: Optional[Callable[..., str]] = None) -> Tuple[Crumb, ...]:
    """Home → the interface a page belongs to, with the interface's own name."""
    if url_for is None:  # pragma: no cover
        from flask import url_for as _url_for
        url_for = _url_for

    crumbs = [Crumb(label="Home", url=url_for("index"), icon="bi-house-door")]
    if current_endpoint and current_endpoint != "index":
        crumbs.append(Crumb(label=label, current=True))
    return tuple(crumbs)


def present_page(endpoint: Optional[str], state, user=None,
                 url_for: Optional[Callable[..., str]] = None,
                 entity_title: Optional[str] = None) -> PagePresentation:
    """What the current page is, according to the registry.

    `entity_title` lets a page add its own subject (a file name, a job id)
    without inventing a product identity: the interface still supplies the
    label, domain, icon, help topic and shortcut.
    """
    if url_for is None:  # pragma: no cover
        from flask import url_for as _url_for
        url_for = _url_for

    interface = get_interface_for_endpoint(endpoint) if endpoint else None
    if interface is None:
        label = _humanise(endpoint)
        return PagePresentation(
            interface_id=None,
            label=label,
            title=entity_title or label,
            description="",
            icon=None,
            domain=None,
            domain_label=None,
            help_topic=None,
            shortcut=None,
            status=None,
            badge=None,
            breadcrumbs=breadcrumbs_for(endpoint, label, url_for=url_for),
            conditions=None,
        )

    status_policy = policy(interface.status)
    return PagePresentation(
        interface_id=interface.interface_id,
        label=interface.name,
        title=entity_title or interface.name,
        description=interface.description,
        icon=interface.icon,
        domain=str(interface.domain),
        domain_label=domain_label(interface.domain),
        help_topic=interface.help_topic,
        shortcut=interface.keyboard_shortcut,
        status=str(interface.status),
        badge=status_policy.badge,
        breadcrumbs=breadcrumbs_for(endpoint, interface.name, url_for=url_for),
        conditions=interface_conditions(interface.interface_id, state, user) if state else None,
    )


def navigation_model(state, user, current_endpoint=None, url_for=None) -> Dict[str, object]:
    """Navigation plus the page's identity, as the shell context."""
    page = present_page(current_endpoint, state, user, url_for)
    return {
        "page": page,
        "navigation": build_navigation(state, user, current_endpoint, url_for),
        "domain_labels": {str(domain): label for domain, label in DOMAIN_LABELS.items()},
    }
