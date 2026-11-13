"""Reconcile the classification database with the category workbook.

The workbook ``classification/domain_data.xlsx`` is authoritative; this module
computes the difference and can write it. It is the single implementation used
by the command line (``tools/i18n/sync_taxonomy.py``), by the ``taxonomy_sync``
job, by the API's drift report and by the scheduler - so "dry run", "apply" and
"what the interface shows" cannot mean three different things.

What "the workbook is authoritative" means, precisely
----------------------------------------------------
* a worksheet is a category, named by the worksheet (rule R1 in
  :mod:`apps.importing.validators.workbook_rules`);
* column ``Term`` holds single words, column ``Keyword`` holds two or more - the
  split the database already makes between ``words_categorys`` (a term in a
  category) and ``keywords`` (a phrase owned by a category);
* a term removed from a sheet is unlinked from that category;
* a keyword removed from a sheet is deleted;
* a word row is **never** deleted implicitly: ``keywords.keyword`` stores word
  ids, so deleting a word would silently change another category's phrase.
  Orphans are reported, and ``prune_words`` removes only those no phrase refers
  to;
* a worksheet that disappears removes its category only with
  ``prune_categories`` - otherwise it is reported, because "I deleted a sheet"
  and "I renamed a sheet" look identical from one state. Two things resolve
  that: an explicit ``renames={old: new}`` mapping, or ``detect_renames``,
  which renames only when a stored category and a new worksheet hold exactly
  the same content and the pairing is unique in both directions.

Everything here is a pure function of the workbook plus the rows it reads,
except :func:`apply_plan`, which writes exactly the plan it is given in one
transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.importing.validators import workbook_rules as rules  # noqa: E402

DEFAULT_XLSX = str(ROOT / "classification" / "domain_data.xlsx")


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------
def connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("DB_HOST") or "localhost",
        port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
        dbname=os.environ.get("DB_NAME") or "postgres",
    )


def _query(conn, sql: str, params=None) -> List[Tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(conn, sql: str, params=None):
    rows = _query(conn, sql, params)
    return rows[0][0] if rows else None


def snapshot_counts(conn) -> Dict[str, int]:
    """The table counts the shrink guard measures a plan against."""
    return snapshot(conn)


def snapshot(conn) -> Dict[str, int]:
    out = {}
    for table in ("words", "categorys", "words_categorys", "keywords"):
        out[table] = _scalar(conn, f"SELECT count(*) FROM {table}")  # nosec B608
    return out


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------
#: How many rows of a removal list a report carries. The count is the answer;
#: twenty examples are enough to recognise a mistake.
SAMPLE_ROWS = 20


@dataclass
class Plan:
    """The difference between the workbook and the database.

    ``delete_categories`` holds only what will actually be deleted (it needs
    ``--prune-categories``); a worksheet that vanished without that flag shows up
    in ``missing_categories`` instead, because a deleted sheet and a renamed one
    look identical from here.
    """

    create_categories: List[str] = field(default_factory=list)
    rename_categories: List[Tuple[str, str]] = field(default_factory=list)
    missing_categories: List[str] = field(default_factory=list)
    delete_categories: List[str] = field(default_factory=list)
    create_words: List[str] = field(default_factory=list)
    link_terms: List[Tuple[str, str]] = field(default_factory=list)
    unlink_terms: List[Tuple[str, str]] = field(default_factory=list)
    create_keywords: List[Tuple[str, str]] = field(default_factory=list)
    delete_keywords: List[Dict[str, Any]] = field(default_factory=list)
    keep_keywords: List[Tuple[str, str]] = field(default_factory=list)
    not_stored: List[Dict[str, str]] = field(default_factory=list)
    stale_keywords: List[Dict[str, str]] = field(default_factory=list)
    orphan_words: List[str] = field(default_factory=list)
    unchanged: Dict[str, int] = field(default_factory=dict)
    #: True when the renames in this plan were inferred from content rather
    #: than stated with ``renames=``; the summary says so to the operator.
    detected_renames: bool = False

    @property
    def changes(self) -> int:
        return sum(len(getattr(self, name)) for name in (
            "create_categories", "rename_categories", "delete_categories",
            "create_words", "link_terms", "unlink_terms",
            "create_keywords", "delete_keywords"))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "changes": self.changes,
            "create_categories": self.create_categories,
            "rename_categories": [f"{old} -> {new}" for old, new in self.rename_categories],
            "missing_categories": self.missing_categories,
            "delete_categories": self.delete_categories,
            "create_words": len(self.create_words),
            "link_terms": len(self.link_terms),
            "unlink_terms": len(self.unlink_terms),
            "create_keywords": len(self.create_keywords),
            "delete_keywords": len(self.delete_keywords),
            "keep_keywords": len(self.keep_keywords),
            "not_stored_shared": self.not_stored,
            "stale_keywords": self.stale_keywords,
            "orphan_words": len(self.orphan_words),
            # The rows themselves, not only the count: a drift report that says
            # "3 orphans" and cannot name them is a report nobody can act on.
            "orphan_words_sample": list(self.orphan_words[:SAMPLE_ROWS]),
            "unchanged": self.unchanged,
        }


def _decode_keyword(conn, blob) -> str:
    """Turn a stored keyword blob back into its phrase (word ids -> texts)."""
    from core.serialization import unpack_int_list

    ids = [int(i) for i in unpack_int_list(blob)]
    if not ids:
        return ""
    rows = dict(_query(conn, "SELECT id, word FROM words WHERE id = ANY(%s)", (ids,)))
    return " ".join(str(rows.get(i, "")) for i in ids).strip()


def _stored_categories(conn) -> Dict[str, Tuple[str, int]]:
    """``word.casefold() -> (word, category id)``, read-only."""
    return {row[0].casefold(): (row[0], int(row[1])) for row in _query(
        conn,
        "SELECT w.word, c.id FROM categorys c JOIN words w ON w.id = c.word_id")}


def content_of(conn) -> Dict[str, Dict[str, set]]:
    """``category name -> {"terms": {...}, "keywords": {...}}``, casefolded.

    Content is what decides whether a vanished worksheet and a new one are the
    same category renamed, so it is read in the same shape for both sides.
    """
    out: Dict[str, Dict[str, set]] = {}
    for name, term in _query(
            conn,
            "SELECT cw.word, tw.word FROM words_categorys wc"
            " JOIN categorys c ON c.id = wc.category_id"
            " JOIN words cw ON cw.id = c.word_id"
            " JOIN words tw ON tw.id = wc.word_id"):
        out.setdefault(name.casefold(), {"terms": set(), "keywords": set()})
        out[name.casefold()]["terms"].add(term.casefold())
    for category_word, blob in _query(
            conn,
            "SELECT w.word, k.keyword FROM keywords k"
            " JOIN categorys c ON c.id = k.category_id"
            " JOIN words w ON w.id = c.word_id"):
        out.setdefault(category_word.casefold(), {"terms": set(), "keywords": set()})
        text = _decode_keyword(conn, blob)
        if text:
            out[category_word.casefold()]["keywords"].add(text.casefold())
    return out


def detect_renames(conn, workbook: rules.Workbook, *,
                   stored: Optional[Dict[str, Tuple[str, int]]] = None) -> Dict[str, str]:
    """``{stored name: worksheet name}`` for unambiguous renames, else ``{}``.

    A rename is inferred only when it cannot be anything else:

    * a stored category whose name is no longer a worksheet, and a worksheet
      whose name is not a stored category ("vanished" and "new" respectively);
    * the two hold **exactly** the same terms and keywords;
    * the pairing is unique in both directions - one candidate each way.

    Anything weaker (a category that lost a term, two sheets with the same
    content) stays a decision for the operator: a wrong rename moves a
    category's whole identity, and guessing is how data disappears. The
    returned mapping is the one :func:`build_plan` accepts as ``renames``.
    """
    state = rules.desired_state(workbook)
    stored = stored if stored is not None else _stored_categories(conn)
    workbook_names = {name.casefold() for name in state.categories}
    vanished = {key: name for key, (name, _id) in stored.items()
                if key not in workbook_names}
    created = {key: name for key, name in
               ((name.casefold(), name) for name in state.categories)
               if key not in stored}
    if not vanished or not created:
        return {}

    stored_content = content_of(conn)
    new_content = {
        name.casefold(): {"terms": {term.casefold() for term in terms},
                          "keywords": {keyword.casefold() for keyword in keywords}}
        for name, (terms, keywords) in state.categories.items()
    }

    def same(a: Dict[str, set], b: Dict[str, set]) -> bool:
        return a["terms"] == b["terms"] and a["keywords"] == b["keywords"]

    candidates: Dict[str, List[str]] = {}
    for old_key, old_name in vanished.items():
        old = stored_content.get(old_key)
        if not old or not (old["terms"] or old["keywords"]):
            continue                       # an empty category matches everything
        for new_key, new_name in created.items():
            if same(old, new_content[new_key]):
                candidates.setdefault(old_name, []).append(new_name)

    # unique both ways, or not a rename
    targets: Dict[str, int] = {}
    for old_name, names in candidates.items():
        for name in names:
            targets[name] = targets.get(name, 0) + 1
    return {old: names[0] for old, names in candidates.items()
            if len(names) == 1 and targets[names[0]] == 1}


def build_plan(conn, workbook: rules.Workbook, *,
               prune_categories: bool = False, prune_words: bool = False,
               renames: Optional[Dict[str, str]] = None,
               auto_renames: bool = False) -> Plan:
    """Compute the difference. Reads only, so it is safe against production.

    ``renames`` is what the operator stated; ``auto_renames`` adds what the
    content proves (see :func:`detect_renames`). An explicit mapping wins over
    a detected one.
    """
    state = rules.desired_state(workbook)
    if auto_renames:
        detected = detect_renames(conn, workbook)
        detected.update({rules.normalise(old): new
                         for old, new in (renames or {}).items()})
        renames = detected
    renames = {rules.normalise(old).casefold(): rules.normalise(new)
               for old, new in (renames or {}).items()}
    plan = Plan()
    plan.detected_renames = bool(auto_renames)
    stored = _stored_categories(conn)

    # A category's effective name is its stored name, or the new name when the
    # operator says the worksheet was renamed.
    effective = {key: renames.get(key, name) for key, (name, _id) in stored.items()}
    effective_by_id = {cid: effective[key] for key, (_n, cid) in stored.items()}

    workbook_names = {name.casefold(): name for name in state.categories}

    # -- categories ------------------------------------------------------
    # A worksheet matching a stored name is unchanged; one matching a rename
    # target is a rename; anything else is new. ``renames`` maps a casefolded
    # old name to the new text, so the comparison is on casefolded keys.
    renamed_targets = {
        new_text.casefold(): old_key
        for old_key, new_text in renames.items()
        if old_key in stored and new_text.casefold() in workbook_names
    }
    for key, name in workbook_names.items():
        if key in stored:
            plan.unchanged["categories"] = plan.unchanged.get("categories", 0) + 1
        elif key in renamed_targets:
            plan.rename_categories.append((stored[renamed_targets[key]][0], name))
        else:
            plan.create_categories.append(name)

    workbook_effective = set(workbook_names.values())
    for key, (name, _cid) in stored.items():
        if effective[key] in workbook_effective:
            continue
        plan.missing_categories.append(name)
        if prune_categories:
            plan.delete_categories.append(name)

    # -- terms -----------------------------------------------------------
    known = {row[0].casefold() for row in _query(conn, "SELECT word FROM words")}
    links: Dict[str, Dict[str, str]] = {}
    for category_word, term in _query(
            conn,
            "SELECT cw.word, tw.word FROM words_categorys wc"
            " JOIN categorys c ON c.id = wc.category_id"
            " JOIN words cw ON cw.id = c.word_id"
            " JOIN words tw ON tw.id = wc.word_id"):
        # keyed by the category's *effective* name, so a renamed worksheet's
        # existing links are compared against the new name, not re-created
        name = effective.get(category_word.casefold(), category_word)
        links.setdefault(name.casefold(), {})[term.casefold()] = term

    for name, (terms, _keywords) in state.categories.items():
        present = links.get(name.casefold(), {})
        for term in terms:
            key = term.casefold()
            if key not in known and term not in plan.create_words:
                plan.create_words.append(term)
            if key in present:
                plan.unchanged["terms"] = plan.unchanged.get("terms", 0) + 1
            else:
                plan.link_terms.append((name, term))
        wanted = {term.casefold() for term in terms}
        for key, text in present.items():
            if key not in wanted:
                plan.unlink_terms.append((name, text))

    # -- keywords --------------------------------------------------------
    # The schema stores a phrase once, so each phrase is compared against the
    # category the workbook says owns it.
    stored_keywords: Dict[str, Dict[str, List[int]]] = {}
    for keyword_id, category_id, blob in _query(
            conn, "SELECT id, category_id, keyword FROM keywords ORDER BY id"):
        name = effective_by_id.get(int(category_id))
        if name is None:
            continue
        text = _decode_keyword(conn, blob)
        stored_keywords.setdefault(name.casefold(), {}).setdefault(
            text.casefold(), []).append((int(keyword_id), text))

    for key, owner in state.keyword_owner.items():
        if owner.casefold() not in workbook_names:
            continue
        text = state.keyword_text(key, owner)
        if key in stored_keywords.get(owner.casefold(), {}):
            plan.keep_keywords.append((owner, text))
        else:
            plan.create_keywords.append((owner, text))

    for name_key, phrases in stored_keywords.items():
        name = workbook_names.get(name_key)
        wanted = {key for key, owner in state.keyword_owner.items()
                  if owner.casefold() == name_key}
        for key in phrases:
            if key in wanted:
                continue
            if name is None:
                owner = next((effective_by_id[cid] for cid in effective_by_id
                              if effective_by_id[cid].casefold() == name_key), name_key)
                plan.stale_keywords.append({"category": owner, "phrase": key})
                continue
            keyword_ids = phrases[key]
            plan.delete_keywords.append({
                "category": name,
                "phrase": keyword_ids[0][1],       # the stored spelling
                "keyword_id": keyword_ids[0][0],   # delete by identity
            })

    for key, listed_in in state.shared.items():
        owner = state.keyword_owner.get(key, "")
        for name in listed_in:
            if name.casefold() != owner.casefold():
                plan.not_stored.append({
                    "sheet": name, "phrase": key, "owner": owner,
                    "reason": "keywords.keyword is UNIQUE (one category per phrase)",
                })

    # -- orphan words ----------------------------------------------------
    # A word used only inside a keyword phrase is *not* an orphan: the phrase is
    # stored as word ids, so deleting it would silently rewrite another
    # category's keyword. Only words nothing at all refers to are orphans.
    from core.serialization import unpack_int_list

    referenced = set()
    for (blob,) in _query(conn, "SELECT keyword FROM keywords"):
        referenced.update(int(i) for i in unpack_int_list(blob))
    plan.orphan_words = [word for (word,) in _query(
        conn,
        "SELECT w.word FROM words w"
        " WHERE NOT EXISTS (SELECT 1 FROM words_categorys wc WHERE wc.word_id = w.id)"
        "   AND NOT EXISTS (SELECT 1 FROM categorys c WHERE c.word_id = w.id)")
        if int(_scalar(conn, "SELECT id FROM words WHERE word = %s", (word,)) or 0)
        not in referenced]
    return plan


def apply_plan(conn, workbook: rules.Workbook, plan: Plan,
               *, prune_words: bool = False) -> Dict[str, int]:
    """Write exactly the plan, in one transaction.

    ``apply`` never recomputes the difference, so what ``--dry-run`` printed is
    what this writes.
    """
    from core.serialization import pack_int_list

    written = {"categories_created": 0, "categories_deleted": 0, "categories_renamed": 0,
               "words_created": 0, "terms_linked": 0, "terms_unlinked": 0,
               "words_kept_for_names": 0,
               "keywords_created": 0, "keywords_deleted": 0, "words_pruned": 0}
    state = rules.desired_state(workbook)

    # ``words.word`` is UNIQUE case-sensitively, while the planner compares
    # words case-insensitively. Matching the same way here keeps the two halves
    # of the tool consistent: a row written by a lowercasing writer is reused,
    # not duplicated. The whole vocabulary is read once - a case-insensitive
    # lookup per word is a sequential scan per word, which turned a five-second
    # apply into a forty-second one on the committed workbook.
    exact: Dict[str, int] = {}
    folded: Dict[str, int] = {}
    for word, wid in _query(conn, "SELECT word, id FROM words ORDER BY id"):
        exact.setdefault(word, int(wid))
        folded.setdefault(word.casefold(), int(wid))   # oldest row wins

    def word_id(text: str) -> int:
        if text in exact:
            return exact[text]
        if text.casefold() in folded:
            return folded[text.casefold()]
        row = _query(conn, "INSERT INTO words (word) VALUES (%s) RETURNING id", (text,))
        written["words_created"] += 1
        new_id = int(row[0][0])
        exact[text] = new_id
        folded.setdefault(text.casefold(), new_id)
        return new_id

    def category_id(name: str, *, create: bool) -> Optional[int]:
        found = _scalar(
            conn,
            "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
            " WHERE lower(w.word) = lower(%s) ORDER BY (w.word = %s) DESC, c.id LIMIT 1",
            (name, name))
        # (categories are few - this one stays a query; it runs once per
        #  category, not once per word)
        if found:
            return int(found)
        if not create:
            return None
        row = _query(conn, "INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                     (word_id(name),))
        written["categories_created"] += 1
        return int(row[0][0])

    with conn:  # one transaction: a failure leaves the database unchanged
        for old, new in plan.rename_categories:
            if _scalar(conn, "SELECT 1 FROM words WHERE word = %s", (new,)):
                raise RuntimeError(
                    f"cannot rename {old!r} to {new!r}: that word already exists")
            with conn.cursor() as cur:
                cur.execute("UPDATE words SET word = %s WHERE word = %s", (new, old))
                written["categories_renamed"] += cur.rowcount

        for name, (terms, _keywords) in state.categories.items():
            cid = category_id(name, create=True)
            for term in terms:
                tid = word_id(term)
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                                " VALUES (%s, %s) ON CONFLICT DO NOTHING", (tid, cid))
                    written["terms_linked"] += cur.rowcount

        for name, term in plan.unlink_terms:
            cid = category_id(name, create=False)
            tid = _scalar(conn, "SELECT id FROM words WHERE word = %s", (term,))
            if cid and tid:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM words_categorys WHERE word_id = %s"
                                " AND category_id = %s", (tid, cid))
                    written["terms_unlinked"] += cur.rowcount

        for name, phrase in plan.create_keywords:
            cid = category_id(name, create=True)
            ids = [word_id(word) for word in phrase.split()]
            blob = pack_int_list(ids)
            try:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO keywords (keyword, category_id)"
                                " VALUES (%s, %s) ON CONFLICT DO NOTHING", (blob, cid))
                    written["keywords_created"] += cur.rowcount
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"the phrase {phrase!r} could not be stored for {name!r}: {exc}") from exc

        for item in plan.delete_keywords:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM keywords WHERE id = %s", (item["keyword_id"],))
                written["keywords_deleted"] += cur.rowcount

        for name in plan.delete_categories:
            cid = category_id(name, create=False)
            if cid:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM categorys WHERE id = %s", (cid,))
                    written["categories_deleted"] += cur.rowcount

        if prune_words and plan.orphan_words:
            referenced = set()
            for (blob,) in _query(conn, "SELECT keyword FROM keywords"):
                from core.serialization import unpack_int_list
                referenced.update(int(i) for i in unpack_int_list(blob))
            named = {int(row[0]) for row in _query(
                conn, "SELECT DISTINCT word_id FROM term_names")}
            for word in plan.orphan_words:
                wid = _scalar(conn, "SELECT id FROM words WHERE word = %s", (word,))
                if wid is None or int(wid) in referenced:
                    continue
                if int(wid) in named:
                    # ``term_names`` is ON DELETE CASCADE and holds names no
                    # workbook can reproduce: a curated Arabic or Hebrew name is
                    # not an orphan, whatever the vocabulary says.
                    written["words_kept_for_names"] += 1
                    continue
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM words WHERE word = %s", (word,))
                    written["words_pruned"] += cur.rowcount

    return written


# ---------------------------------------------------------------------------
# identity, snapshots, and the one entry point
# ---------------------------------------------------------------------------
#: Bumped by hand when the sync's *behaviour* changes - a different reading of a
#: worksheet, a different rule, a different write - so a stored fingerprint from
#: before the change can never authorise the fast path after it.
SYNC_LOGIC_VERSION = "3.0"

#: The modules whose source is part of what a fingerprint means. If the rules
#: that turn worksheets into terms and keywords change, the same workbook means
#: something else and the stored fingerprint must not match it any more.
RULES_MODULES = ("services.importing.taxonomy_rules", "services.importing.taxonomy_sync",
                 "services.importing.taxonomy_guard")


def _rules_digest() -> str:
    """A digest of the rule sources, with the checkout root stripped.

    The path is not part of the rule, so it is not part of the digest: two
    checkouts of the same commit must produce the same fingerprint, and a
    dependency on where the file lives would make that false.
    """
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parents[2]
    for name in RULES_MODULES:
        path = root / (name.replace(".", "/") + ".py")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            digest.update(b"missing:")
            digest.update(name.encode())
            continue
        digest.update(name.encode())
        digest.update(text.replace(str(root), "{root}").encode("utf-8"))
    return digest.hexdigest()


def fingerprint(path: str) -> str:
    """What the stored state records: workbook bytes, the rules, the version.

    The bytes alone were not enough. An unchanged workbook with changed rules
    was skipped by the fast path even though applying it would have written
    something different - the fingerprint said "you have seen this file", when
    what the caller needs to know is "you have already applied this meaning".
    """
    digest = hashlib.sha256()
    digest.update(b"sync-logic-v")
    digest.update(SYNC_LOGIC_VERSION.encode("utf-8"))
    digest.update(b"\nrules:")
    digest.update(_rules_digest().encode("ascii"))
    digest.update(b"\nworkbook:")
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_state(conn, names: Optional[List[str]] = None) -> Dict[str, Any]:
    """The whole taxonomy as plain data: categories, their terms and keywords.

    This is what :func:`restore_state` puts back, so it must be complete enough
    to reverse an apply: the names, the term memberships, the phrases, and the
    category ids so a restore can say whether it re-created or reused a row.
    """
    state: Dict[str, Any] = {"categories": {}}
    stored = _stored_categories(conn)
    wanted = None if names is None else {name.casefold() for name in names}
    for key, (name, category_id) in stored.items():
        if wanted is not None and key not in wanted:
            continue
        state["categories"][name] = {"id": category_id, "terms": [], "keywords": []}
    for category_word, term in _query(
            conn,
            "SELECT cw.word, tw.word FROM words_categorys wc"
            " JOIN categorys c ON c.id = wc.category_id"
            " JOIN words cw ON cw.id = c.word_id"
            " JOIN words tw ON tw.id = wc.word_id ORDER BY tw.word"):
        entry = state["categories"].get(category_word)
        if entry is not None:
            entry["terms"].append(term)
    for category_word, keyword_id, blob in _query(
            conn,
            "SELECT w.word, k.id, k.keyword FROM keywords k"
            " JOIN categorys c ON c.id = k.category_id"
            " JOIN words w ON w.id = c.word_id ORDER BY k.id"):
        entry = state["categories"].get(category_word)
        if entry is not None:
            entry["keywords"].append({"id": int(keyword_id),
                                      "text": _decode_keyword(conn, blob)})
    return state


def restore_state(conn, state: Dict[str, Any],
                  names: Optional[List[str]] = None) -> Dict[str, int]:
    """Put a snapshot back. Idempotent, and it writes only what differs.

    Restoring is deliberately *not* a full re-sync: a category the snapshot
    does not know about is removed, because it was created after the snapshot
    was taken. Word rows are left alone - the vocabulary is shared, and an
    unused word is harmless where a wrongly deleted one is not.

    ``names`` scopes the undo to the categories a sync touched. Without it a
    restore is "the whole taxonomy, as it was", which removes everything another
    writer created while the sync was running - a consequence nobody asked for
    when they clicked *undo*. The sync's own undo passes the names it wrote.
    """
    scope = None if names is None else {name.casefold() for name in names}
    from core.serialization import pack_int_list

    written = {"categories_restored": 0, "categories_removed": 0,
               "terms_linked": 0, "terms_unlinked": 0,
               "keywords_created": 0, "keywords_deleted": 0}
    snapshot = state.get("categories", {})
    current = export_state(conn)

    for name in current["categories"]:
        if scope is not None and name.casefold() not in scope:
            continue
        if name not in snapshot:
            cid = _stored_categories(conn)[name.casefold()][1]
            with conn.cursor() as cur:
                cur.execute("DELETE FROM categorys WHERE id = %s", (cid,))
                written["categories_removed"] += cur.rowcount

    def word_id(text: str) -> int:
        found = _scalar(conn, "SELECT id FROM words WHERE word = %s", (text,))
        if found:
            return int(found)
        return int(_query(conn, "INSERT INTO words (word) VALUES (%s) RETURNING id",
                          (text,))[0][0])

    def category_id(name: str) -> int:
        found = _scalar(conn, "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id"
                              " WHERE w.word = %s", (name,))
        if found:
            return int(found)
        wid = word_id(name)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (wid,))
            return int(cur.fetchone()[0])

    for name, content in snapshot.items():
        if scope is not None and name.casefold() not in scope:
            continue
        cid = category_id(name)
        written["categories_restored"] += 1
        have = {term.casefold() for term in
                current["categories"].get(name, {}).get("terms", [])}
        want = {term.casefold() for term in content.get("terms", [])}
        for term in content.get("terms", []):
            if term.casefold() in have:
                continue
            with conn.cursor() as cur:
                cur.execute("INSERT INTO words_categorys (word_id, category_id)"
                            " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (word_id(term), cid))
                written["terms_linked"] += cur.rowcount
        for term in current["categories"].get(name, {}).get("terms", []):
            if term.casefold() in want:
                continue
            with conn.cursor() as cur:
                cur.execute("DELETE FROM words_categorys WHERE word_id = %s"
                            " AND category_id = %s",
                            (word_id(term), cid))
                written["terms_unlinked"] += cur.rowcount

        wanted_phrases = {item["text"].casefold() for item in content.get("keywords", [])}
        for item in current["categories"].get(name, {}).get("keywords", []):
            if item["text"].casefold() in wanted_phrases:
                continue
            with conn.cursor() as cur:
                cur.execute("DELETE FROM keywords WHERE id = %s", (item["id"],))
                written["keywords_deleted"] += cur.rowcount
        present = {item["text"].casefold() for item in
                   current["categories"].get(name, {}).get("keywords", [])}
        for item in content.get("keywords", []):
            if item["text"].casefold() in present:
                continue
            ids = [word_id(word) for word in item["text"].split()]
            if len(ids) < 2:
                continue
            with conn.cursor() as cur:
                cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)"
                            " ON CONFLICT DO NOTHING", (pack_int_list(ids), cid))
                written["keywords_created"] += cur.rowcount

    return written


def sync_workbook(conn, path: str, *, apply: bool = False,
                  prune_categories: bool = False, prune_words: bool = False,
                  renames: Optional[Dict[str, str]] = None,
                  auto_renames: bool = True,
                  snapshot: bool = True) -> Dict[str, Any]:
    """Read the workbook, plan the difference, and (when asked) write it.

    One transaction, and a snapshot taken before the write when ``snapshot`` is
    set, so an automatic run can be undone. The result is plain data - the job,
    the API and the CLI all report it, so the three cannot describe the same run
    differently.
    """
    try:
        workbook = rules.read(path, renames=renames)
    except Exception as exc:
        # A file caught mid-save, a download that stopped, a name that is not a
        # workbook: all of these used to raise out of here. A boot-time step that
        # raises takes the web app with it, and an API call that raises answers
        # 500 for something the operator can fix by one click.
        return {
            "path": str(path),
            "fingerprint": None,
            "ok": False,
            "applied": False,
            "reason": "unreadable workbook",
            "errors": [f"{exc.__class__.__name__}: the workbook could not be "
                       f"read, so nothing was written"],
            "layout": None,
            "workbook": None,
            "decisions": 0,
        }
    result: Dict[str, Any] = {
        "path": str(path),
        "fingerprint": fingerprint(path),
        "layout": workbook.layout,
        "workbook": workbook.as_dict(),
        "errors": [str(violation) for violation in workbook.errors],
        "decisions": len(workbook.decisions),
    }
    if not workbook.ok:
        result.update({"ok": False, "applied": False, "reason": "rule violations"})
        return result

    plan = build_plan(conn, workbook, prune_categories=prune_categories,
                      prune_words=prune_words, renames=renames,
                      auto_renames=auto_renames)
    result.update({
        "ok": True,
        "plan": plan.as_dict(),
        "planned_changes": plan.changes,
        "applied": False,
    })
    if not apply:
        return result

    # Before anything is written: how much of the stored taxonomy would this
    # remove? A truncated file, a half-saved workbook and a file whose sheets
    # were emptied are all "authoritative" too, and a boot-time apply has nobody
    # to ask. See services/importing/taxonomy_guard.py.
    from services.importing import taxonomy_guard as guard

    verdict = guard.check(plan, snapshot_counts(conn))
    result["removal"] = verdict
    if not verdict["ok"]:
        result.update({"ok": False, "applied": False,
                       "reason": "shrink limit", "errors": [verdict["reason"]]})
        return result
    # ``changes`` counts creations, links, unlinks and phrase edits. Pruning is
    # work too: a run whose only task is removing orphans used to report
    # "nothing to do" and remove nothing, forever.
    pending_prune = ((prune_words and len(plan.orphan_words))
                     or (prune_categories and len(plan.delete_categories)))
    if plan.changes == 0 and not pending_prune:
        result.update({"applied": False, "reason": "nothing to do", "written": {}})
        return result

    before = export_state(conn) if snapshot else None
    written = apply_plan(conn, workbook, plan, prune_words=prune_words)
    conn.commit()
    result.update({"applied": True, "written": written})
    if before is not None:
        result["snapshot"] = before
    return result


#: What a plan means, in sentences an operator can act on. The counts are the
#: answer and these are the words: "3 shared" is not a fact anybody can decide
#: about, "3 memberships are listed in a worksheet and not stored" is.
def summarise(plan: Dict[str, Any]) -> list:
    """Plain-language standing facts about a plan (or a status payload)."""
    lines = []
    not_stored = plan.get("not_stored_shared") or plan.get("not_stored") or []
    if not_stored:
        owners = sorted({str(item.get("owner", "")) for item in not_stored})
        lines.append(
            f"{len(not_stored)} phrase membership(s) are listed in more than one "
            f"worksheet and not stored: `keywords.keyword` is unique, so each "
            f"phrase belongs to one category only ({', '.join(o for o in owners if o)}). "
            f"`{rules.OWNERS_SHEET}` decides which.")
    shared = plan.get("shared_keywords")
    if shared and len(shared) > len(not_stored):
        lines.append(f"{len(shared)} phrase(s) are listed in more than one worksheet.")
    missing = plan.get("missing_categories") or []
    if missing:
        lines.append(
            f"{len(missing)} stored categor{'y is' if len(missing) == 1 else 'ies are'} "
            f"not in the workbook. They are reported, not deleted; deleting them "
            f"needs `prune_categories` in the request, by name.")
    orphans = plan.get("orphan_words")
    if isinstance(orphans, list):
        orphans = len(orphans)
    if orphans:
        lines.append(
            f"{orphans} word row(s) are referenced by nothing and would be removed "
            f"only with `prune_words`: the confirm path is the request, not a "
            f"default.")
    stale = plan.get("stale_keywords") or []
    if stale:
        lines.append(
            f"{len(stale)} stored phrase(s) belong to a category the workbook does "
            f"not describe; they are reported as stale.")
    if not lines:
        lines.append("The database matches the workbook.")
    return lines
