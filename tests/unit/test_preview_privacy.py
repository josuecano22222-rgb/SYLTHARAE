from __future__ import annotations

import base64

from flask import Flask

from Api.routes.preview import register_preview_routes
from Api.services.file_preview import FilePreviewService


def _preview_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_preview_routes(app)
    return app.test_client()


def test_preview_json_never_returns_a_server_path_or_inline_image(monkeypatch):
    image = base64.b64encode(b"safe-preview-image").decode("ascii")
    monkeypatch.setattr(
        FilePreviewService,
        "get_preview",
        lambda **_kwargs: {
            "preview_type": "image",
            "mime_type": "image/png",
            "data": f"data:image/png;base64,{image}",
            "file_name": "evidence.png",
            "file_type": "png",
            "file_path": "/srv/private/evidence.png",
        },
    )

    response = _preview_client().get("/api/preview/17")
    assert response.status_code == 200
    payload = response.get_json()
    assert "file_path" not in payload
    assert "data" not in payload
    assert payload["image_url"] == "/api/preview/17/image?max_width=1200&max_height=800"


def test_preview_image_is_served_by_file_id_with_safe_headers(monkeypatch):
    image_bytes = b"opaque-image-preview"
    image = base64.b64encode(image_bytes).decode("ascii")
    monkeypatch.setattr(
        FilePreviewService,
        "get_preview",
        lambda **_kwargs: {
            "preview_type": "image",
            "data": f"data:image/png;base64,{image}",
        },
    )

    response = _preview_client().get("/api/preview/17/image")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == image_bytes
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_preview_image_rejects_non_raster_data_urls(monkeypatch):
    monkeypatch.setattr(
        FilePreviewService,
        "get_preview",
        lambda **_kwargs: {
            "preview_type": "image",
            "data": "data:image/svg+xml;base64,PHN2Zz4=",
        },
    )
    response = _preview_client().get("/api/preview/17/image")
    assert response.status_code == 404
    assert "unavailable" in response.get_json()["error"].lower()
