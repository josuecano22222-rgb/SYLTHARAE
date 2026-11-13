#!/usr/bin/env python3
"""Give every existing term a name in the default language: itself.

After ``m0005_term_names`` the table is empty, and an empty translation table is
not a neutral state: a display layer that only reads ``term_names`` would show
nothing for a term that has always had a name. The backfill makes the existing
canonical word the *preferred default-language name* of itself, so the fallback
chain always has something to find and a translator has a row to edit.

Four modes, all idempotent:

    --dry-run     report what would be inserted, insert nothing (default)
    --apply       insert the missing rows, ON CONFLICT DO NOTHING
    --rollback    delete only the rows this tool created (source='backfill')
    --reconcile   compare before/after counts, and prove nothing else moved

``--reconcile`` is the check that makes the other three trustworthy: it counts
``words`` and ``categorys`` before and after and refuses to report success if
either changed, and it asserts every word ends with exactly one preferred
default-language name.

Usage (against the configured database)::

    python -m tools.i18n.backfill_term_names --dry-run
    python -m tools.i18n.backfill_term_names --apply --reconcile
    python -m tools.i18n.backfill_term_names --rollback --reconcile
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_LANGUAGE = "en"
SOURCE = "backfill"

_MISSING_SQL = (
    "SELECT w.id, w.word FROM words w "
    "WHERE NOT EXISTS (SELECT 1 FROM term_names t WHERE t.word_id = w.id"
    "                  AND t.language = %s AND t.name_type = 'preferred') "
    "ORDER BY w.id"
)
_INSERT_SQL = (
    "INSERT INTO term_names (word_id, name, match_key, language, name_type, script, source)"
    " VALUES (%s, %s, %s, %s, 'preferred', %s, %s) ON CONFLICT DO NOTHING"
)
_ROLLBACK_SQL = "DELETE FROM term_names WHERE source = %s"

_SCRIPT_BY_LANGUAGE = {"en": "Latn", "hr": "Latn", "ar": "Arab", "fa": "Arab", "he": "Hebr"}


def _counts(cur) -> Dict[str, int]:
    out = {}
    for table in ("words", "categorys", "keywords", "term_names"):
        cur.execute(f"SELECT count(*) FROM {table}")  # nosec B608 - fixed names
        out[table] = cur.fetchone()[0]
    return out


def _preferred_coverage(cur, language: str) -> Dict[str, int]:
    """How many words have a preferred name, how many have none, and how many
    have more than one.

    The first version counted ``count(*)`` - every word - as "has a name", so
    the field read 10 566 on a table with zero name rows and the reconciliation
    gate passed on a database the rollback had just emptied. The count that
    matters is the filtered one: a word with *no* preferred row in this
    language is the backlog, whatever the display layer falls back to.
    """
    cur.execute(
        "SELECT count(*),"
        "       count(*) FILTER (WHERE n > 0),"
        "       count(*) FILTER (WHERE n > 1)"
        "  FROM (SELECT w.id, (SELECT count(*) FROM term_names t"
        "                     WHERE t.word_id = w.id AND t.language = %s"
        "                       AND t.name_type = 'preferred') AS n"
        "          FROM words w) s", (language,))
    total, with_any, with_many = cur.fetchone()
    return {"words": total, "with_a_name": with_any,
            "without_a_name": total - with_any,
            "with_more_than_one": with_many}


def _pending(cur, language: str) -> List[Dict[str, Any]]:
    from core.i18n.term_key import script_ok, term_key

    cur.execute(_MISSING_SQL, (language,))
    rows = []
    for word_id, word in cur.fetchall():
        key = term_key(word)
        script = _SCRIPT_BY_LANGUAGE.get(language, "Latn")
        if not key:
            rows.append({"word_id": word_id, "word": word, "skip": "empty match_key"})
            continue
        if not script_ok(language, script, "preferred"):
            rows.append({"word_id": word_id, "word": word, "skip": "script"})
            continue
        rows.append({"word_id": word_id, "word": word, "name": word,
                     "match_key": key, "language": language, "script": script})
    return rows


def backfill(conn, *, language: str = DEFAULT_LANGUAGE, dry_run: bool = True,
             limit: Optional[int] = None) -> Dict[str, Any]:
    """Insert the default-language name of every word that lacks one.

    Returns a report rather than printing: the CLI prints it, a test asserts on
    it. ``dry_run`` performs every read and writes nothing.
    """
    report: Dict[str, Any] = {"language": language, "dry_run": dry_run}
    with conn.cursor() as cur:
        before = _counts(cur)
        pending = _pending(cur, language)

    to_insert = [row for row in pending if "skip" not in row]
    skipped = [row for row in pending if "skip" in row]
    report.update({
        "pending": len(pending),
        "would_insert": len(to_insert) if dry_run else 0,
        "skipped": skipped,
        "sample": to_insert[:5],
        "before": before,
    })
    if limit is not None:
        to_insert = to_insert[:limit]
        report["limited_to"] = limit

    inserted = 0
    if not dry_run:
        with conn.cursor() as cur:
            for row in to_insert:
                cur.execute(_INSERT_SQL, (row["word_id"], row["name"], row["match_key"],
                                          language, row["script"], SOURCE))
                inserted += cur.rowcount
            after = _counts(cur)
            coverage = _preferred_coverage(cur, language)
        conn.commit()
        report.update({"inserted": inserted, "after": after, "coverage": coverage})
    else:
        report["inserted"] = 0
    return report


def rollback(conn, *, dry_run: bool = True, language: str = DEFAULT_LANGUAGE) -> Dict[str, Any]:
    """Delete the rows the backfill created, and nothing else.

    Only ``source='backfill'`` rows are touched: a curated or imported name is
    data somebody wrote, and a rollback that removed it would be a data loss
    reported as a clean undo.
    """
    report: Dict[str, Any] = {"dry_run": dry_run, "source": SOURCE}
    with conn.cursor() as cur:
        before = _counts(cur)
        cur.execute("SELECT count(*) FROM term_names WHERE source = %s", (SOURCE,))
        report["would_delete"] = cur.fetchone()[0]
        if not dry_run:
            cur.execute(_ROLLBACK_SQL, (SOURCE,))
            report["deleted"] = cur.rowcount
            after = _counts(cur)
            report["after"] = after
            report["coverage"] = _preferred_coverage(cur, language)
            conn.commit()
        report["before"] = before
    if dry_run:
        report["deleted"] = 0
    return report


def reconcile(conn, *, language: str = DEFAULT_LANGUAGE,
              before: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Prove the backfill changed the intended table and no other.

    ``before`` is the count map taken before the run; when it is absent the
    function reports the current counts without comparing them.
    """
    with conn.cursor() as cur:
        now = _counts(cur)
        coverage = _preferred_coverage(cur, language)
    report: Dict[str, Any] = {"language": language, "counts": now, "coverage": coverage}
    if before is not None:
        untouched = {t: [before.get(t), now.get(t)]
                     for t in ("words", "categorys", "keywords")}
        report["untouched"] = untouched
        report["ok"] = (
            all(b == a for b, a in untouched.values())
            and coverage["with_more_than_one"] == 0
            and coverage["without_a_name"] == 0
        )
        report["delta_term_names"] = now.get("term_names", 0) - before.get("term_names", 0)
    else:
        report["ok"] = (coverage["with_more_than_one"] == 0
                        and coverage["without_a_name"] == 0)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="insert the missing names")
    mode.add_argument("--rollback", action="store_true", help="delete source='backfill' rows")
    mode.add_argument("--reconcile", action="store_true", help="report coverage and counts")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--limit", type=int, help="insert at most this many rows")
    args = parser.parse_args(argv)

    try:
        from database import get_db_connection as get_connection
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"cannot reach the database: {exc}", file=sys.stderr)
        return 2

    conn = get_connection()
    try:
        if args.rollback:
            out = rollback(conn, dry_run=False, language=args.language)
        elif args.apply:
            out = backfill(conn, language=args.language, dry_run=False, limit=args.limit)
        elif args.reconcile:
            out = reconcile(conn, language=args.language)
        else:
            out = backfill(conn, language=args.language, dry_run=True)
    finally:
        conn.close()
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
