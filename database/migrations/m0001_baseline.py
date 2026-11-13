"""Single baseline schema for SYLTHARAE-Pro (replaces migrations 0001-0039).

This migration creates the complete schema in dependency order within one
transaction. It incorporates all intentional removals from objectives C, D, F
and the behavior-neutral fixes from E6. The nine tables in C never appear.

Removed entirely (C):
    report_artifacts, report_baselines, report_run_analyses,
    report_run_datasets, rule_evaluations, rule_subject_ledger,
    path_geo_mentions_m0015, monitoring_rule_versions, monitoring_rules

Removed columns (C, D):
    alerts.rule_id, alerts.rule_evaluation_id, alerts.file_name,
    alerts.file_path, saved_searches.monitor_enabled

Removed CHECK values (C):
    alerts.type: 'rule_match', 'rule_status'
    job_schedules.schedule_type: 'rule_evaluation', 'report_run', 'report_artifact'

Removed constraints/indexes (E6):
    titles_content.title_content_id (self-reference), titles_content.title_status 'Branch'
    sessions.ip_address, sessions.user_agent
    Redundant indexes per m0038 prefix analysis

Added primary keys (E6):
    words_hashs (hash_id, word_id), keywords_hashs (hash_id, keyword_id),
    words_categorys (word_id, category_id)

Views (D):
    path_geo_mentions  -- rebuilt over live signals only (no UNION fallback)
    path_overview      -- one row per path, no join fan-out
"""

version = "0001"
name = "baseline"

# ---------------------------------------------------------------------------
# Helper: deterministic SQL fragments for CHECK lists
# ---------------------------------------------------------------------------
# These are literal SQL; they must match the Python enums exactly.
_ALERT_TYPES = (
    "'similar_files', 'future_date', 'future_event', 'processing_complete',"
    "'batch_complete', 'error', 'warning', 'info', 'scenario_outcome',"
    "'scenario_status'"
)
_ALERT_PRIORITIES = "'low', 'medium', 'high', 'critical'"
_JOB_STATES = (
    "'QUEUED', 'RUNNING', 'COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED',"
    "'CANCELLED', 'PAUSED'"
)
_JOB_EVENT_TYPES = (
    "'JOB_CREATED', 'JOB_STARTED', 'JOB_PROGRESS', 'JOB_PAUSED',"
    "'JOB_RESUMED', 'JOB_COMPLETED', 'JOB_COMPLETED_WITH_WARNINGS',"
    "'JOB_FAILED', 'JOB_CANCELLED', 'PROGRESS', 'STATUS_CHANGE'"
)
_SCHEDULE_TYPES = "'scenario_evaluation', 'retention'"
_PROCESSING_STATES = (
    "'discovered', 'queued', 'processing', 'processed', 'partially_processed',"
    "'failed', 'unsupported', 'skipped', 'retrying'"
)
_FILE_STATUSES = "'Read', 'Unread'"
_TITLE_STATUSES = "'Main'"
_SIGNAL_RESOLUTIONS = (
    "'absolute', 'approximate', 'document_relative', 'ambiguous',"
    "'unresolved', 'identified', 'literal'"
)
_SIGNAL_TYPES = "'date_reference', 'place_mention', 'relative_reference', 'orientation', 'identifier'"
_CALENDARS = "'gregorian', 'hijri', 'jalali'"
_TEXT_ORIENTATIONS = "'future', 'present', 'past'"
_GEO_FEATURE_TYPES = "'city', 'country', 'region'"
_GEO_NAME_TYPES = "'endonym', 'exonym', 'historical', 'variant', 'unclassified'"
_GEO_LANGUAGES = "'en', 'ar', 'he', 'fa', 'hr'"
_GEO_SCRIPTS = "'Latn', 'Arab', 'Hebr'"
_USER_ROLES = "'admin', 'analyst', 'viewer'"
_RULE_STATUSES = "'active', 'paused', 'disabled', 'archived'"  # not used, kept for reference
_SCENARIO_STATUSES = "'draft', 'active', 'paused', 'disabled', 'archived'"
_SCENARIO_KINDS = "'evaluation', 'dry_run'"
_SCENARIO_TRIGGERS = (
    "'manual', 'ingestion', 'redetection', 'all_scenarios', 'schedule'"
)
_SCENARIO_EVAL_STATUSES = (
    "'completed', 'passed', 'invalid', 'failed', 'owner_revoked',"
    "'skipped_busy', 'not_active'"
)
_SCENARIO_DELIVERIES = "'baseline', 'recorded', 'notified', 'overflow'"
_SCENARIO_PRIORITIES = "'high', 'medium', 'low'"
_REPORT_DATASET_SEMANTICS = "'exact', 'capped', 'top_n'"

_SQL_STATEMENTS = [
    # =======================================================================
    # Extensions
    # =======================================================================
    # No extensions required for baseline; pg_trgm/pgcrypto not in test pgserver build

    # =======================================================================
    # Core lookup tables (no FK dependencies)
    # =======================================================================
    """
    CREATE TABLE words (
        id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        word TEXT UNIQUE NOT NULL
    )
    """,
    "CREATE INDEX idx_words_word ON words (word)",

    """
    CREATE TABLE punctuation (
        id SERIAL PRIMARY KEY,
        punctuation_text TEXT UNIQUE NOT NULL
    )
    """,
    "CREATE INDEX idx_punctuation_text ON punctuation (punctuation_text)",

    """
    CREATE TABLE categorys (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        word_id INTEGER UNIQUE NOT NULL,
        parent_id INTEGER NULL REFERENCES categorys(id) ON DELETE RESTRICT ON UPDATE CASCADE,
        FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE,
        CHECK (id != parent_id)
    )
    """,
    "CREATE INDEX idx_categorys_word_id ON categorys (word_id)",
    "CREATE INDEX idx_categorys_parent_id ON categorys (parent_id)",

    # Trigger to enforce single-level hierarchy
    """
    CREATE OR REPLACE FUNCTION categorys_hierarchy_check() RETURNS trigger AS $$
    DECLARE
        v_parent_parent_id INTEGER;
        v_child_count INTEGER;
    BEGIN
        -- CHECK (id != parent_id) handles self-parent
        -- Prevent grandchild: parent must be a main category (no parent)
        IF NEW.parent_id IS NOT NULL THEN
            SELECT parent_id INTO v_parent_parent_id FROM categorys WHERE id = NEW.parent_id;
            IF v_parent_parent_id IS NOT NULL THEN
                RAISE EXCEPTION 'A subcategory''s parent must be a main category (no parent)'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        -- Prevent making a main category with children into a subcategory
        IF NEW.parent_id IS NOT NULL THEN
            SELECT count(*) INTO v_child_count FROM categorys WHERE parent_id = NEW.id;
            IF v_child_count > 0 THEN
                RAISE EXCEPTION 'A category with children cannot become a subcategory'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_categorys_hierarchy_check
    BEFORE INSERT OR UPDATE ON categorys
    FOR EACH ROW EXECUTE FUNCTION categorys_hierarchy_check()
    """,

    # words_categorys with PRIMARY KEY (E6)
    """
    CREATE TABLE words_categorys (
        word_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        PRIMARY KEY (word_id, category_id),
        FOREIGN KEY (category_id) REFERENCES categorys(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_wc_word_id ON words_categorys (word_id)",
    "CREATE INDEX idx_wc_category_id ON words_categorys (category_id)",

    """
    CREATE TABLE sides (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        importance DECIMAL(5,4) NOT NULL,
        date_creation DATE NOT NULL
    )
    """,
    "CREATE INDEX idx_sides_importance ON sides (importance DESC)",
    "CREATE INDEX idx_sides_date_creation ON sides (date_creation)",

    """
    CREATE TABLE sources (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        job VARCHAR(255) NOT NULL,
        importance DECIMAL(5,4) NOT NULL,
        country VARCHAR(255) NOT NULL,
        city VARCHAR(255) NULL,
        description VARCHAR(255) NULL,
        accounts VARCHAR(255) NULL,
        note VARCHAR(255) NULL,
        ownership VARCHAR(255) NULL,
        access_status VARCHAR(255) NULL,
        entry_date TIMESTAMP NULL,
        category_id INTEGER NULL,
        FOREIGN KEY (category_id) REFERENCES categorys(id) ON DELETE SET NULL ON UPDATE CASCADE,
        date_creation DATE NOT NULL
    )
    """,
    "CREATE INDEX idx_sources_country ON sources (country)",
    "CREATE INDEX idx_sources_job ON sources (job)",
    "CREATE INDEX idx_sources_importance ON sources (importance DESC)",
    "CREATE INDEX idx_sources_date_creation ON sources (date_creation)",
    "CREATE INDEX idx_sources_ownership ON sources (ownership)",
    "CREATE INDEX idx_sources_access_status ON sources (access_status)",
    "CREATE INDEX idx_sources_entry_date ON sources (entry_date)",
    "CREATE INDEX idx_sources_category_id ON sources (category_id)",

    # =======================================================================
    # Content identity: hashs, hash_contexts, paths
    # =======================================================================
    """
    CREATE TABLE hashs (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash CHAR(64) NOT NULL UNIQUE
    )
    """,
    "CREATE INDEX idx_hashs_hash ON hashs (hash)",

    """
    CREATE TABLE hash_contexts (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash_id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        side_id INTEGER NOT NULL,
        date_creation DATE NOT NULL DEFAULT CURRENT_DATE,
        UNIQUE (hash_id, source_id, side_id),
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON UPDATE CASCADE,
        FOREIGN KEY (side_id) REFERENCES sides(id) ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_hash_contexts_hash_id ON hash_contexts (hash_id)",
    "CREATE INDEX idx_hash_contexts_source_id ON hash_contexts (source_id)",
    "CREATE INDEX idx_hash_contexts_side_id ON hash_contexts (side_id)",

    """
    CREATE TABLE paths (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        file_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_size BIGINT NOT NULL CHECK (file_size >= 0),
        file_type VARCHAR(100) NOT NULL,
        file_status VARCHAR(10) NOT NULL CHECK (file_status IN (""" + _FILE_STATUSES + """)) DEFAULT 'Unread',
        file_date DATE NOT NULL,
        date_creation DATE NOT NULL,
        context_id INTEGER NOT NULL,
        coordinates TEXT NULL,
        extraction_provenance JSONB NULL,
        parent_path_id INTEGER NULL,
        hierarchy_path TEXT NULL,
        processing_status VARCHAR(24) NOT NULL DEFAULT 'discovered'
            CHECK (processing_status IN (""" + _PROCESSING_STATES + """)),
        status_detail TEXT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        status_updated_at TIMESTAMPTZ NULL,
        error_message TEXT NULL,
        FOREIGN KEY (context_id) REFERENCES hash_contexts(id) ON UPDATE CASCADE,
        FOREIGN KEY (parent_path_id) REFERENCES paths(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX idx_paths_context_id ON paths (context_id)",
    "CREATE INDEX idx_paths_file_name ON paths (file_name)",
    "CREATE INDEX idx_paths_file_path ON paths (file_path)",
    "CREATE INDEX idx_paths_file_type ON paths (file_type)",
    "CREATE INDEX idx_paths_file_status ON paths (file_status)",
    "CREATE INDEX idx_paths_date_creation ON paths (date_creation)",
    "CREATE INDEX idx_paths_parent_path_id ON paths (parent_path_id)",
    "CREATE INDEX idx_paths_processing_status ON paths (processing_status)",
    "CREATE INDEX idx_paths_hash_id ON paths (context_id) INCLUDE (id) WHERE context_id IS NOT NULL",

    # =======================================================================
    # Content-derived tables (keyed by hash_id)
    # =======================================================================
    """
    CREATE TABLE contents (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        content_data BYTEA,
        content_date DATE NULL,
        hash_id INTEGER NOT NULL,
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_contents_hash_id ON contents (hash_id)",
    "CREATE INDEX idx_contents_content_date ON contents (content_date)",

    """
    CREATE TABLE contents_raw (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash_id INTEGER NOT NULL,
        chunk_seq INTEGER NOT NULL,
        content TEXT NOT NULL,
        char_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (hash_id, chunk_seq),
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_contents_raw_hash_id ON contents_raw (hash_id)",

    """
    CREATE TABLE titles_content (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        title_data BYTEA,
        title_status VARCHAR(10) NOT NULL CHECK (title_status IN (""" + _TITLE_STATUSES + """)) DEFAULT 'Main',
        hash_id INTEGER NOT NULL,
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_titles_content_hash_id ON titles_content (hash_id)",
    "CREATE INDEX idx_titles_content_status ON titles_content (title_status)",

    # words_hashs with PRIMARY KEY (E6)
    """
    CREATE TABLE words_hashs (
        hash_id INTEGER NOT NULL,
        word_id INTEGER NOT NULL,
        word_count INT,
        position_indexer BYTEA NOT NULL,
        PRIMARY KEY (hash_id, word_id),
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_words_hashs_hash_id ON words_hashs (hash_id)",
    "CREATE INDEX idx_words_hashs_word_id ON words_hashs (word_id)",
    "CREATE INDEX idx_words_hashs_word_hash ON words_hashs (word_id, hash_id)",
    "CREATE INDEX idx_words_hashs_hash_word ON words_hashs (hash_id, word_id)",

    # keywords_hashs with PRIMARY KEY (E6)
    """
    CREATE TABLE keywords (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        keyword BYTEA UNIQUE NOT NULL,
        category_id INTEGER NOT NULL,
        FOREIGN KEY (category_id) REFERENCES categorys(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_keywords_category_id ON keywords (category_id)",

    """
    CREATE TABLE keywords_hashs (
        hash_id INTEGER NOT NULL,
        keyword_id INTEGER NOT NULL,
        word_count INT,
        PRIMARY KEY (hash_id, keyword_id),
        FOREIGN KEY (hash_id) REFERENCES hashs(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_keywords_hashs_hash_id ON keywords_hashs (hash_id)",
    "CREATE INDEX idx_keywords_hashs_keyword_id ON keywords_hashs (keyword_id)",

    # =======================================================================
    # Auth & audit
    # =======================================================================
    """
    CREATE TABLE users (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        username VARCHAR(64) UNIQUE NOT NULL,
        password_hash VARCHAR(512) NOT NULL,
        role VARCHAR(20) NOT NULL CHECK (role IN (""" + _USER_ROLES + """)),
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
        failed_login_count INTEGER NOT NULL DEFAULT 0,
        locked_until TIMESTAMPTZ NULL,
        last_login_at TIMESTAMPTZ NULL,
        password_changed_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # sessions without ip_address, user_agent (E6)
    """
    CREATE TABLE sessions (
        session_id CHAR(64) PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ NULL
    )
    """,
    "CREATE INDEX idx_sessions_user_id ON sessions (user_id)",
    "CREATE INDEX idx_sessions_expires_at ON sessions (expires_at)",

    """
    CREATE TABLE audit_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER NULL,
        username VARCHAR(64) NULL,
        action VARCHAR(255) NOT NULL,
        resource VARCHAR(255) NULL,
        detail JSONB NULL,
        ip_address VARCHAR(64) NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_audit_log_user_id ON audit_log (user_id)",
    "CREATE INDEX idx_audit_log_created_at ON audit_log (created_at DESC)",
    "CREATE INDEX idx_audit_log_action ON audit_log (action)",
    "CREATE INDEX idx_audit_log_username_id ON audit_log (username, id DESC)",
    "CREATE INDEX idx_audit_log_user_id_id ON audit_log (user_id, id DESC)",
    "CREATE INDEX idx_audit_log_resource_prefix ON audit_log (resource text_pattern_ops)",

    # =======================================================================
    # Jobs & scheduling
    # =======================================================================
    """
    CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'QUEUED',
        progress INTEGER NOT NULL DEFAULT 0,
        current_phase TEXT,
        current_item TEXT,
        source TEXT,
        options JSONB DEFAULT '{}'::jsonb,
        stats JSONB DEFAULT '{}'::jsonb,
        errors JSONB DEFAULT '[]'::jsonb,
        warnings JSONB DEFAULT '[]'::jsonb,
        result_summary JSONB,
        created_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
        pause_requested BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_jobs_status CHECK (status IN (""" + _JOB_STATES + """))
    )
    """,
    "CREATE INDEX idx_jobs_status ON jobs (status)",
    "CREATE INDEX idx_jobs_created_at ON jobs (created_at DESC)",

    """
    CREATE TABLE job_events (
        event_id BIGSERIAL PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        event_type TEXT NOT NULL CHECK (event_type IN (""" + _JOB_EVENT_TYPES + """)),
        payload JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_job_events_job ON job_events (job_id, event_id)",

    """
    CREATE TABLE job_schedules (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        schedule_type VARCHAR(32) NOT NULL CHECK (schedule_type IN (""" + _SCHEDULE_TYPES + """)),
        name VARCHAR(200) NOT NULL,
        owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        interval_minutes INTEGER NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        disabled_reason VARCHAR(64) NULL,
        next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_run_at TIMESTAMPTZ NULL,
        last_job_id TEXT NULL,
        last_status VARCHAR(32) NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (owner_user_id, name),
        CHECK (length(btrim(name)) > 0),
        CHECK (interval_minutes BETWEEN 5 AND 525600),
        CHECK (consecutive_failures >= 0)
    )
    """,
    "CREATE INDEX idx_job_schedules_due ON job_schedules (next_run_at) WHERE enabled",
    "CREATE INDEX idx_job_schedules_owner ON job_schedules (owner_user_id)",

    # =======================================================================
    # Analyst categorization
    # =======================================================================
    """
    CREATE TABLE analyst_categories (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        description TEXT NULL,
        color VARCHAR(7) NULL,
        created_by INTEGER NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX uq_analyst_categories_lower_name ON analyst_categories (LOWER(name))",
    "CREATE INDEX idx_analyst_categories_created_by ON analyst_categories (created_by)",

    """
    CREATE TABLE analyst_file_categories (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        path_id INTEGER NOT NULL,
        category_id INTEGER NOT NULL,
        assigned_by INTEGER NULL,
        assigned_by_username VARCHAR(64) NULL,
        source_query TEXT NULL,
        assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (path_id, category_id),
        FOREIGN KEY (path_id) REFERENCES paths(id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (category_id) REFERENCES analyst_categories(id) ON DELETE CASCADE ON UPDATE CASCADE
    )
    """,
    "CREATE INDEX idx_afc_path_id ON analyst_file_categories (path_id)",
    "CREATE INDEX idx_afc_category_id ON analyst_file_categories (category_id)",
    "CREATE INDEX idx_afc_assigned_by ON analyst_file_categories (assigned_by)",
    "CREATE INDEX idx_afc_assigned_at ON analyst_file_categories (assigned_at DESC)",
    "CREATE INDEX idx_afc_category_assigned_at ON analyst_file_categories (category_id, assigned_at DESC)",

    """
    CREATE TABLE analyst_categorization_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        analyst_id INTEGER NULL,
        analyst_username VARCHAR(64) NULL,
        action VARCHAR(32) NOT NULL,
        category_id INTEGER NULL,
        category_name VARCHAR(255) NULL,
        source_query TEXT NULL,
        path_ids JSONB NOT NULL,
        path_count INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX idx_acl_created_at ON analyst_categorization_log (created_at DESC)",
    "CREATE INDEX idx_acl_analyst_id ON analyst_categorization_log (analyst_id)",
    "CREATE INDEX idx_acl_action ON analyst_categorization_log (action)",
    "CREATE INDEX idx_acl_category_id ON analyst_categorization_log (category_id)",

    # =======================================================================
    # Saved searches
    # =======================================================================
    # monitor_enabled column REMOVED (C, F)
    """
    CREATE TABLE saved_searches (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_user_id INTEGER NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        query TEXT NOT NULL DEFAULT '',
        filters JSONB NOT NULL DEFAULT '{}'::jsonb,
        criteria JSONB NOT NULL,
        criteria_fingerprint CHAR(64) NOT NULL,
        criteria_schema_version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_used_at TIMESTAMPTZ NULL,
        legacy_source VARCHAR(64) NULL,
        legacy_id INTEGER NULL,
        import_notes JSONB NULL,
        CHECK (length(btrim(name)) > 0),
        CHECK (criteria_fingerprint ~ '^[0-9a-f]{64}$'),
        CHECK ((legacy_source IS NULL) = (legacy_id IS NULL)),
        UNIQUE (legacy_source, legacy_id)
    )
    """,
    "CREATE INDEX idx_saved_searches_owner_created ON saved_searches (owner_user_id, created_at DESC)",
    "CREATE INDEX idx_saved_searches_fingerprint ON saved_searches (criteria_fingerprint)",
    # idx_saved_searches_monitor REMOVED (no monitor_enabled column)

    """
    CREATE TABLE saved_search_imports (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_file TEXT NOT NULL,
        file_sha256 CHAR(64) NOT NULL,
        entries_found INTEGER NOT NULL CHECK (entries_found >= 0),
        entries_imported INTEGER NOT NULL CHECK (entries_imported >= 0),
        entries_already_present INTEGER NOT NULL CHECK (entries_already_present >= 0),
        failures JSONB NOT NULL DEFAULT '[]'::jsonb,
        imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (file_sha256),
        CHECK (file_sha256 ~ '^[0-9a-f]{64}$')
    )
    """,

    # =======================================================================
    # Translation overrides
    # =======================================================================
    """
    CREATE TABLE translation_overrides (
        locale VARCHAR(16) NOT NULL,
        message_key CHAR(64) NOT NULL,
        msgid TEXT NOT NULL,
        translation TEXT NOT NULL CHECK (length(translation) > 0),
        updated_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (locale, message_key)
    )
    """,

    # =======================================================================
    # Content signals & gazetteer
    # =======================================================================
    """
    CREATE TABLE content_signals (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        detector VARCHAR(40) NOT NULL,
        detector_ver VARCHAR(40) NOT NULL,
        signal_type VARCHAR(40) NOT NULL CHECK (signal_type IN (""" + _SIGNAL_TYPES + """)),
        value TEXT NOT NULL CHECK (length(value) > 0),
        surface TEXT NOT NULL,
        char_start INTEGER NOT NULL CHECK (char_start >= 0),
        char_end INTEGER NOT NULL CHECK (char_end > char_start),
        language VARCHAR(8),
        calendar VARCHAR(16) CHECK (calendar IS NULL OR calendar IN (""" + _CALENDARS + """)),
        resolution VARCHAR(24) NOT NULL CHECK (resolution IN (""" + _SIGNAL_RESOLUTIONS + """)),
        date_from DATE,
        date_to DATE,
        text_orientation VARCHAR(8) CHECK (text_orientation IS NULL OR text_orientation IN (""" + _TEXT_ORIENTATIONS + """)),
        anchor_date DATE,
        evidence JSONB NOT NULL,
        method VARCHAR(64),
        confidence VARCHAR(8) CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low')),
        confidence_basis VARCHAR(40),
        evidence_sentence TEXT,
        sentence_start INTEGER,
        sentence_end INTEGER,
        dedup_key CHAR(64) NOT NULL CHECK (dedup_key ~ '^[0-9a-f]{64}$'),
        detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (dedup_key),
        CHECK ((date_from IS NULL) = (date_to IS NULL)),
        CHECK (date_from IS NULL OR date_from <= date_to),
        CHECK (
            (resolution IN ('absolute', 'approximate', 'document_relative'))
            = (date_from IS NOT NULL)
        ),
        CHECK (resolution <> 'document_relative' OR anchor_date IS NOT NULL),
        CHECK (
            (method IS NULL) = (confidence IS NULL)
            AND (confidence IS NULL) = (confidence_basis IS NULL)
            AND (confidence IS NULL) = (evidence_sentence IS NULL)
            AND (evidence_sentence IS NULL) = (sentence_start IS NULL)
            AND (sentence_start IS NULL) = (sentence_end IS NULL)
        ),
        CHECK (
            sentence_start IS NULL
            OR (sentence_start <= char_start AND char_end <= sentence_end
                AND length(evidence_sentence) = sentence_end - sentence_start)
        )
    )
    """,
    "CREATE INDEX idx_content_signals_hash ON content_signals (hash_id)",
    "CREATE INDEX idx_content_signals_dates ON content_signals (date_from, date_to) WHERE date_from IS NOT NULL",
    "CREATE INDEX idx_content_signals_type ON content_signals (signal_type, detector, detector_ver)",
    "CREATE INDEX idx_content_signals_confidence ON content_signals (signal_type, confidence)",
    "CREATE INDEX idx_content_signals_detector ON content_signals (detector, signal_type, resolution)",

    """
    CREATE TABLE content_signal_runs (
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        detector VARCHAR(40) NOT NULL,
        detector_ver VARCHAR(40) NOT NULL,
        status VARCHAR(16) NOT NULL CHECK (status IN ('complete', 'truncated', 'no_text', 'failed')),
        anchor_date DATE,
        chars_total BIGINT,
        chars_scanned BIGINT,
        signal_count INTEGER NOT NULL DEFAULT 0 CHECK (signal_count >= 0),
        trigger VARCHAR(24) NOT NULL CHECK (trigger IN ('ingestion', 'redetection')),
        job_id TEXT,
        error TEXT,
        ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (hash_id, detector),
        CHECK ((status = 'failed') = (error IS NOT NULL)),
        CHECK (chars_scanned IS NULL OR chars_total IS NULL OR chars_scanned <= chars_total),
        CHECK (status <> 'truncated' OR chars_scanned < chars_total)
    )
    """,
    "CREATE INDEX idx_content_signal_runs_version ON content_signal_runs (detector, detector_ver)",
    "CREATE INDEX idx_content_signal_runs_ran_at ON content_signal_runs (ran_at DESC, hash_id DESC, detector)",

    """
    CREATE TABLE geo_places (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        label VARCHAR(255) NOT NULL CHECK (length(btrim(label)) > 0),
        category_id INTEGER NOT NULL REFERENCES categorys(id) ON DELETE RESTRICT ON UPDATE CASCADE,
        country_codes CHAR(2)[] NOT NULL DEFAULT '{}' CHECK (array_to_string(country_codes, ',') ~ '^([A-Z]{2}(,[A-Z]{2})*)?$'),
        latitude DOUBLE PRECISION NULL,
        longitude DOUBLE PRECISION NULL,
        retired BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK ((latitude IS NULL) = (longitude IS NULL)),
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
        -- Natural uniqueness: label + category + country_codes
        UNIQUE (label, category_id, country_codes)
    )
    """,

    """
    CREATE TABLE geo_place_names (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        place_id INTEGER NOT NULL REFERENCES geo_places(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL CHECK (length(btrim(name)) > 0),
        match_key VARCHAR(255) NOT NULL CHECK (length(match_key) > 0),
        language VARCHAR(8) NOT NULL CHECK (language IN (""" + _GEO_LANGUAGES + """)),
        script CHAR(4) NOT NULL CHECK (
            (language IN ('en', 'hr') AND script = 'Latn')
            OR (language IN ('ar', 'fa') AND script = 'Arab')
            OR (language = 'he' AND script = 'Hebr')
        ),
        name_type VARCHAR(16) NOT NULL CHECK (name_type IN (""" + _GEO_NAME_TYPES + """)),
        UNIQUE (place_id, language, match_key, name_type)
    )
    """,
    "CREATE INDEX idx_geo_place_names_key ON geo_place_names (match_key)",
    "CREATE INDEX idx_geo_place_names_place ON geo_place_names (place_id)",

    # geo_gazetteer_loads removed: fingerprint and counts computed at read time from geo_places/geo_place_names rows.
    # No gazetteer_metadata table; fingerprint computed at read time from live rows.



    """
    CREATE TABLE content_signal_places (
        signal_id BIGINT NOT NULL REFERENCES content_signals(id) ON DELETE CASCADE,
        place_id INTEGER NOT NULL REFERENCES geo_places(id) ON DELETE RESTRICT,
        PRIMARY KEY (signal_id, place_id)
    )
    """,
    "CREATE INDEX idx_content_signal_places_place ON content_signal_places (place_id, signal_id)",

    # =======================================================================
    # path_geo_mentions VIEW - rebuilt over live signals only (C, D)
    # No UNION fallback to path_geo_mentions_m0015
    # =======================================================================
    """
    CREATE VIEW path_geo_mentions AS
    SELECT s.hash_id,
           g.label::varchar(255) AS place_name,
           (SELECT string_agg(c.label, ', ' ORDER BY c.label)
              FROM geo_places c
              JOIN categorys cat ON cat.id = c.category_id
              JOIN words w ON w.id = cat.word_id
             WHERE w.word = 'country'
               AND NOT c.retired
               AND cardinality(c.country_codes) = 1
               AND c.country_codes <@ g.country_codes)::varchar(255) AS country,
           g.latitude, g.longitude,
           count(*)::integer AS mention_count,
           g.id AS place_id,
           'signals'::varchar(16) AS provenance
      FROM content_signals s
      JOIN content_signal_places csp ON csp.signal_id = s.id
      JOIN geo_places g ON g.id = csp.place_id
     WHERE s.detector = 'places'
       AND s.resolution = 'identified'
       AND s.confidence IN ('high', 'medium')
       AND g.latitude IS NOT NULL
     GROUP BY s.hash_id, g.id
    """,

    # =======================================================================
    # path_overview VIEW - one row per path, no join fan-out (D)
    # =======================================================================
    """
    CREATE VIEW path_overview AS
    SELECT p.id AS path_id,
           p.file_name,
           p.file_path,
           p.file_size,
           p.file_type,
           p.file_status,
           p.file_date,
           p.date_creation,
           p.coordinates,
           p.extraction_provenance,
           p.parent_path_id,
           p.hierarchy_path,
           p.processing_status,
           p.status_detail,
           p.attempts,
           p.status_updated_at,
           p.error_message,
           hc.id AS context_id,
           hc.hash_id,
           hc.source_id,
           hc.side_id,
           hc.date_creation AS context_date_creation,
           s.name AS source_name,
           s.job AS source_job,
           s.importance AS source_importance,
           s.country AS source_country,
           s.city AS source_city,
           s.description AS source_description,
           s.ownership AS source_ownership,
           s.access_status AS source_access_status,
           s.entry_date AS source_entry_date,
           si.name AS side_name,
           si.importance AS side_importance,
           si.date_creation AS side_date_creation,
           h.hash AS content_hash
      FROM paths p
      JOIN hash_contexts hc ON hc.id = p.context_id
      JOIN hashs h ON h.id = hc.hash_id
      JOIN sources s ON s.id = hc.source_id
      JOIN sides si ON si.id = hc.side_id
    """,

    # =======================================================================
    # Scenarios (monitoring layer - kept)
    # =======================================================================
    """
    CREATE TABLE scenarios (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL CHECK (length(btrim(name)) > 0),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
        definition JSONB NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$'),
        status VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (status IN (""" + _SCENARIO_STATUSES + """)),
        disabled_reason VARCHAR(64) NULL CHECK ((status = 'disabled') = (disabled_reason IS NOT NULL)),
        activated_dry_run_id BIGINT NULL,
        activated_at TIMESTAMPTZ NULL,
        baselined_version INTEGER NOT NULL DEFAULT 0 CHECK (baselined_version >= 0 AND baselined_version <= version),
        last_evaluated_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX uq_scenarios_owner_name ON scenarios (owner_user_id, lower(name)) WHERE status <> 'archived'",
    "CREATE INDEX idx_scenarios_active ON scenarios (id) WHERE status = 'active'",

    """
    CREATE TABLE scenario_versions (
        scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        definition JSONB NOT NULL,
        definition_fingerprint CHAR(64) NOT NULL CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$'),
        created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (scenario_id, version)
    )
    """,

    """
    CREATE TABLE scenario_evaluations (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
        scenario_version INTEGER NOT NULL,
        kind VARCHAR(16) NOT NULL CHECK (kind IN (""" + _SCENARIO_KINDS + """)),
        trigger VARCHAR(16) NOT NULL CHECK (trigger IN (""" + _SCENARIO_TRIGGERS + """)),
        job_id TEXT NULL,
        requested_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        status VARCHAR(16) NOT NULL CHECK (status IN (""" + _SCENARIO_EVAL_STATUSES + """)),
        owner_role VARCHAR(20) NULL,
        access_scope JSONB NULL,
        criteria_fingerprint CHAR(64) NULL CHECK (criteria_fingerprint IS NULL OR criteria_fingerprint ~ '^[0-9a-f]{64}$'),
        definition_fingerprint CHAR(64) NOT NULL CHECK (definition_fingerprint ~ '^[0-9a-f]{64}$'),
        reference_date DATE NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL,
        counts JSONB NOT NULL DEFAULT '{}'::jsonb,
        report JSONB NULL,
        error TEXT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        finished_at TIMESTAMPTZ NULL,
        CHECK (
            (kind = 'dry_run' AND status IN ('passed', 'invalid', 'failed', 'owner_revoked'))
            OR (kind = 'evaluation' AND status IN ('completed', 'failed', 'owner_revoked', 'skipped_busy', 'not_active'))
        ),
        CHECK (kind = 'evaluation' OR status IN ('failed', 'owner_revoked') OR report IS NOT NULL)
    )
    """,
    "CREATE INDEX idx_scenario_evaluations_scenario ON scenario_evaluations (scenario_id, kind, id DESC)",
    "ALTER TABLE scenarios ADD CONSTRAINT fk_scenarios_activated_dry_run FOREIGN KEY (activated_dry_run_id) REFERENCES scenario_evaluations(id)",

    """
    CREATE TABLE scenario_outcomes (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
        evaluation_id BIGINT NOT NULL REFERENCES scenario_evaluations(id) ON DELETE CASCADE,
        scenario_version INTEGER NOT NULL,
        hash_id INTEGER NOT NULL REFERENCES hashs(id) ON DELETE CASCADE,
        outcomes TEXT[] NOT NULL CHECK (cardinality(outcomes) >= 1),
        matched_cases TEXT[] NOT NULL,
        previous_outcomes TEXT[] NULL,
        delivery VARCHAR(16) NOT NULL CHECK (delivery IN (""" + _SCENARIO_DELIVERIES + """)),
        priority VARCHAR(8) NULL CHECK (priority IS NULL OR priority IN (""" + _SCENARIO_PRIORITIES + """)),
        priority_basis JSONB NULL,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        recorded_at TIMESTAMPTZ NOT NULL,
        CHECK (delivery NOT IN ('notified', 'overflow') OR priority IS NOT NULL)
    )
    """,
    "CREATE INDEX idx_scenario_outcomes_latest ON scenario_outcomes (scenario_id, hash_id, id DESC)",
    "CREATE INDEX idx_scenario_outcomes_evaluation ON scenario_outcomes (evaluation_id)",

    """
    CREATE OR REPLACE FUNCTION scenario_outcomes_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' AND (
                NOT EXISTS (SELECT 1 FROM scenarios WHERE id = OLD.scenario_id)
                OR NOT EXISTS (SELECT 1 FROM scenario_evaluations WHERE id = OLD.evaluation_id)
                OR NOT EXISTS (SELECT 1 FROM hashs WHERE id = OLD.hash_id)) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'scenario_outcomes is append-only (% refused)', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END
    $$
    """,
    "DROP TRIGGER IF EXISTS trg_scenario_outcomes_append_only ON scenario_outcomes",
    "CREATE TRIGGER trg_scenario_outcomes_append_only BEFORE UPDATE OR DELETE ON scenario_outcomes FOR EACH ROW EXECUTE FUNCTION scenario_outcomes_append_only()",

    # =======================================================================
    # Alerts
    # =======================================================================
    """
    CREATE TABLE alerts (
        id SERIAL PRIMARY KEY,
        type VARCHAR(50) NOT NULL CHECK (type IN (""" + _ALERT_TYPES + """)),
        priority VARCHAR(20) NOT NULL CHECK (priority IN (""" + _ALERT_PRIORITIES + """)),
        title VARCHAR(500) NOT NULL,
        message TEXT NOT NULL,
        file_id INTEGER NULL REFERENCES paths(id) ON DELETE CASCADE,
        event_date DATE,
        metadata JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        read BOOLEAN DEFAULT FALSE,
        dismissed BOOLEAN DEFAULT FALSE,
        recipient_user_id INTEGER NULL REFERENCES users(id) ON DELETE CASCADE,
        scenario_id INTEGER NULL REFERENCES scenarios(id) ON DELETE SET NULL,
        scenario_evaluation_id BIGINT NULL REFERENCES scenario_evaluations(id) ON DELETE SET NULL,
        CHECK (scenario_id IS NULL OR recipient_user_id IS NOT NULL)
    )
    """,
    "CREATE INDEX idx_alerts_type ON alerts (type)",
    "CREATE INDEX idx_alerts_priority ON alerts (priority)",
    "CREATE INDEX idx_alerts_event_date ON alerts (event_date)",
    "CREATE INDEX idx_alerts_created_at ON alerts (created_at)",
    "CREATE INDEX idx_alerts_dismissed ON alerts (dismissed)",
    "CREATE INDEX idx_alerts_read ON alerts (read)",
    "CREATE INDEX idx_alerts_file_id ON alerts (file_id)",
    "CREATE INDEX idx_alerts_recipient_dismissed_created ON alerts (recipient_user_id, dismissed, created_at DESC)",
    "CREATE INDEX idx_alerts_type_dismissed_created ON alerts (type, dismissed, created_at DESC)",
    "CREATE INDEX idx_alerts_type_event_date ON alerts (type, event_date)",
    "CREATE INDEX idx_alerts_metadata_hash ON alerts ((metadata->>'hash')) WHERE type = 'similar_files'",

    # =======================================================================
    # Path revisions
    # =======================================================================
    """
    CREATE TABLE path_revisions (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        path_id INTEGER NOT NULL REFERENCES paths(id) ON DELETE CASCADE,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        changed_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        change_kind VARCHAR(16) NOT NULL CHECK (change_kind IN ('modified', 'removed')),
        old_values JSONB NOT NULL,
        new_values JSONB NOT NULL
    )
    """,
    "CREATE INDEX idx_path_revisions_path_id ON path_revisions (path_id, changed_at DESC)",
    "CREATE INDEX idx_path_revisions_changed_at ON path_revisions (changed_at DESC)",

    # =======================================================================
    # User navigation prefs
    # =======================================================================
    """
    CREATE TABLE user_navigation_prefs (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        interface_id VARCHAR(64) NOT NULL,
        hidden BOOLEAN NOT NULL DEFAULT FALSE,
        position INTEGER NULL CHECK (position IS NULL OR position >= 1),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, interface_id)
    )
    """,
    "CREATE INDEX idx_user_navigation_prefs_user ON user_navigation_prefs (user_id)",

    # =======================================================================
    # Schema migrations tracking (for the runner)
    # =======================================================================
    # Created by migration_runner._ensure_tracking_table() before first migration
]


def upgrade(conn) -> None:
    with conn.cursor() as cur:
        for statement in _SQL_STATEMENTS:
            cur.execute(statement)
    # Migration runner records the version in schema_migrations


def downgrade(conn) -> None:
    """Best-effort reversal - drops all objects created by this baseline."""
    with conn.cursor() as cur:
        # Drop in reverse dependency order
        cur.execute("DROP TABLE IF EXISTS user_navigation_prefs")
        cur.execute("DROP TABLE IF EXISTS path_revisions")
        cur.execute("DROP TRIGGER IF EXISTS trg_scenario_outcomes_append_only ON scenario_outcomes")
        cur.execute("DROP FUNCTION IF EXISTS scenario_outcomes_append_only()")
        cur.execute("DROP TABLE IF EXISTS scenario_outcomes")
        cur.execute("ALTER TABLE scenarios DROP CONSTRAINT IF EXISTS fk_scenarios_activated_dry_run")
        cur.execute("DROP TABLE IF EXISTS scenario_evaluations")
        cur.execute("DROP TABLE IF EXISTS scenario_versions")
        cur.execute("DROP TABLE IF EXISTS scenarios")
        cur.execute("DROP VIEW IF EXISTS path_overview")
        cur.execute("DROP VIEW IF EXISTS path_geo_mentions")
        cur.execute("DROP TABLE IF EXISTS content_signal_places")
        cur.execute("DROP TABLE IF EXISTS geo_place_names")
        cur.execute("DROP TABLE IF EXISTS geo_places")
        cur.execute("DROP TABLE IF EXISTS content_signal_runs")
        cur.execute("DROP TABLE IF EXISTS content_signals")
        cur.execute("DROP TABLE IF EXISTS saved_search_imports")
        cur.execute("DROP TABLE IF EXISTS saved_searches")
        cur.execute("DROP TABLE IF EXISTS translation_overrides")
        cur.execute("DROP TABLE IF EXISTS analyst_categorization_log")
        cur.execute("DROP TABLE IF EXISTS analyst_file_categories")
        cur.execute("DROP TABLE IF EXISTS analyst_categories")
        cur.execute("DROP TABLE IF EXISTS job_schedules")
        cur.execute("DROP TABLE IF EXISTS job_events")
        cur.execute("DROP TABLE IF EXISTS jobs")
        cur.execute("DROP TABLE IF EXISTS audit_log")
        cur.execute("DROP TABLE IF EXISTS sessions")
        cur.execute("DROP TABLE IF EXISTS users")
        cur.execute("DROP TABLE IF EXISTS alerts")
        cur.execute("DROP TABLE IF EXISTS keywords_hashs")
        cur.execute("DROP TABLE IF EXISTS keywords")
        cur.execute("DROP TABLE IF EXISTS words_hashs")
        cur.execute("DROP TABLE IF EXISTS titles_content")
        cur.execute("DROP TABLE IF EXISTS contents_raw")
        cur.execute("DROP TABLE IF EXISTS contents")
        cur.execute("DROP TABLE IF EXISTS paths")
        cur.execute("DROP TABLE IF EXISTS hash_contexts")
        cur.execute("DROP TABLE IF EXISTS hashs")
        cur.execute("DROP TABLE IF EXISTS sources")
        cur.execute("DROP TABLE IF EXISTS sides")
        cur.execute("DROP TABLE IF EXISTS words_categorys")
        cur.execute("DROP TABLE IF EXISTS categorys")
        cur.execute("DROP TABLE IF EXISTS punctuation")
        cur.execute("DROP TABLE IF EXISTS words")
        cur.execute("DROP TABLE IF EXISTS schema_migrations")
        cur.execute("DROP EXTENSION IF EXISTS pg_trgm")
        cur.execute("DROP EXTENSION IF EXISTS pgcrypto")