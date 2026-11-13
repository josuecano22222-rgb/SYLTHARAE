"""Direct file operations from the tables: rename, copy, locate.

The list interfaces offer these against a single row through the shared
file-actions control. Every endpoint takes the file id in the URL and reads
the path from the database row - never from the request - the same rule the
original-file service follows.

* **Rename** changes the record's display name and, when the source file is
  on disk and its directory allows it, the file on disk too (path updated in
  the same breath). A file that is not on disk still renames as a record.
* **Copy** duplicates the record and points it at the same bytes on disk:
  One Content, Many Contexts. The original is untouched, and so is the file.
* **Locate** answers where the file lives. When the reader's browser is on
  the same machine as the storage (the usual single-workstation install),
  the folder is also revealed in the operating system's file manager; on a
  remote deployment only the path is returned and the interface offers it
  to copy instead.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from Api.utils import execute_query, get_query_cache
from core.security.disclosure import note_disclosure
from core.security.flask_ext import current_user
from core.security.rate_limit import limiter, INTERACTIVE_READ_LIMIT

logger = logging.getLogger(__name__)

file_ops_bp = Blueprint('file_operations', __name__)

#: The columns a duplicated record shares with its source. The new row keeps
#: the same content occurrence (context_id), so extraction is never rerun and
#: the two records always show the same content - they are the same bytes.
_COPY_COLUMNS = (
    'file_path, file_size, file_type, file_status, file_date, date_creation,'
    ' context_id, coordinates, extraction_provenance, processing_status,'
    ' status_detail, attempts, parent_path_id, hierarchy_path'
)


def _row(file_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query(
        """
        SELECT p.id, p.file_name, p.file_path, p.context_id
        FROM paths p
        WHERE p.id = %s
        """,
        (file_id,),
        fetch="one",
    )
    if not row:
        return None
    return {
        'id': row[0],
        'file_name': row[1],
        'file_path': row[2],
        'context_id': row[3],
    }


def _bad_request(message: str, status: int = 400):
    return jsonify({'success': False, 'error': message}), status


def _clean_name(value: str) -> str:
    """A file name: one line, no separators, a real length."""
    name = (value or '').strip()
    name = ' '.join(name.split())
    return name


def _clear_cache() -> None:
    try:
        cache = get_query_cache()
        cache.clear()
    except Exception:  # pragma: no cover - cache is best-effort
        pass


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_ops_bp.route('/api/file/<int:file_id>/rename', methods=['POST'])
def rename_file(file_id: int):
    """Rename a file: the record always, the file on disk when it is there."""
    body = request.get_json(silent=True) or {}
    new_name = _clean_name(body.get('new_name') or '')
    if not new_name:
        return _bad_request('A new name is required')
    if len(new_name) > 255:
        return _bad_request('The new name is too long (255 characters at most)')
    if '/' in new_name or '\\' in new_name or new_name in ('.', '..'):
        return _bad_request('The new name may not contain path separators')

    row = _row(file_id)
    if row is None:
        return _bad_request('File not found', 404)

    old_name = row['file_name'] or ''
    old_path = row['file_path'] or ''
    new_path = old_path

    # The file on disk follows when it can: same folder, new name, same
    # extension (an operator renaming "report" to "report 2024" keeps
    # ".pdf" from the file itself).
    disk_moved = False
    disk_note = None
    source = Path(old_path) if old_path else None
    if old_path and old_name and source and source.is_file():
        new_disk_name = new_name
        if source.suffix and not new_disk_name.lower().endswith(source.suffix.lower()):
            new_disk_name = new_disk_name + source.suffix
        target = source.with_name(new_disk_name)
        if target.exists():
            disk_note = 'A file with that name already exists in the folder.'
        else:
            try:
                source.rename(target)
                new_path = str(target)
                disk_moved = True
            except OSError as os_error:
                logger.warning(f"Rename on disk failed for file {file_id}: {os_error}")
                disk_note = 'The file on disk could not be renamed; the record was.'

    execute_query(
        "UPDATE paths SET file_name = %s, file_path = %s WHERE id = %s",
        (new_name, new_path, file_id),
        fetch=None,
    )
    # The Change report can only answer "what was it before?" if the
    # previous values are recorded: one revision per rename, with the actor.
    try:
        from services.changes import record_path_revision
        actor = current_user()
        record_path_revision(
            file_id, 'modified',
            old_values={'file_name': old_name, 'file_path': old_path},
            new_values={'file_name': new_name, 'file_path': new_path},
            actor_id=getattr(actor, 'id', None))
    except Exception:
        # The rename itself succeeded; a lost revision must not fail the
        # user's operation - but it is never swallowed: the failure lands
        # in the audit log with the file, so the gap is answerable.
        logger.exception("Revision recording failed for file %s", file_id)
        try:
            from core.security.service import get_auth_service
            user = current_user()
            get_auth_service().audit(
                "PATH_REVISION_FAILED", user_id=getattr(user, "id", None),
                username=getattr(user, "username", None),
                resource=f"file:{file_id}",
                detail={"error": "revision recording failed"},
                ip_address=request.remote_addr)
        except Exception:
            logger.exception("Audit of the failed revision failed for file %s",
                             file_id)
    _clear_cache()
    note_disclosure(kind='file_rename', scope=f'file:{file_id}', unit='file',
                    row_count=1, file_ids=[file_id], detail={'from': old_name, 'to': new_name})
    return jsonify({
        'success': True,
        'id': file_id,
        'name': new_name,
        'renamed_on_disk': disk_moved,
        'note': disk_note,
    })


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_ops_bp.route('/api/file/<int:file_id>/copy', methods=['POST'])
def copy_file(file_id: int):
    """Duplicate the record; the bytes on disk are shared, not copied."""
    body = request.get_json(silent=True) or {}
    row = _row(file_id)
    if row is None:
        return _bad_request('File not found', 404)

    base_name = row['file_name'] or f'file_{file_id}'
    suffix = Path(base_name).suffix
    stem = Path(base_name).stem or base_name
    requested = _clean_name(body.get('new_name') or '')
    new_name = requested if requested else f"{stem} (copy){suffix}"
    if len(new_name) > 255:
        return _bad_request('The new name is too long (255 characters at most)')

    new_id = execute_query(
        f"""
        INSERT INTO paths (
            file_name, {_COPY_COLUMNS}
        )
        SELECT %s, {_COPY_COLUMNS}
        FROM paths
        WHERE id = %s
        RETURNING id
        """,
        (new_name, file_id),
        fetch="one",
    )
    if not new_id:
        return _bad_request('The copy could not be created', 500)
    _clear_cache()
    note_disclosure(kind='file_copy', scope=f'file:{file_id}', unit='file',
                    row_count=1, file_ids=[file_id, new_id[0]])
    return jsonify({'success': True, 'id': new_id[0], 'name': new_name})


@limiter.limit(INTERACTIVE_READ_LIMIT)
@file_ops_bp.route('/api/file/<int:file_id>/locate', methods=['GET'])
def locate_file(file_id: int):
    """Where the file lives - and, on the same machine, its folder revealed."""
    row = _row(file_id)
    if row is None:
        return _bad_request('File not found', 404)

    raw_path = row['file_path'] or ''
    path = Path(raw_path) if raw_path else None
    answer: Dict[str, Any] = {
        'success': True,
        'id': file_id,
        'path': raw_path,
        'exists': bool(path and path.is_file()),
        'revealed': False,
    }
    if not answer['exists']:
        answer['reason'] = 'The file is not at its recorded location.'
        return jsonify(answer)

    # Reveal only for a reader on this machine: a remote deployment has no
    # folder to open on the reader's desktop, and the path alone is honest.
    local_reader = request.remote_addr in ('127.0.0.1', '::1')
    system = __import__('platform').system()
    try:
        if local_reader and system == 'Windows' and shutil.which('explorer'):
            subprocess.Popen(['explorer', '/select,', str(path)])
            answer['revealed'] = True
        elif local_reader and system == 'Darwin' and shutil.which('open'):
            subprocess.Popen(['open', '-R', str(path)])
            answer['revealed'] = True
        elif local_reader and system == 'Linux' and shutil.which('xdg-open'):
            subprocess.Popen(['xdg-open', str(path.parent)])
            answer['revealed'] = True
        else:
            answer['reason'] = ('The folder can be revealed only on the '
                                'machine the application runs on; the path '
                                'is here to copy.')
    except OSError as os_error:  # pragma: no cover - desktop integration
        logger.info(f"Reveal failed for file {file_id}: {os_error}")
        answer['reason'] = 'The file manager could not be opened; the path is here to copy.'

    return jsonify(answer)


def register_file_operations_routes(app):
    """Register the direct file operations with the Flask app"""
    app.register_blueprint(file_ops_bp)
