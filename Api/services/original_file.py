"""Original (source) file access for the comparison viewer.

The pipeline stores extracted text and keeps a reference to the file it came
from (``paths.file_path``). An examiner has to be able to look at THAT file
next to the extracted text - to see the layout, the images, the pages, and to
confirm nothing was lost or invented during extraction.

This service answers two questions for one stored object:

* what is there, and how should it be shown (``describe``);
* how to serve the bytes so the browser can display them safely
  (``content_response``).

Security rules, all deliberate:

* the path is read from the database row, never from the request, so no
  user-supplied path can reach the filesystem;
* "inline" is granted only to formats the browser renders without script -
  raster images, PDF, audio, video, and text files;
* text is served as ``text/plain`` even when the file is HTML, SVG, JS or
  XML: the app must never execute an uploaded document in its own origin;
* SVG is served as an image so ``<img>`` can show it, but as an attachment so
  opening the URL directly cannot run its scripts;
* every other format is an attachment (the browser cannot render a .docx
  anyway, and the download is what the operator needs);
* a response that is meant to be framed (the PDF viewer) relaxes
  ``frame-ancestors``/``X-Frame-Options`` to same-origin only.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Raster images the browser can paint. ``svg`` is handled separately.
IMAGE_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.avif', '.ico', '.tif',
    '.tiff',
}

#: Text-like files shown as source text (always served as text/plain).
TEXT_EXTENSIONS = {
    '.txt', '.log', '.csv', '.tsv', '.md', '.markdown', '.rst', '.json',
    '.jsonl', '.xml', '.yml', '.yaml', '.ini', '.cfg', '.conf', '.toml',
    '.html', '.htm', '.xhtml', '.css', '.js', '.mjs', '.sql', '.sh', '.ps1',
    '.bat', '.py', '.java', '.c', '.h', '.cpp', '.cs', '.rb', '.go', '.rs',
    '.php', '.eml', '.vcf', '.srt', '.tex',
}

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.oga', '.m4a', '.aac', '.flac', '.opus'}
VIDEO_EXTENSIONS = {'.mp4', '.m4v', '.webm', '.ogv', '.mov'}

#: How each kind is displayed. 'iframe' uses the browser's own viewer (PDF).
VIEWERS = {
    'image': 'img',
    'pdf': 'iframe',
    'text': 'text',
    'audio': 'audio',
    'video': 'video',
    'download': 'none',
}

_MIME_OVERRIDES = {
    '.md': 'text/plain',
    '.markdown': 'text/plain',
    '.log': 'text/plain',
    '.jsonl': 'text/plain',
    '.csv': 'text/plain',
}


class OriginalFileService:
    """Resolve and serve the file a stored object was extracted from."""

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    @staticmethod
    def _row(file_id: int) -> Optional[Dict[str, Any]]:
        from Api.utils import execute_query

        row = execute_query(
            """
            SELECT p.id, p.file_name, p.file_path, p.file_size, p.file_type,
                   p.file_status
            FROM paths p
            WHERE p.id = %s
            """,
            (file_id,),
            fetch="one",
        )
        if not row:
            return None
        keys = ("id", "file_name", "file_path", "file_size", "file_type",
                "file_status")
        return dict(zip(keys, row, strict=True))

    @classmethod
    def _disk_state(cls, path_value: str) -> Tuple[Optional[Path], Optional[str]]:
        """(path, reason) - reason is None when the file is readable."""
        if not path_value:
            return None, "no-path"
        path = Path(path_value)
        if not path.is_absolute():
            return None, "relative-path"
        if not path.exists():
            return None, "missing-on-disk"
        if not path.is_file():
            return None, "not-a-file"
        try:
            if not os.access(path, os.R_OK):
                return None, "unreadable"
        except OSError:
            return None, "unreadable"
        return path, None

    @staticmethod
    def _mime_for(extension: str) -> str:
        guessed, _ = mimetypes.guess_type(f"file{extension}")
        return guessed or 'application/octet-stream'

    @classmethod
    def describe(cls, file_id: int, *, include_path: bool = False) -> Dict[str, Any]:
        """Everything the viewer needs to show (or explain) the original file.

        Always returns a payload - an object whose source file has disappeared
        from disk is a normal, reportable state (the extracted text survives
        it), not an error. The server-side path is omitted from API responses;
        only trusted server workflows such as ZIP construction may request it.
        """
        row = cls._row(file_id)
        if row is None:
            return {
                'available': False,
                'file_id': file_id,
                'reason': 'not-found',
                'message': 'No stored object with that id.',
            }

        stored_path = row['file_path'] or ''
        name = row['file_name'] or Path(stored_path).name or f'file-{file_id}'
        extension = Path(name).suffix.lower() or Path(stored_path).suffix.lower()
        path_obj, reason = cls._disk_state(stored_path)

        kind = cls.viewer_kind(extension)
        info: Dict[str, Any] = {
            'file_id': row['id'],
            'name': name,
            'extension': extension,
            'stored_size': row['file_size'] or 0,
            'file_type': row['file_type'] or '',
            'file_status': row['file_status'] or '',
            'kind': kind,
            'viewer': VIEWERS.get(kind, 'none'),
            'available': path_obj is not None,
            'reason': reason,
            'size': path_obj.stat().st_size if path_obj else None,
            'serve_url': f'/api/file/{row["id"]}/original/content',
            'download_url': f'/api/file/{row["id"]}/original/content?download=1',
        }
        if include_path:
            info['path'] = stored_path

        if path_obj is None:
            info['message'] = {
                'no-path': 'This object has no source path recorded.',
                'relative-path': 'The recorded source path is not absolute; it cannot be opened.',
                'missing-on-disk': (
                    'The source file is no longer at the recorded location. '
                    'The extracted text is still available.'
                ),
                'not-a-file': 'The recorded path is not a file.',
                'unreadable': 'The source file exists but cannot be read by the application.',
            }.get(reason, 'The source file is not available.')
            return info

        info['mime_type'] = cls._mime_for(extension)
        if kind == 'image' and extension == '.svg':
            info['mime_type'] = 'image/svg+xml'
        # Same-origin checks are the browser's; the operator just needs to know
        # whether the bytes shown are the bytes stored.
        return info

    @staticmethod
    def viewer_kind(extension: str) -> str:
        """Which viewer shows this extension: image/pdf/text/audio/video/download."""
        if extension in IMAGE_EXTENSIONS:
            return 'image'
        if extension == '.svg':
            return 'image'
        if extension == '.pdf':
            return 'pdf'
        if extension in TEXT_EXTENSIONS:
            return 'text'
        if extension in AUDIO_EXTENSIONS:
            return 'audio'
        if extension in VIDEO_EXTENSIONS:
            return 'video'
        return 'download'

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------
    @classmethod
    def content_response(cls, file_id: int, download: bool = False,
                         filename: str = None):
        """Serve the original bytes with a deliberate disposition and type.

        ``filename`` is the reader's own name for a download (Save As); it is
        reduced to a bare file name and never changes what is served - only
        what the browser calls it. Returns a Flask response, or raises
        ``FileNotFoundError`` (missing row or missing file on disk) so the
        route can answer 404 with a reason.
        """
        from flask import send_file

        row = cls._row(file_id)
        if row is None:
            raise FileNotFoundError('not-found')

        path_obj, reason = cls._disk_state(row['file_path'] or '')
        if path_obj is None:
            raise FileNotFoundError(reason or 'unavailable')

        name = row['file_name'] or path_obj.name
        if filename:
            # A name the reader typed: one line, no directory parts. An
            # extension is kept from the file itself when the name lacks one.
            cleaned = ' '.join(str(filename).replace('\\', '/').split('/')[-1].split()) or name
            suffix = path_obj.suffix.lower()
            if suffix and not cleaned.lower().endswith(suffix):
                cleaned = cleaned + suffix
            name = cleaned[:255]
        extension = Path(name).suffix.lower() or path_obj.suffix.lower()
        kind = cls.viewer_kind(extension)

        if kind == 'text':
            mime_type = 'text/plain'
            as_attachment = bool(download)
        elif kind in ('image', 'audio', 'video'):
            mime_type = cls._mime_for(extension)
            # SVG renders in <img> but must not run when opened directly.
            as_attachment = bool(download) or extension == '.svg'
        elif kind == 'pdf':
            mime_type = 'application/pdf'
            as_attachment = bool(download)
        else:
            mime_type = 'application/octet-stream'
            as_attachment = True  # nothing else can be shown inline safely

        if extension in _MIME_OVERRIDES and kind == 'text':
            mime_type = 'text/plain'

        response = send_file(
            str(path_obj),
            mimetype=mime_type,
            as_attachment=as_attachment,
            download_name=name,
            conditional=True,  # range requests: PDF and video seek
        )
        return cls._frameable(response)

    @staticmethod
    def _frameable(response):
        """Allow this response to be framed by this app only.

        Both the CSP and X-Frame-Options defaults deny framing, which would
        stop the PDF viewer (an iframe pointed at this same app) from loading.
        """
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self'"
        return response
