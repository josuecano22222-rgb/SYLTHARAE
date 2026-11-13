"""Forensic detail extraction shared by the format readers.

The readers in :mod:`reader_file.readers` own the *content* of a document -
text, cells, slides. The modules here own the parts that a library such as
python-docx or openpyxl does not surface, and which a forensic record needs:

* document and custom properties, authors, creation and modification dates;
* hyperlinks, external relationships and embedded objects, with their targets;
* comments, tracked revisions, hidden content, fields and bookmarks;
* digital signatures, encryption and macro projects.

Two rules apply throughout:

1. **Nothing is silently dropped.** Every sub-extraction that fails records a
   reason in ``extraction_errors``; every part skipped for size records its
   name in ``skipped_parts``. A missing field means "the format does not have
   it", never "we did not get round to it".
2. **Bounded work.** Parts are read whole only when small; counts and text are
   capped and truncation is reported, so a 600 MB spreadsheet with 200k rows
   cannot turn metadata extraction into an out-of-memory event.
"""

from .office_package import (
    DEFAULT_LIMITS,
    PackageLimits,
    extract_package_features,
    iter_feature_text,
    macro_parts,
    package_inventory,
    text_from_features,
)
from .vba import extract_vba, macro_summary

__all__ = [
    "DEFAULT_LIMITS",
    "PackageLimits",
    "extract_package_features",
    "extract_vba",
    "iter_feature_text",
    "macro_parts",
    "macro_summary",
    "package_inventory",
    "text_from_features",
]
