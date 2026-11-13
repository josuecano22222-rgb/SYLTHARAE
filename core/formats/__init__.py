"""Format identification: what a file *is*, not what it is named.

The two modules here are the front door of the ingestion pipeline:

``catalog``
    The known-format table: identifier, family, MIME type, the extensions that
    legitimately name it, and the reader that processes it. Kept declarative so
    a new format is one entry rather than one ``if`` per call site.

``detection``
    Content-based identification built on top of
    :mod:`core.detect_binanry_utils` (the signature sniffer that already exists
    in production). It answers the questions forensic processing needs before a
    reader is chosen: what is this really, what is it *declared* to be, do the
    two disagree, which container features are present (macros, encryption,
    signatures, templates, slideshows), and which reader and extraction plan
    apply.

Nothing here trusts a filename. The declared name is preserved as evidence, the
content decides the format, and every disagreement between the two is retained
as a discrepancy record rather than being silently resolved.
"""

from .catalog import (
    CATALOG,
    IDENTIFIED_ONLY_REASONS,
    READER_BY_FAMILY,
    FormatFamily,
    FormatSpec,
    alias_extensions,
    canonical_extension,
    catalogue_extensions,
    formats_for_family,
    lookup_by_extension,
    lookup_format,
    reader_extensions,
)
from .detection import (
    DEFAULT_DECLARED_NAME,
    DetectionResult,
    Discrepancy,
    identify,
    identify_bytes,
)

__all__ = [
    "CATALOG",
    "IDENTIFIED_ONLY_REASONS",
    "DEFAULT_DECLARED_NAME",
    "DetectionResult",
    "Discrepancy",
    "FormatFamily",
    "FormatSpec",
    "READER_BY_FAMILY",
    "alias_extensions",
    "canonical_extension",
    "catalogue_extensions",
    "formats_for_family",
    "identify",
    "identify_bytes",
    "lookup_by_extension",
    "lookup_format",
    "reader_extensions",
]
