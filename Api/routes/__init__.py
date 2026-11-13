"""
Routes module - registers all routes with the Flask app
"""

def register_all_routes(app, babel_instance=None):
    """Register all routes with the Flask app"""
    # Import and register route modules
    from . import common, dashboard, categories, search
    from . import category_tree
    from . import analyst_categories
    from . import analytics, analysis, archives, keywords, words, sources, sides, api, performance, notifications, notifications_page
    from . import preview, import_export, setup
    from . import operations_api, operations_pages
    from . import signals
    from . import places
    from . import scenarios
    from . import schedules
    from . import retention
    from . import preferences
    from . import audit
    from . import lexis_cosmos
    from . import reports
    from . import translation_api
    from . import taxonomy_api
    # Import new settings routes (replaces old settings.py) - optional
    try:
        from settings.routes import register_settings_routes
        has_settings_routes = True
    except ImportError:
        # Settings routes module doesn't exist - create a no-op function
        def register_settings_routes(app):
            """No-op function when settings routes module is not available"""
            pass
        has_settings_routes = False
    
    # Register routes
    if babel_instance:
        common.register_common_routes(app, babel_instance)
    else:
        common.register_common_routes(app, None)
    dashboard.register_dashboard_routes(app)
    categories.register_categories_routes(app)
    # Depth-aware hierarchy surface: tree read, create/adopt/move/delete.
    category_tree.register_category_tree_routes(app)
    search.register_search_routes(app)
    # Analyst-driven manual categorization (fully separate from the smart
    # classification taxonomy - see Api/routes/analyst_categories.py)
    analyst_categories.register_analyst_category_routes(app)
    # Register new settings routes (includes both API and page routes)
    register_settings_routes(app)
    analytics.register_analytics_routes(app)
    analysis.register_analysis_routes(app)
    archives.register_archives_routes(app)
    keywords.register_keywords_routes(app)
    words.register_words_routes(app)
    sources.register_sources_routes(app)
    sides.register_sides_routes(app)
    api.register_api_routes(app)
    performance.register_performance_routes(app)
    notifications.register_notification_routes(app)
    notifications_page.register_notification_page_routes(app)
    preview.register_preview_routes(app)
    import_export.register_import_export_routes(app)
    # Phase 1 content signals (read + admin re-detection job)
    signals.register_signal_routes(app)
    # Phase 2 gazetteer (read-only)
    places.register_place_routes(app)
    # Step 12 scenarios (dry-run + evaluation jobs)
    scenarios.register_scenario_routes(app)
    # Step 20 schedules (scenario_evaluation / retention
    # on an interval, fired through the JobManager by the scheduler)
    schedules.register_schedule_routes(app)
    # Step 21 retention (policy-driven pruning, administrators, audited)
    retention.register_retention_routes(app)
    # Per-user navigation preferences (sidebar order / visibility, audited)
    preferences.register_preference_routes(app)
    # Audit log viewer (administrators, read-only)
    audit.register_audit_routes(app)
    # LEXIS Cosmos: the taxonomy galaxy, its search, viewer, exports and
    # management API (Api/services/lexis_queries.py holds every statement).
    lexis_cosmos.register_lexis_cosmos_routes(app)
    # Reports: the processing report, its dashboard and the printable dossier
    # (registry ``reports`` and ``reports_dashboard``), all read from one
    # statement over paths/hash_contexts/sources/sides/classifications.
    reports.register_report_routes(app)
    # Manual translation (POST only; nothing here runs unattended)
    translation_api.register_translation_routes(app)
    # Taxonomy status and the workbook's one-button reconciliation: the same
    # service the command line and the taxonomy_sync job use.
    taxonomy_api.register_taxonomy_routes(app)
    # Unified operations API + pages (Input / Import Center / Job Center)
    app.register_blueprint(operations_api.operations_bp)
    operations_pages.register_operations_pages(app)
    # Setup routes are registered separately in app.py BEFORE other routes
    # setup.register_setup_routes(app)  # Moved to app.py to register first


