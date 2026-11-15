# SYLTHARAE

SYLTHARAE is a multidimensional information intelligence platform. It
discovers, reads, extracts, classifies, indexes, analyses, preserves and
connects information across large, mixed collections of files: documents,
spreadsheets, e-mail and mailboxes, archives, images (with OCR), audio,
video, e-books, databases and diagrams.

---

## What it is

A Flask + PostgreSQL application that ingests a corpus from disk, stores every
file's content and metadata, and lets you search, analyse and export it. It is
built for corpora far larger than memory: discovery streams from disk, and
checkpointing means an interrupted run can be resumed rather than restarted.

Current version: **2.2.0** (`version.py`). Python 3.10–3.12. Licensed
**AGPL-3.0-or-later**.

## Quick start

```bash
pip install -r requirements.txt          # or requirements.lock
cp .env.example .env                     # then set DB_* and FLASK_SECRET_KEY
# server-path ingestion needs an allowlist (SEC-06):
#   INGESTION_ROOTS=/data/evidence:/srv/inbox
python run_web.py                        # web UI
python run_cli.py --help                 # command line
```

On Windows, `setup.bat` / `start-web.bat` / `run_cli.bat` wrap the same
entry points.

Configuration precedence is 12-factor: OS environment > `.env` >
`settings.json` > `config.example.json` > dataclass defaults.

## Layout

| Path | Contents |
|---|---|
| `apps/web`, `apps/cli`, `apps/importing` | Entry points: Flask app, CLI, import adapters. |
| `Api/` | HTTP layer — routes, services, task manager. |
| `core/` | Engine-level library: file utils, hashing, detection, formats, OCR, forensics, analytics, search, security, i18n, checkpoints. |
| `pipeline/` | Processing engine (`integrated_reader`) and the progress ledger. |
| `reader_file/readers/` | Format readers — PDF (incl. layout), Office, e-mail, archives, images, audio, video, e-books, databases, diagrams. |
| `database/` | Schema, migrations, repositories, query services. |
| `services/` | Domain services: ingesting, importing, jobs, geo, maps, reporting, retention, scheduling, monitoring, translation. |
| `templates/`, `static/` | Jinja templates and front-end modules. |
| `tools/`, `scripts/` | Operations tooling: docs generation, DB utilities, security/CI checks, readiness verification. |
| `translations/` | gettext catalogues (currently `en`, `ar`, `fa`, `he`, `hr`). |

## Reading and writing a corpus

Ingestion runs as a **job** (`services/jobs/`), so it can be queued, paused,
cancelled, retried and watched live. Three shapes:

| Job type | Input | Notes |
|---|---|---|
| `ingestion` | a folder (`options.path`) | Phased: priority files, then PDFs, then images. |
| `ingestion` | an explicit file list (`options.file_paths`) | Sequential, one worker per file. |
| `batch_import` | an explicit file list | Same engine, separate job type. |

Every job carries `source` and `side` (the storage taxonomy) — both mandatory,
no defaults.

**Resumability.** A run checkpoints every file it stores. If the process dies,
the server restarts, or the job is paused, **Retry** continues from the last
checkpoint instead of reprocessing. See [`docs/resume-and-checkpoints.md`](docs/resume-and-checkpoints.md)
for how it works, where checkpoints live, and how to diagnose a run that did
not resume.

## Analysis

Beyond full-text search, the platform derives structured signals from stored
content (`core/detection/`, `core/analytics/`, `core/forensics/`):

- **Identifiers** — phone numbers, e-mail addresses, IBAN/credit-card-like
  values, national IDs, URLs, MAC/IP addresses, coordinates.
- **Temporal** — dates, times, durations, calendar systems, time horizons.
- **Places** — gazetteer-backed place intelligence, with a map view and
  offline map packages (`services/geo/`, `services/map_*`).
- **Signals** — a signal model over the above, with re-detection when detector
  versions change.

> The items above are **inferred** from module and function names
> (`core/detection/identifiers_intel.py`, `temporal_intel.py`,
> `place_intel.py`, `signal_model.py`, `horizon.py`) and have not been audited
> feature by feature.

## Operations

- **Jobs UI** — live progress (SSE, with a polling fallback), cancel, pause,
  resume, retry, event log, warnings and errors per job.
- **Retention** — configurable pruning per area (`services/retention/`).
- **Scheduling** — scheduled jobs via APScheduler (`services/scheduling/`).
- **Audit** — action logging and an audit trail; performance monitoring.
- **Security** — path allowlisting for server-side ingestion (SEC-06), CSRF,
  rate limiting, lockout after repeated failed logins, SQL safety helpers.

## Development

```bash
pytest tests/unit               # unit tests
pytest tests/integration        # needs PostgreSQL
python verify_readiness.py      # deployment readiness check
```

Conventions worth knowing before contributing:

- Runtime paths derive from `APP_DATA_DIR` — nothing is written into the repo
  at runtime.
- The engine streams: discovery, batches and results are never all held in
  memory at once.
- Progress is never faked: the ledger reports real counted work, and a
  completed run reaches 100% because every unit reaches a terminal state.
- Tests must be shown to fail before a fix and pass after it.

## Documentation

- [`docs/resume-and-checkpoints.md`](docs/resume-and-checkpoints.md) — checkpointing, resume, retry, diagnostics.
- [`ROADMAP.md`](ROADMAP.md) — current state, in-flight work, open questions.

## Licence

GNU AGPL-3.0-or-later. See [`LICENSE`](LICENSE).
