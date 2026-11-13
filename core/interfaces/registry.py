"""The interface registry: the one description of what SYLTHARAE offers.

This module replaces ``INTERFACE_METADATA`` - the untyped dictionary that used
to describe interface switches inside the settings adapter, listing endpoints
that did not exist, a page-less switch and two entries for one page. The
registry is a declaration: it says what exists, what it is for, who should see
it, what it needs and where it lives. It owns no logic beyond looking itself up
(``validation.py`` checks it, ``settings/interface_state.py`` applies stored
state to it, the application reads it).

Three rules keep it honest, and each is enforced by a test rather than a
convention:

* **It refers, it does not duplicate.** Routes are endpoint *names* verified
  against the live URL map; settings are ``category.key`` *references*
  verified against the settings model; roles are names verified against
  ``core/security``. The registry never holds a second copy of any of them.
* **It never authorises.** ``required_role`` decides what is *shown*.
  ``core/security`` decides what is *allowed*, and it is untouched by this
  module (see ``docs/INTERFACE_REGISTRY.md``).
* **Defaults live here and nowhere else.** The old code had two: a hard-coded
  dictionary that enabled everything on reset, and a getter that returned
  False for ids it did not know. ``default_enabled`` is the single answer.

The endpoint inventory in ``inventory.py`` is the factual list of what the
application actually serves; a coverage test requires every user-facing page in
it to be owned by exactly one entry below.
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Optional, Tuple

from .domains import Domain
from .model import (
    Feature,
    Interface,
    InterfaceKind,
    InterfaceStatus,
)

# ---------------------------------------------------------------------------
# Retired ids and renamed ids
# ---------------------------------------------------------------------------

#: Ids that no longer name anything. Their stored values are preserved in the
#: settings file (never deleted - an operator's file is theirs) but they cannot
#: switch anything on. ``file_upload`` was the "core" twin of the ingestion
#: page: it pointed at the same endpoint and gated no route.
RETIRED_INTERFACE_IDS: Tuple[str, ...] = (
    "file_upload",
)

#: Old id -> canonical id. Two reasons appear here:
#:
#: * **renames** - ``file_analysis`` displayed as the product name rather than
#:   the page it opens, and ``upload_files`` described an upload page that is
#:   now the ingestion surface. The ids change; stored choices follow them.
#: * **merges** - ``file_browser`` was the same page as ``file_library``, and
#:   ``advanced_search`` was one of the modes of ``search``. One interface, two
#:   settings keys.
#:
#: ``analytics`` pointed at an endpoint that does not exist (checked against
#: the live URL map), so it gated nothing; the reporting overview is what it
#: described, and a stored choice about analytics is honoured there.
LEGACY_INTERFACE_IDS: Dict[str, str] = {
    "file_analysis": "archives",
    "upload_files": "input_ingestion",
    "file_upload": "input_ingestion",
    "file_browser": "file_library",
    "advanced_search": "search",
    "analytics": "comprehensive_dashboard",
}


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def _if(
    interface_id: str,
    name: str,
    description: str,
    domain: Domain,
    route: Optional[str],
    icon: str,
    *,
    aliases: Tuple[str, ...] = (),
    default_enabled: bool = True,
    required_role: Optional[str] = None,
    dependencies: Tuple[str, ...] = (),
    settings: Tuple[str, ...] = (),
    feature_flag: Optional[str] = None,
    help_topic: Optional[str] = None,
    keyboard_shortcut: Optional[str] = None,
    kind: InterfaceKind = InterfaceKind.PAGE,
    status: InterfaceStatus = InterfaceStatus.ACTIVE,
) -> Interface:
    """Terse constructor so the table below stays readable as a table."""
    return Interface(
        interface_id=interface_id,
        name=name,
        description=description,
        domain=domain,
        route=route,
        icon=icon,
        aliases=aliases,
        default_enabled=default_enabled,
        required_role=required_role,
        dependencies=dependencies,
        settings=settings,
        feature_flag=feature_flag,
        help_topic=help_topic,
        keyboard_shortcut=keyboard_shortcut,
        kind=kind,
        status=status,
    )


REGISTRY: Tuple[Interface, ...] = (
    # -- WORK -------------------------------------------------------------
    _if(
        "dashboard", "Dashboard",
        "The working overview: what is stored, what is running, what needs attention.",
        Domain.WORK, "index", "bi-speedometer2",
        help_topic="work/dashboard", keyboard_shortcut="g w",
    ),

    # -- DISCOVER ---------------------------------------------------------
    _if(
        "file_library", "File Library",
        "Every stored object, with its content, metadata and lineage. "
        "The file detail, content and full-content pages are part of this interface.",
        Domain.DISCOVER, "files.files_list", "bi-folder2-open",
        aliases=("files.file_detail", "files.file_content_lazy",
                 "files.file_content_page", "files.file_full_content",
                 "files.file_search_all_pages", "files.file_chart_data",
                 "files.file_types_page", "files.file_type_documents",
                 "files.delete_file",
                 "files.bulk_delete_files", "files.bulk_export_files"),
        help_topic="discover/file-library", keyboard_shortcut="g f",
    ),
    _if(
        "search", "Search",
        "Search across stored content, with the advanced and enhanced builders "
        "and saved searches as modes of the same interface.",
        Domain.DISCOVER, "search_page", "bi-search",
        aliases=("search_advanced", "search_enhanced_page", "saved_searches_page",
                 "search_advanced_api"),
        settings=("search.max_results", "search.highlight_results",
                  "search.enable_saved_searches"),
        help_topic="discover/search", keyboard_shortcut="g s",
    ),
    _if(
        "sources", "Sources",
        "Where material came from, and everything recorded about that origin.",
        Domain.DISCOVER, "sources_list", "bi-building",
        aliases=("source_add", "source_detail", "source_edit",
                 "source_categories_keywords"),
        help_topic="discover/sources",
    ),
    _if(
        "sides", "Sides",
        "The parties the material belongs to.",
        Domain.DISCOVER, "sides_list", "bi-diagram-3",
        aliases=("side_add", "side_detail", "side_edit", "side_categories_keywords"),
        help_topic="discover/sides",
    ),
    _if(
        "keywords", "Keywords",
        "Keywords identified in the material.",
        Domain.DISCOVER, "keywords_list", "bi-key",
        aliases=("keyword_detail", "keywords_add", "keyword_documents"),
        help_topic="discover/keywords",
    ),
    _if(
        "words", "Words",
        "The word dictionary used by extraction and classification.",
        Domain.DISCOVER, "words_list", "bi-book",
        aliases=("word_detail", "words_add"),
        help_topic="discover/words",
    ),
    _if(
        "categories", "Categories",
        "Categories and the words assigned to them.",
        Domain.DISCOVER, "categories_list", "bi-tags",
        # ``categories_tree_page`` is the hierarchy view of the same interface:
        # it reads the same taxonomy and writes through the same rules, so
        # switching Categories off must hide it too (an unregistered page
        # endpoint is refused outright by the request gate).
        aliases=("category_words", "category_add", "category_documents",
                 "categories_tree_page", "category_names"),
        help_topic="discover/categories",
    ),
    _if(
        "email_words", "Email Words",
        "Words and phrases that identify e-mail material.",
        Domain.DISCOVER, "email_words", "bi-envelope-at",
        help_topic="discover/email-words",
    ),
    _if(
        "lexis_cosmos", "LEXIS Cosmos",
        "The taxonomy galaxy: root, word and keyword lenses, categories, "
        "subcategories and terms, with the file stack under each term, the "
        "scoped search sidebar, the document viewer and the taxonomy manager.",
        Domain.CLASSIFY, "lexis_cosmos_page", "bi-diagram-2",
        # No ``dependencies``: the galaxy reads the taxonomy tables through its
        # own statements (Api/services/lexis_queries.py), so switching the
        # Categories or Keywords *pages* off leaves it complete. Declaring them
        # would refuse an operator the right to hide those pages while LEXIS
        # is on - a coupling that does not exist in the request path.
        help_topic="classify/lexis-cosmos", keyboard_shortcut="g x",
    ),
    _if(
        "analyst_categorization", "Analyst Categories",
        "The analyst's own layer: categories and assignments made by hand, "
        "kept separate from automatic classification.",
        Domain.CLASSIFY, "analyst_categorization_page", "bi-person-check",
        dependencies=("file_library",),
        help_topic="classify/analyst-categories",
    ),
    _if(
        "notifications", "Notifications",
        "What the system has to tell the operator, including similar-file and "
        "future-date findings.",
        Domain.DISCOVER, "notifications_page", "bi-bell",
        settings=("notifications.enabled", "notifications.similar_files_enabled",
                  "notifications.auto_analyze_files"),
        help_topic="discover/notifications", keyboard_shortcut="g n",
    ),
    _if(
        "signal_horizon", "Horizon & Signal Explorer",
        "Dated references on a forward-looking horizon (overdue, next 7/30/90 "
        "days, later) and every stored signal - dates and places - with its "
        "evidence sentence, confidence, method and detector version.",
        Domain.DISCOVER, "signals_page", "bi-calendar-range",
        help_topic="discover/signals", keyboard_shortcut="g h",
    ),
    _if(
        "monitoring", "Monitoring",
        "Scenarios (cases over signal and document conditions, one outcome per "
        "document, mandatory dry-run before activation, append-only outcome "
        "history) and monitoring rules. Notifications reach the owner only.",
        Domain.DISCOVER, "monitoring_page", "bi-diagram-3",
        dependencies=("signal_horizon", "notifications"),
        help_topic="discover/monitoring", keyboard_shortcut="g m",
    ),

    # -- INGEST -----------------------------------------------------------
    _if(
        "input_ingestion", "Input / Ingestion",
        "Bring material in - one file, a folder, or a path on the server - and "
        "follow the jobs it creates. Each finished intake has a dedicated "
        "printable batch report. The old /upload page redirects here.",
        Domain.INGEST, "operations_input_page", "bi-folder-plus",
        aliases=("files.upload_page", "files.get_active_tasks",
                 "files.upload_progress", "files.api_cancel_task",
                 "files.pause_task", "files.resume_task",
                 "ingestion_batch_print"),
        settings=("processing.auto_process_uploads", "processing.extract_archives"),
        help_topic="ingest/input", keyboard_shortcut="g i",
    ),
    _if(
        "import_center", "Import Center",
        "Structured imports: validate, preview and resolve before anything is stored.",
        Domain.INGEST, "operations_import_page", "bi-box-arrow-in-down",
        settings=("processing.extract_attachments",),
        help_topic="ingest/import-center",
    ),

    # -- ANALYZE ----------------------------------------------------------
    _if(
        "archives", "File Management and Analysis",
        "The archive of everything stored, organised by source, side and hash, "
        "and the entry point to the analysis workspaces.",
        Domain.ANALYZE, "archives_page", "bi-archive",
        dependencies=("file_library",),
        help_topic="analyze/archives", keyboard_shortcut="g a",
    ),
    _if(
        "path_analysis", "Path Analysis",
        "How material is arranged on disk: directories, trees and their contents.",
        Domain.ANALYZE, "path_analysis_page", "bi-folder",
        dependencies=("file_library",),
        help_topic="analyze/path-analysis", keyboard_shortcut="g p",
    ),
    _if(
        "map_viewer", "Map Viewer",
        "Browse geotagged files on offline or online basemaps; download map packages "
        "into the project for use without network access.",
        Domain.ANALYZE, "map_viewer_page", "bi-map",
        dependencies=("archives", "file_library"),
        # "g m" belongs to Monitoring, which was declared first: two interfaces
        # cannot share a sequence (validate_registry rejects it, and a user
        # pressing it would land on whichever the palette listed first). The
        # map takes the domain word - geography - rather than its page name.
        help_topic="analyze/map-viewer", keyboard_shortcut="g g",
    ),
    _if(
        "batch_analysis", "Batch Analysis",
        "Process many objects at once, with the outcome of every run recorded "
        "as a job.",
        Domain.ANALYZE, "analysis_batch", "bi-lightning-charge",
        dependencies=("file_library",),
        aliases=("analysis_batch_process",),
        settings=("processing.use_threading", "processing.max_workers",
                  "processing.auto_process_uploads"),
        help_topic="analyze/batch", keyboard_shortcut="g b",
    ),
    _if(
        "classification", "Classification",
        "What the material is: classification outcomes across the stored objects.",
        Domain.CLASSIFY, "file_classification_page", "bi-file-earmark-text",
        dependencies=("file_library", "words"),
        help_topic="classify/classification",
    ),

    # -- REPORT -----------------------------------------------------------
    _if(
        "comprehensive_dashboard", "Comprehensive Dashboard",
        "Detailed reporting over what is stored and what has been processed.",
        Domain.REPORT, "comprehensive_dashboard", "bi-graph-up",
        dependencies=("file_library",),
        help_topic="report/detailed-dashboard",
    ),
    _if(
        "reports", "Reports",
        "Registered, versioned reports: choose one, give its parameters (or a "
        "saved search), run it as a background job and read the result - every "
        "dataset from one database snapshot, with counts and any shortening "
        "stated. The Full Entry Report and its live printable dossier belong "
        "here.",
        Domain.REPORT, "reports_page", "bi-file-earmark-bar-graph",
        aliases=("entry_dossier_print",),
        dependencies=("search", "jobs"),
        help_topic="report/reports", keyboard_shortcut="g r",
    ),
    _if(
        "reports_dashboard", "Reports Dashboard",
        "The reporting system at a glance: the report versions your role can "
        "read, the runs by measured outcome (yours, or everyone's for an "
        "administrator), the artifacts made from them, and the schedules "
        "that fire next. Every number is counted from the stored runs - "
        "never assumed.",
        Domain.REPORT, "reports_dashboard_page", "bi-speedometer2",
        dependencies=("reports", "jobs", "schedules"),
        help_topic="report/dashboard",
    ),
    _if(
        "retention", "Retention",
        "The pruning policies for everything that grows: finished jobs, the "
        "rule ledger and evaluation log, scenario outcomes, notifications, "
        "report runs and artifacts, the revision log and the audit log - "
        "each with an explicit keep-forever default where pruning would "
        "destroy evidence. Every applied prune is audited with its count.",
        Domain.ADMINISTRATION, "retention_page", "bi-hourglass-split",
        dependencies=("jobs", "reports"),
        help_topic="operate/retention",
    ),
    _if(
        "schedules", "Schedules",
        "Run a registered report, or evaluate every active rule / scenario, on "
        "an interval. Each fire re-checks the owner's role and the payload "
        "before anything runs; a schedule that can no longer run is disabled "
        "with the reason stated.",
        Domain.REPORT, "schedules_page", "bi-clock-history",
        dependencies=("reports", "jobs", "monitoring"),
        help_topic="report/schedules",
    ),
    _if(
        "charts_dashboard", "Charts Dashboard",
        "The same stored material presented as charts and timelines.",
        Domain.REPORT, "charts_dashboard", "bi-bar-chart",
        dependencies=("file_library",),
        help_topic="report/charts",
    ),

    # -- OPERATE ----------------------------------------------------------
    _if(
        "detection", "Detection",
        "Which detector version analysed which content, with what outcome: "
        "coverage per detector, content never analysed or analysed by an older "
        "version, failed runs with their error, and re-detection as a "
        "background job.",
        Domain.OPERATE, "detection_page", "bi-radar",
        dependencies=("signal_horizon", "jobs"),
        required_role="admin",
        help_topic="operate/detection", keyboard_shortcut="g d",
    ),
    _if(
        "jobs", "Jobs",
        "Every long-running operation: what it is doing, what it did, and the "
        "event stream behind it. This is the system of record for processing.",
        Domain.OPERATE, "operations_jobs_page", "bi-list-check",
        aliases=("operations_job_detail_page",),
        help_topic="operate/jobs", keyboard_shortcut="g j",
    ),
    _if(
        "import_export_console", "Import/Export",
        "The earlier one-page console for batch import, database backups and "
        "settings exchange. Superseded by the Import Center and Settings; kept "
        "working until everything it does has moved.",
        Domain.OPERATE, "import_export_page", "bi-arrow-down-up",
        required_role="admin",
        settings=("storage.enable_storage",),
        status=InterfaceStatus.DEPRECATED,
    ),

    # -- ADMINISTRATION ---------------------------------------------------
    _if(
        "users", "User Management",
        "People who may sign in, the roles they hold and the sessions they are using.",
        Domain.ADMINISTRATION, "users_page", "bi-people",
        required_role="admin",
        help_topic="administration/users", keyboard_shortcut="g u",
    ),
    _if(
        "audit_log", "Audit Log",
        "Who did what, to what, when and from where: every file that left the "
        "system (DATA_EXPORTED, with its checksum), report runs and files, rule "
        "and scenario changes, sign-ins and user administration. Read-only.",
        Domain.ADMINISTRATION, "audit_page", "bi-journal-check",
        required_role="admin",
        help_topic="administration/audit", keyboard_shortcut="g l",
    ),

    # -- SETTINGS ---------------------------------------------------------
    _if(
        "settings", "Settings",
        "Configuration of the product: system, display, processing, storage, "
        "database, theme and interfaces.",
        Domain.SETTINGS, "settings_page_direct", "bi-gear",
        aliases=("settings_api.settings_page",),
        required_role="admin",
        settings=("system.language", "system.theme", "processing.compute_mode"),
        help_topic="settings/overview", keyboard_shortcut="g ,",
    ),
    _if(
        "translation_manager", "Translation Management",
        "Review and manage localized strings across every screen.",
        Domain.SETTINGS, "translations.translation_management_page", "bi-translate",
        required_role="admin",
        help_topic="settings/translations",
        keyboard_shortcut="g t",
    ),

    # -- INTERNAL ---------------------------------------------------------
    _if(
        "concurrency_monitor", "Concurrency Monitor",
        "Runtime diagnostics: worker threads, resource coordination and pool state.",
        Domain.INTERNAL, "concurrency.dashboard", "bi-cpu",
        aliases=("concurrency.get_metrics", "concurrency.get_threads",
                 "concurrency.get_processes", "concurrency.get_pools",
                 "concurrency.get_async_tasks"),
        required_role="admin",
        settings=("processing.max_workers", "processing.enable_monitoring"),
        kind=InterfaceKind.INTERNAL,
    ),
)


#: Behaviour switches that are not pages. ``page_tips`` has no page and no
#: navigation entry; it turns explanatory tips on and off across the product,
#: so it is declared as a feature rather than dressed up as an interface.
FEATURES: Tuple[Feature, ...] = (
    Feature(
        feature_id="page_tips",
        name="Page Tips & Documentation",
        description="Explanatory tips at the top of each page, describing the "
                    "elements on it, how to use them and how to add data.",
        default_enabled=True,
    ),
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

_BY_ID: Dict[str, Interface] = {i.interface_id: i for i in REGISTRY}
_FEATURE_BY_ID: Dict[str, Feature] = {f.feature_id: f for f in FEATURES}


def get_all_interfaces() -> Tuple[Interface, ...]:
    """Every interface, in declaration order."""
    return REGISTRY


def get_interface(interface_id: str) -> Optional[Interface]:
    """The interface with this id, or ``None`` if nothing declares it."""
    return _BY_ID.get(interface_id)


def is_registered(interface_id: str) -> bool:
    """Is this id declared as a live interface or feature?

    False is the answer for anything unknown - a typo, a retired id, or a
    setting left behind by an older version. Nothing may be switched on by an
    id the product does not declare.
    """
    interface = _BY_ID.get(interface_id)
    if interface is not None and interface.live:
        return True
    feature = _FEATURE_BY_ID.get(interface_id)
    return feature is not None and feature.live


def get_feature(feature_id: str) -> Optional[Feature]:
    return _FEATURE_BY_ID.get(feature_id)


def get_features() -> Tuple[Feature, ...]:
    return FEATURES


def get_interfaces_by_domain() -> Dict[str, List[Interface]]:
    """Interfaces grouped by domain, in ``DOMAIN_ORDER``."""
    grouped: Dict[str, List[Interface]] = {}
    for interface in REGISTRY:
        grouped.setdefault(str(interface.domain), []).append(interface)
    return grouped


def resolve_interface_id(value: str) -> Optional[str]:
    """Map a possibly-legacy id onto the canonical one.

    Used by the migration path: a settings file may still name
    ``file_analysis`` or ``upload_files``, and the value it holds has to reach
    the interface that replaced it. Retired ids resolve to ``None`` unless they
    were merged into something (``file_upload`` was), because a retired id must
    never switch anything on by itself.
    """
    if value in _BY_ID or value in _FEATURE_BY_ID:
        return value if is_registered(value) else None
    target = LEGACY_INTERFACE_IDS.get(value)
    if target is not None and is_registered(target):
        return target
    return None


def get_interface_for_endpoint(endpoint: str) -> Optional[Interface]:
    """Which interface owns this Flask endpoint?

    ``None`` means no interface declares it: either it is an infrastructure
    endpoint (classified in ``inventory.py``) or it is an unregistered page -
    which the coverage test treats as a defect and the request gate treats as
    unavailable.
    """
    if not endpoint:
        return None
    for interface in REGISTRY:
        if endpoint in interface.endpoints:
            return interface
    return None


def endpoints_to_interfaces() -> Dict[str, str]:
    """Every owned endpoint -> its interface id."""
    mapping: Dict[str, str] = {}
    for interface in REGISTRY:
        for endpoint in interface.endpoints:
            mapping[endpoint] = interface.interface_id
    return mapping


def get_dependencies(interface_id: str) -> Tuple[str, ...]:
    """What this interface needs in order to be usable."""
    interface = get_interface(interface_id)
    return interface.dependencies if interface else ()


def get_dependents(interface_id: str) -> Tuple[Interface, ...]:
    """Which interfaces declare this one as a dependency."""
    return tuple(i for i in REGISTRY if interface_id in i.dependencies)


def default_enabled(interface_id: str) -> Optional[bool]:
    """The registry's default for an id - the single definition of it."""
    interface = _BY_ID.get(interface_id)
    if interface is not None:
        return interface.default_enabled
    feature = _FEATURE_BY_ID.get(interface_id)
    if feature is not None:
        return feature.default_enabled
    return None


def defaults() -> Dict[str, bool]:
    """``{id: default}`` for every live interface and feature."""
    values: Dict[str, bool] = {
        i.interface_id: i.default_enabled for i in REGISTRY if i.live
    }
    values.update({f.feature_id: f.default_enabled for f in FEATURES if f.live})
    return values


def interface_ids() -> Tuple[str, ...]:
    return tuple(i.interface_id for i in REGISTRY)


def navigable_interfaces() -> Tuple[Interface, ...]:
    """Interfaces that could appear in navigation, registry order."""
    return tuple(i for i in REGISTRY if i.navigable)


def summary() -> Dict[str, object]:
    """What the product currently considers part of itself.

    Generated from the registry (never a hand-maintained count), for the
    ``/api/interfaces`` view and the registry document.
    """
    by_domain = {domain: len(items) for domain, items in get_interfaces_by_domain().items()}
    by_status: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for interface in REGISTRY:
        by_status[str(interface.status)] = by_status.get(str(interface.status), 0) + 1
        by_kind[str(interface.kind)] = by_kind.get(str(interface.kind), 0) + 1
    return {
        "interfaces": len(REGISTRY),
        "features": len(FEATURES),
        "by_domain": by_domain,
        "by_status": by_status,
        "by_kind": by_kind,
        "endpoints_owned": len(endpoints_to_interfaces()),
        "with_shortcut": sum(1 for i in REGISTRY if i.keyboard_shortcut),
        "with_help": sum(1 for i in REGISTRY if i.help_topic),
    }


def iter_endpoints() -> Iterator[str]:
    for interface in REGISTRY:
        yield from interface.endpoints
