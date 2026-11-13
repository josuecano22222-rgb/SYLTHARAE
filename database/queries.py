"""
Helper functions for database queries - provides convenient access to repository methods
"""
from database.services.contents_db_service import ContentDBService

# Global service instance (lazy initialization)
_db_service = None


def _get_db_service():
    """Get or create database service instance"""
    global _db_service
    if _db_service is None:
        _db_service = ContentDBService()
    return _db_service


# Source functions
def list_sources(search=None, limit=50):
    """List sources with optional search"""
    service = _get_db_service()
    results = service.sources_repo.list_sources(search=search, limit=limit)
    # Results are tuples from database, convert to dicts for easier use
    if results and len(results) > 0:
        # Query returns: id, name, job, importance, country, city, description, 
        # accounts, note, attachments, date_creation, ownership, access_status, 
        # category_id
        return [
            {
                'id': r[0],
                'name': r[1],
                'job': r[2] if len(r) > 2 else None,
                'importance': r[3] if len(r) > 3 else None,
                'country': r[4] if len(r) > 4 else None,
                'city': r[5] if len(r) > 5 else None,
                'description': r[6] if len(r) > 6 else None,
            }
            for r in results
        ]
    return []


def search_sources(search_term, limit=50):
    """Search sources by name, country, or job"""
    return list_sources(search=search_term, limit=limit)


def get_source_by_name(name):
    """Get source by name"""
    service = _get_db_service()
    result = service.sources_repo.get_by_name(name)
    if result and isinstance(result, tuple):
        return {
            'id': result[0],
            'name': result[1],
            'job': result[2] if len(result) > 2 else None,
            'importance': result[3] if len(result) > 3 else None,
            'country': result[4] if len(result) > 4 else None,
            'city': result[5] if len(result) > 5 else None,
            'description': result[6] if len(result) > 6 else None,
            'accounts': result[7] if len(result) > 7 else None,
            'note': result[8] if len(result) > 8 else None,
            'attachments': result[9] if len(result) > 9 else None,
            'date_creation': result[10] if len(result) > 10 else None,
            'ownership': result[11] if len(result) > 11 else None,
            'access_status': result[12] if len(result) > 12 else None,
            'entry_date': result[13] if len(result) > 13 else None,
            'category_id': result[14] if len(result) > 14 else None,
        }
    return result

def get_source_by_id(source_id):
    """Get source by ID"""
    service = _get_db_service()
    result = service.sources_repo.get_by_id(source_id)
    if result and isinstance(result, tuple):
        return {
            'id': result[0],
            'name': result[1],
            'job': result[2] if len(result) > 2 else None,
            'importance': result[3] if len(result) > 3 else None,
            'country': result[4] if len(result) > 4 else None,
            'city': result[5] if len(result) > 5 else None,
            'description': result[6] if len(result) > 6 else None,
            'accounts': result[7] if len(result) > 7 else None,
            'note': result[8] if len(result) > 8 else None,
            'attachments': result[9] if len(result) > 9 else None,
            'date_creation': result[10] if len(result) > 10 else None,
            'ownership': result[11] if len(result) > 11 else None,
            'access_status': result[12] if len(result) > 12 else None,
            'entry_date': result[13] if len(result) > 13 else None,
            'category_id': result[14] if len(result) > 14 else None,
        }
    return result


def create_source(name, job, country="", city="", description="", importance=0.5, **kwargs):
    """Create a new source"""
    service = _get_db_service()
    # Map category_id to id_categorys for compatibility with ContentDBService
    if 'category_id' in kwargs:
        kwargs['id_categorys'] = kwargs.pop('category_id')
    return service.create_source(
        name=name,
        country=country,
        city=city,
        job=job,
        description=description,
        importance=importance,
        **kwargs
    )


def insert_source(name, job, country="", city="", description="", importance=0.5, **kwargs):
    """Insert a new source (alias for create_source)"""
    result = create_source(name, job, country, city, description, importance, **kwargs)
    if result and isinstance(result, tuple):
        return result[0] if len(result) > 0 else None
    return result


# Side functions
def list_sides(search=None, limit=50):
    """List sides with optional search"""
    service = _get_db_service()
    results = service.sides_repo.list_sides(search=search, limit=limit)
    # Results are tuples from database, convert to dicts for easier use
    if results and len(results) > 0:
        # Query returns: id, name, importance, date_creation
        return [
            {
                'id': r[0],
                'name': r[1],
                'importance': r[2] if len(r) > 2 else None,
                'date_creation': r[3] if len(r) > 3 else None,
            }
            for r in results
        ]
    return []


def search_sides(search_term, limit=50):
    """Search sides by name"""
    return list_sides(search=search_term, limit=limit)


def get_side_by_id(side_id):
    """Get side by ID"""
    service = _get_db_service()
    result = service.sides_repo.get_by_id(side_id)
    if result and isinstance(result, tuple):
        return {
            'id': result[0],
            'name': result[1],
            'importance': result[2] if len(result) > 2 else None,
            'date_creation': result[3] if len(result) > 3 else None,
        }
    return result


def get_side_by_name(name):
    """Get side by name"""
    service = _get_db_service()
    result = service.sides_repo.get_by_name(name)
    if result and isinstance(result, tuple):
        return {
            'id': result[0],
            'name': result[1],
            'importance': result[2] if len(result) > 2 else None,
            'date_creation': result[3] if len(result) > 3 else None,
        }
    return result


def create_side(name, importance=0.5, date_creation=None):
    """Create a new side"""
    service = _get_db_service()
    return service.create_side(name=name, importance=importance, date_creation=date_creation)


def insert_side(name, importance=0.5, date_creation=None):
    """Insert a new side (alias for create_side)"""
    return create_side(name, importance, date_creation)
