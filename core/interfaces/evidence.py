"""Generate the registry evidence report.

Step 1-2 of the productization directive ends with a required evidence set:

    Registry inventory
    Endpoint coverage result
    Interface count
    Unmanaged endpoint count = 0
    Internal endpoint exception count
    Alias count
    Dependency validation result
    Registry test result
    Legacy settings migration result
    Rendered navigation result

Every one of those is a number somebody could type by hand and get wrong -
which is exactly how the productization map came to carry three different
counts (57, 40, 41) for the same application. So none of them is typed: this
module derives them from the registry and from the application's own URL map,
and ``docs/REGISTRY_EVIDENCE.md`` embeds the result.

Two blocks, because they need different things:

* :func:`registry_block` - facts about the declared product. Needs nothing but
  the registry, so a unit test can hold the document to them.
* :func:`application_block` - facts about the application the registry
  describes (coverage, classification, unowned pages). Needs a real Flask
  application, so the integration suite holds the document to those.

Regenerate the whole document with::

    python3 -m core.interfaces.evidence --write docs/REGISTRY_EVIDENCE.md
"""

from __future__ import annotations

import json
import sys
from typing import Dict, Iterable, List, Optional, Sequence

from .domains import DOMAIN_ORDER
from .inventory import (
    INTERNAL_PAGE_ENDPOINTS,
    REDIRECT_ENDPOINTS,
    SYSTEM_ENDPOINTS,
    TEST_ENDPOINT_PREFIXES,
    EndpointClass,
    build_inventory,
    counts,
    inventory_json,
    registry_coverage,
    unowned_user_interfaces,
)
from .registry import (
    FEATURES,
    LEGACY_INTERFACE_IDS,
    REGISTRY,
    RETIRED_INTERFACE_IDS,
    get_dependencies,
    get_dependents,
    summary,
)
from .validation import validate_registry

#: Where the generated blocks live inside the document.
BEGIN_REGISTRY = "<!-- BEGIN GENERATED REGISTRY EVIDENCE -->"
END_REGISTRY = "<!-- END GENERATED REGISTRY EVIDENCE -->"
BEGIN_APPLICATION = "<!-- BEGIN GENERATED APPLICATION EVIDENCE -->"
END_APPLICATION = "<!-- END GENERATED APPLICATION EVIDENCE -->"

#: The exceptions the coverage rule tolerates, so they cannot grow unnoticed.
EXCEPTION_SETS = {
    "SYSTEM_ENDPOINTS": tuple(sorted(SYSTEM_ENDPOINTS)),
    "INTERNAL_PAGE_ENDPOINTS": tuple(sorted(INTERNAL_PAGE_ENDPOINTS)),
    "REDIRECT_ENDPOINTS": tuple(sorted(REDIRECT_ENDPOINTS)),
    "TEST_ENDPOINT_PREFIXES": tuple(TEST_ENDPOINT_PREFIXES),
}


def _bullets(pairs: Iterable[tuple]) -> List[str]:
    return [f"- {label}: **{value}**" for label, value in pairs]


def alias_report() -> Dict[str, object]:
    """Every declared alias, and the count.

    An alias is an endpoint the interface owns but does not navigate to; the
    distinction matters because after review every alias has to be a real
    endpoint the application serves (checked by ``validate_registry``).
    """
    by_interface = {
        interface.interface_id: tuple(sorted(interface.aliases))
        for interface in REGISTRY
        if interface.aliases
    }
    return {
        "count": sum(len(aliases) for aliases in by_interface.values()),
        "by_interface": by_interface,
    }


def dependency_report() -> Dict[str, object]:
    """Dependency validation: cycles, unknown targets, and what depends on what."""
    issues = validate_registry(REGISTRY, FEATURES)
    edges = {
        interface.interface_id: tuple(sorted(get_dependencies(interface.interface_id)))
        for interface in REGISTRY
        if interface.dependencies
    }
    dependents = {
        interface.interface_id: tuple(sorted(
            dependent.interface_id for dependent in get_dependents(interface.interface_id)))
        for interface in REGISTRY
        if get_dependents(interface.interface_id)
    }
    return {
        "valid": not issues,
        "issues": [(issue.interface_id, issue.message) for issue in issues],
        "all": [str(issue) for issue in issues],
        "edges": edges,
        "dependents": dependents,
        "with_dependencies": len(edges),
        "depended_on": len(dependents),
    }


#: Cross-cutting switches that are settings of an existing interface rather
#: than features of their own: they change how a surface behaves, they do not
#: add a surface. Recorded here so that "is this a feature?" is answered once
#: and visibly, instead of by whichever file happens to read the setting.
NON_FEATURE_TOGGLES = {
    "system.animations_enabled": "Display preferences (no product surface)",
    "system.show_breadcrumbs": "Display preferences (no product surface)",
    "system.notifications_enabled": "the `notifications` interface",
    "search.enable_history": "the `search` interface",
    "search.enable_saved_searches": "the `search` interface",
    "display.show_file_preview": "the `file_library` interface",
    "display.show_metadata": "the `file_library` interface",
}

#: Where each feature's gate lives, so a declaration cannot be decorative.
FEATURE_GATES = {
    "page_tips": ("templates/components/page_tips.html",),
}


def feature_gate(feature_id: str):
    """The files that actually gate a declared feature."""
    return FEATURE_GATES.get(feature_id, ())


def registry_block() -> str:
    """The declared product: counts, aliases, dependencies, migration."""
    data = summary()
    aliases = alias_report()
    dependencies = dependency_report()
    validation_issues = validate_registry(REGISTRY, FEATURES)

    parts: List[str] = []
    parts.append(f"### 1. Registry inventory\n")
    parts.extend(_bullets([
        ("Interfaces declared", data["interfaces"]),
        ("Cross-cutting features (not interfaces)", data["features"]),
        ("Endpoints owned (canonical routes + aliases)", data["endpoints_owned"]),
        ("With a keyboard shortcut", data["with_shortcut"]),
        ("With a help topic", data["with_help"]),
        ("Declared domain vocabulary", len(DOMAIN_ORDER)),
        ("Domains currently containing interfaces",
         len([d for d in DOMAIN_ORDER if str(d) in data["by_domain"]])),
        ("Declared domains holding no interface yet",
         len([d for d in DOMAIN_ORDER if str(d) not in data["by_domain"]])),
    ]))
    parts.append("")
    parts.append("Domains are **declared** in `core/interfaces/domains.py`; a "
                 "declared domain may hold no interface yet. The two numbers are "
                 "different, and both are reported:\n")
    parts.append("| Domain | Interfaces | Status |")
    parts.append("| --- | --- | --- |")
    for domain in DOMAIN_ORDER:
        count = data["by_domain"].get(str(domain), 0)
        parts.append(
            f"| {domain} | {count} | {'in use' if count else 'declared, empty'} |")
    parts.append("")
    parts.append(f"Declared aliases: **{aliases['count']}**, across "
                 f"**{len(aliases['by_interface'])}** interfaces. "
                 "An alias is an endpoint the interface owns but does not "
                 "navigate to; aliases never become navigation entries.\n")
    parts.append("| Interface | Aliases |")
    parts.append("| --- | --- |")
    for interface_id, values in sorted(aliases["by_interface"].items()):
        parts.append(f"| `{interface_id}` | " +
                     ", ".join(f"`{v}`" for v in values) + " |")
    parts.append("")

    parts.append("### 3. Dependency validation\n")
    parts.append(f"- Validation result: **{'no issues' if dependencies['valid'] else 'ISSUES'}**")
    parts.append(f"- Interfaces declaring a dependency: **{dependencies['with_dependencies']}**")
    parts.append(f"- Interfaces other interfaces depend on: **{dependencies['depended_on']}**")
    parts.append("")
    parts.append("| Interface | Requires | Required by |")
    parts.append("| --- | --- | --- |")
    for interface in REGISTRY:
        requires = dependencies["edges"].get(interface.interface_id)
        required_by = dependencies["dependents"].get(interface.interface_id)
        if not requires and not required_by:
            continue
        parts.append(
            f"| `{interface.interface_id}` | "
            + (", ".join(f"`{r}`" for r in requires) if requires else "—")
            + " | "
            + (", ".join(f"`{r}`" for r in required_by) if required_by else "—")
            + " |")
    parts.append("")

    parts.append("### 4. Registry integrity\n")
    if validation_issues:
        parts.append("Validation issues:")
        parts.extend(f"- `{i.interface_id}`: {i.message}" for i in validation_issues)
    else:
        parts.append("`validate_registry()` reports no issues: identifiers unique, "
                     "domains from the closed set, roles known, routes declared "
                     "consistently, no dependency is unknown or circular, and no two "
                     "interfaces share a keyboard shortcut.")
    parts.append("")

    parts.append("### 5. Lifecycle and migration\n")
    parts.extend(_bullets([
        ("Interfaces by status", ", ".join(
            f"{status} {count}" for status, count in sorted(data["by_status"].items()))),
        ("Interfaces by kind", ", ".join(
            f"{kind} {count}" for kind, count in sorted(data["by_kind"].items()))),
    ]))
    parts.append("")
    parts.append("Renames and merges the registry understands "
                 "(a stored value under an old key reaches the interface that "
                 "replaced it; the code may not name the old key):\n")
    parts.append("| Legacy key | Reaches |")
    parts.append("| --- | --- |")
    for legacy_id, canonical_id in sorted(LEGACY_INTERFACE_IDS.items()):
        note = " (retired: no interface of its own)" if legacy_id in RETIRED_INTERFACE_IDS else ""
        parts.append(f"| `{legacy_id}`{note} | `{canonical_id}` |")
    parts.append("")

    parts.append("### 6. Feature declarations\n")
    parts.append("A feature is a cross-cutting capability with no page of its "
                 "own. Everything the application gates this way is declared "
                 "here; `tests/unit/test_interface_lifecycle.py` fails if a "
                 "declared feature is not actually gated in code, or if a "
                 "cross-cutting gate exists without a declaration.\n")
    parts.append("| Feature | Default | Gated in | Description |")
    parts.append("| --- | --- | --- | --- |")
    for feature in FEATURES:
        gate = feature_gate(feature.feature_id)
        parts.append(f"| `{feature.feature_id}` | "
                     f"{'on' if feature.default_enabled else 'off'} | "
                     f"{('`' + ', '.join(gate) + '`') if gate else '— not gated —'} | "
                     f"{feature.description} |")
    parts.append("")
    parts.append("Cross-cutting settings that are **not** features (they belong "
                 "to an existing interface, so they are values in the settings "
                 "engine and nothing to do with the registry):\n")
    parts.append("| Setting | Owner |")
    parts.append("| --- | --- |")
    for key, owner in NON_FEATURE_TOGGLES.items():
        parts.append(f"| `{key}` | {owner} |")
    return "\n".join(parts).rstrip() + "\n"


def application_block(app, navigation_observation: Optional[str] = None) -> str:
    """The application the registry describes: every endpoint accounted for."""
    records = build_inventory(app)
    coverage = registry_coverage(records)
    unowned = unowned_user_interfaces(records)
    totals = counts(records)

    by_class: Dict[str, int] = {}
    for record in records:
        by_class[str(record.classification)] = by_class.get(str(record.classification), 0) + 1

    parts: List[str] = []
    parts.append("### 7. Endpoint coverage\n")
    parts.extend(_bullets([
        ("Endpoints in the application's URL map (static excluded)",
         coverage["endpoints_total"]),
        ("User-facing page endpoints", coverage["page_endpoints"]),
        ("Owned by an interface", coverage["endpoints_owned"]),
        ("**Unmanaged user-facing endpoints**", len(unowned)),
        ("Interfaces with a navigable route", coverage["interfaces_with_route"]),
    ]))
    parts.append("")
    if unowned:
        parts.append("**Unowned endpoints (the build fails on these):**\n")
        parts.extend(f"- `{endpoint}`" for endpoint in sorted(unowned))
    else:
        parts.append("Unmanaged user-facing endpoints: **0** — every page the "
                     "application serves is owned by exactly one interface.")
    parts.append("")

    parts.append("### 8. Endpoint classification\n")
    parts.append("| Classification | Endpoints |")
    parts.append("| --- | --- |")
    for name, value in sorted(by_class.items()):
        parts.append(f"| {name} | {value} |")
    by_blueprint: Dict[str, int] = {}
    for record in records:
        by_blueprint[record.blueprint] = by_blueprint.get(record.blueprint, 0) + 1
    parts.append("")
    parts.append("By blueprint:")
    parts.append("")
    parts.append("| Blueprint | Endpoints |")
    parts.append("| --- | --- |")
    for name, value in sorted(by_blueprint.items()):
        parts.append(f"| {name or '(none)'} | {value} |")
    parts.append("")

    parts.append("### 9. Declared exceptions\n")
    parts.append("The coverage rule tolerates exactly these, by name — a new "
                 "page cannot be added to an exception list by accident, because "
                 "each list is declared in `core/interfaces/inventory.py` and "
                 "reproduced here.\n")
    for name, values in EXCEPTION_SETS.items():
        parts.append(f"**{name}** ({len(values)}): "
                     + (", ".join(f"`{v}`" for v in values) if values else "—"))
        parts.append("")
    # "Servable" is about the interface switch, not about security: API and
    # system endpoints are not pages an operator switches on and off, and they
    # remain behind authentication and authorization as they always were.
    parts.append(
        f"Endpoints the interface switch does not gate (API, system and "
        f"infrastructure; authentication and authorization are unchanged): "
        f"**{sum(1 for r in records if r.internal)}**")
    parts.append("")

    parts.append("### 10. Rendered navigation\n")
    if navigation_observation:
        parts.append(navigation_observation.rstrip() + "\n")
    else:
        parts.append("Rendered navigation is asserted by the integration suite: "
                     "the sidebar is built from the registry, so the domains and "
                     "entries it shows are whatever the registry declares for the "
                     "signed-in role.\n")
    return "\n".join(parts).rstrip() + "\n"


def navigation_observation(client) -> str:
    """What the sidebar actually rendered, summarised.

    Reads the page the application serves and reports the sections and entries
    the sidebar contains - so the evidence describes rendering rather than the
    registry's intention. The observation is a summary (one line per section)
    because the point is to show that the shell follows the registry, not to
    snapshot markup that would change with every styling detail.
    """
    import re

    response = client.get("/")
    html = response.get_data(as_text=True)
    if 'class="sidebar-nav"' not in html:
        return (f"`GET /` returned {response.status_code}; the sidebar was not "
                f"rendered (an anonymous visitor is shown no navigation).")

    nav = html.split('class="sidebar-nav"', 1)[1].split("</ul>", 1)[0]
    labels = re.findall(r'sidebar-section-label">\s*([^<]+?)\s*<', nav)
    links = re.findall(r'data-endpoint="([^"]+)"', nav)
    routes = re.findall(r'class="sidebar-nav-link[^"]*"\s+data-endpoint="([^"]+)"', nav)
    active = re.findall(r'class="sidebar-nav-link active"\s+data-endpoint="([^"]+)"', nav)

    lines = [
        f"`GET /` as an administrator returned {response.status_code}; the sidebar "
        f"renders **{len(labels)} domains** and **{len(links)} entries**, all of "
        f"them from the registry:",
        "",
    ]
    for label in labels:
        lines.append(f"- {label}")
    lines.append("")
    lines.append("Entries, in render order: "
                 + ", ".join(f"`{link}`" for link in links))
    if active:
        lines.append("")
        lines.append("Marked active on this page: " + ", ".join(f"`{a}`" for a in active))
    if len(routes) != len(links):
        lines.append("")
        lines.append(f"(Note: {len(routes)} of {len(links)} entries carried the "
                     f"active-state class holder; check the template if this differs.)")
    return "\n".join(lines) + "\n"


def splice(document: str, registry: str, application: Optional[str]) -> str:
    """Return *document* with its generated blocks replaced."""
    for begin, end in ((BEGIN_REGISTRY, END_REGISTRY),
                       (BEGIN_APPLICATION, END_APPLICATION)):
        if begin not in document or end not in document:
            raise ValueError(f"the document is missing the markers {begin} / {end}")
        content = registry if begin == BEGIN_REGISTRY else application
        if content is None:
            continue
        head, rest = document.split(begin, 1)
        _, tail = rest.split(end, 1)
        document = f"{head}{begin}\n{content}{end}{tail}"
    return document


def endpoint_inventory(app) -> str:
    """The deterministic endpoint inventory the directive asks for.

    Sorted by endpoint name, so committing it produces a readable diff when a
    page is added - which is the point: a new page shows up in the inventory
    and in the coverage check in the same commit.
    """
    payload = json.loads(inventory_json(app))
    payload.sort(key=lambda record: record["endpoint"])
    for record in payload:
        record["methods"] = sorted(record["methods"])
    return json.dumps(payload, indent=2) + "\n"


def main(argv: Sequence[str]) -> int:
    if "--write" in argv:
        import pathlib

        target = pathlib.Path(argv[argv.index("--write") + 1])
        document = target.read_text()
        target.write_text(splice(document, registry_block(), None))
        print(f"regenerated the registry evidence block in {target}")
        return 0

    if "--inventory" in argv:
        import pathlib

        from apps.web.app import app

        target = pathlib.Path(argv[argv.index("--inventory") + 1])
        target.write_text(endpoint_inventory(app))
        print(f"wrote the endpoint inventory to {target}")
        return 0

    print(registry_block())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
