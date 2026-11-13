"""Registry integrity checks.

The registry is a declaration, so the useful thing to do with it is check it
against the facts: does the endpoint exist, does the dependency exist, do two
entries claim the same page, does the same shortcut mean two different things.

Everything here is a *pure function* over a sequence of interfaces, so a test
can hand it a deliberately broken registry and prove each rule fires. The real
registry is checked by ``tests/unit/test_interface_registry.py`` at suite
level, which is what turns the architectural rule into a guardrail: a page that
is served but not declared fails the build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .domains import domain_values
from .model import (
    HELP_TOPIC_PATTERN,
    ICON_PATTERN,
    ID_PATTERN,
    SETTING_REF_PATTERN,
    Feature,
    Interface,
    InterfaceKind,
    InterfaceStatus,
)

#: Roles the security layer knows. ``None`` means "any signed-in user".
ALLOWED_ROLES = frozenset({None, "viewer", "analyst", "admin"})

#: Shortcut shape: one or more keys separated by spaces ("g w", "?").
SHORTCUT_PATTERN = None  # compiled lazily below (keeps the import surface small)

import re as _re

SHORTCUT_PATTERN = _re.compile(r"^[a-z0-9?/,.;\[\]](\s+[a-z0-9?/,.;\[\]]){0,2}$")


@dataclass(frozen=True)
class Issue:
    """One thing wrong with a registry."""

    code: str
    interface_id: Optional[str]
    message: str
    severity: str = "error"  # error | warning

    def __str__(self) -> str:
        where = f" [{self.interface_id}]" if self.interface_id else ""
        return f"{self.severity.upper()}{where} {self.code}: {self.message}"


def _cycle_paths(graph: Dict[str, Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    """Find dependency cycles, each reported once, as a path."""
    found: List[Tuple[str, ...]] = []
    seen_cycles: Set[frozenset] = set()

    def walk(node: str, path: List[str], visiting: Set[str]) -> None:
        for dep in graph.get(node, ()):
            if dep in visiting:
                cycle = tuple(path[path.index(dep):] + [dep])
                key = frozenset(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    found.append(cycle)
                continue
            walk(dep, path + [dep], visiting | {dep})

    for start in graph:
        walk(start, [start], {start})
    return found


def validate_registry(
    interfaces: Sequence[Interface],
    features: Sequence[Feature] = (),
    known_endpoints: Optional[Iterable[str]] = None,
    known_settings: Optional[Iterable[str]] = None,
) -> List[Issue]:
    """Check a registry against the facts it claims.

    Args:
        interfaces: the entries to check.
        features: behaviour switches, checked for id collisions with interfaces.
        known_endpoints: Flask endpoint names; when given, every route and alias
            must be one of them.
        known_settings: ``category.key`` paths that exist; when given, every
            settings reference must be one of them.
    """
    issues: List[Issue] = []
    known = set(known_endpoints) if known_endpoints is not None else None
    valid_settings = set(known_settings) if known_settings is not None else None

    ids: Dict[str, int] = {}
    endpoint_owners: Dict[str, List[str]] = {}
    shortcut_owners: Dict[str, List[str]] = {}
    help_owners: Dict[str, List[str]] = {}
    domains = set(domain_values())

    # -- per-entry checks -------------------------------------------------
    for interface in interfaces:
        iid = interface.interface_id
        ids[iid] = ids.get(iid, 0) + 1

        if not ID_PATTERN.match(iid or ""):
            issues.append(Issue(
                "invalid_id", iid,
                f"id {iid!r} must be lowercase, start with a letter and use only a-z, 0-9 and _"))
        if not interface.name.strip():
            issues.append(Issue("missing_name", iid, "an interface must have a name"))
        if not interface.description.strip():
            issues.append(Issue("missing_description", iid,
                                "an interface must describe what it is for"))
        if str(interface.domain) not in domains:
            issues.append(Issue("invalid_domain", iid,
                                f"domain {interface.domain!r} is not a declared domain"))
        if interface.required_role not in ALLOWED_ROLES:
            issues.append(Issue("invalid_role", iid,
                                f"role {interface.required_role!r} is not a role the "
                                f"security layer knows"))
        if not ICON_PATTERN.match(interface.icon or ""):
            issues.append(Issue("invalid_icon", iid, f"icon {interface.icon!r} is not a "
                                                      f"bootstrap icon class"))
        if interface.help_topic is not None and not HELP_TOPIC_PATTERN.match(interface.help_topic):
            issues.append(Issue("invalid_help_topic", iid,
                                f"help topic {interface.help_topic!r} is not a slug"))
        if interface.keyboard_shortcut is not None:
            if not SHORTCUT_PATTERN.match(interface.keyboard_shortcut):
                issues.append(Issue("invalid_shortcut", iid,
                                    f"shortcut {interface.keyboard_shortcut!r} is not a key sequence"))
            else:
                shortcut_owners.setdefault(interface.keyboard_shortcut, []).append(iid)
        if interface.help_topic:
            help_owners.setdefault(interface.help_topic, []).append(iid)
        if interface.kind == InterfaceKind.PAGE and not interface.route:
            issues.append(Issue("page_without_route", iid,
                                "a PAGE interface must name the endpoint that serves it"))
        if interface.kind == InterfaceKind.SECTION and interface.route:
            # A section may still own endpoints via aliases; the route field is
            # reserved for interfaces that are opened directly.
            issues.append(Issue(
                "section_with_route", iid,
                "a SECTION lives inside another page; use aliases for the endpoints "
                "it owns, or declare it a PAGE", severity="warning"))
        if interface.status == InterfaceStatus.RETIRED:
            issues.append(Issue("retired_in_registry", iid,
                                "retired interfaces belong in RETIRED_INTERFACE_IDS, "
                                "not in the registry", severity="warning"))
        if interface.status == InterfaceStatus.EXPERIMENTAL and not interface.feature_flag:
            issues.append(Issue("experimental_without_flag", iid,
                                "an EXPERIMENTAL interface must name the feature flag "
                                "that gates it"))

        for ref in interface.settings:
            if not SETTING_REF_PATTERN.match(ref):
                issues.append(Issue("invalid_setting_reference", iid,
                                    f"setting reference {ref!r} is not category.key"))
            elif valid_settings is not None and ref not in valid_settings:
                issues.append(Issue("unknown_setting_reference", iid,
                                    f"setting reference {ref!r} does not exist"))

        for endpoint in interface.endpoints:
            endpoint_owners.setdefault(endpoint, []).append(iid)
            if known is not None and endpoint not in known:
                issues.append(Issue("unknown_endpoint", iid,
                                    f"{endpoint!r} is not an endpoint of this application"))

        for dep in interface.dependencies:
            if dep == iid:
                issues.append(Issue("self_dependency", iid, "an interface cannot depend on itself"))

    # -- cross-entry checks ----------------------------------------------
    for iid, count in ids.items():
        if count > 1:
            issues.append(Issue("duplicate_id", iid, f"declared {count} times"))

    known_ids = {i.interface_id for i in interfaces}
    feature_ids = {f.feature_id for f in features}
    for interface in interfaces:
        for dep in interface.dependencies:
            if dep not in known_ids:
                issues.append(Issue("unknown_dependency", interface.interface_id,
                                    f"depends on {dep!r}, which is not a declared interface"))
            elif dep == interface.interface_id:
                continue  # already reported
            elif getattr(dep, "startswith", None) and dep in feature_ids:
                issues.append(Issue("feature_dependency", interface.interface_id,
                                    f"depends on the feature {dep!r}; features are not pages",
                                    severity="warning"))

    graph = {i.interface_id: tuple(i.dependencies) for i in interfaces}
    for cycle in _cycle_paths(graph):
        issues.append(Issue("dependency_cycle", cycle[0],
                            "dependency cycle: " + " -> ".join(cycle)))

    for endpoint, owners in endpoint_owners.items():
        if len(owners) > 1:
            issues.append(Issue(
                "duplicate_endpoint", owners[0],
                f"endpoint {endpoint!r} is claimed by {', '.join(sorted(owners))}; "
                f"declare it once, and use aliases only within one interface"))

    for shortcut, owners in shortcut_owners.items():
        if len(owners) > 1:
            issues.append(Issue(
                "shortcut_collision", owners[0],
                f"shortcut {shortcut!r} is claimed by {', '.join(sorted(owners))}"))

    collision = sorted(iid for iid, count in ids.items() if count > 1)
    for fid in feature_ids & known_ids:
        issues.append(Issue("feature_id_collision", fid,
                            "a feature and an interface cannot share an id"))

    for topic, owners in help_owners.items():
        if len(owners) > 1:
            issues.append(Issue("duplicate_help_topic", owners[0],
                                f"help topic {topic!r} is shared by {', '.join(sorted(owners))}",
                                severity="warning"))

    if collision and not any(i.code == "duplicate_id" for i in issues):
        issues.append(Issue("duplicate_id", collision[0], "duplicate interface ids"))
    return issues


def errors_only(issues: Sequence[Issue]) -> List[Issue]:
    return [i for i in issues if i.severity == "error"]


def format_issues(issues: Sequence[Issue]) -> str:
    return "\n".join(str(i) for i in issues)
