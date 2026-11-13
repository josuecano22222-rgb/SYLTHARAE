"""File-preview endpoints backed by opaque file IDs, never server paths."""

import base64
import binascii
import re
from io import BytesIO

from flask import Blueprint, Response, jsonify, request, send_file

from Api.services.file_preview import FilePreviewService
import logging

logger = logging.getLogger(__name__)

preview_bp = Blueprint('preview', __name__, url_prefix='/api/preview')

_DEFAULT_MAX_WIDTH = 1200
_DEFAULT_MAX_HEIGHT = 800
_MAX_PREVIEW_DIMENSION = 4096
_SAFE_PREVIEW_IMAGE = re.compile(
    r"^data:(image/(?:jpeg|png));base64,([A-Za-z0-9+/]*={0,2})$",
    re.IGNORECASE,
)


def _preview_dimensions():
    """Parse bounded raster dimensions; reject malformed or abusive values."""
    dimensions = []
    for key, default in (("max_width", _DEFAULT_MAX_WIDTH),
                         ("max_height", _DEFAULT_MAX_HEIGHT)):
        raw = request.args.get(key, str(default))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number") from None
        if value < 1 or value > _MAX_PREVIEW_DIMENSION:
            raise ValueError(f"{key} must be between 1 and {_MAX_PREVIEW_DIMENSION}")
        dimensions.append(value)
    return dimensions


@preview_bp.route('/<int:file_id>', methods=['GET'])
def get_file_preview(file_id):
    """Return preview text/metadata and an opaque URL for preview images."""
    try:
        max_width, max_height = _preview_dimensions()
        preview_data = FilePreviewService.get_preview(
            file_id=file_id,
            max_width=max_width,
            max_height=max_height,
        )
        preview_data.pop('file_path', None)
        # Image bytes are served by the ID-only endpoint below. Do not return
        # either base64 image content or a database path in the JSON payload.
        if preview_data.get('preview_type') == 'image':
            preview_data.pop('data', None)
            preview_data['image_url'] = (
                f"/api/preview/{file_id}/image"
                f"?max_width={max_width}&max_height={max_height}")
        response = jsonify(preview_data)
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ValueError as invalid:
        return jsonify({'preview_type': 'error', 'error': str(invalid)}), 400
    except Exception as e:
        # SEC-08: client-safe error, details logged server-side only.
        from core.errors import client_error
        return client_error(e, subsystem="preview",
                            public_message="Preview generation failed")


@preview_bp.route('/<int:file_id>/image', methods=['GET'])
def get_file_preview_image(file_id):
    """Serve a bounded preview raster through an authenticated opaque ID."""
    try:
        max_width, max_height = _preview_dimensions()
        preview_data = FilePreviewService.get_preview(
            file_id=file_id,
            max_width=max_width,
            max_height=max_height,
        )
        data_url = preview_data.get('data') if preview_data.get('preview_type') == 'image' else None
        match = _SAFE_PREVIEW_IMAGE.fullmatch(str(data_url or ''))
        if not match:
            return jsonify({'error': 'Image preview is unavailable'}), 404
        try:
            image_bytes = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError):
            logger.warning('Invalid encoded preview image for file id %s', file_id)
            return jsonify({'error': 'Image preview is unavailable'}), 404
        if not image_bytes:
            return jsonify({'error': 'Image preview is unavailable'}), 404

        response = send_file(
            BytesIO(image_bytes),
            mimetype=match.group(1).lower(),
            as_attachment=False,
            max_age=0,
        )
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response
    except ValueError as invalid:
        return jsonify({'error': str(invalid)}), 400
    except Exception as e:
        from core.errors import client_error
        return client_error(e, subsystem="preview-image",
                            public_message="Preview image generation failed")


def register_preview_routes(app):
    """Register preview routes with the Flask app."""
    app.register_blueprint(preview_bp)
