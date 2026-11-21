# Changelog

All notable changes to SYLTHARAE are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.org/en/1.1.0/); this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The single source of truth for the current version is `version.py`, mirrored in
the `version` field of `pyproject.toml`. Both are checked by
`tests/unit/test_version_consistency.py`.

## Unreleased

### Fixed

- **Two more handlers put the exception text in front of the user.** The file
  list (`Api/blueprints/files.py`) and the notification scan
  (`Api/routes/notifications.py`) rendered `str(e)` into a flash message and a
  JSON `message`. Both now say a fixed sentence; the detail goes to the log.
  `tests/unit/test_error_text_disclosure.py` pins the rule narrowly — response
  calls inside a broad `except` may not interpolate the exception text.
- **The compiled catalogs were older than the sources they came from.**
  `translations/*/LC_MESSAGES/messages.mo` did not match the `.po` files, so
  pages showed English even where a translation existed. The `.mo` files are
  rebuilt from the shipped `.po` files.
- **Message extraction scanned the wrong trees.** `babel.cfg` walked the whole
  checkout, so it harvested a stale `build/lib` copy of the templates, test
  assertion strings and the third-party corpus under `test-doc/`. Its patterns
  are root-relative again (a path-prefixed pattern matches nothing when
  extraction is pointed at a single package, which is what the determinism test
  does) and `build/`, `dist/`, `tests/`, `test-doc/`, `node_modules`, `.venv`
  and `audit_report/` are excluded by name.
- **Two strings the catalogs never received.** The jobs page renders
  "The job list could not be loaded." and "Retrying…" through `_()`; they are
  now in the `.pot` and translated in Arabic, Hebrew, Persian, Croatian and
  English.
- **`/health` answered with a redirect while the wizard was open.** With a
  migrated database and no account yet, the setup gate sent every path to
  `/setup` — including the liveness probe, whose caller is a reverse proxy or a
  supervisor, not a person. A 302 to an HTML page is not an answer; the health
  endpoint now reports its own checks in both windows.
- **A signed-in browser could be bounced to the installer.** The zero-account
  redirect is right for an anonymous visitor and wrong for a request that
  carries a validated session: the accounts probe can answer "empty" wrongly
  (a delete racing the read, a replica catching up), and the installer then
  refuses to reinstall. A session cookie now outranks the probe.
- **A stale-install probe could point the wizard at the wrong place.**
  `installed_state()` no longer reports an installed system as uninstalled when
  only the filesystem marker is missing (the database is the authority).

### Security

- **Sign-in redirect (`next`).** `/auth/login?next=/\evil.example` passed the
  "starts with `/`, not `//`" check, and browsers normalise the backslash to a
  slash: the user signed in for real and landed on `//evil.example`. The
  continuation is now decided by `_safe_next_url` (same-origin path, no
  backslash raw or percent-decoded, no control characters).
- **Spreadsheet formula injection.** The search-results export (CSV and XLSX)
  wrote `line_text`, `context_before` and file names straight into cells, and the
  keyword export wrote user-typed keywords the same way; a document line
  beginning with `=` is a formula to Excel and LibreOffice. Every cell now goes
  through `spreadsheet_safe_text`, as the file and category exports already did.
- **Map package path escape.** `str(path).startswith(str(root))` accepts a
  sibling whose name begins with the root's, so an archive member
  `../pkg_evil/x` or a manifest field pointing there was treated as part of the
  package — and the manifest names files that are served over HTTP.
  `Path.is_relative_to` compares components.
- **Bounded manager start.** `multiprocessing.Manager()` was called lazily inside
  `/concurrency/api/metrics` with no deadline; on a host where it cannot start —
  including the ordinary case of forking from a process that already runs threads
  — the request and its worker hung with no error to report. The start is bounded
  and the endpoint reports a degraded manager.

- **The concurrency dashboard lied when a manager could not start.** With the
  bounded start in place, `/concurrency/api/metrics` still answered
  `{"status": "error", "error": "Internal error (ERR-…)"}` for the process and
  pool cards, and `/concurrency/api/processes` and `/api/pools` returned a 500 —
  while the thread and async managers, which need no multiprocessing, were
  answering normally. The endpoint now reports `mode: "full"` or
  `"threads-only"`, keeps the live cards, explains each unavailable one with a
  fixed sentence and `reason_code: multiprocessing_unavailable`, and reports the
  host's `worker_capacity` so an operator knows what the machine can still do.
  The dashboard shows a banner only when the server says it is degraded and
  explains an empty process/pool list instead of claiming there are none.
- **Two PyMuPDF companion pins were reported as unknown licences.**
  `pymupdf4llm` and `pymupdf_layout` are hard requirements of PyMuPDF 2.1 and are
  empty packages (their own metadata says they exist to dodge a pip upgrade bug
  and that the code moved into `pymupdf`). The licence checker gains a reviewed
  entry for each, and a test proves they ship no files, so a future release that
  does ship code fails the test rather than sliding through the exception.
  `check_licenses.py` now answers 118 compatible / 0 incompatible / 0 unknown.

### Changed

- The migration freeze gate records `m0006_index_hygiene` as a reviewed
  exception, in the test that owns the freeze, and the schema reference is
  regenerated from the migrated database.
- **The audit's RTL check could not see the direction it was checking.** It
  looked for `dir="{{`, so `base.html`'s
  `dir="{% if current_language in [...] %}rtl{% else %}ltr{% endif %}"` was
  reported as a missing attribute. It now reports whether the direction is
  derived from the language — a hardcoded `dir="ltr"` passed the old check.
- **The audit's i18n check no longer answers "0" when it is blind.**
  `tools/audit/check_structure.py` read a generated inventory file and printed
  "msgids with an incomplete catalogue: 0" whenever that file was absent. It now
  says "not measured", names the generator, and reports the untranslated/fuzzy
  entry count straight from the catalogs (`1236`: ar 309, he 284, fa 284, hr
  359).
- **The translation gap is handed over as worksheets.** New
  `tools/i18n/export_translation_gaps.py` writes one CSV per language
  (`audit_report/findings/i18n_untranslated_<lang>.csv`) with the msgid, its
  status (missing / fuzzy / partial), the untrusted draft, its source locations
  and an always-empty `translation` column. It never writes into `translations/`,
  and `--check` fails if the committed sheets drift from the catalogs.

### Documentation

- `docs/reference/` (Python API, database schema), `docs/EXPERIENCE_CONTRACT.md`,
  `docs/ACTION_SURFACE_AUDIT.md` and `docs/SCREEN_INSPECTOR_EVIDENCE.md`
  regenerated from the current tree.
- `docs/AUDIT_ROUND_2.md` — what this round found, what it fixed, and what it
  deliberately did not: the whole-catalog translation gap is a translator's
  decision, not a code change, and it is recorded there with its numbers.
- `docs/TRANSLATION_HANDOFF.md` — the 1236 untranslated/fuzzy entries per locale
  and per area, the apply/recompile/verify workflow, and the Croatian decision
  brief (keep excluded and documented, enable in the UI and the Translation
  Manager, or add an explicit configuration flag — cost and consequence for
  each, none chosen by this release).
- `docs/RELEASE_SIGNOFF.md` — the final pre-release verification matrix and the
  release readiness verdict.

## v2.2.0

### Added

- **Crash-safe checkpoint and resume for ingestion.** Per-file progress is
  journalled durably (fsynced append-only journal plus a JSON snapshot), so a
  run interrupted by a crash, shutdown or operator action resumes from the last
  completed checkpoint instead of reprocessing stored files.
- **Checkpoints for batch ingestion.** File-list and `batch_import` runs now
  obtain a checkpoint. Previously `_checkpoint_file()` returned `None` whenever
  no folder path was set, so no batch was ever checkpointed and every
  interrupted batch restarted from zero.
- **Resume diagnostics.** Every run reports what it found on disk — `resumed`,
  `fresh` or `rejected` with a reason — in one log line, in the job's
  statistics, on the job detail page, and via `scripts/checkpoint_inspect.py`.
- **Durable directory entries on Windows.** A journal's name is now flushed
  alongside its contents, using a directory handle opened with
  `FILE_FLAG_BACKUP_SEMANTICS` and `FlushFileBuffers`.
- `docs/resume-and-checkpoints.md`, covering checkpoint identity, policies and
  how to diagnose a run that appears to restart.

### Fixed

- **Negative work totals on resumed folder ingestions.** `total_files` was
  computed as `inventory['total'] - skipped_count`, but skipped files were
  already excluded from `total`, so the count was reduced twice and went
  negative once a checkpoint covered more than half the corpus — printing a
  negative "remaining" and making the "all files already processed" branch
  unreachable.
- **Silent loss of checkpoint marks.** A journal write failure was caught and
  logged, but did not propagate, so `mark_failures` stayed at zero and a run
  that lost every mark was indistinguishable from a healthy one. Failures now
  reach the warn-once counter, and `get_statistics()` reports `mark_failures`.
- **An unusable checkpoint directory aborted setup silently.** It now logs
  that resuming is DISABLED for that run, rather than failing with a bare OS
  error that said nothing about the consequence.
- **File-list runs double-counted progress** (`files_total` reported roughly
  twice the real figure).
- **`batch_import` could not be cancelled or paused** — the control callbacks
  were never wired.
- **A path that could not be resolved disabled checkpointing for the whole
  corpus.** Replaced with `safe_resolve()`, which degrades to a stable
  fallback instead of failing the run.
- **Licence declarations.** `pyproject.toml` omitted `THIRD_PARTY_NOTICES.md`
  from `license-files`, so the notices were not packaged with a distribution;
  and the README did not spell out the licence name.

### Changed

- Checkpoint identifiers are now derived with `safe_resolve()` on every side of
  the checkpoint, so the digest cannot diverge between the writer and the
  reader.

### Documentation

- `ROADMAP.md` — verified current state, defects with dispositions, open
  questions.
- `docs/LICENSING.md` — AGPL obligations, `SOURCE_CODE_URL`, third-party
  notices, optional-component licence caveats.
- `docs/CONFIGURATION.md` — precedence, storage and database settings,
  ingestion tuning, OCR, checkpoints, security.
- `THIRD_PARTY_NOTICES.md` — generated by `tools/licenses/generate_notices.py`
  from installed-distribution metadata and `git ls-files static`.

### Known limitations

- The Windows directory flush is verified at the level of call sequence and
  failure handling. Survival of a real power cut depends on the disk, its write
  cache and its driver, and is **not** verified.
- A file's checkpoint identifier embeds a local-time date, so a file modified
  near local midnight can be reprocessed across a DST change. Affects
  individual files, never a whole corpus. Retained by decision: changing it
  invalidates every existing checkpoint.
- Path case (`C:\Data` vs `c:\data`) is expected to normalise on Windows but is
  unverified.

## Earlier releases

No changelog was maintained before v2.2.0 and the repository's history was
squashed before this file was started, so earlier changes are not reconstructed
here. Inventing them would be worse than leaving them out; consult the commit
history for the period in question.
