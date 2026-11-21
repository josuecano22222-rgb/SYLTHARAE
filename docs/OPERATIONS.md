# Operations

Running SYLTHARAE day to day: starting it, keeping it healthy, and getting out
of the situations that come up in practice.

For configuration reference see [CONFIGURATION](CONFIGURATION.md). For the
security controls this depends on see [SECURITY](SECURITY.md).

## First run

The application refuses to serve until it is installed. On a fresh checkout:

```
python install.py            # interactive: database, admin account, .env
python verify_readiness.py   # checks the environment before you rely on it
python run_web.py            # serves on 127.0.0.1:5000
```

`install.py` creates the database, applies migrations, loads the reviewed
gazetteer seed, creates the initial administrator, writes `.env` and creates
the runtime directories. It is idempotent: running it again applies only what
is missing.

The same installation is available through the web setup wizard on first
visit, and both paths call the same `core/installer.py`, so the wizard and the
CLI cannot diverge.

If the administrator password is lost, use
`python scripts/reset_admin_password.py` rather than editing the database: it
goes through the same validation, so the replacement password cannot bypass
the policy.

## Serving

In production the application runs on loopback behind a TLS reverse proxy:

```
sudo systemctl enable --now syltharae    # see deploy/systemd/syltharae.service
sudo nginx -t && sudo systemctl reload nginx
```

The service is a **single process**, deliberately. The application keeps
bounded in-process worker threads and a checkpoint journal; two instances
would give two writers the same journal. Scale by sizing the host.

### Start-up validation

`apps/web/deployment.py` validates the environment before serving. Dangerous
settings are refused and invalid ones are errors — the process exits `2`,
which the unit maps to `RestartPreventExitStatus`, so a misconfiguration stops
instead of restart-looping. Unsafe-but-valid settings (the Flask development
server, a `0.0.0.0` bind, a short secret) warn and start.

`python verify_readiness.py` runs the same checks without starting, and
`--json` emits a machine-readable report for CI.

### Behind a proxy

Set `TRUSTED_PROXY_COUNT` to the number of proxies in front of the
application — one for the shipped nginx configuration. The value decides how
much of `X-Forwarded-For` is trusted, so a wrong value misattributes client
addresses and silently defeats rate limits and lockout.

The proxy must forward `Host` and `X-Forwarded-Host`. Without them the
application sees `127.0.0.1:5000`, the browser's `Referer` no longer matches,
and **every sign-in answers `400`** — a failure seen in practice, and the
reason `deploy/nginx/syltharae.conf` sets both.

## Data locations

| What | Where |
|---|---|
| Database | PostgreSQL, `DB_NAME` |
| Uploads, job artifacts, checkpoints | `APP_DATA_DIR` (`/var/lib/syltharae` as shipped) |
| Search history, settings, instance lock | the install directory (see the unit's `ReadWritePaths`) |
| Logs | journald (`journalctl -u syltharae`) |

## Routine checks

- `journalctl -u syltharae -f` — the application logs one line per
  refused/warned start-up setting, and one per checkpoint resume.
- `python verify_readiness.py` — run after any configuration change.
- Job progress is streamed with SSE; if a browser cannot hold an event stream,
  the UI falls back to polling. See
  [resume and checkpoints](resume-and-checkpoints.md) for how interrupted work
  is recovered.

## Interrupted work

Ingestion is crash-safe. A batch records a durable checkpoint as it goes, and
retrying a failed or interrupted job resumes from the last checkpoint instead
of reprocessing what was already stored. If a resume looks wrong, inspect the
journal:

```
python scripts/checkpoint_inspect.py <job_id>
```

## Common failures

| Symptom | Cause |
|---|---|
| Every sign-in answers 400 | Proxy not forwarding `Host` / `X-Forwarded-Host` |
| Service will not start, exits 2 | Start-up validation refused a setting; read the journal |
| Rate limits and lockout have no effect | `TRUSTED_PROXY_COUNT` does not match the proxy chain |
| Scanned pages return no text | No OCR engine installed; see [CONFIGURATION](CONFIGURATION.md) |
| Pages load with undefined handlers | HTTP/2 connection churn; see `keepalive_requests` in `deploy/nginx/syltharae.conf` |

## Backup and restore

The database and the files it points at are consistent only as a pair: every
row in `paths` names an artifact on disk, and retention deletes both sides when
they expire. A restore of one without the other produces an installation whose
pages render and whose file operations fail.

### What to back up

| Item | Path | Why |
|---|---|---|
| PostgreSQL database | `DB_NAME` | accounts, vocabulary, paths, audit trail, job history |
| Application data | `APP_DATA_DIR` (default `/var/lib/syltharae`) | uploads, job artifacts, checkpoints, the SECRET_KEY file, `.system_initialized` |
| Translation memory | `$APP_DATA_DIR/translation/translation_memory.db` | analyst corrections; SQLite, not in PostgreSQL |
| Map registry | `map_data/registry.db` under the install directory | installed map packages; SQLite, not in PostgreSQL |
| Configuration | `.env` (or `config.json`), plus `map_data/catalog.json` | database credentials, paths, limits |

`GET /api/export/database` (the "database backup" export in the UI) is **not**
a disaster-recovery backup: it exports the evidence tables only and
deliberately omits `users`, `sessions`, `audit_log`, `jobs` and
`schema_migrations` so that credential material never lands in a downloadable
file and an export cannot be restored over a live system. Use `pg_dump` for
recovery.

### Taking a backup

Stop the service first, or accept that the dump and the data directory are a
few seconds apart:

```bash
systemctl stop syltharae
pg_dump -Fc -d "$DB_NAME" -f /backup/syltharae-$(date +%F).dump
tar -czf /backup/appdata-$(date +%F).tar.gz "$APP_DATA_DIR" map_data/registry.db map_data/catalog.json .env
systemctl start syltharae
```

`-Fc` (custom format) restores with `pg_restore`, can be inspected with
`pg_restore -l`, and compresses.

### Restoring

```bash
systemctl stop syltharae
createdb -O "$DB_USER" "$DB_NAME"
pg_restore -d "$DB_NAME" /backup/syltharae-2026-11-13.dump
tar -xzf /backup/appdata-2026-11-13.tar.gz -C /
systemctl start syltharae
```

Then verify, in this order:

1. `journalctl -u syltharae -n 50` — expect
   `[OK] Database schema is up to date (version …)`. Pending migrations are
   applied at start-up, so a dump from an older release is upgraded in place.
2. `curl -fsS localhost:5000/health` — 200.
3. Sign in. A restore into a fresh `APP_DATA_DIR` has no `.system_initialized`
   marker; the application asks PostgreSQL instead and re-creates the marker
   (`[OK] Re-created the initialization marker`). It does not open the setup
   wizard over a restored installation.
4. Open a document from **Files**. If it fails, the data directory and the
   dump do not belong together.

### Lost initialization marker

`.system_initialized` is a cache of the database's answer, not the authority.
If it disappears — an ephemeral container filesystem, a restore that only
carried the dump, a changed `APP_DATA_DIR` — the next start re-creates it when
the critical tables are present. If the file is present but the database was
replaced by an empty one, the setup wizard is offered (there are no accounts to
lock out).

### Restore drill

Perform this once per release on a staging host: dump, restore into a
freshly-created empty database and a fresh `APP_DATA_DIR`, start the service,
sign in, open a document. The drill is what proves the backup exists in a form
that can be restored — a dump nobody has restored is an assumption.
