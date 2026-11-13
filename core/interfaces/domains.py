"""Application domains - what part of the work an interface belongs to.

A domain is the *operator's* grouping of the product, not a database schema and
not a settings category. It answers "what is this for?" so that navigation,
permissions, help and setting screens can all be organised the same way, and so
that nobody has to guess whether "Analyst Categories" is a discovery tool or an
analysis tool.

Every interface belongs to exactly one domain. The set is closed: a new domain
is a deliberate product decision, made here, not a string invented at a call
site. :func:`validate_registry` enforces that.

The information model itself is documented alongside these domains in
``docs/DOMAIN_MODEL.md`` (Source → Side → Hash → Path → Content → Analysis …
plus the analyst layer) - the domains here are the *work surface*, the model
there is the *data substrate*.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Tuple


class Domain(str, Enum):
    """The task domains the product is organised around."""

    WORK = "WORK"
    DISCOVER = "DISCOVER"
    INGEST = "INGEST"
    PROCESS = "PROCESS"
    ANALYZE = "ANALYZE"
    CLASSIFY = "CLASSIFY"
    REPORT = "REPORT"
    OPERATE = "OPERATE"
    ADMINISTRATION = "ADMINISTRATION"
    SETTINGS = "SETTINGS"
    SECURITY = "SECURITY"
    INTERNAL = "INTERNAL"

    def __str__(self) -> str:  # so f-strings and templates read "WORK", not "Domain.WORK"
        return self.value


#: The order domains are presented in: the working loop first, then the
#: machinery that supports it, with internal surfaces last.
DOMAIN_ORDER: Tuple[Domain, ...] = (
    Domain.WORK,
    Domain.DISCOVER,
    Domain.INGEST,
    Domain.PROCESS,
    Domain.ANALYZE,
    Domain.CLASSIFY,
    Domain.REPORT,
    Domain.OPERATE,
    Domain.ADMINISTRATION,
    Domain.SETTINGS,
    Domain.SECURITY,
    Domain.INTERNAL,
)

#: Short labels for headings: a domain is an identifier (ANALYZE), a label is
#: something a person reads.
DOMAIN_LABELS: Dict[Domain, str] = {
    Domain.WORK: "Work",
    Domain.DISCOVER: "Discover",
    Domain.INGEST: "Ingest",
    Domain.PROCESS: "Processing",
    Domain.ANALYZE: "Analyze",
    Domain.CLASSIFY: "Classify",
    Domain.REPORT: "Report",
    Domain.OPERATE: "Operate",
    Domain.ADMINISTRATION: "Administration",
    Domain.SETTINGS: "Settings",
    Domain.SECURITY: "Security",
    Domain.INTERNAL: "Cross-cutting and internal",
}


def domain_label(domain) -> str:
    """The heading to show for a domain."""
    try:
        from core.i18n import translate_ui

        return translate_ui(DOMAIN_LABELS[coerce_domain(domain)])
    except Exception:
        return str(domain)


#: One line per domain, used by the settings screen and the registry document
#: so the operator is told what a domain means rather than shown a bare label.
DOMAIN_DESCRIPTIONS: Dict[Domain, str] = {
    Domain.WORK: "The working overview - where a session starts and what needs attention.",
    Domain.DISCOVER: "Finding and reading what is stored: files, sources, sides, words, categories, notifications.",
    Domain.INGEST: "Bringing material in: input, imports and the validation that precedes them.",
    Domain.PROCESS: "Reading, extracting and storing: what happens to material once it is accepted.",
    Domain.ANALYZE: "Working the material: archives, paths, batches, classification and relationships.",
    Domain.CLASSIFY: "Deciding what material is: analyst categories and classification outcomes.",
    Domain.REPORT: "Summarising and presenting findings.",
    Domain.OPERATE: "Running the system: jobs, recovery and operational tooling.",
    Domain.ADMINISTRATION: "People, roles, audit and system health.",
    Domain.SETTINGS: "Configuration of the product itself.",
    Domain.SECURITY: "Authentication, authorization and the controls around them.",
    Domain.INTERNAL: "Surfaces the product needs but does not advertise.",
}


def domain_values() -> Tuple[str, ...]:
    """The allowed domain values, as plain strings."""
    return tuple(d.value for d in Domain)


def coerce_domain(value) -> Domain:
    """Accept a ``Domain`` or its string form; anything else is a mistake."""
    if isinstance(value, Domain):
        return value
    return Domain(str(value).upper())
