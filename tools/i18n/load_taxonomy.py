#!/usr/bin/env python3
"""Load the operator's real taxonomy workbook into a database.

Criterion 5 asks for collisions "in the real taxonomy", and the real taxonomy is
``classification/domain_data.xlsx``: a *Master Index* sheet of every term, plus
one sheet per domain whose rows are the terms assigned to that domain. Nothing
in the fixture data has the shape of that file, which is why the audit needs a
loader rather than a seeded table.

    python -m tools.i18n.load_taxonomy --dry-run
    python -m tools.i18n.load_taxonomy --apply

What it writes, and only this:

* ``words`` - one row per distinct term (the Master Index and every domain row);
* ``categorys`` - one row per domain sheet, named by the sheet (``word_id``);
* ``words_categorys`` - the domain's membership list.

A term that appears in five domains is one ``words`` row and five links: that is
the model the application already uses, and merging is what makes the collision
audit meaningful (the same string is one term, not five).

Idempotent: re-running inserts nothing, so the audit can be re-measured after a
workbook edit without rebuilding the database. ``term_names`` is **not**
touched - the backfill tool owns that, and keeping them separate is what lets
the collision audit see a vocabulary before any translation exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Iterable, List, Set, Tuple

DEFAULT_XLSX = "classification/domain_data.xlsx"
MASTER_SHEET = "Master Index"


def _connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("DB_HOST") or "localhost",
        port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
        dbname=os.environ.get("DB_NAME") or "postgres",
    )


def read_workbook(path: str) -> Tuple[Set[str], Dict[str, List[str]]]:
    """Return (every term, {domain: members}) with whitespace normalised."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    terms: Set[str] = set()
    domains: Dict[str, List[str]] = {}

    def clean(value) -> str:
        return " ".join(str(value or "").split())

    for name in book.sheetnames:
        sheet = book[name]
        column = []
        for row in sheet.iter_rows(values_only=True):
            value = clean(row[0] if row else "")
            if value:
                column.append(value)
        if name == MASTER_SHEET:
            terms.update(column)
        else:
            # The first row of a domain sheet repeats the domain's own name;
            # it is a term like any other and is kept.
            domains[name] = column
            terms.update(column)
    return terms, domains


def load(conn, terms: Iterable[str], domains: Dict[str, List[str]],
         *, dry_run: bool = True) -> Dict[str, object]:
    terms = sorted(terms)
    with conn.cursor() as cur:
        before = {}
        for table in ("words", "categorys", "words_categorys"):
            cur.execute(f"SELECT count(*) FROM {table}")  # nosec B608 - fixed names
            before[table] = cur.fetchone()[0]

        inserted_words = 0
        for term in terms:
            cur.execute("SELECT 1 FROM words WHERE word = %s", (term,))
            if cur.fetchone():
                continue
            inserted_words += 1
            if not dry_run:
                cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT DO NOTHING",
                            (term,))

        inserted_categories = 0
        inserted_links = 0
        for domain in sorted(domains):
            members = domains[domain]
            cur.execute("SELECT 1 FROM words WHERE word = %s", (domain,))
            if not cur.fetchone():
                inserted_words += 1
                if not dry_run:
                    cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT DO NOTHING",
                                (domain,))
            if dry_run:
                cur.execute(
                    "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
                    " WHERE w.word = %s", (domain,))
                found = cur.fetchone()
                if not found:
                    inserted_categories += 1
                inserted_links += len(members)
                continue
            cur.execute(
                "INSERT INTO categorys (word_id) SELECT id FROM words WHERE word = %s"
                " ON CONFLICT DO NOTHING", (domain,))
            cur.execute(
                "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
                " WHERE w.word = %s", (domain,))
            category_id = cur.fetchone()[0]
            for member in members:
                cur.execute(
                    "INSERT INTO words_categorys (word_id, category_id)"
                    " SELECT id, %s FROM words WHERE word = %s"
                    " ON CONFLICT DO NOTHING", (category_id, member))
                inserted_links += cur.rowcount

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    after = {}
    with conn.cursor() as cur:
        for table in ("words", "categorys", "words_categorys"):
            cur.execute(f"SELECT count(*) FROM {table}")  # nosec B608 - fixed names
            after[table] = cur.fetchone()[0]
    return {
        "dry_run": dry_run,
        "terms_in_workbook": len(terms),
        "domains_in_workbook": len(domains),
        "inserted": {"words": inserted_words, "categorys": inserted_categories,
                     "words_categorys": inserted_links},
        "before": before,
        "after": after,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--xlsx", default=DEFAULT_XLSX)
    parser.add_argument("--dry-run", action="store_true", help="report only (default)")
    parser.add_argument("--apply", action="store_true", help="write the rows")
    args = parser.parse_args(argv)

    terms, domains = read_workbook(args.xlsx)
    conn = _connect()
    try:
        report = load(conn, terms, domains, dry_run=not args.apply)
    finally:
        conn.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
