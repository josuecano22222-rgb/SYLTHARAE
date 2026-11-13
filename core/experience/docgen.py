"""Generate the Experience Contract reference from the contracts themselves.

``docs/EXPERIENCE_CONTRACT.md`` embeds the output of :func:`reference_markdown`
between markers, and a test compares the two, so the document cannot drift from
the product. Every count in it is generated; nothing is typed.

Regenerate with::

    python3 -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md
"""

from __future__ import annotations

import sys
from typing import Dict, List

from .contract import (
    contracts,
    counts as contract_counts,
    screens_without_experience,
)
from .coverage import counts as coverage_counts, language_coverage, screen_coverage
from .validation import check_all

BEGIN = "<!-- BEGIN GENERATED EXPERIENCE CONTRACT -->"
END = "<!-- END GENERATED EXPERIENCE CONTRACT -->"

_TABLE_HEADER = (
    "| Interface | Domain | Declared | Title key | Columns | Filters | "
    "Actions | States | Help |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def _cell(value) -> str:
    return "—" if value in (None, "", (), 0) else value


def _contract_row(item) -> str:
    screen = item.screen
    return (
        f"| `{item.interface_id}` | {item.domain} "
        f"| {'yes' if screen.declared else 'derived'} "
        f"| `{screen.title_key}` "
        f"| {_cell(len(screen.columns))} | {_cell(len(screen.filters))} "
        f"| {_cell(len(screen.actions))} | {_cell(len(screen.states))} "
        f"| {'yes' if screen.help else '—'} |"
    )


def reference_markdown() -> str:
    """The whole generated block: counts, contracts, coverage, gaps."""
    counts = contract_counts()
    coverage = coverage_counts()
    lines: List[str] = []

    lines.append("### What this build declares\n")
    lines.append(
        f"- Contracts: **{counts['contracts']}** "
        f"(described: **{counts['declared_screens']}**, derived from the "
        f"registry only: **{counts['derived_screens']}**)")
    lines.append(
        f"- Definitions: **{counts['actions']}** actions, "
        f"**{counts['columns']}** columns, **{counts['filters']}** filters, "
        f"**{counts['fields']}** fields, **{counts['states']}** states")
    lines.append(
        f"- Translation keys the screens need: **{counts['translations']}**")
    lines.append(
        f"- With help: **{counts['with_help']}**; with a shortcut: "
        f"**{counts['with_shortcut']}**; with a navigation entry: "
        f"**{counts['with_navigation']}**\n")

    lines.append("### Every screen\n")
    lines.append(_TABLE_HEADER)
    for item in contracts():
        lines.append(_contract_row(item))
    lines.append("")

    lines.append("### Translation coverage, measured\n")
    lines.append(
        "Counted from the catalogs the build ships - the Babel catalogs under "
        "`translations/` and the JavaScript UI packs under "
        "`static/js/i18n/locales` - against the strings in the message "
        "template. `translated` excludes strings that are identical to the "
        "source, which are counted as `fallback`.\n")
    lines.append("| Language | Catalog entries | Source strings | Translated | "
                 "Fallback | Missing | Coverage | Of which translated |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in language_coverage():
        lines.append(
            f"| `{row['locale']}` | {row['entries']} | {row['source_strings']} "
            f"| {row['translated']} | {row['fallback']} | {row['missing']} "
            f"| {row['coverage']}% | {row['translated_coverage']}% |")
    lines.append("")

    lines.append("### Coverage per screen\n")
    lines.append(
        "The strings a screen's contract asks for, and how many of them a "
        "language actually translates. Looked up by semantic key first and by "
        "the English source string second, because the catalogs still key by "
        "source string today; `by key` is the part of the migration that has "
        "happened.\n")
    lines.append("| Interface | Keys | " + " | ".join(
        row["locale"] for row in language_coverage()) + " |")
    lines.append("| --- | --- | " + " | ".join(
        "---" for _ in language_coverage()) + " |")
    for screen in screen_coverage():
        cells = []
        for language in language_coverage():
            match = next((entry for entry in screen["languages"]
                          if entry["locale"] == language["locale"]), None)
            if match is None or not screen["keys"]:
                cells.append("—")
            else:
                cells.append(
                    f"{match['coverage']}% ({match['by_key']} by key)")
        lines.append(
            f"| `{screen['interface_id']}` | {screen['keys']} | "
            + " | ".join(cells) + " |")
    lines.append("")

    gaps = screens_without_experience()
    lines.append("### Screens nobody has described yet\n")
    lines.append(
        "These have a contract derived from the registry, so they work: they "
        "have an identity, a navigation entry, a lifecycle and a help topic. "
        "What they do not have is a description of what they offer - columns, "
        "filters, actions, states - because nobody has decided it. The list is "
        "the remaining work, not a defect.\n")
    lines.append(", ".join(f"`{name}`" for name in gaps) if gaps else
                 "Every navigable screen is described.")
    lines.append("")

    problems = check_all()
    lines.append("### Contract validation\n")
    if problems:
        lines.append("The following definitions fail validation and are defects:\n")
        lines += [f"- {problem}" for problem in problems]
    else:
        lines.append(
            "Every contract passes the declarative checks: no SQL, no imports, "
            "no calls, no authorisation decisions, destructive actions carry a "
            "confirmation, bulk actions require a selection, and no key holds "
            "two different source strings.")
    lines.append("")

    lines.append(
        f"_Generated from {counts['contracts']} contracts, "
        f"{coverage['source_strings']} source strings and "
        f"{coverage['catalogs']} catalogs._")
    return "\n".join(lines) + "\n"


def write_document(path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        document = handle.read()
    if BEGIN not in document or END not in document:
        raise SystemExit(
            "the document is missing the generated block markers "
            f"({BEGIN} / {END})")
    head, rest = document.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{head}{BEGIN}\n{reference_markdown()}{END}{tail}")


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        print("usage: python3 -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md")
        return 2
    write_document(argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
