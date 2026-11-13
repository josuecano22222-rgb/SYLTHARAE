#!/usr/bin/env python3
"""Measure the term-name feature against a real database, three ways.

    python -m tools.i18n.audit_terms collisions   # folding collisions in the real vocabulary
    python -m tools.i18n.audit_terms matrix       # every name x every locale -> the same word
    python -m tools.i18n.audit_terms plans        # EXPLAIN for the six search predicates

Why a tool and not a one-off query
==================================

The three questions this answers are the ones that can only be answered against
real data, and each has to be re-answerable after the vocabulary changes:

``collisions``
    Two different terms whose normalised key is identical. The folding rules
    make ``سياسي`` and ``السياسي`` one key on purpose; they must not silently
    make two unrelated terms one key. Every collision is listed with both words
    and its cause (article, case, diacritics, letter variants) so a human decides
    each one, and the exit code is non-zero if a collision exists that the
    register does not explain.

``matrix``
    For every ``term_names`` row, query the expansion with that name and check
    it resolves to that word id. This is the R4 claim - "a search in any
    language finds the entity" - measured over the whole table rather than over
    the two examples in the test fixture. It also runs the article and
    diacritic variants in both directions.

``plans``
    ``EXPLAIN (ANALYZE, BUFFERS)`` for the expanded predicate at the id-set cap,
    next to the text-only predicate it replaces, so a plan regression is a
    number in the report rather than an opinion.

Connection: ``DB_HOST``/``DB_PORT``/``DB_USER``/``DB_PASSWORD``/``DB_NAME``, the
same variables the test fixtures use. Output is CSV on stdout (and optionally a
file) so the report's tables are generated from the run, not transcribed.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.i18n.term_key import ARABIC_ARTICLE, key_variants, term_key  # noqa: E402
from services.i18n.term_labels import ID_SET_CAP, MIN_QUERY_CHARS  # noqa: E402


def _connect():
    import psycopg2

    settings = {
        "host": os.environ.get("DB_HOST") or "localhost",
        "port": int(os.environ.get("DB_PORT") or 5432),
        "user": os.environ.get("DB_USER") or "postgres",
        "password": os.environ.get("DB_PASSWORD") or "",
        "dbname": os.environ.get("DB_NAME") or "postgres",
    }
    return psycopg2.connect(**settings)


def run_sql(conn, sql, params=None, fetch="all"):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        if fetch == "all":
            return cur.fetchall()
        if fetch == "one":
            return cur.fetchone()
        return None


def _write(rows: Sequence[Dict[str, Any]], fields: Sequence[str], path: Optional[str]):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fields))
    writer.writeheader()
    writer.writerows(rows)
    text = buffer.getvalue()
    if path:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    return text


# ---------------------------------------------------------------------------
# collisions
# ---------------------------------------------------------------------------
def load_register(path: Optional[str]) -> Dict[str, Dict[str, str]]:
    """``match_key -> {decision, note}`` from the review register, if there is one.

    The register is the human half of the collision audit: the tool finds every
    pair, a reviewer decides each one, and the decision is stored beside the
    evidence. A collision with no decision fails the run, so "we looked at it"
    cannot quietly become "we forgot about it".
    """
    if not path or not os.path.exists(path):
        return {}
    register: Dict[str, Dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("key") or "").strip()
            if key:
                register[key] = {"decision": (row.get("decision") or "").strip().lower(),
                                 "note": (row.get("note") or "").strip()}
    return register


def collisions(conn, *, limit: Optional[int] = None,
               register: Optional[Dict[str, Dict[str, str]]] = None
               ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Every pair of *different* terms whose normalised key is identical."""
    sql = "SELECT id, word FROM words ORDER BY id"
    params: Tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT %s"
        params = (limit,)
    words = run_sql(conn, sql, params)
    by_key: Dict[str, List[Tuple[int, str]]] = {}
    for word_id, word in words:
        by_key.setdefault(term_key(word), []).append((word_id, word))

    rows: List[Dict[str, Any]] = []
    for key, members in by_key.items():
        if len(members) < 2:
            continue
        stripped = term_key(key[len(ARABIC_ARTICLE):]) if key.startswith(ARABIC_ARTICLE) else ""
        case_only = len({w.lower() for _i, w in members}) == 1
        for index, (word_id, word) in enumerate(members):
            partner_id, partner = members[(index + 1) % len(members)]
            if key.startswith(ARABIC_ARTICLE) and stripped and \
                    any(term_key(w) == stripped for _i, w in members):
                cause = "arabic-article"
            elif case_only:
                cause = "latin-case"
            elif word != term_key(word):
                cause = "diacritics-or-letter-variants"
            else:
                cause = "unknown"
            decided = (register or {}).get(key, {})
            rows.append({"key": key, "word_id": word_id, "word": word,
                         "partner_id": partner_id, "partner": partner,
                         "cause": cause,
                         "decision": decided.get("decision") or "undecided",
                         "note": decided.get("note", "")})
    summary: Dict[str, int] = {}
    for row in rows:
        summary[row["cause"]] = summary.get(row["cause"], 0) + 1
        summary[f"decision:{row['decision']}"] = summary.get(
            f"decision:{row['decision']}", 0) + 1
    return rows, summary


# ---------------------------------------------------------------------------
# matrix
# ---------------------------------------------------------------------------
def _expand(conn, query: str) -> List[int]:
    """The ids a query resolves to, through the same resolver the app uses.

    ``run_sql`` needs the connection bound, so it is curried here rather than
    passed as the resolver's ``run`` argument positionally - passing the bare
    function would hand the resolver a signature it does not have.
    """
    from services.i18n.term_labels import expand_query

    def run(sql, params=None, fetch="all"):
        return run_sql(conn, sql, params, fetch=fetch)

    expansion = expand_query(run, query)
    return list(expansion.word_ids)


FATHA = "\u064e"
ZWNJ = "\u200c"
TATWEEL = "\u0640"


def _is_arabic(ch: str) -> bool:
    return "\u0600" <= ch <= "\u06ff"


def variants_of(name: str, language: str) -> Dict[str, Tuple[str, bool]]:
    """``case -> (query, expected_to_resolve)`` for one stored name.

    The positive cases are the ways a reader legitimately spells the same term:
    with the article, without it, with harakat, with a tatweel, with a ZWNJ.
    The negative cases are the ways a *different* term could be typed: a
    misspelling and a mixed-script string. Both directions are recorded because
    "recall went up" and "precision stayed put" are different claims, and the
    folding rules can break the second while improving the first.
    """
    out: Dict[str, Tuple[str, bool]] = {"exact": (name, True)}
    if name.startswith(ARABIC_ARTICLE):
        out["without-article"] = (name[len(ARABIC_ARTICLE):], True)
    elif language in ("ar", "fa"):
        out["with-article"] = (ARABIC_ARTICLE + name, True)
    if language in ("ar", "fa") and len(name) > 2:
        out["with-tatweel"] = (name[0] + TATWEEL + name[1:], True)
        out["with-diacritics"] = ("".join(
            ch + FATHA if _is_arabic(ch) else ch for ch in name), True)
    if language == "fa" and len(name) > 2:
        out["with-zwnj"] = (name[0] + ZWNJ + name[1:], True)
    if len(name) > 5:
        out["misspelled"] = (name[:-1], False)
    out["mixed-script"] = (name + "x", False)
    return out


def matrix(conn, *, limit: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Every stored name, queried as a reader would spell it, in every locale."""
    sql = ("SELECT t.word_id, t.name, t.language, t.name_type, w.word "
           "FROM term_names t JOIN words w ON w.id = t.word_id ORDER BY t.id")
    params: Tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT %s"
        params = (limit,)
    names = run_sql(conn, sql, params)
    languages = [row[0] for row in run_sql(
        conn, "SELECT DISTINCT language FROM term_names ORDER BY 1")]

    rows: List[Dict[str, Any]] = []
    summary: Dict[str, int] = {}

    def record(case: str, query: str, word_id: int, expected: bool, actual: bool,
               detail: str = ""):
        summary[case] = summary.get(case, 0) + 1
        verdict = "ok" if expected == actual else "wrong"
        summary[f"{case}:{verdict}"] = summary.get(f"{case}:{verdict}", 0) + 1
        rows.append({"case": case, "query": query, "expected_word_id": word_id,
                     "expected": "match" if expected else "no-match",
                     "actual": "match" if actual else "no-match",
                     "verdict": verdict, "detail": detail})

    for word_id, name, language, name_type, _word in names:
        # The same name is queried as if the reader were in each supported
        # language: the resolver must not depend on the interface language.
        for locale in languages or [language]:
            for case, (query, expected) in variants_of(name, language).items():
                if len(term_key(query)) < MIN_QUERY_CHARS:
                    continue
                found = _expand(conn, query)
                record(f"{case}:{language}", query, word_id, expected,
                       word_id in found, f"locale={locale}; {name_type}")

    return rows, summary


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------
_PLAN_SQL = {
    "category-list": (
        "SELECT c.id, w.word FROM categorys c JOIN words w ON c.word_id = w.id "
        "WHERE {predicate} ORDER BY w.word LIMIT 200"),
    "category-api": (
        "SELECT c.id, w.word, count(*) FROM categorys c JOIN words w ON c.word_id = w.id "
        "LEFT JOIN words_categorys wc ON wc.category_id = c.id "
        "WHERE {predicate} GROUP BY c.id, w.word ORDER BY w.word LIMIT 200"),
    "tree-search": (
        "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id "
        "WHERE {predicate} ORDER BY lower(w.word), c.id LIMIT 501"),
    "word-picker": (
        "SELECT w.id, w.word FROM words w WHERE {predicate} LIMIT 20"),
    "keyword-scan": (
        "SELECT k.id FROM keywords k JOIN words w ON w.id = k.category_id "
        "WHERE {predicate} LIMIT 20"),
    "file-analysis": (
        "SELECT wc.word_id, w.word FROM words_categorys wc JOIN words w "
        "ON w.id = wc.word_id WHERE {predicate} LIMIT 50"),
    # The resolver's own query, which is where ``idx_term_names_key`` matters:
    # it runs once per search, before any of the six predicates above.
    "resolver-lookup": (
        "SELECT t.word_id, w.word, t.language FROM term_names t "
        "JOIN words w ON w.id = t.word_id WHERE t.match_key = ANY(%s) LIMIT 21"),
}


def plans(conn, *, cap_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """EXPLAIN the text-only and expanded predicates for each of the six sites."""
    if cap_ids is None:
        cap_ids = [row[0] for row in run_sql(
            conn, "SELECT id FROM words ORDER BY id LIMIT %s", (ID_SET_CAP,))]
    rows = []
    # A term that exists, so "text-only" is not silently measuring zero rows.
    sample = run_sql(conn, "SELECT word FROM words ORDER BY id LIMIT 1")
    term = (sample[0][0] if sample else "term")
    for name, template in _PLAN_SQL.items():
        if name == "resolver-lookup":
            for kind, params in (
                    ("text-only", ([term_key(term)],)),
                    ("expanded", ([term_key(term), term_key(term) + "s"],))):
                try:
                    with conn.cursor() as cur:
                        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                                    + template, params)
                        plan = cur.fetchone()[0]
                    conn.rollback()
                    data = plan[0]
                    rows.append({
                        "site": name, "variant": kind,
                        "execution_ms": round(data.get("Execution Time", 0), 3),
                        "planning_ms": round(data.get("Planning Time", 0), 3),
                        "plan_rows": data["Plan"].get("Actual Rows"),
                        "node": data["Plan"].get("Node Type"),
                        "index_used": _index_names(data["Plan"]),
                        "id_set": len(params[0]),
                        "term": term,
                    })
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    rows.append({"site": name, "variant": kind,
                                 "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"})
            continue
        text_only = template.format(predicate="w.word ILIKE %s")
        expanded = template.format(predicate="(w.word ILIKE %s OR w.id = ANY(%s))")
        for kind, sql, params in (
                ("text-only", text_only, (f"%{term}%",)),
                ("expanded", expanded, (f"%{term}%", list(cap_ids)))):
            try:
                with conn.cursor() as cur:
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
                    plan = cur.fetchone()[0]
                conn.rollback()
                data = plan[0]
                rows.append({
                    "site": name, "variant": kind,
                    "execution_ms": round(data.get("Execution Time", 0), 3),
                    "planning_ms": round(data.get("Planning Time", 0), 3),
                    "plan_rows": data["Plan"].get("Actual Rows"),
                    "node": data["Plan"].get("Node Type"),
                    "index_used": _index_names(data["Plan"]),
                    "id_set": len(cap_ids),
                    "term": term,
                })
            except Exception as exc:  # noqa: BLE001 - one bad plan must not stop the rest
                conn.rollback()
                rows.append({"site": name, "variant": kind, "error":
                             f"{type(exc).__name__}: {str(exc).splitlines()[0]}"})
    return rows


def _index_names(plan: Dict[str, Any]) -> str:
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("Index Name"):
                found.append(node["Index Name"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plan)
    return ";".join(dict.fromkeys(found))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("collisions", "matrix", "plans"))
    parser.add_argument("--csv", help="also write the rows here")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap the words examined (0 = all of them)")
    parser.add_argument("--register", default="audit_report/r21_i18n/collision_decisions.csv",
                        help="CSV of key,decision,note - one reviewed decision per key")
    args = parser.parse_args(argv)

    conn = _connect()
    try:
        if args.mode == "collisions":
            rows, summary = collisions(conn, limit=args.limit or None,
                                       register=load_register(args.register))
            text = _write(rows, ("key", "word_id", "word", "partner_id", "partner",
                                 "cause", "decision", "note"), args.csv)
            print(text, end="")
            print(f"# {len(rows)} row(s); " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())),
                  file=sys.stderr)
            undecided = summary.get("decision:undecided", 0)
            return 1 if (summary.get("unknown") or undecided) else 0
        if args.mode == "matrix":
            rows, summary = matrix(conn, limit=args.limit or None)
            text = _write(rows, ("case", "query", "expected_word_id", "expected",
                                 "actual", "verdict", "detail"), args.csv)
            print(text, end="")
            wrong = {k: v for k, v in summary.items() if k.endswith(":wrong")}
            print(f"# {len(rows)} row(s); wrong: {wrong or 'none'}", file=sys.stderr)
            return 1 if wrong else 0
        rows = plans(conn)
        text = _write(rows, ("site", "variant", "execution_ms", "planning_ms",
                             "plan_rows", "node", "index_used", "id_set", "term",
                             "error"), args.csv)
        print(text, end="")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
