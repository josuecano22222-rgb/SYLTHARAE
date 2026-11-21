# Registry evidence

The evidence that every user-facing route is owned by a registered interface,
in two halves. Both blocks are generated, so neither can drift from the code:
regenerate with `python3 -m core.interfaces.evidence --write docs/REGISTRY_EVIDENCE.md`.

## Part A - the declared product

What the registry says exists.

<!-- BEGIN GENERATED REGISTRY EVIDENCE -->
### 1. Registry inventory

- Interfaces declared: **35**
- Cross-cutting features (not interfaces): **1**
- Endpoints owned (canonical routes + aliases): **84**
- With a keyboard shortcut: **19**
- With a help topic: **33**
- Declared domain vocabulary: **12**
- Domains currently containing interfaces: **10**
- Declared domains holding no interface yet: **2**

Domains are **declared** in `core/interfaces/domains.py`; a declared domain may hold no interface yet. The two numbers are different, and both are reported:

| Domain | Interfaces | Status |
| --- | --- | --- |
| WORK | 1 | in use |
| DISCOVER | 11 | in use |
| INGEST | 2 | in use |
| PROCESS | 0 | declared, empty |
| ANALYZE | 4 | in use |
| CLASSIFY | 3 | in use |
| REPORT | 5 | in use |
| OPERATE | 3 | in use |
| ADMINISTRATION | 3 | in use |
| SETTINGS | 2 | in use |
| SECURITY | 0 | declared, empty |
| INTERNAL | 1 | in use |

Declared aliases: **49**, across **13** interfaces. An alias is an endpoint the interface owns but does not navigate to; aliases never become navigation entries.

| Interface | Aliases |
| --- | --- |
| `batch_analysis` | `analysis_batch_process` |
| `categories` | `categories_tree_page`, `category_add`, `category_documents`, `category_names`, `category_words` |
| `concurrency_monitor` | `concurrency.get_async_tasks`, `concurrency.get_metrics`, `concurrency.get_pools`, `concurrency.get_processes`, `concurrency.get_threads` |
| `file_library` | `files.bulk_delete_files`, `files.bulk_export_files`, `files.delete_file`, `files.file_chart_data`, `files.file_content_lazy`, `files.file_content_page`, `files.file_detail`, `files.file_full_content`, `files.file_search_all_pages`, `files.file_type_documents`, `files.file_types_page` |
| `input_ingestion` | `files.api_cancel_task`, `files.get_active_tasks`, `files.pause_task`, `files.resume_task`, `files.upload_page`, `files.upload_progress`, `ingestion_batch_print` |
| `jobs` | `operations_job_detail_page` |
| `keywords` | `keyword_detail`, `keyword_documents`, `keywords_add` |
| `reports` | `entry_dossier_print` |
| `search` | `saved_searches_page`, `search_advanced`, `search_advanced_api`, `search_enhanced_page` |
| `settings` | `settings_api.settings_page` |
| `sides` | `side_add`, `side_categories_keywords`, `side_detail`, `side_edit` |
| `sources` | `source_add`, `source_categories_keywords`, `source_detail`, `source_edit` |
| `words` | `word_detail`, `words_add` |

### 3. Dependency validation

- Validation result: **no issues**
- Interfaces declaring a dependency: **14**
- Interfaces other interfaces depend on: **10**

| Interface | Requires | Required by |
| --- | --- | --- |
| `file_library` | — | `analyst_categorization`, `archives`, `batch_analysis`, `charts_dashboard`, `classification`, `comprehensive_dashboard`, `map_viewer`, `path_analysis` |
| `search` | — | `reports` |
| `words` | — | `classification` |
| `analyst_categorization` | `file_library` | — |
| `notifications` | — | `monitoring` |
| `signal_horizon` | — | `detection`, `monitoring` |
| `monitoring` | `notifications`, `signal_horizon` | `schedules` |
| `archives` | `file_library` | `map_viewer` |
| `path_analysis` | `file_library` | — |
| `map_viewer` | `archives`, `file_library` | — |
| `batch_analysis` | `file_library` | — |
| `classification` | `file_library`, `words` | — |
| `comprehensive_dashboard` | `file_library` | — |
| `reports` | `jobs`, `search` | `reports_dashboard`, `retention`, `schedules` |
| `reports_dashboard` | `jobs`, `reports`, `schedules` | — |
| `retention` | `jobs`, `reports` | — |
| `schedules` | `jobs`, `monitoring`, `reports` | `reports_dashboard` |
| `charts_dashboard` | `file_library` | — |
| `detection` | `jobs`, `signal_horizon` | — |
| `jobs` | — | `detection`, `reports`, `reports_dashboard`, `retention`, `schedules` |

### 4. Registry integrity

`validate_registry()` reports no issues: identifiers unique, domains from the closed set, roles known, routes declared consistently, no dependency is unknown or circular, and no two interfaces share a keyboard shortcut.

### 5. Lifecycle and migration

- Interfaces by status: **ACTIVE 34, DEPRECATED 1**
- Interfaces by kind: **INTERNAL 1, PAGE 34**

Renames and merges the registry understands (a stored value under an old key reaches the interface that replaced it; the code may not name the old key):

| Legacy key | Reaches |
| --- | --- |
| `advanced_search` | `search` |
| `analytics` | `comprehensive_dashboard` |
| `file_analysis` | `archives` |
| `file_browser` | `file_library` |
| `file_upload` (retired: no interface of its own) | `input_ingestion` |
| `upload_files` | `input_ingestion` |

### 6. Feature declarations

A feature is a cross-cutting capability with no page of its own. Everything the application gates this way is declared here; `tests/unit/test_interface_lifecycle.py` fails if a declared feature is not actually gated in code, or if a cross-cutting gate exists without a declaration.

| Feature | Default | Gated in | Description |
| --- | --- | --- | --- |
| `page_tips` | on | `templates/components/page_tips.html` | Explanatory tips at the top of each page, describing the elements on it, how to use them and how to add data. |

Cross-cutting settings that are **not** features (they belong to an existing interface, so they are values in the settings engine and nothing to do with the registry):

| Setting | Owner |
| --- | --- |
| `system.animations_enabled` | Display preferences (no product surface) |
| `system.show_breadcrumbs` | Display preferences (no product surface) |
| `system.notifications_enabled` | the `notifications` interface |
| `search.enable_history` | the `search` interface |
| `search.enable_saved_searches` | the `search` interface |
| `display.show_file_preview` | the `file_library` interface |
| `display.show_metadata` | the `file_library` interface |

<!-- END GENERATED REGISTRY EVIDENCE -->

## Part B - the application it describes

What the running application actually serves, checked against Part A.

<!-- BEGIN GENERATED APPLICATION EVIDENCE -->
### 7. Endpoint coverage

- Endpoints in the application's URL map (static excluded): **512**
- User-facing page endpoints: **66**
- Owned by an interface: **84**
- **Unmanaged user-facing endpoints**: **0**
- Interfaces with a navigable route: **35**

Unmanaged user-facing endpoints: **0** — every page the application serves is owned by exactly one interface.

### 8. Endpoint classification

| Classification | Endpoints |
| --- | --- |
| ACTION | 9 |
| API_ENDPOINT | 420 |
| INTERNAL_PAGE | 1 |
| REDIRECT | 1 |
| SYSTEM_ENDPOINT | 16 |
| USER_INTERFACE | 65 |

By blueprint:

| Blueprint | Endpoints |
| --- | --- |
| (app) | 286 |
| analytics | 22 |
| archives_api | 12 |
| auth | 12 |
| concurrency | 6 |
| content_analysis | 6 |
| cursor_api | 3 |
| error_dashboard | 4 |
| exports | 2 |
| file_analysis | 20 |
| file_operations | 3 |
| files | 34 |
| health | 1 |
| import_export | 6 |
| map_api | 11 |
| operations_api | 29 |
| paths | 3 |
| performance | 7 |
| preview | 2 |
| settings_api | 27 |
| setup | 9 |
| translations | 7 |

### 9. Declared exceptions

The coverage rule tolerates exactly these, by name — a new page cannot be added to an exception list by accident, because each list is declared in `core/interfaces/inventory.py` and reproduced here.

**SYSTEM_ENDPOINTS** (17): `auth.change_password`, `auth.first_admin_create`, `auth.first_admin_page`, `auth.login`, `auth.login_page`, `auth.logout`, `auth.me`, `favicon`, `get_csrf_token`, `health.health`, `set_language`, `setup.check_setup_status`, `setup.run_installation`, `setup.setup_page`, `setup.system_check`, `setup.test_database`, `static`

**INTERNAL_PAGE_ENDPOINTS** (1): `concurrency.dashboard`

**REDIRECT_ENDPOINTS** (1): `files.upload_page`

**TEST_ENDPOINT_PREFIXES** (1): `/_test/`

Endpoints the interface switch does not gate (API, system and infrastructure; authentication and authorization are unchanged): **437**

### 10. Rendered navigation

`GET /` as an administrator returned 200; the sidebar renders **9 domains** and **34 entries**, all of them from the registry:

- Work
- Discover
- Ingest
- Analyze
- Classify
- Report
- Operate
- Administration
- Settings

Entries, in render order: `index`, `files.files_list`, `search_page`, `sources_list`, `sides_list`, `keywords_list`, `words_list`, `categories_list`, `email_words`, `notifications_page`, `signals_page`, `monitoring_page`, `operations_input_page`, `operations_import_page`, `archives_page`, `path_analysis_page`, `map_viewer_page`, `analysis_batch`, `lexis_cosmos_page`, `analyst_categorization_page`, `file_classification_page`, `comprehensive_dashboard`, `reports_page`, `reports_dashboard_page`, `schedules_page`, `charts_dashboard`, `detection_page`, `operations_jobs_page`, `import_export_page`, `retention_page`, `users_page`, `audit_page`, `settings_page_direct`, `translations.translation_management_page`

Marked active on this page: `index`
<!-- END GENERATED APPLICATION EVIDENCE -->

## What remains legacy

The registry replaced `INTERFACE_METADATA` - the untyped dictionary that used to
describe interface switches inside the settings adapter. It listed endpoints
that did not exist, a page-less switch, and two entries for one page. The
registry is a declaration instead: it says what exists, what it is for, who
should see it, what it needs and where it lives, and it owns no logic beyond
looking itself up.

Stored state that still names an old id is not discarded. `LEGACY_INTERFACE_IDS`
maps each retired id to the interface that now owns it, so a deployment
upgraded from an older version keeps its choices:

* `file_analysis` and `analytics` named things that were never one interface -
  the first is now `archives`, the second `comprehensive_dashboard`.
* `upload_files` and `file_upload` were two ids for one page, now one
  `input_ingestion`.
* `file_browser` is `file_library`; `advanced_search` was a mode of `search`.

`LEGACY_INTERFACE_IDS` is therefore a migration table, not a feature: it is read
when old state is applied and never written to. An id is removed from it only
once no supported upgrade path can carry it.
