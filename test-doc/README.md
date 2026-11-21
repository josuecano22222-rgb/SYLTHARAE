# Test corpus

Sample documents used as fixtures by the extraction and reader tests (688 files,
about 67 MB, in `Documents/`, `Spreadsheets/`, `Presentations/`, `Images/`,
`Audio/`, `Archives/`, `Executables/`, `Programming/`, `Design/`, `Other/`,
`Python/`).

## Why it is here

Real office files contain what generated ones do not: embedded OLE objects,
nested tables, content controls, unusual encodings, truncated archives and
files that are deliberately malformed. Several reader behaviours exist only
because one of these files exposed them - `Documents/testInstrLink.docx` is a
body that is a table nested more than a dozen levels deep, and
`Spreadsheets/testRecordSizeExceeded.xlsx` is the size-cap fixture. Tests refer
to these files by name; replacing the corpus means rewriting those tests.

## Licensing

The documents are third-party material. The repository records no per-file
origin or licence, so their terms are **unresolved, not permissive**. Because of
that:

* the corpus is excluded from the built wheel (`pyproject.toml`,
  `packages.find.exclude`) and is never served by the application;
* it must not be redistributed as part of a published package;
* a public mirror or an sdist needs either a corpus whose licence is known, or
  a per-file provenance record (`file, source URL, licence, retrieved on`).

Track this in `THIRD_PARTY_NOTICES.md` ("Test corpus"), which carries the same
statement for the release checklist.
