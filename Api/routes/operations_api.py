"""Unified operations API (spec section 24).

Endpoints for the frontend control surface: file input/ingestion, the
Import Center, and the shared Jobs/Operations system.

Security model (server-side enforced):
* authentication  - middleware default-deny (core.security.flask_ext);
* authorization   - viewers read; analysts/admins start/pause/resume/cancel
                    ingestion and batch-import jobs; backup-import jobs and
                    job deletion are admin-only;
* CSRF            - global flask-wtf protection (no exemptions);
* validation      - every payload validated server-side (services raise
                    IngestionValidationError/... -> HTTP 400 structured error);
* rate limiting   - strict per-route limits on job creation.
"""
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from core.validation import query_int, query_text

from core.path_safety import configured_ingestion_roots
from core.security.flask_ext import admin_required, current_user
from core.archive_safety import windows_safe_component
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from services.importing.backup_import_service import (
    BackupImportService, BatchImportService,
)
from services.ingesting.options import IngestionOptions
from services.ingesting.service import (
    IngestionRequest, IngestionService, IngestionValidationError,
)
from services.jobs import job_state
from services.jobs.manager import JobManager
from services.jobs.models import job_to_api
from services.sources import (
    SourceSideError, create_side_svc, create_source_svc,
    list_sides_svc, list_sources_svc, search_sides_svc, search_sources_svc,
)

logger = logging.getLogger(__name__)

operations_bp = Blueprint("operations_api", __name__)


def _manager() -> JobManager:
    return JobManager.get_instance()


def _error(code: str, message: str, status: int, details=None):
    """Structured error envelope (spec section 23)."""
    body = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "request_id": uuid.uuid4().hex[:12],
        },
    }
    if details is not None:
        body["error"]["details"] = details
    return jsonify(body), status


#: Errnos that mean "the disk cannot take this right now", not "we have a bug".
_STORAGE_ERRNOS = frozenset({
    errno.ENOSPC,    # no space left on device
    errno.EDQUOT,    # quota exceeded
    errno.EROFS,     # read-only filesystem
    errno.EACCES,    # permission denied on the staging tree
    errno.EPERM,     # ditto, on some filesystems
    errno.EIO,       # I/O error on the device
})


def _storage_failure(exc: BaseException):
    """507 for a storage problem, ``None`` when it is anything else.

    Staging is plain filesystem work. A full volume or an unwritable upload
    directory is an expected condition with a plain answer; letting it become an
    unhandled 500 (measured in the round-10 disk-full drill) hides the one thing
    the operator needs to know.
    """
    if isinstance(exc, OSError) and exc.errno in _STORAGE_ERRNOS:
        return _error(
            "STORAGE_UNAVAILABLE",
            "The upload storage cannot be written right now "
            f"(ERRNO {exc.errno}: {exc.strerror or exc}). Free space on the "
            "data volume or check its permissions, then retry.",
            507,
        )
    return None


def _validation_error(exc: Exception):
    return _error("VALIDATION_FAILED", str(exc), 400)


def _reject_invalid_domain_import(itype, data):
    """Validate a domain import before a job row exists (400, not a doomed job).

    The worker validates again (the file can disappear in between); this
    check only moves the common rejections - a path instead of a file name,
    a file that is not there - to the request, where the user sees the reason.
    """
    if itype != "domain_import":
        return None
    from services.importing.domain_import_service import (
        DomainImportRequest, DomainImportService, DomainImportValidationError,
    )

    try:
        DomainImportService().validate(DomainImportRequest(data_file=data.get("data_file")))
    except DomainImportValidationError as exc:
        return _validation_error(exc)
    return None


def _username() -> str:
    user = current_user()
    return getattr(user, "username", None) or "system"


def _job_or_404(job_id):
    try:
        job = _manager().get(job_id)
    except KeyError:
        job = None
    if job is None:
        return None, _error("JOB_NOT_FOUND", "Job not found", 404)
    return job, None


MAX_UPLOAD_BYTES = int(os.environ.get("OPERATIONS_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
# Large files are streamed in chunks, so the per-request ceiling above does not
# have to be the ceiling for the feature. Both numbers are reported by
# /api/input/options-info so the interface never has to guess a limit.
MAX_CHUNKED_UPLOAD_BYTES = (
    int(os.environ.get("OPERATIONS_MAX_CHUNKED_UPLOAD_MB", "20480")) * 1024 * 1024
)
CHUNK_SIZE_BYTES = int(os.environ.get("OPERATIONS_UPLOAD_CHUNK_MB", "8")) * 1024 * 1024
UPLOAD_SUBDIR = "uploads"
CHUNKED_SUBDIR = "chunked"
#: A chunked upload id is a uuid4 hex; validated before it is used as a path.
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _staged_uploads_dir() -> Path:
    from core.app_paths import get_data_root

    d = Path(get_data_root()) / UPLOAD_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chunked_root() -> Path:
    d = _staged_uploads_dir() / CHUNKED_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_staging_dir() -> Path:
    """One directory per staged batch, shared by direct and chunked uploads."""
    d = _staged_uploads_dir() / uuid.uuid4().hex[:12]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_relative_parts(name: str):
    """Client-supplied name -> containment-safe, Windows-safe relative parts.

    Browsers send ``sub/dir/file.txt`` (forward slashes, on every OS) for a
    folder selection and a bare name for a single file. Windows hosts can also
    send a drive-qualified or UNC path for the same selection, so those shapes
    are reduced to what lies below the root rather than refused.

    Each component is then made *writable on Windows* by the same helper the
    archive extractor uses (``core.archive_safety.windows_safe_component``):
    reserved device names (CON, NUL, LPT1 ...), trailing dots/spaces and
    over-long components are defused instead of losing the file, and the file
    name is what the browser sent. ``..`` and empty segments are dropped, so a
    staged file can never escape its batch directory - ``Path(fs.filename).name``
    alone silently flattened folders and let two same-named files overwrite
    each other.
    """
    if "\x00" in (name or ""):
        raise _StagingError("BAD_NAME", "File name contains a NUL byte", 400)

    raw = (name or "").replace("\\", "/")
    # A drive-qualified path ("C:/docs/a.txt") and absolute/UNC shapes: keep the
    # part below the root; a drive letter is never a directory of ours.
    # Only "X:/" is a drive. "a:b.txt" is a legal file name on macOS and Linux
    # and the colon must be *mapped*, not treated as a prefix to cut off -
    # otherwise the stored name silently loses everything before the colon.
    raw = re.sub(r"^[A-Za-z]:[\\/]", "", raw).lstrip("/")

    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        # Characters Windows refuses outright. Sources on POSIX/macOS can carry
        # them (and a Windows client cannot create them), so map rather than
        # drop - the response reports the stored name either way.
        part = re.sub(r'[:*?"<>|]', "_", part)
        part = windows_safe_component(part)
        if part:
            parts.append(part)
    if not parts:
        parts = ["upload.bin"]
    return parts


class _StagingError(ValueError):
    """A staging failure that maps to one API error (code, message, status)."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _staged_path_limit() -> int:
    """Longest staged *path* we will write, in characters.

    Windows still enforces MAX_PATH (260) unless long paths are enabled for the
    process, and the staging directory itself consumes part of that budget, so
    the default is deliberately conservative there. Other platforms get the
    kernel's typical PATH_MAX. Overridable for deployments that know their own
    limit: ``OPERATIONS_MAX_STAGED_PATH_CHARS``.
    """
    default = 240 if os.name == "nt" else 4096
    try:
        value = int(os.environ.get("OPERATIONS_MAX_STAGED_PATH_CHARS", str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _checked_destination(directory: Path, parts):
    """Destination for ``parts``, refused up front when it cannot be written.

    The check happens before any directory is created, so a file that cannot be
    staged leaves nothing behind for the next run to trip over.
    """
    limit = _staged_path_limit()
    candidate = directory.joinpath(*parts)
    if len(str(candidate)) > limit:
        raise _StagingError(
            "PATH_TOO_LONG",
            f"Stored path would be {len(str(candidate))} characters; the limit "
            f"is {limit} (OPERATIONS_MAX_STAGED_PATH_CHARS). Shorten the folder "
            f"name and try again.",
            400,
        )
    try:
        return _unique_destination(directory, parts)
    except (FileExistsError, NotADirectoryError) as exc:
        # The client's own names collided: `Case/a.txt` and
        # `Case/a.txt/inner.txt` cannot both exist, on Windows or anywhere
        # else, because the first needs `a.txt` to be a file and the second
        # needs it to be a directory. That is one file's problem - the rest of
        # the selection still stages - and it is the client's naming, not a
        # server fault, so it is reported rather than raised as a 500.
        raise _StagingError(
            "NAME_CONFLICT",
            "A file and a folder in this selection have the same name; rename "
            "one of them and select again.",
            400,
        ) from exc
    except OSError as exc:
        # Genuinely unwritable staging (permissions, space, a path the
        # filesystem refuses): a server-side failure, said plainly.
        logger.warning("staging area could not be prepared for %s: %s", parts, exc)
        raise _StagingError(
            "STORAGE_FAILED",
            "The staging area could not be written to for this file.",
            500,
        ) from exc


def _unique_destination(directory: Path, parts) -> Path:
    """Reserve a non-colliding path inside ``directory`` (never overwrite)."""
    base = directory.joinpath(*parts[:-1])
    base.mkdir(parents=True, exist_ok=True)
    stem, suffix = os.path.splitext(parts[-1])
    candidate = base / parts[-1]
    counter = 2
    while candidate.exists():
        candidate = base / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def _stream_to(dest: Path, stream, declared_size=None, digest=None):
    """Copy an upload stream to ``dest``; returns bytes written.

    ``digest`` (a hashlib object) is updated as the bytes go by, so a chunk can
    be verified against the hash the client computed without reading it twice.
    """
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if declared_size is not None and written > declared_size:
                raise ValueError("chunk larger than declared")
            if written > MAX_CHUNKED_UPLOAD_BYTES:
                raise ValueError("size")
            if digest is not None:
                digest.update(chunk)
            out.write(chunk)
    return written


# ===========================================================================
# Sources & sides (Input page selects)
# ===========================================================================
@operations_bp.route("/api/input/options-info", methods=["GET"])
def api_input_options_info():
    """Capability info for the Input page (real backend facts only)."""
    try:
        roots = bool(configured_ingestion_roots())
    except Exception:
        roots = False
    try:
        root_list = [str(r) for r in configured_ingestion_roots()]
    except Exception:
        root_list = []
    return jsonify({
        "success": True,
        "ingestion_roots_configured": roots,
        "server_path_import_available": roots,
        "ingestion_roots": root_list,
        "upload_available": True,
        "max_direct_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_chunked_upload_mb": MAX_CHUNKED_UPLOAD_BYTES // (1024 * 1024),
        "chunk_size_mb": max(CHUNK_SIZE_BYTES // (1024 * 1024), 1),
        "max_staged_path_chars": _staged_path_limit(),
        "platform": os.name,
        "hashing": "sha256-streamed (always on)",
        "deduplication": "identity=(hash,source,side) (always on)",
        "archive_safety": "core.archive_safety (always on)",
    })


@operations_bp.route("/api/input/sources", methods=["GET"])
def api_input_sources():
    q = query_text("q", max_length=255)
    rows = search_sources_svc(q) if q else list_sources_svc()
    return jsonify({"success": True, "sources": rows})


@operations_bp.route("/api/input/sides", methods=["GET"])
def api_input_sides():
    q = query_text("q", max_length=255)
    rows = search_sides_svc(q) if q else list_sides_svc()
    return jsonify({"success": True, "sides": rows})


@operations_bp.route("/api/input/sources", methods=["POST"])
@limiter.limit("20 per minute")
def api_input_create_source():
    payload = request.get_json(silent=True) or {}
    try:
        created = create_source_svc(payload)
        return jsonify({"success": True, **created}), 201
    except SourceSideError as exc:
        return _validation_error(exc)
    except Exception:
        logger.exception("create source failed")
        return _error("SOURCE_CREATE_FAILED", "Source could not be created", 500)


@operations_bp.route("/api/input/sides", methods=["POST"])
@limiter.limit("20 per minute")
def api_input_create_side():
    payload = request.get_json(silent=True) or {}
    try:
        created = create_side_svc(payload)
        return jsonify({"success": True, **created}), 201
    except SourceSideError as exc:
        return _validation_error(exc)
    except Exception:
        logger.exception("create side failed")
        return _error("SIDE_CREATE_FAILED", "Side could not be created", 500)


# ===========================================================================
# Input: staged uploads + ingestion jobs
# ===========================================================================
@operations_bp.route("/api/input/uploads", methods=["POST"])
@limiter.limit("30 per minute")
def api_input_upload():
    """Stream uploaded files to server-side staging; returns staged paths.

    The browser cannot reference arbitrary server filesystem paths; uploads
    are staged under the application data root and can then be ingested.
    Archive/path safety applies later, at processing time, to staged files
    exactly as to any other input.
    """
    files = request.files.getlist("files") or (
        [request.files["file"]] if "file" in request.files else []
    )
    if not files:
        return _error("NO_FILES", "No files provided", 400)
    # Folder uploads keep their structure; the client sends the browser's
    # relative path (if any) for each file, in the same order as the files.
    names = []
    if request.form.get("relative_paths"):
        try:
            names = json.loads(request.form["relative_paths"])
        except (TypeError, ValueError):
            return _error("BAD_PATHS", "relative_paths must be a JSON array", 400)
        if not isinstance(names, list):
            return _error("BAD_PATHS", "relative_paths must be a JSON array", 400)

    staged_dir = _new_staging_dir()
    staged = []
    failed = []
    total = 0
    for index, fs in enumerate(files):
        supplied = names[index] if index < len(names) else fs.filename
        try:
            parts = _safe_relative_parts(supplied)
            dest = _checked_destination(staged_dir, parts)
        except _StagingError as exc:
            # One impossible name must not cost the operator the other files in
            # the same selection: record it and keep going.
            failed.append({"name": str(supplied), "code": exc.code, "message": exc.message})
            continue
        try:
            written = _stream_to(dest, fs.stream, declared_size=MAX_UPLOAD_BYTES)
        except ValueError:
            dest.unlink(missing_ok=True)
            failed.append({
                "name": str(supplied),
                "code": "UPLOAD_TOO_LARGE",
                "message": (
                    f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                    "single-request limit; it is sent in chunks instead when "
                    "the browser supports it."
                ),
            })
            continue
        except OSError as exc:
            dest.unlink(missing_ok=True)
            logger.warning("could not store upload %r: %s", supplied, exc)
            failed.append({
                "name": str(supplied),
                "code": "STORAGE_FAILED",
                "message": "The file could not be written to the staging area.",
            })
            continue
        except Exception:
            dest.unlink(missing_ok=True)
            logger.exception("upload staging failed")
            failed.append({
                "name": str(supplied),
                "code": "UPLOAD_FAILED",
                "message": "Upload could not be stored.",
            })
            continue
        total += written
        staged.append({"path": str(dest), "name": str(dest.relative_to(staged_dir)), "bytes": written})

    if not staged:
        shutil.rmtree(staged_dir, ignore_errors=True)
        first = failed[0] if failed else {"code": "NO_FILES",
                                          "message": "No valid files provided"}
        status = {"UPLOAD_TOO_LARGE": 413, "PATH_TOO_LONG": 400, "BAD_NAME": 400,
                  "NAME_CONFLICT": 400, "STORAGE_FAILED": 500}.get(first["code"], 400)
        return _error(first["code"], first["message"], status, details={"failed": failed})
    return jsonify({
        "success": True,
        "staged_paths": [entry["path"] for entry in staged],
        "staged": staged,
        "failed": failed,
        "bytes": total,
        "expires_note": "Staged files are plain files; ingest or delete them.",
    }), 201


# ===========================================================================
# Input: chunked staging for files larger than a single request may carry
# ===========================================================================
#
# The direct endpoint above streams one file per request and is bounded by
# OPERATIONS_MAX_UPLOAD_MB. Large files are staged the same way through
# chunked requests, and produce exactly the same result: plain files under the
# staging directory, which a job then ingests like any other path. Session
# state is on disk (one directory per upload), so an interrupted upload can be
# resumed and a restarted server does not lose it.


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _chunked_session_dir(upload_id: str) -> Path:
    return _chunked_root() / upload_id


def _load_session(upload_id: str):
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        return None
    meta = _chunked_session_dir(upload_id) / "session.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _session_state(upload_id: str, session: dict) -> dict:
    directory = _chunked_session_dir(upload_id)
    uploaded = sorted(
        int(p.name.split("_")[1])
        for p in directory.glob("chunk_*")
        if p.name.split("_")[1].isdigit()
    )
    return {
        "upload_id": upload_id,
        "filename": session["filename"],
        "total_chunks": session["total_chunks"],
        "chunk_size": session["chunk_size"],
        "bytes_received": sum(p.stat().st_size for p in directory.glob("chunk_*")),
        "uploaded_chunks": uploaded,
        "missing_chunks": [
            i for i in range(session["total_chunks"]) if i not in set(uploaded)
        ],
    }


@operations_bp.route("/api/input/uploads/chunked/start", methods=["POST"])
@limiter.limit("60 per minute")
def api_chunked_start():
    """Begin a chunked staging session; returns the chunk plan."""
    data = request.get_json(silent=True) or {}
    filename = str(data.get("filename") or "").strip()
    try:
        size = int(data.get("size") or 0)
    except (TypeError, ValueError):
        return _error("BAD_SIZE", "size must be an integer number of bytes", 400)
    if not filename:
        return _error("NO_FILENAME", "filename is required", 400)
    if size <= 0:
        return _error("BAD_SIZE", "size must be greater than zero", 400)
    if size > MAX_CHUNKED_UPLOAD_BYTES:
        return _error(
            "UPLOAD_TOO_LARGE",
            f"File exceeds the {MAX_CHUNKED_UPLOAD_BYTES // (1024 * 1024)} MB chunked limit",
            413,
        )
    declared_hash = str(data.get("sha256") or "").strip().lower()
    if declared_hash and not re.fullmatch(r"[0-9a-f]{64}", declared_hash):
        return _error("BAD_HASH", "sha256 must be 64 hex characters", 400)

    chunk_size = CHUNK_SIZE_BYTES
    upload_id = uuid.uuid4().hex
    directory = _chunked_session_dir(upload_id)
    total_chunks = max((size + chunk_size - 1) // chunk_size, 1)
    session = {
        "filename": filename,
        "size": size,
        "sha256": declared_hash,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "created_at": _utc_now(),
    }
    try:
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "session.json").write_text(json.dumps(session), encoding="utf-8")
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        answer = _storage_failure(exc)
        if answer is not None:
            return answer
        raise
    return jsonify({
        "success": True,
        "upload_id": upload_id,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "max_bytes": MAX_CHUNKED_UPLOAD_BYTES,
    }), 201


@operations_bp.route(
    "/api/input/uploads/chunked/<upload_id>/chunk/<int:index>", methods=["POST"]
)
@limiter.limit("1200 per minute")
def api_chunked_chunk(upload_id: str, index: int):
    """Store one chunk. Chunks are written to a temporary name and renamed, so
    a half-written chunk is never mistaken for a complete one."""
    session = _load_session(upload_id)
    if session is None:
        return _error("UPLOAD_NOT_FOUND", "Upload session not found", 404)
    if index < 0 or index >= int(session["total_chunks"]):
        return _error("BAD_CHUNK", "Chunk index out of range", 400)
    part = request.files.get("chunk")
    if part is None:
        return _error("NO_CHUNK", "No chunk provided", 400)

    directory = _chunked_session_dir(upload_id)
    incoming = directory / f"chunk_{index}.part"
    final = directory / f"chunk_{index}"
    remaining = int(session["size"]) - index * int(session["chunk_size"])
    declared = (request.form.get("sha256") or "").strip().lower()
    if declared and not re.fullmatch(r"[0-9a-f]{64}", declared):
        return _error("BAD_HASH", "sha256 must be 64 hex characters", 400)
    digest = hashlib.sha256() if declared else None
    try:
        written = _stream_to(
            incoming, part.stream, declared_size=max(remaining, 1), digest=digest
        )
    except ValueError:
        incoming.unlink(missing_ok=True)
        return _error("CHUNK_TOO_LARGE", "Chunk is larger than the plan allows", 413)
    except Exception as exc:
        incoming.unlink(missing_ok=True)
        answer = _storage_failure(exc)
        if answer is not None:
            return answer
        logger.exception("chunk write failed")
        return _error("CHUNK_FAILED", "Chunk could not be stored", 500)
    if digest is not None and digest.hexdigest() != declared:
        incoming.unlink(missing_ok=True)
        return _error(
            "CHUNK_HASH_MISMATCH",
            f"Chunk {index} does not match its SHA-256; it was discarded, retry the chunk",
            422,
        )
    incoming.replace(final)
    state = _session_state(upload_id, session)
    return jsonify({"success": True, "bytes": written, **state})


@operations_bp.route("/api/input/uploads/chunked/<upload_id>", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_chunked_status(upload_id: str):
    session = _load_session(upload_id)
    if session is None:
        return _error("UPLOAD_NOT_FOUND", "Upload session not found", 404)
    return jsonify({"success": True, **_session_state(upload_id, session)})


@operations_bp.route("/api/input/uploads/chunked/<upload_id>/complete", methods=["POST"])
@limiter.limit("60 per minute")
def api_chunked_complete(upload_id: str):
    """Assemble the chunks into the staging directory and verify integrity.

    The assembled file is byte-identical to what the browser held: the size is
    checked against the plan, the per-session SHA-256 is verified when the
    client declared one, and the digest of the staged file is returned so the
    interface can show what was actually stored. Nothing enters the database
    here - the job does that, and it hashes the file again for deduplication.
    """
    session = _load_session(upload_id)
    if session is None:
        return _error("UPLOAD_NOT_FOUND", "Upload session not found", 404)
    state = _session_state(upload_id, session)
    if state["missing_chunks"]:
        return _error(
            "UPLOAD_INCOMPLETE",
            f"{len(state['missing_chunks'])} of {state['total_chunks']} chunks are missing",
            409,
            details={"missing_chunks": state["missing_chunks"][:50]},
        )

    directory = _chunked_session_dir(upload_id)
    staged_dir = _new_staging_dir()
    try:
        dest = _checked_destination(
            staged_dir, _safe_relative_parts(session["filename"])
        )
    except _StagingError as exc:
        shutil.rmtree(staged_dir, ignore_errors=True)
        return _error(exc.code, exc.message, exc.status)
    digest = hashlib.sha256()
    total = 0
    try:
        with open(dest, "wb") as out:
            for index in range(int(session["total_chunks"])):
                with open(directory / f"chunk_{index}", "rb") as part:
                    while True:
                        block = part.read(4 * 1024 * 1024)
                        if not block:
                            break
                        digest.update(block)
                        total += len(block)
                        out.write(block)
    except OSError as exc:
        shutil.rmtree(staged_dir, ignore_errors=True)
        logger.warning("chunk assembly could not write %r: %s", session["filename"], exc)
        return _error(
            "STORAGE_FAILED",
            "The assembled file could not be written to the staging area.",
            500,
        )
    except Exception:
        shutil.rmtree(staged_dir, ignore_errors=True)
        logger.exception("chunk assembly failed")
        return _error("ASSEMBLY_FAILED", "Uploaded chunks could not be assembled", 500)

    if total != int(session["size"]):
        shutil.rmtree(staged_dir, ignore_errors=True)
        return _error(
            "SIZE_MISMATCH",
            f"Assembled {total} bytes but {session['size']} were declared",
            422,
        )
    actual_hash = digest.hexdigest()
    if session.get("sha256") and actual_hash != session["sha256"]:
        shutil.rmtree(staged_dir, ignore_errors=True)
        return _error(
            "HASH_MISMATCH",
            "Assembled file does not match the declared SHA-256",
            422,
            details={"expected": session["sha256"], "actual": actual_hash},
        )

    shutil.rmtree(directory, ignore_errors=True)
    return jsonify({
        "success": True,
        "staged_path": str(dest),
        "name": str(dest.relative_to(staged_dir)),
        "bytes": total,
        "sha256": actual_hash,
    }), 201


@operations_bp.route("/api/input/uploads/chunked/<upload_id>", methods=["DELETE"])
@limiter.limit("60 per minute")
def api_chunked_cancel(upload_id: str):
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        return _error("UPLOAD_NOT_FOUND", "Upload session not found", 404)
    directory = _chunked_session_dir(upload_id)
    if not directory.exists():
        return _error("UPLOAD_NOT_FOUND", "Upload session not found", 404)
    shutil.rmtree(directory, ignore_errors=True)
    return jsonify({"success": True, "cancelled": upload_id})


def _as_bool(value, default: bool) -> bool:
    """Boolean coercion for multipart form fields (string "false" → False)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _parse_ingestion_payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    # multipart: staged upload followed by job creation in one request
    data = request.form.to_dict()
    paths = data.pop("file_paths", "[]")
    try:
        data["file_paths"] = json.loads(paths) if paths else []
    except (TypeError, ValueError):
        raise IngestionValidationError("file_paths must be a JSON array")
    if request.files:
        staged_dir = _staged_uploads_dir() / uuid.uuid4().hex[:12]
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged = []
        for fs in request.files.getlist("files"):
            name = Path(fs.filename or "upload.bin").name
            dest = staged_dir / name
            fs.save(dest)
            staged.append(str(dest))
        data["file_paths"] = (data.get("file_paths") or []) + staged
    return data


@operations_bp.route("/api/input/jobs", methods=["POST"])
@limiter.limit("10 per minute")
def api_create_input_job():
    """Create an ingestion job (dry_run supported)."""
    try:
        data = _parse_ingestion_payload()
    except IngestionValidationError as exc:
        return _validation_error(exc)

    processing = data.get("processing") or {}
    if not isinstance(processing, dict):
        return _validation_error(IngestionValidationError("processing must be an object"))
    try:
        options = IngestionOptions(**{
            k: processing[k] for k in
            ("max_workers", "checkpoint", "enable_monitoring")
            if k in processing
        })
    except (TypeError, ValueError):
        return _validation_error(IngestionValidationError("Invalid processing options"))

    request_payload = {
        "path": data.get("path"),
        "file_paths": data.get("file_paths") or [],
        "source": data.get("source") or "",
        "side": data.get("side") or "",
        "recursive": bool(data.get("recursive", True)),
        "dry_run": bool(data.get("dry_run", False)),
        "processing": options.to_dict(),
    }
    # Validate BEFORE persisting the job (fail fast, no doomed job rows).
    probe = IngestionService()
    req = IngestionRequest(
        path=request_payload["path"],
        file_paths=request_payload["file_paths"],
        source=request_payload["source"],
        side=request_payload["side"],
        recursive=request_payload["recursive"],
        options=options,
        dry_run=request_payload["dry_run"],
        created_by=_username(),
    )
    try:
        probe.validate(req)
    except IngestionValidationError as exc:
        return _validation_error(exc)

    if request_payload["dry_run"]:
        # Dry runs are quick; answer synchronously (still no DB writes).
        try:
            return jsonify({
                "success": True, "dry_run": True,
                "preview": probe.discover(req),
            })
        except IngestionValidationError as exc:
            return _validation_error(exc)

    try:
        job = _manager().create_job(
            "ingestion",
            source=req.path or f"{len(req.file_paths)} files",
            options={
                "path": req.path,
                "file_paths": req.file_paths,
                "source": req.source,
                "side": req.side,
                "recursive": req.recursive,
                "processing": options.to_dict(),
            },
            created_by=_username(),
        )
    except Exception:
        logger.exception("job creation failed")
        return _error("JOB_CREATE_FAILED", "Job could not be created", 500)
    return jsonify({"success": True, "job": job_to_api(job)}), 202


@operations_bp.route("/api/input/jobs", methods=["GET"])
def api_list_input_jobs():
    jobs = _manager().list(job_type="ingestion",
                           limit=query_int("limit", 50, minimum=1, maximum=200))
    return jsonify({"success": True, "jobs": [job_to_api(j) for j in jobs]})


# ===========================================================================
# Import Center
# ===========================================================================
IMPORT_TYPES = ("domain_import", "backup_import", "batch_import")


@operations_bp.route("/api/import/validate", methods=["POST"])
@limiter.limit("20 per minute")
def api_import_validate():
    """Validate an import source without committing anything."""
    data = request.get_json(silent=True) or {}
    itype = data.get("type")
    try:
        if itype == "batch_import":
            preview = BatchImportService().preview(data.get("file_paths") or [])
            return jsonify({"success": True, "preview": preview})
        if itype == "backup_import":
            svc = BackupImportService()
            path = data.get("backup_path")
            if not path:
                return _error("VALIDATION_FAILED", "No backup file provided", 400)
            return jsonify({"success": True, "preview": svc.validate_backup(path)})
        if itype == "domain_import":
            from services.importing.domain_import_service import (
                DomainImportRequest, DomainImportService,
            )

            svc = DomainImportService()
            req = svc.validate(DomainImportRequest(data_file=data.get("data_file")))
            return jsonify({"success": True, "preview": {
                "data_file": req.data_file or "(default)",
                "note": "Use preview=true on the job to parse without writing.",
            }})
    except Exception as exc:
        return _validation_error(exc)
    return _error("VALIDATION_FAILED", f"Unknown import type: {itype}", 400)


@operations_bp.route("/api/import/preview", methods=["POST"])
@limiter.limit("10 per minute")
def api_import_preview():
    """Dry-run preview of a full import job (no destructive changes)."""
    data = request.get_json(silent=True) or {}
    itype = data.get("type")
    if itype not in IMPORT_TYPES:
        return _error("VALIDATION_FAILED", f"Unknown import type: {itype}", 400)
    rejected = _reject_invalid_domain_import(itype, data)
    if rejected is not None:
        return rejected
    try:
        job = _manager().create_job(
            itype,
            source=data.get("source") or data.get("data_file") or "preview",
            options={**data, "dry_run": True},
            created_by=_username(),
            starter=None if itype != "backup_import" else None,
        )
    except Exception:
        logger.exception("preview job failed")
        return _error("JOB_CREATE_FAILED", "Preview job could not be created", 500)
    if _manager().synchronous:
        job = _manager().get(job["job_id"])
        return jsonify({"success": True, "job": job_to_api(job),
                        "stats": job.get("stats")})
    return jsonify({"success": True, "job": job_to_api(job)}), 202


@operations_bp.route("/api/import/jobs", methods=["POST"])
@limiter.limit("10 per minute")
def api_create_import_job():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    itype = data.get("type")
    if itype not in IMPORT_TYPES:
        return _validation_error(
            Exception(f"type must be one of: {', '.join(IMPORT_TYPES)}"))
    if itype == "backup_import":
        # Admin-only regardless of button visibility (spec section 17).
        user = current_user()
        if getattr(user, "role", None) != "admin":
            return _error("FORBIDDEN", "Administrator role required", 403)
        # staged upload support
        fs = request.files.get("file")
        if fs is not None:
            staged_dir = _staged_uploads_dir() / uuid.uuid4().hex[:12]
            staged_dir.mkdir(parents=True, exist_ok=True)
            name = Path(fs.filename or "backup.zip").name
            dest = staged_dir / name
            fs.save(dest)
            data["backup_path"] = str(dest)
        if not data.get("backup_path"):
            # Fail fast: no doomed job rows (same contract as the
            # /api/import/validate endpoint).
            return _validation_error(Exception("No backup file provided"))
    if itype == "domain_import" and not data.get("data_file"):
        return _validation_error(Exception("No data_file provided"))
    rejected = _reject_invalid_domain_import(itype, data)
    if rejected is not None:
        return rejected
    if itype == "batch_import":
        # accept staged uploads too
        fs_list = request.files.getlist("files")
        if fs_list:
            staged_dir = _staged_uploads_dir() / uuid.uuid4().hex[:12]
            staged_dir.mkdir(parents=True, exist_ok=True)
            staged = []
            for fs in fs_list:
                name = Path(fs.filename or "upload.bin").name
                dest = staged_dir / name
                fs.save(dest)
                staged.append(str(dest))
            data["file_paths"] = (data.get("file_paths") or []) + staged
        if not data.get("source") or not data.get("side"):
            return _validation_error(Exception("source and side are required"))
    try:
        options = {k: v for k, v in data.items()
                   if k in ("file_paths", "source", "side", "data_file",
                            "backup_path", "backup_name", "processing")}
        job = _manager().create_job(itype, source=str(data.get("source") or itype),
                                    options=options, created_by=_username())
    except Exception:
        logger.exception("import job failed to start")
        return _error("JOB_CREATE_FAILED", "Import job could not be created", 500)
    return jsonify({"success": True, "job": job_to_api(job)}), 202


@operations_bp.route("/api/import/jobs", methods=["GET"])
def api_list_import_jobs():
    jobs = _manager().list(
        job_type=request.args.get("type") or None,
        limit=query_int("limit", 50, minimum=1, maximum=200),
    )
    return jsonify({"success": True, "jobs": [job_to_api(j) for j in jobs]})


# ===========================================================================
# Jobs / Operations Center (shared across job types)
# ===========================================================================
# The limit below is the project-wide one for read-only endpoints an
# interactive page calls repeatedly (core.security.rate_limit): the Jobs page
# polls its progress and error endpoints every couple of seconds during a run,
# and with the 60/minute default in force the error endpoint answered 429 for
# the whole duration of a production ingest - the panel that explains what
# failed was never available. Defined once, used by every such route.


@operations_bp.route("/api/jobs", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_list_jobs():
    try:
        limit = query_int("limit", 50, minimum=1, maximum=200)
        offset = query_int("offset", 0, minimum=0)
    except (TypeError, ValueError):
        return _error("VALIDATION_FAILED", "limit/offset must be integers", 400)
    jobs = _manager().list(
        job_type=request.args.get("type") or None,
        status=request.args.get("status") or None,
        created_by=request.args.get("user") or None,
        limit=limit, offset=offset,
    )
    return jsonify({"success": True, "jobs": [job_to_api(j) for j in jobs]})


@operations_bp.route("/api/jobs/<job_id>", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_get_job(job_id):
    job, err = _job_or_404(job_id)
    if err:
        return err
    return jsonify({"success": True, "job": job_to_api(job, include_issues=True),
                    "stats": job.get("stats")})


@operations_bp.route("/api/jobs/<job_id>/events", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_job_events(job_id):
    job, err = _job_or_404(job_id)
    if err:
        return err
    try:
        after = query_int("after_id", 0, minimum=0)
        limit = query_int("limit", 200, minimum=1, maximum=1000)
    except (TypeError, ValueError):
        return _error("VALIDATION_FAILED", "after_id/limit must be integers", 400)
    events = _manager().events(job_id, limit=limit, after_id=after)
    return jsonify({"success": True, "events": events})


@operations_bp.route("/api/jobs/<job_id>/errors", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_job_errors(job_id):
    job, err = _job_or_404(job_id)
    if err:
        return err
    return jsonify({"success": True,
                    "errors": (job.get("errors") or [])[:500],
                    "warnings": (job.get("warnings") or [])[:500]})


@operations_bp.route("/api/jobs/<job_id>/results", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_job_results(job_id):
    job, err = _job_or_404(job_id)
    if err:
        return err
    return jsonify({
        "success": True,
        "status": job.get("status"),
        "result_summary": job.get("result_summary"),
        "stats": job.get("stats"),
    })


def _control(job_id, action):
    try:
        result = getattr(_manager(), action)(job_id)
    except KeyError:
        return _error("JOB_NOT_FOUND", "Job not found", 404)
    except ValueError as exc:
        return _error("INVALID_STATE", str(exc), 409)
    except Exception:
        logger.exception("job %s failed", action)
        return _error("CONTROL_FAILED", f"Job {action} failed", 500)
    return jsonify({"success": True, "job": job_to_api(result)})


@operations_bp.route("/api/jobs/<job_id>/cancel", methods=["POST"])
@limiter.limit("30 per minute")
def api_cancel_job(job_id):
    return _control(job_id, "cancel")


@operations_bp.route("/api/jobs/<job_id>/pause", methods=["POST"])
@limiter.limit("30 per minute")
def api_pause_job(job_id):
    return _control(job_id, "pause")


@operations_bp.route("/api/jobs/<job_id>/resume", methods=["POST"])
@limiter.limit("30 per minute")
def api_resume_job(job_id):
    try:
        job = _manager().resume(job_id, created_by=_username())
    except KeyError:
        return _error("JOB_NOT_FOUND", "Job not found", 404)
    except ValueError as exc:
        return _error("INVALID_STATE", str(exc), 409)
    return jsonify({"success": True, "job": job_to_api(job)}), 202


@operations_bp.route("/api/jobs/<job_id>/retry", methods=["POST"])
@limiter.limit("10 per minute")
def api_retry_job(job_id):
    try:
        job = _manager().retry(job_id, created_by=_username())
    except KeyError:
        return _error("JOB_NOT_FOUND", "Job not found", 404)
    except ValueError as exc:
        return _error("INVALID_STATE", str(exc), 409)
    return jsonify({"success": True, "job": job_to_api(job)}), 202


@operations_bp.route("/api/jobs/<job_id>", methods=["DELETE"])
@admin_required
def api_delete_job(job_id):
    try:
        deleted = _manager().delete(job_id)
    except KeyError:
        return _error("JOB_NOT_FOUND", "Job not found", 404)
    except ValueError as exc:
        return _error("INVALID_STATE", str(exc), 409)
    return jsonify({"success": deleted})


@operations_bp.route("/api/jobs/stream", methods=["GET"])
def api_job_stream():
    """SSE stream of job events (single-process deployments).

    Multi-process deployments should use the polling fallback built into
    the Operations UI (events endpoint, ~2s). This endpoint degrades to a
    heartbeat-only stream when no events arrive.
    """
    from queue import Queue, Empty

    q = Queue(maxsize=200)
    mgr = _manager()
    mgr.subscribe(q)

    def generate():
        try:
            yield ": connected\n\n"
            import time as _time

            last = _time.time()
            while True:
                try:
                    evt = q.get(timeout=5)
                    yield (f"event: {evt['event_type']}\n"
                           f"data: {json.dumps(evt)}\n\n")
                    last = _time.time()
                except Empty:
                    yield ": keep-alive\n\n"
                    if _time.time() - last > 300:
                        return
        finally:
            mgr.unsubscribe(q)

    resp = current_app.response_class(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@operations_bp.route("/api/jobs/summary", methods=["GET"])
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_jobs_summary():
    counts = _manager().repo.count_by_status()
    active = counts.get(job_state.RUNNING, 0) + counts.get(job_state.QUEUED, 0)
    return jsonify({"success": True,
                    "counts": counts,
                    "active": active})
