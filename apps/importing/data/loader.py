"""
Data loaders for the domain import application.

``DataLoaderFactory.load(data_file)`` reads a domain data file and returns
raw rows for the parser: a list of ``(domain_name, [term, ...])`` pairs.
Supported formats (apps.importing.utils.constants.SUPPORTED_FORMATS):

* ``.xlsx`` - one worksheet per domain: the sheet title is the domain name
  and column A holds one term per row (the production layout of
  ``classification/domain_data.xlsx``). The ``Master Index`` sheet, when
  present, is a table of contents and is skipped. As a fallback, a
  single-sheet workbook whose rows carry two columns is read as
  ``domain, term`` pairs with an optional leading header row.
* ``.csv``  - ``domain, term`` pairs, one per row (a leading header row
  containing "domain"/"term" is detected and skipped).
* ``.json`` - a list of objects, one per domain::

      [{"name": "Animals", "terms": ["cat", "sea lion", ...]}, ...]

  (``"domain"`` is accepted as an alias of ``"name"``.)

Note: this module was missing from the repository (its consumers in
apps/importing.core and apps/importing.import_domains import it, but the
file itself was never committed). It is reconstructed against the exact
contract those consumers use: ``DataLoaderFactory().load(path)`` returns a
list whose length is the domain count, and raises ``DataLoadException``
(a ``DomainImportException``) for a missing or unsupported file so
``Application.run`` reports the failure instead of crashing.
"""

import csv
import json
from pathlib import Path
from typing import Any, List, Tuple

from apps.importing.utils.constants import SUPPORTED_FORMATS
from apps.importing.utils.exceptions import DataLoadException
from apps.importing.utils.logger import get_logger

logger = get_logger(__name__)

#: Raw rows handed to the parser: (domain name, [term texts]).
RawRows = List[Tuple[str, List[str]]]

#: Table-of-contents sheet in the production workbook; not a domain.
INDEX_SHEET_NAMES = {"master index"}

_HEADER_HINTS = ("domain", "term", "category", "name")


def _looks_like_header(row: List[Any]) -> bool:
    """True when the first CSV/fallback row names the columns."""
    cells = [str(c).strip().lower() for c in row if c is not None]
    if not cells:
        return True  # blank leading row
    return any(any(h in c for h in _HEADER_HINTS) for c in cells)


def _has_term_headers(sheets) -> bool:
    """True when a single worksheet uses the ``Term`` / ``Keyword`` header."""
    from apps.importing.validators.workbook_rules import KEYWORD_HEADER, TERM_HEADER

    if len(sheets) != 1:
        return False
    first = next(sheets[0].iter_rows(values_only=True, max_row=1), None)
    if not first:
        return False
    cells = list(first) + [None, None]
    return (str(cells[0] or "").strip().casefold().startswith(TERM_HEADER.casefold())
            or str(cells[1] or "").strip().casefold().startswith(KEYWORD_HEADER.casefold()))


def _load_category_workbook(path: Path) -> RawRows:
    """Read the category workbook through the shared rules, or refuse it.

    Raises:
        DataLoadException: the workbook breaks a data-entry rule. The message
            names the sheet and the cell, because "import failed" is not
            something an operator can act on.
    """
    from apps.importing.validators.workbook_rules import read

    # Repeated entries are valid for this import path: repository operations
    # reuse existing words and category links instead of creating duplicates.
    workbook = read(str(path), allow_duplicates=True)
    if not workbook.ok:
        problems = "; ".join(str(v) for v in workbook.errors[:5])
        more = len(workbook.errors) - 5
        if more > 0:
            problems += f"; and {more} more"
        raise DataLoadException(
            f"{path.name} breaks the category data rules: {problems}"
        )
    rows: RawRows = []
    for category in workbook.categories:
        terms = category.term_texts + category.keyword_texts
        if terms:
            rows.append((category.name, terms))
    if not rows:
        raise DataLoadException(
            f"{path.name}: no worksheet contains a term or a keyword"
        )
    for decision in workbook.decisions:
        logger.warning(f"{path.name}: {decision}")
    return rows


class DataLoaderFactory:
    """Loads a domain data file in any supported format."""

    def load(self, data_file: str) -> RawRows:
        """Read ``data_file`` and return ``(domain, [terms])`` rows.

        Raises:
            DataLoadException: the file is missing, has an unsupported
                extension, or cannot be read/parsed.
        """
        path = Path(data_file)
        if not path.exists():
            raise DataLoadException(f"Data file not found: {data_file}")

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_FORMATS:
            raise DataLoadException(
                f"Unsupported data file format '{suffix}'. "
                f"Supported formats: {', '.join(SUPPORTED_FORMATS)}"
            )

        logger.info(f"Loading data file: {data_file} ({suffix})")
        try:
            if suffix == ".xlsx":
                rows = self._load_xlsx(path)
            elif suffix == ".csv":
                rows = self._load_csv(path)
            else:
                rows = self._load_json(path)
        except DataLoadException:
            raise
        except Exception as exc:
            raise DataLoadException(f"Failed to read {data_file}: {exc}") from exc

        if not rows:
            raise DataLoadException(f"Data file is empty: {data_file}")

        logger.info(f"Loaded {len(rows)} domains from {path.name}")
        return rows

    # -- format readers --------------------------------------------------

    @staticmethod
    def _load_xlsx(path: Path) -> RawRows:
        """One worksheet per domain; two-column fallback for flat workbooks."""
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover - openpyxl is a requirement
            raise DataLoadException(
                "openpyxl is required to read .xlsx data files"
            ) from exc

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = [
                ws for ws in workbook.worksheets
                if ws.title.strip().lower() not in INDEX_SHEET_NAMES
            ]

            # Sheet-per-category layout: worksheet name = category name, and
            # the worksheet's two columns are its terms (one word each) and its
            # keywords (two or more words). The shape rules live in
            # ``apps.importing.validators.workbook_rules`` so the importer, the
            # sync tool and the tests cannot disagree about them; a file that
            # breaks a rule is refused with the cell that broke it, rather than
            # imported approximately.
            category_sheets = [
                ws for ws in sheets
                if ws.title.strip().lower() not in INDEX_SHEET_NAMES
                and not ws.title.strip().startswith("_")
            ]
            if len(category_sheets) > 1 or _has_term_headers(category_sheets):
                return _load_category_workbook(path)


            # Flat fallback: a single sheet of ``domain, term`` columns.
            rows = []
            sheet = sheets[0] if sheets else workbook.active
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                cells = list(row) + [None, None]
                domain = str(cells[0]).strip() if cells[0] is not None else ""
                term = str(cells[1]).strip() if cells[1] is not None else ""
                if index == 0 and _looks_like_header([cells[0], cells[1]]):
                    continue
                if not domain and not term:
                    continue
                if not domain or not term:
                    raise DataLoadException(
                        f"{path.name} row {index + 1}: each row needs a "
                        f"domain name and a term (got {list(row)})"
                    )
                rows.append((domain, [term]))
            return rows
        finally:
            workbook.close()

    @staticmethod
    def _load_csv(path: Path) -> RawRows:
        """One term per row: column 0 = domain, column 1 = term."""
        rows: RawRows = []
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader):
                cells = list(row) + ["", ""]
                domain = cells[0].strip()
                term = cells[1].strip()
                if index == 0 and _looks_like_header([cells[0], cells[1]]):
                    continue
                if not domain and not term:
                    continue
                if not domain or not term:
                    raise DataLoadException(
                        f"{path.name} row {index + 1}: each row needs a "
                        f"domain name and a term (got {row})"
                    )
                rows.append((domain, [term]))
        return rows

    @staticmethod
    def _load_json(path: Path) -> RawRows:
        """A list of ``{"name": ..., "terms": [...]}`` objects."""
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        if not isinstance(data, list):
            raise DataLoadException(
                f"{path.name}: expected a JSON list of domain objects"
            )

        rows: RawRows = []
        for index, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise DataLoadException(
                    f"{path.name} entry {index + 1}: expected an object with "
                    f"'name' and 'terms'"
                )
            name = str(entry.get("name") or entry.get("domain") or "").strip()
            terms = entry.get("terms") or []
            if not name:
                raise DataLoadException(
                    f"{path.name} entry {index + 1}: missing 'name'"
                )
            if not isinstance(terms, list):
                raise DataLoadException(
                    f"{path.name} entry {index + 1}: 'terms' must be a list"
                )
            rows.append((name, [str(t) for t in terms]))
        return rows
