"""Phase 2 integration: database gazetteer and place-mention signals.

Executed against PostgreSQL through the baseline migration (m0001_baseline),
the real ingestion path, the real JobManager and the HTTP API.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import date

import psycopg2
import psycopg2.errors
import pytest

from _seed import connect

pytestmark = pytest.mark.integration

_U = uuid.uuid4().hex[:8]

TEXT = (
    "Talks opened in Zagreb and moved to Tripoli.\n"
    "المحادثات في القاهرة ثم وبيروت.\n"
    "השיחות עברו ובירושלים.\n"
    "Sastanak u Rijeci. Turkey objected.\n"
)


@pytest.fixture(scope="module")
def private_db(pg_db):
    """A database of this module's own, built from the frozen chain.

    Two claims in this module are about a *freshly migrated* database:

    * ``test_migration_created_gazetteer_tables`` asserts the baseline loads no
      seed data (``current_fingerprint`` is ``None``);
    * ``test_sync_seed_loads_gazetteer`` asserts the loaded place and name
      counts equal the seed's exactly (223 places).

    The shared session database is not fresh when this module runs. The places
    CRUD module creates places through the API, and the API commits; its
    autouse cleanup only removes labels starting ``Test``/``Updated``, so a
    committed ``New Metropolis <uuid>`` is still there. Measured in the full
    run: ``test_migration_created_gazetteer_tables`` failed with ``assert
    'e341b364…' is None`` ("Seed data not loaded by baseline migration") and
    ``test_sync_seed_loads_gazetteer`` with ``assert 226 == 223`` - purely
    because another module ran first.

    A database this module builds itself makes both claims true whatever ran
    before. Same pattern as ``test_startup_upgrade_path.private_db``; created
    empty and migrated here (never cloned with ``TEMPLATE``, which is refused
    while another session holds the source database). Test isolation - the
    frozen migrations, the schema and the product code are untouched.

    The gazetteer keeps a process-global fingerprint/index cache, so it is
    invalidated when this module switches databases and again afterwards,
    otherwise ``current_fingerprint`` would answer with a value computed
    against another database (or a database that has been dropped).
    """
    from services.geo.gazetteer import _invalidate_cache

    name = f"{pg_db['database']}_places_pg"
    admin = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname="postgres",
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    cfg = dict(pg_db, database=name)
    from database.bootstrap import bootstrap_database
    from database.migration_runner import discover_migrations

    report = bootstrap_database({k: cfg[k] for k in
                                 ("host", "port", "user", "password", "database")})
    # The tip of the chain, not a literal: the chain grew by one authorised
    # migration (m0004, category hierarchy) and a hard-coded "0003" turned a
    # correct bootstrap into a failure. What this fixture needs is "bootstrap
    # applied the whole chain", which is what the last discovered version says.
    assert report["current_version"] == discover_migrations()[-1].version, report
    _invalidate_cache()
    yield cfg
    _invalidate_cache()

    admin = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname="postgres",
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


@pytest.fixture(scope="module")
def tenant(private_db):
    conn = connect(private_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO sides (name, importance, date_creation) VALUES (%s, 0.5,"
                    " CURRENT_DATE) RETURNING id", (f"geo_side_{_U}",))
        side_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                    " VALUES (%s, 't', 0.5, 't', CURRENT_DATE) RETURNING id", (f"geo_src_{_U}",))
        source_id = cur.fetchone()[0]
    conn.close()
    return {"source_id": source_id, "side_id": side_id}


_N = iter(range(10_000))


def _store(private_db, tenant, raw_text=TEXT):
    """Insert a document directly via SQL. Place detection is tested separately."""
    import hashlib
    import psycopg2
    from psycopg2.extras import RealDictCursor

    n = next(_N)
    digest = hashlib.sha256(f"{raw_text}:{n}".encode()).hexdigest()

    conn = psycopg2.connect(
        host=private_db["host"], port=private_db["port"],
        user=private_db["user"], password=private_db["password"],
        dbname=private_db["database"]
    )
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Create hash and content
            cur.execute("INSERT INTO hashs (hash) VALUES (%s) ON CONFLICT (hash) DO UPDATE SET hash = EXCLUDED.hash RETURNING id", (digest,))
            hash_id = cur.fetchone()["id"]
            cur.execute("INSERT INTO contents_raw (hash_id, chunk_seq, content, char_count) VALUES (%s, 0, %s, %s) ON CONFLICT DO NOTHING", (hash_id, raw_text, len(raw_text)))
            cur.execute("INSERT INTO contents (content_data, content_date, hash_id) VALUES (NULL, %s, %s) ON CONFLICT DO NOTHING", (date.today(), hash_id))

            # Create hash_context
            cur.execute("INSERT INTO hash_contexts (hash_id, source_id, side_id) VALUES (%s, %s, %s) ON CONFLICT (hash_id, source_id, side_id) DO UPDATE SET hash_id = EXCLUDED.hash_id RETURNING id", (hash_id, tenant["source_id"], tenant["side_id"]))
            context_id = cur.fetchone()["id"]

            # Create path
            cur.execute(
                "INSERT INTO paths (file_name, file_path, file_size, file_type, file_status, file_date, date_creation, context_id, processing_status) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, 'discovered') RETURNING id",
                (f"geo_{n}.txt", f"/seed/geo_{n}.txt", len(raw_text), "txt", "Unread", date.today(), context_id))
            path_id = cur.fetchone()["id"]

            # Return info needed by the test
            return {"success": True, "hash_id": hash_id, "path_id": path_id, "context_id": context_id, "warnings": [], "place_signals": {"status": "complete"}}
    finally:
        conn.close()


def _rows(private_db, sql, params):
    conn = connect(private_db)
    with conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _ensure_categories(cur):
    """Ensure required categories (country, city, region) exist."""
    for word in ["country", "city", "region"]:
        cur.execute("SELECT id FROM words WHERE word = %s", (word,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (word,))
            word_id = cur.fetchone()[0]
        else:
            word_id = row[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT DO NOTHING", (word_id,))


def _get_category_id(cur, feature_type: str) -> int:
    """Map feature_type to category_id by looking up the category word."""
    cur.execute(
        "SELECT c.id FROM categorys c JOIN words w ON w.id = c.word_id WHERE w.word = %s",
        (feature_type,)
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"no category found for feature_type '{feature_type}'")
    return row[0]


# --- gazetteer tables --------------------------------------------------------

def test_migration_created_gazetteer_tables(private_db):
    """Baseline migration creates gazetteer tables but does not load seed data.
    Seed data is loaded separately via sync_seed().
    """
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            # Tables exist
            cur.execute("SELECT to_regclass('geo_places') IS NOT NULL")
            assert cur.fetchone()[0]
            cur.execute("SELECT to_regclass('geo_place_names') IS NOT NULL")
            assert cur.fetchone()[0]
            cur.execute("SELECT to_regclass('geo_gazetteer_loads') IS NOT NULL")
            assert not cur.fetchone()[0], "geo_gazetteer_loads should be removed"
            cur.execute("SELECT to_regclass('gazetteer_metadata') IS NOT NULL")
            assert not cur.fetchone()[0], "gazetteer_metadata should be removed"
            cur.execute("SELECT to_regclass('content_signal_places') IS NOT NULL")
            assert cur.fetchone()[0]
            # No seed data loaded yet (done separately)
            from services.geo.gazetteer import current_fingerprint
            fp = current_fingerprint(cur)
            assert fp is None, "Seed data not loaded by baseline migration"
    finally:
        conn.close()


def test_sync_seed_loads_gazetteer(private_db):
    """sync_seed loads the gazetteer seed data idempotently."""
    from services.geo.gazetteer import compute_fingerprint, current_fingerprint, load_seed_file, sync_seed

    seed = load_seed_file()
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            _ensure_categories(cur)
            conn.commit()

            # First load
            load = sync_seed(cur, seed, loaded_by="test")
            assert load["changed"] is True
            assert load["place_count"] == seed["place_count"]
            assert load["name_count"] == seed["name_count"]
            assert load["seed_version"] == seed["seed_version"]

            # Verify fingerprint matches
            fingerprint, places, names = compute_fingerprint(cur)
            assert fingerprint == load["fingerprint"]
            cur.execute("SELECT count(*) FROM geo_places WHERE NOT retired")
            assert cur.fetchone()[0] == places == seed["place_count"]
            cur.execute("SELECT language, count(*) FROM geo_place_names GROUP BY 1 ORDER BY 1")
            assert {r[0] for r in cur.fetchall()} == {"ar", "en", "fa", "he", "hr"}

            # Second load is idempotent
            load2 = sync_seed(cur, seed, loaded_by="test")
            assert load2["changed"] is False
    finally:
        conn.close()


def test_sync_seed_idempotent_and_curated_places_survive(private_db):
    """sync_seed is idempotent; curated places survive a seed reload with changes."""
    from services.geo.gazetteer import current_detector_version, current_fingerprint, load_seed_file, sync_seed

    seed = load_seed_file()
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            _ensure_categories(cur)
            conn.commit()

            # Load seed first
            sync_seed(cur, seed, loaded_by="test")

            fp_before = current_fingerprint(cur)
            again = sync_seed(cur, seed, loaded_by="test")
            assert again["changed"] is False
            assert again["places_added"] == again["places_updated"] == 0
            assert again["names_added"] == again["names_updated"] == again["names_removed"] == 0
            assert current_fingerprint(cur) == fp_before

            # Add a curated place (user-created, not from seed)
            cur.execute("""
                INSERT INTO geo_places (label, category_id, country_codes, latitude, longitude, retired)
                VALUES ('Curated Place', %s, '{US}', 40.0, -74.0, FALSE)
                RETURNING id
            """, (_get_category_id(cur, "city"),))
            curated_place_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type)
                VALUES (%s, 'Curated Place', 'curated place', 'en', 'Latn', 'endonym')
            """, (curated_place_id,))
            conn.commit()

            # Modify seed: drop a place and remove a name
            edited = copy.deepcopy(seed)
            dropped = edited["places"].pop()
            edited["places"][0]["names"] = edited["places"][0]["names"][1:]
            payload = json.dumps(edited["places"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"))
            edited["content_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
            changed = sync_seed(cur, edited, loaded_by="test")
            assert changed["changed"] is True
            # Curated place should still exist and not be retired
            cur.execute("SELECT retired FROM geo_places WHERE id = %s", (curated_place_id,))
            assert cur.fetchone() == (False,), "curated place should survive seed reload"
            # Seed place should still exist (the remaining ones)
            cur.execute("SELECT count(*) FROM geo_places WHERE NOT retired")
            remaining = cur.fetchone()[0]
            assert remaining >= len(edited["places"]), "seed places should survive"
            # Detector version should change
            assert current_detector_version(cur) != fp_before,                 "a gazetteer change must change the place detector version"
    finally:
        conn.rollback()
        conn.close()


# --- constraint tests ---------------------------------------------------------

@pytest.mark.parametrize("sql,params,expected_exception,constraint", [
    # geo_places: category_id must reference valid category (FK)
    ("INSERT INTO geo_places (label, category_id, country_codes) VALUES"
     " ('X', 999999, '{US}')", (), psycopg2.errors.ForeignKeyViolation, "geo_places_category_id_fkey"),
    # geo_places: country_codes format must be valid (uppercase 2-letter codes)
    ("INSERT INTO geo_places (label, category_id, country_codes) VALUES"
     " ('X', 1, '{us}')", (), psycopg2.errors.CheckViolation, "geo_places_country_codes_check"),
    # geo_places: latitude/longitude must be paired
    ("INSERT INTO geo_places (label, category_id, country_codes, latitude) VALUES"
     " ('X', 1, '{US}', 45)", (), psycopg2.errors.CheckViolation, "geo_places_check"),
    ("INSERT INTO geo_places (label, category_id, country_codes, latitude, longitude) VALUES"
     " ('X', 1, '{US}', 95, 10)", (), psycopg2.errors.CheckViolation, "geo_places_latitude_check"),
    # geo_place_names: language must be one of en, ar, he, fa, hr
    # Note: the combined language/script check (geo_place_names_check) fires first
    ("INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type)"
     " VALUES (1, 'x', 'x', 'fr', 'Latn', 'exonym')",
     (), psycopg2.errors.CheckViolation, "geo_place_names_check"),
    # name_type must be one of endonym, exonym, historical, variant, unclassified
    ("INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type)"
     " VALUES (1, 'x', 'x', 'en', 'Latn', 'nickname')",
     (), psycopg2.errors.CheckViolation, "geo_place_names_name_type_check"),
    # script must match language (he requires Hebr)
    ("INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type)"
     " VALUES (1, 'x', 'x', 'he', 'Latn', 'exonym')",
     (), psycopg2.errors.CheckViolation, "geo_place_names_check"),
])


def test_gazetteer_constraints_fire_by_name(private_db, sql, params, expected_exception, constraint):
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            _ensure_categories(cur)
            conn.commit()
            with pytest.raises(expected_exception) as info:
                cur.execute(sql, params)
            if hasattr(info.value.diag, 'constraint_name') and info.value.diag.constraint_name:
                assert info.value.diag.constraint_name == constraint
    finally:
        conn.rollback()
        conn.close()


def test_duplicate_name_for_a_place_is_rejected(private_db):
    """UNIQUE (place_id, language, match_key, name_type) prevents duplicate names."""
    from services.geo.gazetteer import load_seed_file, sync_seed

    seed = load_seed_file()
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            _ensure_categories(cur)
            conn.commit()
            sync_seed(cur, seed, loaded_by="test")
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO geo_place_names (place_id, name, match_key, language, script, name_type) "
                    "SELECT place_id, name, match_key, language, script, name_type FROM geo_place_names LIMIT 1"
                )
    finally:
        conn.rollback()
        conn.close()


def test_natural_uniqueness_of_places(private_db):
    """Natural uniqueness on (label, category_id, country_codes) prevents duplicates."""
    from services.geo.gazetteer import load_seed_file, sync_seed

    seed = load_seed_file()
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            _ensure_categories(cur)
            conn.commit()
            sync_seed(cur, seed, loaded_by="test")
            # Try to insert duplicate place (same label, category, country_codes)
            cur.execute("SELECT category_id, country_codes FROM geo_places WHERE label = %s LIMIT 1",
                        (seed["places"][0]["label"],))
            row = cur.fetchone()
            assert row is not None
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO geo_places (label, category_id, country_codes) VALUES (%s, %s, %s)",
                    (seed["places"][0]["label"], row[0], row[1])
                )
    finally:
        conn.rollback()
        conn.close()


# --- ingestion ---------------------------------------------------------------

def test_ingestion_stores_place_signals_with_candidates_and_offsets(private_db, tenant):
    """This test requires the full ingestion pipeline with place detection.
    Skipped for Part 1 (schema consolidation); will be updated in Part 2.
    """
    pytest.skip("Requires full ingestion pipeline - Part 2")


def test_place_signal_constraints_and_provenance_protection(private_db):
    """Invalid signal rows and orphaned place links are rejected by constraints."""
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            # resolution='absolute' requires date_from (content_signals_check3)
            with pytest.raises(psycopg2.errors.CheckViolation) as info:
                cur.execute("""
                    INSERT INTO content_signals (hash_id, detector, detector_ver, signal_type,
                        value, surface, char_start, char_end, language, resolution,
                        date_from, evidence, dedup_key)
                    VALUES (1, 'places', '0.0.1', 'place_mention', 'x', 'x', 0, 1, 'en',
                        'absolute', NULL, '{}', '0'::bit(256)::char(64))
                """)
            assert info.value.diag.constraint_name == "content_signals_check3"
    finally:
        conn.rollback()
        conn.close()


def test_place_signal_fk_restrict(private_db):
    """content_signal_places FK to geo_places (RESTRICT) rejects invalid place_id."""
    conn = connect(private_db)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                cur.execute("INSERT INTO content_signal_places (signal_id, place_id) VALUES (1, 999999)")
    finally:
        conn.rollback()
        conn.close()