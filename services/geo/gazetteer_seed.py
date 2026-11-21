"""Load the reviewed gazetteer seed during installation.

This lives in ``services`` because the seed is geo domain logic and because
``core`` must not import ``services`` - the layering rule pinned in
``tests/unit/test_architecture_claims.py``. ``core.installer`` calls it through
an injected seeder; see :func:`core.installer.set_gazetteer_seeder`.

The baseline migration creates ``geo_places`` / ``geo_place_names`` empty, and
the place detector matches against their rows, so a fresh install stored every
document with "no gazetteer is loaded" and zero place signals until this ran.
"""

from __future__ import annotations

from typing import Any, Dict


def ensure_seed(
    host: str,
    port: Any,
    user: str,
    password: str,
    database: str,
) -> Dict[str, Any]:
    """Load the reviewed gazetteer seed into the database (idempotent).

    :func:`services.geo.gazetteer.sync_seed` only writes rows that differ and
    never retires by absence, so this is safe to call repeatedly.
    """
    from database.bootstrap import connect_application_db

    from services.geo import gazetteer as gz

    conn = connect_application_db({
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    })
    try:
        seed = gz.load_seed_file()
        with conn.cursor() as cur:
            result = gz.sync_seed(cur, seed, loaded_by="installer")
        conn.commit()
        return result
    finally:
        conn.close()
