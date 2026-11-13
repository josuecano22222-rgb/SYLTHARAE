"""Map viewer API: offline package catalog, downloads, and tile serving."""

from __future__ import annotations

import logging

from flask import Blueprint, Response, jsonify, make_response, render_template, request, send_file

from core.security.flask_ext import login_required
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from flask_babel import gettext as _

from services import map_bundle, map_layers, map_packages, map_registry, map_tile_cache
from services.map_bundle import BUNDLE_SUFFIX


def _translate_layer_name(_key: str, fallback: str) -> str:
    return _(fallback)

logger = logging.getLogger(__name__)

map_api_bp = Blueprint("map_api", __name__)

MAP_VIEWER_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "worker-src 'self' blob:; "
    "child-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: "
    "https://tile.openstreetmap.org https://tile.opentopomap.org "
    "https://basemaps.cartocdn.com https://server.arcgisonline.com "
    "https://*.arcgisonline.com; "
    "font-src 'self' https://demotiles.maplibre.org; "
    "connect-src 'self' "
    "https://tile.openstreetmap.org https://tile.opentopomap.org "
    "https://basemaps.cartocdn.com https://server.arcgisonline.com "
    "https://*.arcgisonline.com https://demotiles.maplibre.org; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


def register_map_routes(app):
    """Register map viewer page and API blueprint."""

    @app.route("/analytics/map-viewer")
    @login_required
    def map_viewer_page():
        response = make_response(render_template("map/map_viewer.html"))
        response.headers["Content-Security-Policy"] = MAP_VIEWER_CSP
        return response

    app.register_blueprint(map_api_bp)
    logger.info("Map viewer routes registered")


@map_api_bp.route("/api/map/layers", methods=["GET"])
@login_required
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_map_layers():
    try:
        map_registry.sync_from_catalog()
        layers = map_layers.list_map_layers(translate_name=_translate_layer_name)
        return jsonify({
            "success": True,
            "layers": layers,
            "registry": map_registry.registry_summary(),
        })
    except Exception as exc:
        logger.error("Map layer list failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Could not load map layers"}), 500


@map_api_bp.route("/api/map/storage", methods=["GET"])
@login_required
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_map_storage():
    try:
        summary = map_layers.storage_summary()
        summary["registry"] = map_registry.registry_summary()
        summary["bundle_files"] = map_bundle.list_bundle_contents()
        return jsonify({"success": True, **summary})
    except Exception as exc:
        logger.error("Map storage summary failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Could not read map storage"}), 500


@map_api_bp.route("/api/map/packages", methods=["GET"])
@login_required
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_map_packages():
    try:
        return jsonify({"success": True, "packages": map_packages.list_packages()})
    except Exception as exc:
        logger.error("Map package list failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Could not load map packages"}), 500


@map_api_bp.route("/api/map/packages/<package_id>/download", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def api_map_package_download(package_id: str):
    try:
        status = map_packages.start_package_download(package_id)
        return jsonify({"success": True, **status})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("Map download start failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Download could not start"}), 500


@map_api_bp.route("/api/map/packages/<package_id>/download/status", methods=["GET"])
@login_required
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_map_package_download_status(package_id: str):
    return jsonify({"success": True, **map_packages.read_download_status(package_id)})


@map_api_bp.route("/api/map/packages/<package_id>", methods=["DELETE"])
@login_required
@limiter.limit("30 per minute")
def api_map_package_remove(package_id: str):
    try:
        map_packages.remove_installed_package(package_id)
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("Map package remove failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Could not remove package"}), 500


@map_api_bp.route("/api/map/pmtiles/<package_id>", methods=["GET"])
@login_required
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_map_pmtiles(package_id: str):
    path = map_packages.installed_pmtiles_path(package_id)
    if path is None:
        return jsonify({"success": False, "error": "PMTiles not installed for this package"}), 404
    return send_file(path, mimetype="application/octet-stream", conditional=True)


@map_api_bp.route("/api/map/cache/<layer_id>/<source_id>/<int:z>/<int:x>/<int:y>.png", methods=["GET"])
@login_required
@limiter.limit("600 per minute")
def api_map_cache_tile(layer_id: str, source_id: str, z: int, x: int, y: int):
    """Serve a cached tile; fetch and store from upstream when online and missing."""
    allow_fetch = request.args.get("offline_only") != "1"
    try:
        data = map_tile_cache.get_or_fetch_tile(
            layer_id, source_id, z, x, y, allow_fetch=allow_fetch
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if data is None:
        return Response(status=404)
    return Response(data, mimetype="image/png", headers={"Cache-Control": "private, max-age=86400"})


@map_api_bp.route("/api/map/raster/<package_id>/<int:z>/<int:x>/<int:y>.png", methods=["GET"])
@login_required
@limiter.limit(INTERACTIVE_READ_LIMIT)
def api_map_raster_tile(package_id: str, z: int, x: int, y: int):
    path = map_packages.raster_tile_path(package_id, z, x, y)
    if path is None:
        return Response(status=404)
    return send_file(path, mimetype="image/png", conditional=True)


@map_api_bp.route("/api/map/bundle/export", methods=["GET"])
@login_required
@limiter.limit("10 per minute")
def api_map_bundle_export():
    """Download compressed portable map_data bundle (.sylmap.tar.gz)."""
    try:
        from datetime import datetime

        buffer = map_bundle.create_portable_bundle()
        stamp = datetime.now().strftime("%Y%m%d")
        filename = f"syltharae_map_{stamp}{BUNDLE_SUFFIX}"
        return Response(
            buffer.getvalue(),
            mimetype="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Map bundle export failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Could not export map bundle"}), 500


@map_api_bp.route("/api/map/bundle/import", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def api_map_bundle_import():
    """Restore map_data from a portable .sylmap.tar.gz bundle."""
    upload = request.files.get("bundle")
    if upload is None or not upload.filename:
        return jsonify({"success": False, "error": "No bundle file uploaded"}), 400
    name = (upload.filename or "").lower()
    if not (name.endswith(".tar.gz") or name.endswith(BUNDLE_SUFFIX) or name.endswith(".gz")):
        return jsonify({"success": False, "error": "Expected a .tar.gz map bundle"}), 400
    try:
        result = map_bundle.import_portable_bundle(upload.stream)
        return jsonify({"success": True, **result})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("Map bundle import failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": "Could not import map bundle"}), 500
