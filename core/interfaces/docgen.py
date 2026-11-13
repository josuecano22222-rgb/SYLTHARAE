"""Generate the registry reference from the registry.

A table of interfaces maintained by hand is a table that is wrong within a
month - the same reason the endpoint inventory is generated from the URL map
rather than counted. ``docs/INTERFACE_REGISTRY.md`` embeds the output of
:func:`reference_markdown`, and ``tests/unit/test_interface_docs.py`` compares
the two, so the document cannot drift from the product it describes.

Regenerate with::

    python3 -c "from core.interfaces.docgen import *; print(reference_markdown())"
    # or the whole document:
    python3 -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md
"""

from __future__ import annotations

import sys
from typing import Dict, List

from .domains import DOMAIN_ORDER
from .registry import FEATURES, REGISTRY, get_interfaces_by_domain, summary

#: Markers the document uses to know which part it must not edit by hand.
BEGIN = "<!-- BEGIN GENERATED REGISTRY TABLE -->"
END = "<!-- END GENERATED REGISTRY TABLE -->"

_TABLE_HEADER = (
    "| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def _cell(value) -> str:
    return "—" if value in (None, "", ()) else value


def _row(interface) -> str:
    aliases = ", ".join(f"`{a}`" for a in interface.aliases) or "—"
    deps = ", ".join(f"`{d}`" for d in interface.dependencies) or "—"
    return (
        f"| `{interface.interface_id}` | {interface.name} | {interface.domain} "
        f"| {'`' + interface.route + '`' if interface.route else '—'} | {aliases} "
        f"| {interface.required_role or 'any'} "
        f"| {'on' if interface.default_enabled else 'off'} | {deps} "
        f"| {_cell(interface.help_topic)} | {_cell(interface.keyboard_shortcut)} "
        f"| {interface.status} |"
    )


def summary_lines() -> List[str]:
    """The generated counts, as the bullet list the document shows."""
    data = summary()
    lines = [
        f"- Interfaces: **{data['interfaces']}** "
        f"(features declared separately: **{data['features']}**)",
        f"- Endpoints owned: **{data['endpoints_owned']}**",
        f"- With a keyboard shortcut: **{data['with_shortcut']}**; "
        f"with a help topic: **{data['with_help']}**",
        "- By domain: " + ", ".join(
            f"{domain} {count}" for domain, count in sorted(data["by_domain"].items())),
        "- By status: " + ", ".join(
            f"{status} {count}" for status, count in sorted(data["by_status"].items())),
        "- By kind: " + ", ".join(
            f"{kind} {count}" for kind, count in sorted(data["by_kind"].items())),
    ]
    return lines


def reference_markdown() -> str:
    """The interface reference: counts, then one table per domain."""
    parts: List[str] = ["".join(line + "\n" for line in summary_lines())]
    grouped: Dict[str, list] = get_interfaces_by_domain()

    for domain in DOMAIN_ORDER:
        entries = grouped.get(str(domain))
        if not entries:
            continue
        parts.append(f"\n### {domain} ({len(entries)})\n")
        parts.append(_TABLE_HEADER)
        parts.extend(_row(i) for i in entries)

    if FEATURES:
        parts.append("\n### Features (not interfaces)\n")
        parts.append("| ID | Name | Description | Default |\n| --- | --- | --- | --- |")
        for feature in FEATURES:
            parts.append(
                f"| `{feature.feature_id}` | {feature.name} | {feature.description} "
                f"| {'on' if feature.default_enabled else 'off'} |")

    return "\n".join(parts).strip() + "\n"


def splice(document: str) -> str:
    """Return *document* with its generated block replaced."""
    if BEGIN not in document or END not in document:
        raise ValueError(
            f"the document is missing the generated block markers ({BEGIN} / {END})")
    head, rest = document.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{reference_markdown()}{END}{tail}"


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(reference_markdown())
        return 0
    import pathlib

    path = pathlib.Path(argv[1])
    path.write_text(splice(path.read_text()))
    print(f"regenerated the registry table in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
