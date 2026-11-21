# Productization map

Where each part of the product lives, and what state it is in.

This document is a **map, not a source of truth**. Every number it could quote
is generated somewhere else, and quoting it here would only give it a second
place to go stale. The generated sources are authoritative:

- [`INTERFACE_REGISTRY.md`](INTERFACE_REGISTRY.md) — every registered
  interface, generated from `core/interfaces`.
- [`REGISTRY_EVIDENCE.md`](REGISTRY_EVIDENCE.md) — the evidence that
  user-facing routes are owned, generated from the registry and the live URL
  map.
- [`endpoint_inventory.json`](endpoint_inventory.json) — the deterministic
  endpoint snapshot.
- [`ACTION_SURFACE_AUDIT.md`](ACTION_SURFACE_AUDIT.md) — the screen-action
  survey.
- [`COMPONENT_LIBRARY.md`](COMPONENT_LIBRARY.md) — the shared UI components.

When a capability below is described as complete or partial, that is a
statement about the code, not a count copied from those documents.

## Work

| Capability | Where | State |
|---|---|---|
| Overview dashboard | `services/reporting`, `Api/routes` | Complete |
| Notifications | `services/monitoring` | Complete |

## Discover

| Capability | Where | State |
|---|---|---|
| Search (standard, boolean, enhanced, advanced) | `core/search`, `Api/routes/search*`, `templates/Search` | Complete |
| Cursor pagination with count modes | `Api/cursor_pagination.py` | Complete |
| File library and content reader | `templates/file`, `Api/routes/file*` | Complete |
| Sources, sides, words, keywords, email words | `templates/Source`, `Side`, `Word`, `Keyword`, `email_words` | Complete |
| Archive analysis workspace | `templates/Analysis` | Complete |

## Ingest

| Capability | Where | State |
|---|---|---|
| Uploads (single, chunked, folder) | `Api/routes/ingest*`, `templates/Ingest` | Complete |
| Allowlisted server-path ingestion | `services/ingesting`, `core/path_safety.py` | Complete |
| Import Center with validation and preview | `services/importing`, `templates/ImportExport` | Complete |
| Crash-safe checkpoint and resume | `core/checkpoint_manager.py` | Complete; see [resume-and-checkpoints.md](resume-and-checkpoints.md) |

## Processing

| Capability | Where | State |
|---|---|---|
| Job queue and lifecycle | `services/jobs` | Complete |
| SSE progress with polling fallback | `Api/routes/operations_api.py` | Complete |
| Pause / resume / cancel / retry | `services/jobs/manager.py` | Complete |
| Compute routing | `core/` compute policy | Partial — policies are selectable; coordination is coarse |

## Analyze

| Capability | Where | State |
|---|---|---|
| Signal detection | `services/detection` | Complete |
| Temporal intelligence and Horizon Explorer | `core/` temporal modules, `templates/Signals` | Complete |
| Map Viewer and offline packages | `services/geo`, `services/map_packages.py`, `templates/map` | Complete |
| Scenario monitoring | `services/monitoring` | Complete |

## Classify

| Capability | Where | State |
|---|---|---|
| Taxonomy and category trees | `classification/`, `services/categories` | Complete |
| Analyst categorization (separate layer) | `templates/Analyst` | Complete |
| LEXIS Cosmos | `templates/Lexis` | Complete |

## Report

| Capability | Where | State |
|---|---|---|
| Dashboards and analytics | `templates/Reports`, `core/analytics` | Complete |
| Versioned reports and artifacts | `services/reporting` | Complete |
| Exports (CSV, XLSX, JSON, ZIP) | `Api/routes/export*`, `services/reporting` | Complete |

## Operate / Administration / Settings

| Capability | Where | State |
|---|---|---|
| Setup wizard and CLI installer | `templates/Setup`, `install.py`, `core/installer.py` | Complete |
| Users, roles, sessions | `Api/routes/admin*` | Complete |
| Interface toggles | `settings/interface_state.py` | Complete |
| Audit log (append-only) | `core/experience/audit.py` | Complete |
| Retention policies | `services/retention` | Complete |
| Localization (five locales) | `translations/`, `services/i18n`, `services/translation` | Complete |

## Known gaps

Stated as gaps rather than hidden, and each is anchored to code that exists in
[`ACTION_SURFACE_AUDIT.md`](ACTION_SURFACE_AUDIT.md):

- Some screens still wire actions by hand instead of through the Action
  Registry. They work; they are not yet declared.
- A number of interfaces are still reachable through their legacy ids. The
  registry carries them forward through its legacy mapping so stored choices
  survive an upgrade, but the old names have not been removed from every
  template.
- Compute routing exposes the policies; live resource coordination is
  approximate and applies backpressure rather than scheduling precisely.

These are debts, not defects: nothing above is broken, but some of it is not
yet expressed in the single source of truth.
