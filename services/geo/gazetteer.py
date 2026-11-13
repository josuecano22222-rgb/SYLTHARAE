"""The database gazetteer: seed loading, fingerprint and the detector index.

The gazetteer lives in PostgreSQL (``geo_places``, ``geo_place_names``,
created by baseline migration m0001). Its reviewed content is the committed seed
``data/gazetteer/places_seed.json`` (built by ``tools/gazetteer/build_seed.py``
from Wikidata, CC0). :func:`sync_seed` is the seed synchronizer; approved
curation writes use :func:`record_curation_change` so their active detector
fingerprint stays in step:

* idempotent upsert keyed on (label, category_id, country_codes) / (place_id, language, match_key, name_type);
* places that leave the seed are *retired*, never deleted - stored place
  signals keep pointing at them (``content_signal_places`` is ``ON DELETE
  RESTRICT``), so evidence is never orphaned;
* the fingerprint is computed at read time from live rows, cached in process
  and invalidated by the loader and by ``record_curation_change``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from core.geo.names import LANGUAGE_SCRIPT, match_key, script_of

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = ROOT / "data" / "gazetteer" / "places_seed.json"
SEED_FORMAT = 1
SEED_SOURCE = "wikidata"


class SeedIntegrityError(ValueError):
    """The seed file is malformed or does not match its recorded SHA-256."""


class GazetteerUnavailable(RuntimeError):
    """No gazetteer has been loaded into this database."""


# In-process cache for fingerprint and detector index
_FINGERPRINT_CACHE: Optional[str] = None
_INDEX_CACHE: Dict[str, Any] = {}


def _invalidate_cache() -> None:
    """Invalidate the in-process fingerprint and detector cache."""
    global _FINGERPRINT_CACHE
    _FINGERPRINT_CACHE = None
    _INDEX_CACHE.clear()


def load_seed_file(path: Path = DEFAULT_SEED) -> Dict[str, Any]:
    """Read and verify a seed file (format, SHA-256 of the places payload)."""
    try:
        seed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SeedIntegrityError(f"cannot read gazetteer seed {path}: {exc}") from exc
    if seed.get("format") != SEED_FORMAT:
        raise SeedIntegrityError(f"unsupported gazetteer seed format {seed.get('format')!r}")
    payload = json.dumps(seed.get("places"), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if digest != seed.get("content_sha256"):
        raise SeedIntegrityError("gazetteer seed content does not match its content_sha256")
    for place in seed["places"]:
        for name in place["names"]:
            if script_of(match_key(name["name"])) != LANGUAGE_SCRIPT.get(name["language"]):
                raise SeedIntegrityError(
                    f"{place.get('place_key', 'unknown')}: {name['language']} name {name['name']!r} "
                    "is not in the language's script")
    return seed


def _get_category_id(cur, feature_type: str) -> int:
    """Map feature_type to category_id by looking up the category word.
    
    Creates the main category if it doesn't exist (idempotent).
    """
    cur.execute(
        "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id WHERE w.word = %s",
        (feature_type,)
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]
    # Create the main category (no parent) for this feature type
    cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id", (feature_type,))
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT id FROM words WHERE word = %s", (feature_type,))
        row = cur.fetchone()
    word_id = row[0]
    cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id", (word_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT id FROM categorys c JOIN words w ON w.id = c.word_id WHERE w.word = %s", (feature_type,))
        row = cur.fetchone()
    return row[0]


_UPSERT_PLACE = (
    "INSERT INTO geo_places (label, category_id, country_codes, latitude, longitude)"
    " VALUES %s"
    " ON CONFLICT (label, category_id, country_codes) DO UPDATE SET"
    " latitude = EXCLUDED.latitude,"
    " longitude = EXCLUDED.longitude,"
    " retired = FALSE,"
    " updated_at = NOW()"
    " WHERE (geo_places.latitude, geo_places.longitude, geo_places.retired)"
    " IS DISTINCT FROM (EXCLUDED.latitude, EXCLUDED.longitude, FALSE)"
    " RETURNING (xmax = 0) AS inserted"
)

_UPSERT_NAME = (
    "INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type) VALUES %s"
    " ON CONFLICT (place_id, language, match_key, name_type) DO UPDATE SET"
    " name = EXCLUDED.name,"
    " script = EXCLUDED.script"
    " WHERE (geo_place_names.name, geo_place_names.script)"
    " IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.script)"
    " RETURNING (xmax = 0) AS inserted"
)


def compute_fingerprint(cur) -> Tuple[str, int, int]:
    """SHA-256 over the active gazetteer as stored (places and names, sorted).

    Computed from the database, not the seed file, so it describes exactly
    what the detector will match against.
    """
    cur.execute("""
        SELECT c.word, g.label, g.category_id, g.country_codes, g.latitude, g.longitude
        FROM geo_places g
        JOIN categorys cat ON cat.id = g.category_id
        JOIN words c ON c.id = cat.word_id
        WHERE NOT g.retired
        ORDER BY c.word, g.label, g.country_codes
    """)
    places = [[r[0], r[1], r[2], list(r[3] or []),
               None if r[4] is None else repr(float(r[4])),
               None if r[5] is None else repr(float(r[5]))] for r in cur.fetchall()]

    cur.execute("""
        SELECT c.word, g.label, g.country_codes, n.language, n.match_key, n.name, n.script,
               n.name_type
        FROM geo_place_names n
        JOIN geo_places g ON g.id = n.place_id
        JOIN categorys cat ON cat.id = g.category_id
        JOIN words c ON c.id = cat.word_id
        WHERE NOT g.retired
        ORDER BY c.word, g.label, g.country_codes, n.language, n.match_key, n.name_type
    """)
    names = [list(r) for r in cur.fetchall()]

    payload = json.dumps({"places": places, "names": names}, ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), len(places), len(names)


def lock_curation(cur) -> None:
    """Serialize authorized curation mutations before they change stored rows.

    The lock is transaction-scoped. It keeps concurrent place/name edits from
    recording out-of-order fingerprints or missing an intermediate revision.
    """
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (0x47415A45545445,))


def record_curation_change(cur, *, loaded_by: str, action: str) -> Dict[str, Any]:
    """Record a manual curation revision when detector-visible rows changed.

    The fingerprint is computed from the active stored gazetteer, so every
    matching change advances the detector version. The cache is invalidated.
    """
    fingerprint, place_count, name_count = compute_fingerprint(cur)

    # Check if fingerprint changed from cached value
    global _FINGERPRINT_CACHE
    old_fingerprint = _FINGERPRINT_CACHE
    changed = fingerprint != old_fingerprint

    if changed:
        _FINGERPRINT_CACHE = fingerprint
        _INDEX_CACHE.clear()

    logger.info("gazetteer curation by %s: action=%s changed=%s places=%d names=%d fingerprint=%s",
                loaded_by, action, changed, place_count, name_count, fingerprint[:12])
    return {"changed": changed, "fingerprint": fingerprint,
            "place_count": place_count, "name_count": name_count}


def current_fingerprint(cur) -> Optional[str]:
    """The fingerprint of the currently loaded gazetteer, or None.

    Computed from live rows and cached in process.
    """
    global _FINGERPRINT_CACHE
    if _FINGERPRINT_CACHE is not None:
        return _FINGERPRINT_CACHE

    # Check if there are any active places
    cur.execute("SELECT 1 FROM geo_places WHERE NOT retired LIMIT 1")
    if cur.fetchone() is None:
        return None

    fingerprint, _, _ = compute_fingerprint(cur)
    _FINGERPRINT_CACHE = fingerprint
    return fingerprint


def current_content_sha256(cur) -> Optional[str]:
    """The content_sha256 of the reviewed seed payload, or None.

    Computed from the seed file when needed.
    """
    try:
        seed = load_seed_file()
        return seed["content_sha256"]
    except Exception:
        return None


def sync_seed(cur, seed: Optional[Dict[str, Any]] = None, *, loaded_by: str) -> Dict[str, Any]:
    """Make the database gazetteer equal to ``seed`` (idempotent).

    Runs inside the caller's transaction; a failure leaves nothing changed.
    Returns counts and whether anything changed.

    Seed sync never retires by absence - a seed run only inserts new places,
    updates changed ones (matched by label, category and country codes) and
    adds missing names. Retirement happens only through the existing
    ``retired`` flag set by curation.
    """
    if seed is None:
        seed = load_seed_file()
    if not loaded_by:
        raise ValueError("loaded_by is required (who applied the seed)")
    lock_curation(cur)
    places = seed["places"]
    stats = {"places_added": 0, "places_updated": 0, "places_retired": 0,
             "names_added": 0, "names_updated": 0, "names_removed": 0}

    # Compute fingerprint BEFORE changes to detect if anything actually changed
    cur.execute("SELECT 1 FROM geo_places WHERE NOT retired LIMIT 1")
    has_existing = cur.fetchone() is not None
    old_fingerprint = None
    if has_existing:
        old_fingerprint, _, _ = compute_fingerprint(cur)

    # Build place rows with category_id instead of feature_type
    # Ensure main categories exist for all feature types in the seed
    rows = []
    for p in places:
        category_id = _get_category_id(cur, p["feature_type"])
        rows.append((p["label"], category_id, list(p["country_codes"]),
                     p["latitude"], p["longitude"]))

    for (inserted,) in psycopg2.extras.execute_values(
            cur, _UPSERT_PLACE, rows, page_size=500, fetch=True):
        stats["places_added" if inserted else "places_updated"] += 1

    # NOTE: We do NOT retire places that leave the seed.
    # Retirement happens only through curation (the retired flag).

    # Map label+category+country_codes to place_id for names
    cur.execute("""
        SELECT g.id, c.word, g.label, g.country_codes
        FROM geo_places g
        JOIN categorys cat ON cat.id = g.category_id
        JOIN words c ON c.id = cat.word_id
        WHERE c.word = ANY(%s) AND NOT g.retired
    """, ([p["feature_type"] for p in places],))
    ids = {}
    for pid, ftype, label, cc in cur.fetchall():
        key = (ftype, label, tuple(cc))
        ids[key] = pid

    # Build desired names
    desired = []
    for p in places:
        cat_id = _get_category_id(cur, p["feature_type"])
        key = (p["feature_type"], p["label"], tuple(p["country_codes"]))
        place_id = ids[key]
        for n in p["names"]:
            desired.append((place_id, n["name"], match_key(n["name"]), n["language"],
                            n["script"], n["name_type"]))

    # Remove names for these places that are not in desired
    cur.execute("""
        CREATE TEMP TABLE _gz_names (place_id INTEGER, language VARCHAR(8),
                                     match_key VARCHAR(255), name_type VARCHAR(16))
        ON COMMIT DROP
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO _gz_names VALUES %s",
                                   [(d[0], d[3], d[2], d[5]) for d in desired], page_size=1000)
    cur.execute("""
        DELETE FROM geo_place_names n
        WHERE n.place_id = ANY(%s)
        AND NOT EXISTS (
            SELECT 1 FROM _gz_names d
            WHERE d.place_id = n.place_id
            AND d.language = n.language
            AND d.match_key = n.match_key
            AND d.name_type = n.name_type
        )
    """, (list(ids.values()),))
    stats["names_removed"] = cur.rowcount
    cur.execute("DROP TABLE _gz_names")

    # Upsert names
    for (inserted,) in psycopg2.extras.execute_values(cur, _UPSERT_NAME, desired,
                                                      page_size=1000, fetch=True):
        stats["names_added" if inserted else "names_updated"] += 1

    # Compute fingerprint AFTER changes
    fingerprint, place_count, name_count = compute_fingerprint(cur)
    changed = fingerprint != old_fingerprint
    
    if changed:
        _FINGERPRINT_CACHE = fingerprint
        _INDEX_CACHE.clear()
    else:
        # No actual change - restore cache
        _FINGERPRINT_CACHE = old_fingerprint

    logger.info("gazetteer sync by %s: changed=%s %s fingerprint=%s", loaded_by, changed,
                stats, fingerprint[:12])
    return {"changed": changed, "fingerprint": fingerprint, "place_count": place_count,
            "name_count": name_count, "seed_version": seed["seed_version"], **stats}


# ---------------------------------------------------------------------------
# Detector index
# ---------------------------------------------------------------------------


def detector_gazetteer(cur):
    """The ``place_intel.Gazetteer`` for the current load, cached per fingerprint.

    Names are read only when the fingerprint changes. Raises :class:`GazetteerUnavailable`
    when no gazetteer is loaded - a place run then fails loudly instead of
    recording "no places found".
    """
    from core.detection import place_intel

    fingerprint = current_fingerprint(cur)
    if fingerprint is None:
        raise GazetteerUnavailable("no gazetteer is loaded (migration m0001 not applied?)")

    cached = _INDEX_CACHE.get("gazetteer")
    if cached is not None and cached.fingerprint == fingerprint:
        return cached

    cur.execute("""
        SELECT g.id, 'place:' || g.id, g.label, c.word,
               g.country_codes, n.name, n.language, n.script, n.name_type,
               FALSE, NULL
        FROM geo_place_names n
        JOIN geo_places g ON g.id = n.place_id
        JOIN categorys cat ON cat.id = g.category_id
        JOIN words c ON c.id = cat.word_id
        WHERE NOT g.retired
        ORDER BY g.id, n.language, n.match_key, n.name_type
    """)
    gazetteer = place_intel.Gazetteer.from_rows(cur.fetchall(), fingerprint)
    _INDEX_CACHE["gazetteer"] = gazetteer
    return gazetteer


def current_detector_version(cur) -> Optional[str]:
    """``places-X.Y.Z+g<fingerprint>`` for the loaded gazetteer, or None."""
    from core.detection import place_intel

    fingerprint = current_fingerprint(cur)
    return place_intel.detector_version(fingerprint) if fingerprint else None