# Interface registry

Every user-facing route is owned by a registered interface, and this
**registry is the single source of truth** for what an interface is. It is
`frozen`: the set of interfaces is defined here and nowhere else, and the
governance tests fail if a page appears that was never registered.

Each entry carries the interface's identity, its domain, the route and
blueprint that serve it, the endpoint that owns it, and its lifecycle status.
It also declares four things that used to live in separate places and drifted
from each other:

- **Registration** — an interface exists only when it is registered. A page
  that is not registered is reported as unowned, not silently served.
- **Navigation** — whether the interface is navigable, and from which status.
  The model asks the lifecycle policy; nothing else decides this.
- **Shortcut** — the keyboard shortcut that opens it, if it has one.
- **Help** — the help topic that documents it.
- **Visibility** — which users may see it, and whether it is enabled at all.

Statuses come from the lifecycle policy. An `ACTIVE` interface is reachable; a
**Deprecated** one still resolves through its legacy id so that stored links
and saved preferences keep working after an upgrade, but it is no longer
offered in navigation and new code must not point at it. Deprecation is a
migration, not a deletion: it is how a rename ships without orphaning
anything a user already saved.

The table below is generated from `core/interfaces` by
`python -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md`; do not edit it
by hand. Counts live in the generated block because a number typed into prose
is a number that will drift.

<!-- BEGIN GENERATED REGISTRY TABLE -->
- Interfaces: **35** (features declared separately: **1**)
- Endpoints owned: **84**
- With a keyboard shortcut: **19**; with a help topic: **33**
- By domain: ADMINISTRATION 3, ANALYZE 4, CLASSIFY 3, DISCOVER 11, INGEST 2, INTERNAL 1, OPERATE 3, REPORT 5, SETTINGS 2, WORK 1
- By status: ACTIVE 34, DEPRECATED 1
- By kind: INTERNAL 1, PAGE 34


### WORK (1)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dashboard` | Dashboard | WORK | `index` | — | any | on | — | work/dashboard | g w | ACTIVE |

### DISCOVER (11)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `file_library` | File Library | DISCOVER | `files.files_list` | `files.file_detail`, `files.file_content_lazy`, `files.file_content_page`, `files.file_full_content`, `files.file_search_all_pages`, `files.file_chart_data`, `files.file_types_page`, `files.file_type_documents`, `files.delete_file`, `files.bulk_delete_files`, `files.bulk_export_files` | any | on | — | discover/file-library | g f | ACTIVE |
| `search` | Search | DISCOVER | `search_page` | `search_advanced`, `search_enhanced_page`, `saved_searches_page`, `search_advanced_api` | any | on | — | discover/search | g s | ACTIVE |
| `sources` | Sources | DISCOVER | `sources_list` | `source_add`, `source_detail`, `source_edit`, `source_categories_keywords` | any | on | — | discover/sources | — | ACTIVE |
| `sides` | Sides | DISCOVER | `sides_list` | `side_add`, `side_detail`, `side_edit`, `side_categories_keywords` | any | on | — | discover/sides | — | ACTIVE |
| `keywords` | Keywords | DISCOVER | `keywords_list` | `keyword_detail`, `keywords_add`, `keyword_documents` | any | on | — | discover/keywords | — | ACTIVE |
| `words` | Words | DISCOVER | `words_list` | `word_detail`, `words_add` | any | on | — | discover/words | — | ACTIVE |
| `categories` | Categories | DISCOVER | `categories_list` | `category_words`, `category_add`, `category_documents`, `categories_tree_page`, `category_names` | any | on | — | discover/categories | — | ACTIVE |
| `email_words` | Email Words | DISCOVER | `email_words` | — | any | on | — | discover/email-words | — | ACTIVE |
| `notifications` | Notifications | DISCOVER | `notifications_page` | — | any | on | — | discover/notifications | g n | ACTIVE |
| `signal_horizon` | Horizon & Signal Explorer | DISCOVER | `signals_page` | — | any | on | — | discover/signals | g h | ACTIVE |
| `monitoring` | Monitoring | DISCOVER | `monitoring_page` | — | any | on | `signal_horizon`, `notifications` | discover/monitoring | g m | ACTIVE |

### INGEST (2)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `input_ingestion` | Input / Ingestion | INGEST | `operations_input_page` | `files.upload_page`, `files.get_active_tasks`, `files.upload_progress`, `files.api_cancel_task`, `files.pause_task`, `files.resume_task`, `ingestion_batch_print` | any | on | — | ingest/input | g i | ACTIVE |
| `import_center` | Import Center | INGEST | `operations_import_page` | — | any | on | — | ingest/import-center | — | ACTIVE |

### ANALYZE (4)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `archives` | File Management and Analysis | ANALYZE | `archives_page` | — | any | on | `file_library` | analyze/archives | g a | ACTIVE |
| `path_analysis` | Path Analysis | ANALYZE | `path_analysis_page` | — | any | on | `file_library` | analyze/path-analysis | g p | ACTIVE |
| `map_viewer` | Map Viewer | ANALYZE | `map_viewer_page` | — | any | on | `archives`, `file_library` | analyze/map-viewer | g g | ACTIVE |
| `batch_analysis` | Batch Analysis | ANALYZE | `analysis_batch` | `analysis_batch_process` | any | on | `file_library` | analyze/batch | g b | ACTIVE |

### CLASSIFY (3)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `lexis_cosmos` | LEXIS Cosmos | CLASSIFY | `lexis_cosmos_page` | — | any | on | — | classify/lexis-cosmos | g x | ACTIVE |
| `analyst_categorization` | Analyst Categories | CLASSIFY | `analyst_categorization_page` | — | any | on | `file_library` | classify/analyst-categories | — | ACTIVE |
| `classification` | Classification | CLASSIFY | `file_classification_page` | — | any | on | `file_library`, `words` | classify/classification | — | ACTIVE |

### REPORT (5)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `comprehensive_dashboard` | Comprehensive Dashboard | REPORT | `comprehensive_dashboard` | — | any | on | `file_library` | report/detailed-dashboard | — | ACTIVE |
| `reports` | Reports | REPORT | `reports_page` | `entry_dossier_print` | any | on | `search`, `jobs` | report/reports | g r | ACTIVE |
| `reports_dashboard` | Reports Dashboard | REPORT | `reports_dashboard_page` | — | any | on | `reports`, `jobs`, `schedules` | report/dashboard | — | ACTIVE |
| `schedules` | Schedules | REPORT | `schedules_page` | — | any | on | `reports`, `jobs`, `monitoring` | report/schedules | — | ACTIVE |
| `charts_dashboard` | Charts Dashboard | REPORT | `charts_dashboard` | — | any | on | `file_library` | report/charts | — | ACTIVE |

### OPERATE (3)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `detection` | Detection | OPERATE | `detection_page` | — | admin | on | `signal_horizon`, `jobs` | operate/detection | g d | ACTIVE |
| `jobs` | Jobs | OPERATE | `operations_jobs_page` | `operations_job_detail_page` | any | on | — | operate/jobs | g j | ACTIVE |
| `import_export_console` | Import/Export | OPERATE | `import_export_page` | — | admin | on | — | — | — | DEPRECATED |

### ADMINISTRATION (3)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `retention` | Retention | ADMINISTRATION | `retention_page` | — | any | on | `jobs`, `reports` | operate/retention | — | ACTIVE |
| `users` | User Management | ADMINISTRATION | `users_page` | — | admin | on | — | administration/users | g u | ACTIVE |
| `audit_log` | Audit Log | ADMINISTRATION | `audit_page` | — | admin | on | — | administration/audit | g l | ACTIVE |

### SETTINGS (2)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `settings` | Settings | SETTINGS | `settings_page_direct` | `settings_api.settings_page` | admin | on | — | settings/overview | g , | ACTIVE |
| `translation_manager` | Translation Management | SETTINGS | `translations.translation_management_page` | — | admin | on | — | settings/translations | g t | ACTIVE |

### INTERNAL (1)

| ID | Name | Domain | Route | Aliases | Role | Default | Depends on | Help | Shortcut | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `concurrency_monitor` | Concurrency Monitor | INTERNAL | `concurrency.dashboard` | `concurrency.get_metrics`, `concurrency.get_threads`, `concurrency.get_processes`, `concurrency.get_pools`, `concurrency.get_async_tasks` | admin | on | — | — | — | ACTIVE |

### Features (not interfaces)

| ID | Name | Description | Default |
| --- | --- | --- | --- |
| `page_tips` | Page Tips & Documentation | Explanatory tips at the top of each page, describing the elements on it, how to use them and how to add data. | on |
<!-- END GENERATED REGISTRY TABLE -->
