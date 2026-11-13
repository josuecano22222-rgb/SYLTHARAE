"""The in-app importer accepts the committed workbook and refuses a broken one.

The loader is the door the Import Center uses. It must speak the same rules as
the sync tool - otherwise a file the tool accepts is refused by the interface,
or worse, accepted and imported approximately. Both halves of that sentence are
checked here against the real committed file.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from apps.importing.data.loader import DataLoaderFactory  # noqa: E402
from apps.importing.data.parser import DataParser  # noqa: E402
from apps.importing.utils.exceptions import DataLoadException  # noqa: E402

WORKBOOK = ROOT / "classification" / "domain_data.xlsx"

pytestmark = pytest.mark.skipif(not WORKBOOK.exists(),
                                reason="classification/domain_data.xlsx not present")


def test_the_committed_workbook_loads_as_its_category_sheets():
    rows = DataLoaderFactory().load(str(WORKBOOK))
    assert len(rows) == 56
    names = [name for name, _terms in rows]
    assert names[0] == "Networks" and len(set(names)) == len(names)
    # the helper sheets are not domains
    assert not any(name.startswith("_") or name == "Master Index" for name in names)


def test_every_loaded_row_carries_its_terms_and_keywords():
    rows = DataLoaderFactory().load(str(WORKBOOK))
    total = sum(len(terms) for _name, terms in rows)
    assert total == 3096 + 7958, "a row lost content between the sheets and the loader"


def test_the_parser_separates_words_from_phrases():
    """The loader is flat; the parser must recover the two kinds exactly.

    It recovers them from shape, which is sound only because the rules make a
    term one word and a keyword two or more. This asserts the two agree.
    """
    rows = DataLoaderFactory().load(str(WORKBOOK))
    domains = DataParser().parse(rows)
    assert len(domains) == 56
    words = sum(1 for domain in domains for term in domain.terms
                if term.is_single_word())
    phrases = sum(1 for domain in domains for term in domain.terms
                  if not term.is_single_word())
    assert (words, phrases) == (3096, 7958)


def test_a_broken_workbook_is_refused_and_the_cell_is_named(tmp_path):
    copy = tmp_path / "domain_data.xlsx"
    shutil.copy(WORKBOOK, copy)
    book = openpyxl.load_workbook(copy)
    sheet = book["Networks"]
    sheet.cell(row=2, column=1, value="Load balancing")   # a phrase in Term
    book.save(copy)

    with pytest.raises(DataLoadException) as caught:
        DataLoaderFactory().load(str(copy))
    message = str(caught.value)
    assert "Networks!A2" in message
    assert "TERM_HAS_WHITESPACE" in message
    assert "Keyword" in message, "the operator is not told where it belongs"


def test_importer_preserves_repeated_terms_and_keywords(tmp_path):
    path = tmp_path / "repeats.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Networks"
    sheet.append(["Term", "Keyword"])
    sheet.append(["Server", "Load balancing"])
    sheet.append(["Server", "Load balancing"])
    book.save(path)

    rows = DataLoaderFactory().load(str(path))

    assert rows == [
        ("Networks", ["Server", "Server", "Load balancing", "Load balancing"]),
    ]


def test_a_missing_file_is_refused_by_name(tmp_path):
    with pytest.raises(DataLoadException) as caught:
        DataLoaderFactory().load(str(tmp_path / "nope.xlsx"))
    assert "not found" in str(caught.value).lower()


def test_an_unsupported_extension_is_refused(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("Networks,Router\n", encoding="utf-8")
    with pytest.raises(DataLoadException) as caught:
        DataLoaderFactory().load(str(path))
    assert "Unsupported" in str(caught.value)
