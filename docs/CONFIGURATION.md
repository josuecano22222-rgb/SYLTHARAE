# Configuration

Where a setting comes from, what it does, and which ones matter operationally.

## Precedence

Later sources win over earlier ones:

```
OS environment  >  .env  >  settings.json  >  config.example.json  >  dataclass defaults
```

This is 12-factor precedence. The runtime never writes back into the
repository: **all** runtime state lives under `APP_DATA_DIR` (see below).

## Storage and runtime paths

| Setting | Purpose |
|---|---|
| `APP_DATA_DIR` | Root for every runtime artifact: ingested bytes, `settings.json`, logs, caches, and checkpoints. Nothing else in the tree is written at runtime. |
| `MAP_DATA_DIR` | Moves the `map_data/` tree (the SQLite map registry and its cached WebP tiles). `tests/conftest.py` points it at `.test_runtime/map_data` before the application imports, so a test run never touches the shipped catalog. |
| `INGESTION_ROOTS` | The server-path ingestion allowlist (SEC-06). A path outside these roots is refused with `IngestionValidationError` rather than read. Required for folder and file-list ingestion; uploads are unaffected. |

## Database

Connection pooling is configured by `DB_POOL_MIN_CONNECTIONS` and
`DB_POOL_MAX_CONNECTIONS`. The older `DB_MIN_CONNECTIONS` /
`DB_MAX_CONNECTIONS` names are still read and still win when both are present,
so an existing deployment keeps its tuning after an upgrade. Pool defaults are
1 and 20.

| Setting | Purpose |
|---|---|
| `DB_POOL_TIMEOUT` | How long to wait for a connection from the pool before failing. |
| `DB_QUERY_TIMEOUT` | Per-query ceiling. A query that exceeds it is cancelled rather than allowed to hold a connection indefinitely. |

## Ingestion and processing

| Setting | Purpose |
|---|---|
| `MAX_WORKERS` | Worker threads for ingestion. `0` means auto-size from the CPU count. |
| `FILE_CHUNK_SIZE` | Read size for streaming extraction. Memory use is bounded by this times the worker count, which is what lets multi-gigabyte files be read without exhausting RAM. |
| `FILE_PROCESSING_TIMEOUT` | Per-file ceiling. A file that exceeds it is recorded as a retryable failure rather than stalling the job. |

Compute routing (`cpu`, `gpu`, `cpu+gpu`, `auto`) is an operator policy chosen
per run; `auto` coordinates with live resource monitoring and applies
backpressure rather than oversubscribing the host.

## Observability

| Setting | Purpose |
|---|---|
| `LOG_LEVEL` | Honoured by the web app; not hard-coded. |
| `ACTION_LOGGING_ENABLED` | Appends user actions to the audit log. The log is append-only; this toggles recording, never rewriting. |
| `PERFORMANCE_MONITORING` | Enables runtime performance counters and the concurrency diagnostics surface. |

## OCR

OCR needs the external Tesseract binary plus its language data (`eng`, `ara`,
`heb`). When Tesseract is not on `PATH`, set **`TESSERACT_CMD`** to the
executable.

Failure to read is recorded, never papered over. A missing language pack or a
missing binary is stored as an **explicit retryable failure** on the file — it
is not silently downgraded to English, and it is not dropped. The OCR
self-check reports these states and the setting that remediates each one:

```bash
python tools/ci/ocr_selfcheck.py
```

## Licence disclosure

**`SOURCE_CODE_URL`** is the URL offered to users of a modified deployment, as
AGPL-3.0 section 13 requires when people interact with it over a network. It
accepts `http:` and `https:` only. See [`LICENSING.md`](LICENSING.md) for when
it must be set.

## Checkpoints

Checkpoints are written under `APP_DATA_DIR` as an fsynced JSON snapshot plus
an append-only journal. If that directory is unusable, ingestion logs that
resuming is DISABLED and continues without a checkpoint — an interrupted run
then reprocesses from the beginning. See
[`resume-and-checkpoints.md`](resume-and-checkpoints.md).

## Security

`SECRET_KEY`, session lifetime, lockout thresholds and rate limits are all
environment-driven. The application refuses to boot with debug enabled in
production. `TRUSTED_PROXY_COUNT` must be set when running behind a reverse
proxy, or client addresses are taken from the proxy rather than the real
client.

## Verifying a configuration

```bash
python verify_readiness.py     # deployment health check
```
