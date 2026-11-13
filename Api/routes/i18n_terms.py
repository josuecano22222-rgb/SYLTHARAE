"""Term names over HTTP: read a page of labels, and curate them.

Two audiences, one module:

* ``GET /api/i18n/term-labels`` - a page script that only holds category or word
  ids asks once for their labels in the reader's language. The response says
  where each label came from (``translated``, ``base-language``,
  ``default-language``, ``canonical``), so the caller can mark a name that has
  not been translated yet instead of presenting English as Arabic.
* the curation endpoints under ``/api/term-names`` - list, create, update,
  delete, and a CSV import. Writes are administrator-only and every one is
  recorded in ``audit_log``.

Error bodies here follow the round-20 rule: a stable code and a fixed message,
never an exception text, a table name, SQL or a path. Validation failures say
what is wrong with the input because that is the caller's own data.
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional

from flask import jsonify, request

from Api.utils import execute_query
from core.errors import client_error
from core.security.flask_ext import current_user
from services.i18n.term_labels import (
    DEFAULT_LANGUAGE,
    labels_for,
    labels_for_categories,
    language_chain,
)
from services.i18n.term_names import (
    ImportUnavailable,
    TermNameError,
    create_name,
    delete_name,
    import_csv,
    list_names,
    update_name,
)

logger = logging.getLogger(__name__)

MAX_IDS = 500
#: Subsystem label for 5xx logs, so a correlation id names its origin.
_SUBSYSTEM = "Api.routes.i18n_terms"


def _run(sql, params=None, fetch="all"):
    return execute_query(sql, params, fetch=fetch)


def resolve_label_locale() -> Optional[str]:
    """``?lang=`` wins, then the interface language resolved for this request."""
    explicit = (request.args.get("lang") or "").strip()
    if explicit:
        return explicit
    try:
        from Api.routes.common import resolve_interface_language
        from flask import current_app
        return resolve_interface_language(current_app)
    except Exception:  # noqa: BLE001 - the default is a fine answer
        return None


def _ids(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    out = []
    for piece in raw.replace(" ", ",").split(","):
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError:
            continue
    return out[:MAX_IDS]


def _error(code: str, message: str, status: int, **details):
    """A 4xx body: the caller's own data, a stable code, no internals.

    Server-side failures do not come through here - they go to
    :func:`core.errors.client_error`, which logs the exception and returns a
    correlation id instead of an explanation (round-20 rule).
    """
    payload = {"success": False, "error": message, "code": code}
    if details:
        payload["details"] = details
    return jsonify(payload), status


def register_i18n_term_routes(app) -> None:
    """Register the label read endpoint and the curation API."""

    @app.route("/api/i18n/term-labels", methods=["GET"])
    def api_term_labels():
        locale = resolve_label_locale()
        word_ids = _ids(request.args.get("word_ids") or request.args.get("ids"))
        category_ids = _ids(request.args.get("category_ids"))
        if not word_ids and not category_ids:
            return _error("VALIDATION_FAILED",
                          "give word_ids or category_ids", 400)
        try:
            payload: Dict[str, Any] = {
                "success": True,
                "locale": locale,
                "chain": list(language_chain(locale)),
                "labels": {},
            }
            if word_ids:
                for word_id, label in labels_for(_run, word_ids, locale).items():
                    payload["labels"][str(word_id)] = {
                        "name": label.name, "language": label.language,
                        "source": label.source, "translated": label.translated,
                    }
            if category_ids:
                payload["category_labels"] = {}
                for category_id, label in labels_for_categories(
                        _run, category_ids, locale).items():
                    payload["category_labels"][str(category_id)] = {
                        "name": label.name, "language": label.language,
                        "source": label.source, "translated": label.translated,
                    }
            return jsonify(payload)
        except Exception as exc:  # noqa: BLE001 - never leak the reason
            logger.error("term labels failed: %s: %s", type(exc).__name__, exc)
            return client_error(exc, status=503, subsystem=_SUBSYSTEM,
                                  public_message="term labels are unavailable right now",
                                  success_key="success", code="TERM_LABELS_UNAVAILABLE")

    @app.route("/api/term-names", methods=["GET"])
    def api_term_names_list():
        try:
            word_id = request.args.get("word_id", type=int)
            category_id = request.args.get("category_id", type=int)
            language = (request.args.get("language") or "").strip() or None
            if word_id is None and category_id is None:
                return _error("VALIDATION_FAILED", "give word_id or category_id", 400)
            rows = list_names(_run, word_id=word_id, category_id=category_id,
                              language=language)
            return jsonify({"success": True, "names": rows})
        except TermNameError as exc:
            return _error(exc.code, exc.message, exc.status, **exc.details)
        except Exception as exc:  # noqa: BLE001
            logger.error("term names read failed: %s: %s", type(exc).__name__, exc)
            return client_error(exc, status=503, subsystem=_SUBSYSTEM,
                                  public_message="term names are unavailable",
                                  success_key="success", code="TERM_NAMES_UNAVAILABLE")

    @app.route("/api/term-names", methods=["POST"])
    def api_term_names_create():
        payload = request.get_json(silent=True) or {}
        try:
            row = create_name(_run, payload, **_actor())
            return jsonify({"success": True, "name": row}), 201
        except TermNameError as exc:
            return _error(exc.code, exc.message, exc.status, **exc.details)
        except Exception as exc:  # noqa: BLE001
            logger.error("term name create failed: %s: %s", type(exc).__name__, exc)
            return client_error(exc, subsystem=_SUBSYSTEM,
                                  public_message="the name was not saved",
                                  success_key="success", code="TERM_NAME_WRITE_FAILED")

    @app.route("/api/term-names/<int:name_id>", methods=["PUT"])
    def api_term_names_update(name_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            row = update_name(_run, name_id, payload, **_actor())
            return jsonify({"success": True, "name": row})
        except TermNameError as exc:
            return _error(exc.code, exc.message, exc.status, **exc.details)
        except Exception as exc:  # noqa: BLE001
            logger.error("term name update failed: %s: %s", type(exc).__name__, exc)
            return client_error(exc, subsystem=_SUBSYSTEM,
                                  public_message="the name was not saved",
                                  success_key="success", code="TERM_NAME_WRITE_FAILED")

    @app.route("/api/term-names/<int:name_id>", methods=["DELETE"])
    def api_term_names_delete(name_id: int):
        try:
            delete_name(_run, name_id, **_actor())
            return jsonify({"success": True})
        except TermNameError as exc:
            return _error(exc.code, exc.message, exc.status, **exc.details)
        except Exception as exc:  # noqa: BLE001
            logger.error("term name delete failed: %s: %s", type(exc).__name__, exc)
            return client_error(exc, subsystem=_SUBSYSTEM,
                                  public_message="the name was not removed",
                                  success_key="success", code="TERM_NAME_WRITE_FAILED")

    @app.route("/api/term-names/import", methods=["POST"])
    def api_term_names_import():
        """Dry-run by default; ``?apply=1`` writes the valid rows."""
        # A browser file input sends multipart/form-data; ``curl -F`` and the
        # page's own script may send the CSV as the raw body. Both are read
        # here, because the first version read only the body and a multipart
        # upload therefore failed as "the header must contain ..." - a file
        # the user could see was fine, refused for a reason about boundaries.
        upload = request.files.get("file")
        raw = upload.read() if upload else (request.get_data(cache=False) or b"")
        text = raw.decode("utf-8-sig", errors="replace")
        if not text.strip():
            return _error("VALIDATION_FAILED", "the upload is empty", 400)
        apply = request.args.get("apply") in ("1", "true", "yes")
        try:
            report = import_csv(_run, io.StringIO(text), apply=apply, **_actor())
            return jsonify({"success": True, "applied": apply, **report})
        except TermNameError as exc:
            return _error(exc.code, exc.message, exc.status, **exc.details)
        except ImportUnavailable as exc:
            return client_error(exc, subsystem=_SUBSYSTEM,
                                  public_message="the import did not run",
                                  success_key="success", code="TERM_NAME_IMPORT_FAILED")
        except Exception as exc:  # noqa: BLE001
            logger.error("term name import failed: %s: %s", type(exc).__name__, exc)
            return client_error(exc, subsystem=_SUBSYSTEM,
                                  public_message="the import did not run",
                                  success_key="success", code="TERM_NAME_IMPORT_FAILED")


def _actor() -> Dict[str, Any]:
    """The caller, from *this* application's session.

    ``flask_login`` is not installed in this project and never has been: the
    application loads its own user into ``g`` (``core.security.flask_ext``).
    Asking ``flask_login`` therefore raised ``ImportError`` on every write, the
    actor became ``"system"``, and the audit row - which the *service* looked up
    by username - was silently never written. The audit row is looked up by id
    now (``get_auth_service().audit``), and the lookup is gone, so a missing
    session can at worst produce a row with a null ``user_id``, not a lost row.
    """
    try:
        user = current_user()
    except Exception:  # noqa: BLE001 - no session, no actor, still no crash
        return {"actor": "system", "actor_id": None}
    username = getattr(user, "username", None) or "system"
    return {"actor": username, "actor_id": getattr(user, "id", None)}
