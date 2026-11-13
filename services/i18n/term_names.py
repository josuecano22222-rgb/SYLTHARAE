"""Reading and writing ``term_names``: validation in one place, for API and CSV.

Every rule the table enforces is checked here first, with a message a curator
can act on:

* the language is one the interface supports;
* the script matches the language, except a ``transliteration``, which is Latin
  (:func:`core.i18n.term_key.script_ok`, the same function the migration's CHECK
  is tested against);
* a word has at most one ``preferred`` name per language - an update that would
  make a second one is refused *before* PostgreSQL raises a unique violation,
  because "duplicate key value violates unique constraint
  uq_term_names_preferred" is not a message a curator should have to read;
* the name is 1-255 characters, has no control characters, and is not only
  whitespace;
* ``match_key`` is computed here and never accepted from a client.

``import_csv`` is the bulk path (decision D5: CSV first, in-UI curation after).
It is a dry run unless ``apply=True``, reports every rejected row with its line
number and reason, and writes nothing at all if the file has no header it
recognises - a half-imported glossary is worse than a refused one.

Writes record themselves in ``audit_log`` (the same table every other
administrative write uses), so "who changed this name" has an answer.
"""
from __future__ import annotations

import csv
import logging
from typing import Any, Dict, Iterable, List, Optional

from core.geo.names import LANGUAGE_SCRIPT, script_of
from core.i18n.term_key import term_key

logger = logging.getLogger(__name__)

LANGUAGES = tuple(LANGUAGE_SCRIPT)
NAME_TYPES = ("preferred", "alternate", "transliteration")
MAX_NAME = 255
CSV_COLUMNS = ("word", "language", "name", "name_type")


class TermNameError(Exception):
    """A refusal a client may see: stable code, safe message, HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


class ImportUnavailable(Exception):
    """Every line failed for a reason that was not about the line.

    A database that is down looks, row by row, exactly like a file full of
    typos: ``ROW_FAILED``. Reporting that as a 200 hides an outage behind a
    validation message, so a file where *nothing* could be read is a server
    error, and the route answers it with a correlation id.
    """

    def __init__(self, message: str, first_line: int, reason: str):
        super().__init__(message)
        self.message = message
        self.first_line = first_line
        self.reason = reason


def _clean_name(raw: Any) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise TermNameError("VALIDATION_FAILED", "the name cannot be empty")
    if len(name) > MAX_NAME:
        raise TermNameError("VALIDATION_FAILED",
                            f"the name is longer than {MAX_NAME} characters")
    if any(ord(ch) < 32 for ch in name):
        raise TermNameError("VALIDATION_FAILED",
                            "the name contains a control character")
    return name


def _check(language: str, name_type: str, name: str) -> str:
    if language not in LANGUAGES:
        raise TermNameError("VALIDATION_FAILED",
                            f"language must be one of {', '.join(LANGUAGES)}")
    if name_type not in NAME_TYPES:
        raise TermNameError("VALIDATION_FAILED",
                            f"name_type must be one of {', '.join(NAME_TYPES)}")
    script = script_of(name)
    expected = "Latn" if name_type == "transliteration" else LANGUAGE_SCRIPT[language]
    if script is None:
        # Mixed or unrecognised script: the database CHECK would refuse it, and
        # the message there would not tell a curator which language was wrong.
        raise TermNameError(
            "VALIDATION_FAILED",
            f"'{name}' is not written in a single recognised script; "
            f"{language} expects {expected}")
    if script != expected:
        raise TermNameError(
            "VALIDATION_FAILED",
            f"'{name}' is written in {script}, but {language} uses {expected}",
            language=language, script=script, expected=expected)
    return term_key(name)


def _audit(action: str, word_id: int, language: str,
           before: Optional[str], after: Optional[str],
           actor: str = "system", actor_id: Optional[int] = None) -> None:
    """Record one write in ``audit_log`` through the application's own service.

    The first version issued ``INSERT ... SELECT id FROM users WHERE username =
    %s`` itself. When the actor did not resolve, that statement inserted **zero
    rows and raised nothing** - a write that says it was audited and was not.
    ``AuthService.audit`` takes the id it already has, so there is no lookup to
    come up empty, and it never raises either.

    An audit failure must not fail the write, but it must be visible: this logs
    at ``error`` with the action and the word id, and
    ``test_the_write_is_audited`` plus
    ``TestTheAuditCannotFailSilently`` hold that behaviour.
    """
    try:
        from core.security.service import get_auth_service

        get_auth_service().audit(
            f"TERM_NAME_{action}",
            user_id=actor_id,
            username=actor,
            resource=f"term_names:{word_id}:{language}",
            detail={"before": before, "after": after},
        )
    except Exception as exc:  # noqa: BLE001 - auditing must not fail the write
        logger.error(
            "term name audit write failed: action=TERM_NAME_%s word_id=%s "
            "language=%s actor=%s: %s: %s",
            action, word_id, language, actor, type(exc).__name__, exc)


def _audit_write(action: str, word_id: int, language: str,
                 before: Optional[str], after: Optional[str],
                 actor: str = "system", actor_id: Optional[int] = None) -> None:
    """Call :func:`_audit`, and survive even a *broken* audit function.

    ``_audit`` already swallows a failing audit backend. This second guard
    exists for the other failure: ``_audit`` itself raising - an import error,
    a logging handler failure, a signature change. Without it the row is
    committed and the client is told the write failed, which is the worst of
    both answers: a partial write the caller will retry.
    """
    try:
        _audit(action, word_id, language, before, after, actor, actor_id)
    except Exception:  # noqa: BLE001 - auditing can never fail the write
        logger.exception(
            "term name audit call crashed: action=TERM_NAME_%s word_id=%s "
            "language=%s actor=%s", action, word_id, language, actor)


def list_names(run, *, word_id: Optional[int] = None,
               category_id: Optional[int] = None,
               language: Optional[str] = None) -> List[Dict[str, Any]]:
    """Names for one word (directly or through its category), newest last."""
    where, params = [], []
    if word_id is not None:
        where.append("t.word_id = %s")
        params.append(int(word_id))
    if category_id is not None:
        where.append("t.word_id = (SELECT word_id FROM categorys WHERE id = %s)")
        params.append(int(category_id))
    if language:
        if language not in LANGUAGES:
            raise TermNameError("VALIDATION_FAILED",
                                f"language must be one of {', '.join(LANGUAGES)}")
        where.append("t.language = %s")
        params.append(language)
    sql = ("SELECT t.id, t.word_id, w.word, t.language, t.name, t.match_key,"
           " t.name_type, t.script, t.source, t.updated_at"
           " FROM term_names t JOIN words w ON w.id = t.word_id"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY t.language, t.name_type, t.id")
    rows = run(sql, tuple(params), fetch="all") or []
    return [
        {"id": r[0], "word_id": r[1], "word": r[2], "language": r[3], "name": r[4],
         "match_key": r[5], "name_type": r[6], "script": r[7], "source": r[8],
         "updated_at": r[9].isoformat() if hasattr(r[9], "isoformat") else r[9]}
        for r in rows
    ]


def create_name(run, payload: Dict[str, Any], *, actor: str = "system",
                actor_id: Optional[int] = None) -> Dict[str, Any]:
    word_id = payload.get("word_id")
    if word_id is None and payload.get("category_id") is not None:
        found = run("SELECT word_id FROM categorys WHERE id = %s",
                    (int(payload["category_id"]),), fetch="one")
        if not found:
            raise TermNameError("NOT_FOUND", "that category does not exist", 404)
        word_id = found[0]
    if word_id is None:
        raise TermNameError("VALIDATION_FAILED", "give word_id or category_id")
    try:
        word_id = int(word_id)
    except (TypeError, ValueError):
        raise TermNameError("VALIDATION_FAILED", "word_id must be a number")

    language = str(payload.get("language") or "").strip().lower()
    name_type = str(payload.get("name_type") or "preferred").strip().lower()
    name = _clean_name(payload.get("name"))
    key = _check(language, name_type, name)

    if run("SELECT 1 FROM words WHERE id = %s", (word_id,), fetch="one") is None:
        raise TermNameError("NOT_FOUND", "that word does not exist", 404)
    if name_type == "preferred":
        existing = run(
            "SELECT id FROM term_names WHERE word_id = %s AND language = %s"
            " AND name_type = 'preferred'", (word_id, language), fetch="one")
        if existing:
            raise TermNameError(
                "PREFERRED_NAME_EXISTS",
                f"this word already has a preferred {language} name; "
                f"update it instead", 409, existing_id=existing[0])

    row = run(
        "INSERT INTO term_names (word_id, name, match_key, language, name_type,"
        " script, source) VALUES (%s, %s, %s, %s, %s, %s, 'admin')"
        " RETURNING id", (word_id, name, key, language, name_type,
                          script_of(name)), fetch="one")
    _audit_write("CREATE", word_id, language, None, name, actor, actor_id)
    return {"id": row[0] if row else None, "word_id": word_id, "language": language,
            "name": name, "name_type": name_type, "match_key": key}


def update_name(run, name_id: int, payload: Dict[str, Any], *,
                actor: str = "system", actor_id: Optional[int] = None
                ) -> Dict[str, Any]:
    current = run("SELECT word_id, language, name, name_type FROM term_names"
                  " WHERE id = %s", (name_id,), fetch="one")
    if not current:
        raise TermNameError("NOT_FOUND", "that name does not exist", 404)
    word_id, language, old_name, old_type = current

    language = str(payload.get("language") or language).strip().lower()
    name_type = str(payload.get("name_type") or old_type).strip().lower()
    name = _clean_name(payload.get("name", old_name))
    key = _check(language, name_type, name)

    if name_type == "preferred":
        clash = run(
            "SELECT id FROM term_names WHERE word_id = %s AND language = %s"
            " AND name_type = 'preferred' AND id <> %s",
            (word_id, language, name_id), fetch="one")
        if clash:
            raise TermNameError(
                "PREFERRED_NAME_EXISTS",
                f"another preferred {language} name exists for this word", 409,
                existing_id=clash[0])

    run("UPDATE term_names SET name = %s, match_key = %s, language = %s,"
        " name_type = %s, script = %s, source = 'admin', updated_at = NOW()"
        " WHERE id = %s",
        (name, key, language, name_type, script_of(name), name_id), fetch=None)
    _audit_write("UPDATE", word_id, language, old_name, name, actor, actor_id)
    return {"id": name_id, "word_id": word_id, "language": language, "name": name,
            "name_type": name_type, "match_key": key}


def delete_name(run, name_id: int, *, actor: str = "system",
                actor_id: Optional[int] = None) -> Dict[str, Any]:
    current = run("SELECT word_id, language, name FROM term_names WHERE id = %s",
                  (name_id,), fetch="one")
    if not current:
        raise TermNameError("NOT_FOUND", "that name does not exist", 404)
    run("DELETE FROM term_names WHERE id = %s", (name_id,), fetch=None)
    _audit_write("DELETE", current[0], current[1], current[2], None, actor, actor_id)
    return {"id": name_id, "deleted": True}


def _rows_from_csv(stream) -> Iterable[tuple]:
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        raise TermNameError("VALIDATION_FAILED", "the file has no header row")
    header = {(name or "").strip().lower() for name in reader.fieldnames}
    missing = [column for column in CSV_COLUMNS if column not in header]
    if missing:
        raise TermNameError(
            "VALIDATION_FAILED",
            f"the header must contain: {', '.join(CSV_COLUMNS)} (missing: "
            f"{', '.join(missing)})")
    for line_number, row in enumerate(reader, start=2):
        yield line_number, {(k or "").strip().lower(): (v or "").strip()
                            for k, v in row.items()}


def import_csv(run, stream, *, apply: bool = False,
               actor: str = "system", actor_id: Optional[int] = None
               ) -> Dict[str, Any]:
    """Validate (and optionally apply) a glossary CSV.

    Columns: ``word, language, name, name_type`` - ``name_type`` may be empty
    and defaults to ``preferred``. ``word`` must already exist in ``words``; the
    importer never invents a term, because a typo in a bulk file must not create
    a category.
    """
    accepted, rejected, written, updated = [], [], 0, 0
    row_failures = 0
    first_row_failure = None
    for line_number, row in _rows_from_csv(stream):
        if not any(row.values()):
            continue
        try:
            word = row.get("word", "")
            if not word:
                raise TermNameError("VALIDATION_FAILED", "word is empty")
            found = run("SELECT id FROM words WHERE word = %s", (word,), fetch="one")
            if not found:
                raise TermNameError("UNKNOWN_WORD", f"no term named {word!r}")
            word_id = found[0]
            language = (row.get("language") or "").strip().lower()
            name_type = (row.get("name_type") or "preferred").strip().lower()
            name = _clean_name(row.get("name"))
            key = _check(language, name_type, name)

            existing = run(
                "SELECT id, name FROM term_names WHERE word_id = %s AND language = %s"
                " AND match_key = %s AND name_type = %s",
                (word_id, language, key, name_type), fetch="one")
            if name_type == "preferred":
                other = run(
                    "SELECT id, name FROM term_names WHERE word_id = %s"
                    " AND language = %s AND name_type = 'preferred'"
                    " AND (name = %s) IS FALSE",
                    (word_id, language, name), fetch="one")
                if other and not existing:
                    raise TermNameError(
                        "PREFERRED_NAME_EXISTS",
                        f"already has the preferred {language} name {other[1]!r}")
            accepted.append({"line": line_number, "word": word, "language": language,
                             "name": name, "name_type": name_type, "key": key,
                             "word_id": word_id, "existing_id": existing[0] if existing else None})
            if apply:
                if existing:
                    if existing[1] != name:
                        run("UPDATE term_names SET name = %s, updated_at = NOW()"
                            " WHERE id = %s", (name, existing[0]), fetch=None)
                        updated += 1
                else:
                    run("INSERT INTO term_names (word_id, name, match_key, language,"
                        " name_type, script, source) VALUES (%s, %s, %s, %s, %s, %s,"
                        " 'imported')",
                        (word_id, name, key, language, name_type, script_of(name)),
                        fetch=None)
                    written += 1
        except TermNameError as exc:
            rejected.append({"line": line_number, "word": row.get("word", ""),
                             "code": exc.code, "reason": exc.message})
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the file
            row_failures += 1
            if first_row_failure is None:
                first_row_failure = (line_number, f"{type(exc).__name__}: {exc}")
            rejected.append({"line": line_number, "word": row.get("word", ""),
                             "code": "ROW_FAILED",
                             "reason": f"{type(exc).__name__}"})
    if rejected and row_failures == len(rejected) and not accepted:
        # Not a validation result: the store could not be read at all.
        logger.error("term name import could not run: %s rows, first failure at "
                     "line %s: %s", row_failures, first_row_failure[0],
                     first_row_failure[1])
        raise ImportUnavailable("the import did not run", first_row_failure[0],
                                first_row_failure[1])
    if apply:
        _audit_write("IMPORT", 0, "-", None,
               f"{written} inserted, {updated} updated, {len(rejected)} rejected",
               actor, actor_id)
    return {"rows": len(accepted) + len(rejected), "accepted": len(accepted),
            "rejected": rejected, "inserted": written, "updated": updated,
            "sample": accepted[:5]}
