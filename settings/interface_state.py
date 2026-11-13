"""Stored interface state, applied to the registry.

The registry says what exists. This module says what is switched on for this
installation, and answers the questions the rest of the application asks -
"is this enabled?", "does the operator see it?", "may it be switched off?" -
by combining the two.

Three boundaries are load-bearing:

* **Defaults come from the registry.** There is no second list of defaults, and
  enabling everything on reset is gone: an interface whose registry default is
  off stays off when settings are reset.
* **Unknown means off.** An id the registry does not declare - a typo, a
  retired switch, a settings file from an older version - is not enabled,
  however it is stored. It is never deleted from the file; it simply cannot
  turn anything on.
* **Visibility is not authorisation.** ``is_visible`` decides whether an
  operator is *shown* an interface. Every request is still authorised
  server-side by ``core/security``; nothing here may be used as a gate on its

Dependency policy: the registry says what an interface needs. Rather than
silently mutating an operator's choices, this module refuses new invalid states
and reports existing ones - ``state_report()`` is what the interfaces view and
the tests read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.interfaces import (
    Feature,
    Interface,
    InterfaceStatus,
    get_all_interfaces,
    get_dependents,
    get_dependencies,
    get_feature,
    get_features,
    get_interface,
    get_interface_for_endpoint,
    is_registered,
    iter_endpoints,
    resolve_interface_id,
    summary,
)

logger = logging.getLogger(__name__)

#: Role ordering for visibility: an interface that requires ``analyst`` is also
#: visible to an ``admin``.
_ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}


@dataclass(frozen=True)
class StateReport:
    """What the current stored state means, in one object.

    Used by the administrative view and by tests; deliberately not used to
    mutate anything.
    """

    enabled: Tuple[str, ...]
    disabled: Tuple[str, ...]
    unknown_stored: Tuple[str, ...]
    missing_dependencies: Tuple[Tuple[str, str], ...]
    retired_stored: Tuple[str, ...]
    migrated_from: Tuple[Tuple[str, str], ...]

    @property
    def consistent(self) -> bool:
        return not self.missing_dependencies


def _role_of(user: Any) -> Optional[str]:
    """The role of a user object, whatever kind it is."""
    if user is None:
        return None
    role = getattr(user, "role", None)
    return role if isinstance(role, str) else None


def _authenticated(user: Any) -> bool:
    """Is this a signed-in user?

    ``AnonymousUser.is_authenticated`` is False and ``.role`` is None, so the
    role check alone would let an anonymous visitor see interfaces that require
    no particular role. Visibility always requires a session.
    """
    if user is None:
        return False
    flag = getattr(user, "is_authenticated", None)
    if callable(flag):
        return bool(flag())
    if flag is not None:
        return bool(flag)
    return _role_of(user) is not None


class InterfaceState:
    """Registry + stored settings -> the answers the application needs."""

    def __init__(
        self,
        visibility,
        feature_flags: Optional[Mapping[str, bool]] = None,
    ) -> None:
        """
        Args:
            visibility: the persisted ``InterfaceVisibility`` (or anything with
                the same ``interfaces`` mapping of ``InterfaceConfig``).
            feature_flags: which feature flags are on. ``None`` means none are;
                wiring a real flag source is a later step, and until then an
                EXPERIMENTAL interface stays off.
        """
        self._visibility = visibility
        self._feature_flags = dict(feature_flags or {})

    # -- stored state -----------------------------------------------------
    def stored(self, interface_id: str) -> Optional[bool]:
        """What the settings file says, or ``None`` if it says nothing."""
        config = getattr(self._visibility, "interfaces", {}).get(interface_id)
        if config is None:
            return None
        enabled = getattr(config, "enabled", None)
        return bool(enabled) if enabled is not None else None

    def stored_ids(self) -> Tuple[str, ...]:
        return tuple(getattr(self._visibility, "interfaces", {}).keys())

    def _flag_on(self, interface: Interface) -> bool:
        if not interface.feature_flag:
            return True
        return self.feature_on(interface.feature_flag)

    def feature_on(self, feature_flag: str) -> bool:
        """Is a named feature flag on?

        Public because callers that need to *explain* a state ("waiting on the
        feature flag x") must not re-implement the mapping lookup and drift
        from the answer this service gives.
        """
        return bool(self._feature_flags.get(feature_flag, False))

    # -- the core questions ----------------------------------------------
    def is_enabled(self, interface_id: str) -> bool:
        """Is this interface switched on and usable?

        False for: ids the registry does not declare, retired interfaces,
        experimental interfaces whose flag is off, interfaces with a switched
        off dependency, and anything stored as disabled.
        """
        if not self.own_enabled(interface_id):
            return False
        # A dependent cannot work while something it needs is off.
        for dependency in get_dependencies(interface_id):
            if not self.is_enabled(dependency):
                return False
        return True

    def own_enabled(self, interface_id: str) -> bool:
        """What this interface's own switch says, ignoring its dependencies.

        ``is_enabled`` answers "can this be used now?", which is the question
        the gate and the navigation ask. This answers "is this switched on?",
        which is what a report needs - otherwise an interface whose dependency
        is off would simply read as disabled and the underlying inconsistency
        would be invisible.
        """
        interface = get_interface(interface_id)
        if interface is None:
            feature = get_feature(interface_id)
            if feature is None or not feature.live:
                return False
            return self._stored_or_default(feature.feature_id, feature.default_enabled)
        if not interface.live:
            return False
        if interface.status == InterfaceStatus.EXPERIMENTAL and not self._flag_on(interface):
            return False
        return self._stored_or_default(interface.interface_id, interface.default_enabled)

    def _stored_or_default(self, interface_id: str, default: bool) -> bool:
        value = self.stored(interface_id)
        return default if value is None else value

    def is_visible(self, interface_id: str, user: Any = None) -> bool:
        """Should this interface be shown to this operator?

        Combines enabled state, role metadata and dependencies. Authorisation
        is not decided here: ``required_role`` only hides things a person cannot
        use, and the server still refuses the request if it is made directly.
        """
        interface = get_interface(interface_id)
        if interface is None:
            return self.is_enabled(interface_id)  # a feature, e.g. page_tips
        if not interface.navigable:
            return False
        if not self.is_enabled(interface_id):
            return False
        return self._role_allows(interface, user)

    def _role_allows(self, interface: Interface, user: Any) -> bool:
        required = interface.required_role
        if required is None and _authenticated(user) is False and user is not None:
            return False
        if required is None:
            return True if user is None else _authenticated(user)
        if not _authenticated(user):
            return False
        return _ROLE_RANK.get(_role_of(user) or "", 0) >= _ROLE_RANK.get(required, 99)

    # -- collections ------------------------------------------------------
    def get_visible_interfaces(self, user: Any = None) -> Tuple[Interface, ...]:
        """Interfaces this operator may see, in registry order."""
        return tuple(i for i in get_all_interfaces() if self.is_visible(i.interface_id, user))

    def get_visible_by_domain(self, user: Any = None) -> Dict[str, List[Interface]]:
        """The same set, grouped by domain - what navigation consumes."""
        grouped: Dict[str, List[Interface]] = {}
        for interface in self.get_visible_interfaces(user):
            grouped.setdefault(str(interface.domain), []).append(interface)
        return grouped

    def interfaces_with_state(self, user: Any = None) -> List[Dict[str, Any]]:
        """Every interface with its state attached - the administrative view.

        Everything a README of the product would say about an entry, generated
        from the registry rather than written twice: what it is, where it lives,
        who sees it, whether it is on, what it needs, which settings it uses,
        its help and its shortcut.
        """
        rows: List[Dict[str, Any]] = []
        for interface in get_all_interfaces():
            row = interface.to_dict()
            from core.interfaces import interface_conditions, status_policy

            # Four separate answers, never collapsed into one boolean: the
            # product declares it (exists), this installation has it on
            # (enabled), this operator may see it (visible), and its own
            # conditions for use are met (accessible). Authorization is not
            # decided here - see `to_dict()`, which says so.
            conditions = interface_conditions(interface.interface_id, self, user)
            lifecycle = status_policy(interface.status)
            row.update({
                "exists": conditions.exists,
                "enabled": conditions.enabled,
                "visible": conditions.visible,
                "accessible": conditions.accessible,
                "state_reason": conditions.reason,
                "status_policy": {
                    "navigable": lifecycle.navigable,
                    "switchable": lifecycle.switchable,
                    "badge": lifecycle.badge,
                    "note": lifecycle.note,
                },
                "stored": self.stored(interface.interface_id),
                "missing_dependencies": list(self.missing_dependencies(interface.interface_id)),
                "dependent_interfaces": [d.interface_id for d in get_dependents(interface.interface_id)],
                "can_disable": self.can_disable(interface.interface_id)[0],
            })
            rows.append(row)
        for feature in get_features():
            # A feature is listed in the same shape as an interface, because a
            # consumer reading this list should not have to special-case it.
            # It has no page, so it is never "visible"; it is accessible where
            # it is switched on, and its subject is the interface it sits in.
            enabled = self.is_enabled(feature.feature_id)
            row = feature.to_dict()
            row.update({
                "kind": "feature",
                "exists": True,
                "enabled": enabled,
                "visible": False,
                "accessible": enabled,
                "state_reason": ("Switched on." if enabled else "Switched off."),
                "status_policy": None,
                "stored": self.stored(feature.feature_id),
                "missing_dependencies": [], "dependent_interfaces": [],
                "can_disable": True,
            })
            rows.append(row)
        return rows

    # -- dependencies -----------------------------------------------------
    def missing_dependencies(self, interface_id: str) -> Tuple[str, ...]:
        """Declared dependencies that are currently switched off."""
        return tuple(dep for dep in get_dependencies(interface_id) if not self.is_enabled(dep))

    def enabled_dependents(self, interface_id: str) -> Tuple[Interface, ...]:
        """Enabled interfaces that would stop working if this were switched off."""
        return tuple(d for d in get_dependents(interface_id) if self.is_enabled(d.interface_id))

    def can_disable(self, interface_id: str) -> Tuple[bool, Optional[str]]:
        """May this interface be switched off, and if not, why not?

        The explanation is written for an operator: it names the dependent
        interfaces and offers what to do about them, because refusing without
        saying why is how a settings screen becomes a maze.
        """
        interface = get_interface(interface_id)
        if interface is None:
            if get_feature(interface_id) is not None:
                return True, None
            return False, f"There is no interface called '{interface_id}'."
        dependents = self.enabled_dependents(interface_id)
        if not dependents:
            return True, None
        names = ", ".join(d.name for d in dependents)
        return False, (
            f"{interface.name} cannot be switched off on its own.\n\n"
            f"It is required by:\n"
            + "\n".join(f"• {d.name}" for d in dependents)
            + f"\n\nSwitch {names} off first, or leave {interface.name} on."
        )

    def can_enable(self, interface_id: str) -> Tuple[bool, Optional[str]]:
        """May this interface be switched on, and if not, what is missing?"""
        interface = get_interface(interface_id)
        if interface is None:
            if get_feature(interface_id) is not None:
                return True, None
            return False, f"There is no interface called '{interface_id}'."
        missing = self.missing_dependencies(interface_id)
        if not missing:
            return True, None
        names = ", ".join((get_interface(m).name if get_interface(m) else m) for m in missing)
        return False, (
            f"{interface.name} needs {names} to be available first.\n\n"
            f"Enable {names}, then enable {interface.name}."
        )

    # -- diagnostics ------------------------------------------------------
    def state_report(self) -> StateReport:
        """Everything the stored state currently says, including problems."""
        stored_ids = self.stored_ids()
        resolved: Dict[str, bool] = {}
        unknown: List[str] = []
        retired: List[str] = []
        migrated: List[Tuple[str, str]] = []

        for stored_id in stored_ids:
            # Retirement is reported from the registry's own list, whether or
            # not the value was carried onto a successor: "this switch no
            # longer exists" and "its choice now lives here" are both facts an
            # operator needs, and the settings file keeps the old key.
            if stored_id in _retired_ids():
                retired.append(stored_id)
            canonical = resolve_interface_id(stored_id)
            if canonical is None:
                if stored_id not in retired:
                    unknown.append(stored_id)
                continue
            if canonical != stored_id:
                migrated.append((stored_id, canonical))
            value = self.stored(stored_id)
            if value is None:
                continue
            # Explicit wins; a merged twin only ever turns something on.
            resolved[canonical] = resolved.get(canonical, False) or value

        # ``enabled``/``disabled`` describe the switches themselves;
        # ``missing_dependencies`` is what makes a switched-on interface that
        # cannot actually run visible instead of silently reading as off.
        enabled = tuple(sorted(i for i in _live_ids() if self.own_enabled(i)))
        disabled = tuple(sorted(i for i in _live_ids() if not self.own_enabled(i)))
        missing = []
        for interface in get_all_interfaces():
            if not self.own_enabled(interface.interface_id):
                continue
            for dep in interface.dependencies:
                if not self.is_enabled(dep):
                    missing.append((interface.interface_id, dep))
        return StateReport(
            enabled=enabled,
            disabled=disabled,
            unknown_stored=tuple(sorted(unknown)),
            missing_dependencies=tuple(missing),
            retired_stored=tuple(sorted(retired)),
            migrated_from=tuple(sorted(migrated)),
        )

    def summary(self) -> Dict[str, Any]:
        """Registry summary plus the current state, for the interfaces view."""
        data = dict(summary())
        report = self.state_report()
        data.update({
            "enabled": len(report.enabled),
            "disabled": len(report.disabled),
            "unknown_stored": len(report.unknown_stored),
            "retired_stored": len(report.retired_stored),
            "inconsistent": not report.consistent,
        })
        return data

    # -- defaults ---------------------------------------------------------
    def defaults(self) -> Dict[str, bool]:
        """The default state of everything, from the registry."""
        from core.interfaces import defaults as registry_defaults

        return registry_defaults()

    def endpoint_enabled(self, endpoint: Optional[str], path: str = "") -> bool:
        """May this endpoint be served?

        The rule the request gate uses:

        * an endpoint that belongs to an interface follows that interface, with
          all of the dependency and flag logic above;
        * an endpoint no interface declares is **not** served - the old
          "unknown endpoint, therefore enabled" fallback is gone, because it is
          indistinguishable from a page somebody forgot to register;
        * infrastructure endpoints (health, auth, setup, locale, favicon) are
          classified in ``inventory.py`` and never reach this method.
        """
        if not endpoint:
            return True
        # Infrastructure (authentication, setup, health, locale, favicon, every
        # /api subtree) is how the application works: it is not part of the
        # product surface the interface switches govern.
        from core.interfaces import is_infrastructure_endpoint

        if is_infrastructure_endpoint(endpoint, path):
            return True
        owner = get_interface_for_endpoint(endpoint)
        if owner is None:
            logger.debug("Endpoint %s is not owned by any interface; refusing it", endpoint)
            return False
        return self.is_enabled(owner.interface_id)


def _live_ids() -> Tuple[str, ...]:
    return tuple(i.interface_id for i in get_all_interfaces() if i.live)


def _retired_ids() -> frozenset:
    from core.interfaces import RETIRED_INTERFACE_IDS, LEGACY_INTERFACE_IDS

    return frozenset(RETIRED_INTERFACE_IDS) | frozenset(LEGACY_INTERFACE_IDS)
