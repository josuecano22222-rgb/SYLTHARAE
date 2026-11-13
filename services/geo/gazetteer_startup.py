"""Startup reconciliation: the committed seed is authoritative, so load it on boot.

The place detector matches text against the database gazetteer (``geo_places``,
``geo_place_names``). A fresh install applies the baseline migration - which
creates those tables empty - but nothing in the serving path ever loaded the
reviewed seed ``data/gazetteer/places_seed.json`` into them. The result was a
schema-complete database whose place detector could never run: every ingested
document was stored with ``places detector unavailable: no gazetteer is loaded``
and zero place signals.

This mirrors :mod:`services.importing.taxonomy_startup`: the web app calls
:func:`reconcile` once at startup, next to the taxonomy reconcile. An operator
who rebuilds the seed and restarts does not also have to remember a command.

Modes come from ``SYLTHARAE_GAZETTEER_SYNC``:

===========  ==================================================================
``0``/``off``  do nothing at all
``check``      report whether a gazetteer is loaded (the default)
``apply``      synchronise the seed into the database (idempotent)
===========  ==================================================================

:func:`services.geo.gazetteer.sync_seed` is idempotent - it only writes rows
that differ and never retires by absence - so an already-seeded database costs
one upsert pass and reports ``already_in_sync``. ``sync_seed`` takes a
transaction-scoped advisory lock (``lock_curation``), so two instances booting
together serialise rather than corrupt each other's load.

Returns plain data and never raises: a startup step that threw would stop the
web app from serving, a worse outcome than a degraded gazetteer with a warning.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

APPLY_MODES = ("apply", "1", "yes", "true")
OFF_MODES = ("0", "off", "no", "false")


def mode_from_env(default: str = "check") -> str:
    import os

    return (os.environ.get("SYLTHARAE_GAZETTEER_SYNC") or default).strip().lower()


def reconcile(mode: Optional[str] = None) -> Dict[str, Any]:
    """Load the committed gazetteer seed into the database in ``apply`` mode.

    Returns ``{"action": ..., ...}`` where ``action`` is one of ``disabled``,
    ``not_seeded``, ``loaded``, ``applied``, ``already_in_sync`` or ``error``.
    """
    mode = (mode or mode_from_env()).strip().lower()
    if mode in OFF_MODES:
        return {"action": "disabled", "mode": mode}
    try:
        from services.geo import gazetteer as gz
        from database import get_db_config
        from database.bootstrap import connect_application_db

        seed = gz.load_seed_file()
        conn = connect_application_db(get_db_config())
        try:
            with conn.cursor() as cur:
                fingerprint = gz.current_fingerprint(cur)
            conn.rollback()

            if mode not in APPLY_MODES:
                if fingerprint is None:
                    return {"action": "not_seeded",
                            "seed_place_count": seed.get("place_count"),
                            "seed_name_count": seed.get("name_count")}
                return {"action": "loaded", "fingerprint": fingerprint}

            with conn.cursor() as cur:
                result = gz.sync_seed(cur, seed, loaded_by="startup")
            conn.commit()
            return {"action": "applied" if result["changed"] else "already_in_sync",
                    "fingerprint": result["fingerprint"],
                    "place_count": result["place_count"],
                    "name_count": result["name_count"],
                    "changed": result["changed"],
                    "places_added": result.get("places_added"),
                    "names_added": result.get("names_added")}
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - this may never stop the app
        return {"action": "error", "error": f"{exc.__class__.__name__}: {exc}"}
