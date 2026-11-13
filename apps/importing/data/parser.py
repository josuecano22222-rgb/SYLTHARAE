"""
Parser: turns the loader's raw rows into ``Domain`` model objects.

The loader returns ``(domain_name, [term, ...])`` pairs; ``DataParser.parse``
groups them by domain and derives each term's type from its shape (whitespace
separates a phrase, anything else is a single word). Empty terms are dropped.

Note: this module was missing from the repository (its consumers in
apps/importing.core and apps/importing.import_domains import it, but the file
itself was never committed). It is reconstructed against the exact contract
its consumers use: ``DataParser().parse(rows)`` returns a list of ``Domain``
whose length the orchestrator reports as the parsed domain count.
"""

from typing import Any, List

from apps.importing.data.loader import RawRows
from apps.importing.data.models import Domain, Term
from apps.importing.utils.exceptions import DataParseException
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)


class DataParser:
    """Converts raw loader rows into ``Domain`` objects."""

    def parse(self, raw_data: RawRows) -> List[Domain]:
        """Group ``(domain, [terms])`` rows into ordered ``Domain`` objects.

        Raises:
            DataParseException: the raw data is not in the expected shape.
        """
        if not raw_data:
            raise DataParseException("No data to parse")

        if not isinstance(raw_data, (list, tuple)):
            raise DataParseException(
                f"Expected a list of (domain, terms) rows, "
                f"got {type(raw_data).__name__}"
            )

        domains: List[Domain] = []
        by_name = {}

        for entry in raw_data:
            try:
                name, terms = entry
            except (TypeError, ValueError) as exc:
                raise DataParseException(
                    f"Malformed raw row (expected (domain, terms)): {entry!r}"
                ) from exc

            name = str(name).strip()
            if not name:
                raise DataParseException("Encountered a domain with an empty name")

            if name not in by_name:
                domain = Domain(name=name)
                by_name[name] = domain
                domains.append(domain)
            else:
                domain = by_name[name]

            if isinstance(terms, str):
                terms = [terms]
            for text in terms or []:
                text = str(text).strip()
                if text:
                    domain.terms.append(Term(text))

        for domain in domains:
            logger.info(
                f"Parsed domain '{domain.name}' with {len(domain)} term(s)"
            )
        return domains
