"""The disclosure register: every file that leaves the system is recorded.

``DATA_EXPORTED`` is written to ``audit_log`` for **every** response that
hands the client a file (``Content-Disposition: attachment``), from whichever
route produced it - search exports, bulk file exports, keyword and category
exports, backups, original-file downloads, and report artifacts. Coverage is
enforced centrally by an ``after_request`` hook rather than by remembering to
call a helper in each route, so a new export route cannot be added unaudited.

Routes enrich the record with what only they know (scope, row count, the
criteria fingerprint, truncation) via :func:`note_disclosure`.

The hook **fails closed**: if the audit record cannot be written, the export
is replaced with ``503 disclosure_audit_failed``. "What left this system, and
who took it?" is the one question this installation must never be unable to
answer, so an unrecordable disclosure is refused rather than allowed.

Measured, not assumed: the artifact's byte size and SHA-256 are computed from
the bytes actually sent when they can be read without streaming a huge file
into memory. When they cannot, the record says ``"sha256": null`` with the
reason - never a fabricated digest.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, Optional

from flask import g, jsonify, request

logger = logging.getLogger(__name__)

ACTION = "DATA_EXPORTED"

#: Largest in-memory/passthrough body digested inline. Above this, digesting
#: would mean reading a potentially multi-gigabyte original into memory.
DIGEST_LIMIT_BYTES = 64 * 1024 * 1024

_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)


def note_disclosure(**detail: Any) -> None:
    """Attach route-specific facts to this request's disclosure record.

    Well-known keys: ``kind`` (e.g. ``search_export``), ``scope``, ``format``,
    ``row_count``, ``total``, ``truncated``, ``criteria_fingerprint``,
    ``query_fingerprint``, ``sources``, ``columns``, ``artifact_sha256``.
    Values must be JSON-serialisable.
    """
    current = g.get("_disclosure") or {}
    current.update({k: v for k, v in detail.items()})
    g._disclosure = current


def _is_attachment(response) -> bool:
    disposition = response.headers.get("Content-Disposition", "")
    return "attachment" in disposition.lower()


def _filename(response) -> Optional[str]:
    match = _FILENAME_RE.search(response.headers.get("Content-Disposition", ""))
    return match.group(1) if match else None


def _measure(response, detail: Dict[str, Any]) -> Dict[str, Any]:
    """Digest the bytes actually sent, when that is safe to do."""
    if detail.get("artifact_sha256"):
        return {"sha256": detail["artifact_sha256"], "bytes": detail.get("artifact_bytes"),
                "measured_by": "route"}
    try:
        if not response.direct_passthrough and not response.is_streamed:
            data = response.get_data()
            return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                    "measured_by": "response_body"}
        length = response.content_length
        if response.direct_passthrough and length is not None and length <= DIGEST_LIMIT_BYTES:
            # send_file(BytesIO/path) wraps the body; buffer it (bounded) so the
            # digest describes exactly what is sent, then send the buffer.
            chunks = []
            iterable = response.response
            try:
                for chunk in iterable:
                    chunks.append(chunk)
            finally:
                close = getattr(iterable, "close", None)
                if close:
                    close()
            data = b"".join(chunks)
            response.direct_passthrough = False
            response.set_data(data)
            return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                    "measured_by": "response_body"}
        return {"sha256": None, "bytes": length,
                "not_measured": ("streamed response" if length is None
                                 else f"larger than the {DIGEST_LIMIT_BYTES}-byte digest limit")}
    except Exception as exc:  # measuring must not be the reason an export fails
        logger.warning("Could not digest export body: %s", exc.__class__.__name__)
        return {"sha256": None, "bytes": response.content_length,
                "not_measured": f"digest failed: {exc.__class__.__name__}"}


def build_record(response) -> Dict[str, Any]:
    detail = dict(g.get("_disclosure") or {})
    user = g.get("user")
    measured = _measure(response, detail)
    record = {
        "kind": detail.pop("kind", None) or (request.endpoint or "unknown"),
        "endpoint": request.endpoint,
        "method": request.method,
        "path": request.path,
        "status": response.status_code,
        "filename": _filename(response),
        "content_type": response.mimetype,
        "actor_role": getattr(user, "role", None),
        "artifact": measured,
    }
    detail.pop("artifact_sha256", None)
    detail.pop("artifact_bytes", None)
    detail.pop("force", None)
    record.update(detail)
    # Every record states format and count - measured or declared by the
    # route; a count the route did not declare is null with a reason, never 0.
    if not record.get("format"):
        name = record.get("filename") or ""
        record["format"] = (name.rsplit(".", 1)[1].lower() if "." in name
                            else (response.mimetype or "unknown"))
    if "row_count" not in record:
        record["row_count"] = None
        record["row_count_reason"] = "not declared by the route"
    record.setdefault("scope", None)
    return record


def init_disclosure_audit(app) -> None:
    """Register the fail-closed disclosure hook on ``app``."""

    @app.after_request
    def _record_disclosure(response):
        if response.status_code >= 400:
            return response
        if not (_is_attachment(response) or g.get("_disclosure", {}).get("force")):
            return response
        user = g.get("user")
        try:
            from core.security.service import get_auth_service

            record = build_record(response)
            resource = f"export:{record.get('kind')}"
            if record.get("scope"):
                resource += f":{record['scope']}"
            audit_id = get_auth_service().audit_strict(
                ACTION,
                user_id=getattr(user, "id", None),
                username=getattr(user, "username", None),
                resource=resource,
                detail=record,
                ip_address=request.remote_addr or "",
            )
            response.headers["X-Disclosure-Audit-Id"] = str(audit_id)
            return response
        except Exception as exc:
            logger.error("Export refused: DATA_EXPORTED could not be recorded (%s)",
                         exc.__class__.__name__, exc_info=True)
            close = getattr(response, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
            refused = jsonify({
                "success": False,
                "error": "Export refused: the disclosure could not be recorded in "
                         "the audit log. Nothing was sent.",
                "code": "disclosure_audit_failed",
            })
            refused.status_code = 503
            return refused
