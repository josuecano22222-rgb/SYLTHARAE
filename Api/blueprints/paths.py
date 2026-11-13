import logging

from core.errors import client_error
from core.validation import query_int
from flask import Blueprint, request, jsonify

from Api.models.paths import PathCreate
from Api.services.paths import PathsService
from Api.repositories.paths import PathsRepository

logger = logging.getLogger(__name__)


paths_bp = Blueprint("paths", __name__, url_prefix="/api/paths")


@paths_bp.post("")
def api_create_path():
    """
    API endpoint to create a new path.
    
    Creates a new path record from the provided JSON payload.
    
    Request Body:
        JSON object matching PathCreate model
        
    Returns:
        JSON response with created path ID (201) or error (400)
    """
    try:
        payload = request.get_json(force=True) or {}
        dto = PathCreate.from_dict(payload)
        new_id = PathsService.create_path(dto)
        return jsonify({"id": new_id}), 201
    except Exception as e:
        # A path that could not be created: the reader is told what failed, not
        # what the database said (spec §38, §76).
        logger.error(f"Could not create path: {e}", exc_info=True)
        return client_error(e, subsystem="paths",
                            public_message="The path could not be created",
                            status=400)


@paths_bp.get("/<int:path_id>")
def api_get_path(path_id: int):
    """
    API endpoint to get a path by ID.
    
    Retrieves path information for the specified path ID.
    
    Args:
        path_id: ID of the path to retrieve
        
    Returns:
        JSON response with path data (200) or error (404)
    """
    dto = PathsService.get_path(path_id)
    if not dto:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": dto.id,
        "file_name": dto.file_name,
        "file_path": dto.file_path,
        "file_size": dto.file_size,
        "file_type": dto.file_type,
        "file_status": dto.file_status,
        "file_date": dto.file_date.isoformat() if dto.file_date else None,
        "date_creation": dto.date_creation.isoformat() if dto.date_creation else None,
        "context_id": dto.context_id,
    })


@paths_bp.get("")
def api_list_paths():
    """
    API endpoint to list paths with pagination and filtering.
    
    Query Parameters:
        offset (int, optional): Number of records to skip (default: 0)
        limit (int, optional): Maximum number of records to return (default: 50, max: 500)
        file_type (str, optional): Filter by file type
        status (str, optional): Filter by file status
        
    Returns:
        JSON response with list of path records
    """
    # A negative offset is a database error, not a page: clamp both.
    offset = query_int("offset", 0, minimum=0, maximum=10_000_000)
    limit = query_int("limit", 50, minimum=1, maximum=500)
    file_type = request.args.get("file_type")
    status = request.args.get("status")
    rows = PathsRepository.list(offset=offset, limit=limit, file_type=file_type, status=status)
    return jsonify([
        {
            "id": r[0],
            "file_name": r[1],
            "file_path": r[2],
            "file_size": r[3],
            "file_type": r[4],
            "file_status": str(r[5]),
            "file_date": r[6].isoformat() if r[6] else None,
            "date_creation": r[7].isoformat() if r[7] else None,
            "context_id": r[8],
        }
        for r in (rows or [])
    ])


