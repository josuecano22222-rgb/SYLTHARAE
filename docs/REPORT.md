# Report — SYLTHARAE hardening and completion pass

**Branch:** `arena/5ea93606-syltharae`, from `156cd0f` (main).
**Commits this pass:** `f54f293` … `9fc71b3` (4), on top of `d89ebe2`.
**Scope:** 48 files changed, +51 339 / −31 347.
**Test result:** unit, security and integration suites all **0 failed / 0 errors**.

---

## 1. What was found

The codebase is mature and largely sound. An audit at the start of the pass
concluded that the work was **defect-finding, verification and polish** rather
than greenfield, and that held: the dominant genuine defect class was not
broken algorithms but **missing governance artifacts and defects that no test
was running**.

The starting state of the suite was **153 failed / 53 errors**. Three causes
were mixed together, and separating them mattered:

| Cause | Verdict |
|---|---|
| Missing optional dependencies | Environmental — resolved by installing `requirements.txt` |
| Absent `docs/` and `deploy/` artifacts read by registry-governance tests | **Real defect** — the tests were right and the files did not exist |
| Genuine product bugs | **Real defect** — three found and fixed |

A caution worth recording: an earlier note that "all failures are missing
optional dependencies" was **wrong**, and acting on it would have hidden real
bugs. Every failure was triaged by reading the assertion, not the error class.

## 2. What was fixed

### 2.1 Resumed runs reported stored files as failed (real bug)

**Symptom.** After a crash and restart, a run reported files it had *successfully
stored* as "could not be processed".

**Reproduced** against a real PostgreSQL: a folder of two files and a zip of
three members, killed after the sixth store, came back with `LEDGER failed=3`
while all six files were correctly present exactly once.

**Root cause.** The three were the archive members. The container published them
into the progress ledger's denominator when it was opened. The nested reader
then found them already in the checkpoint and called
`record(OUTCOME_SKIPPED, 3)`. But `record` *adds* to the denominator and settles
in one step — it is for work that was **never registered** — so the denominator
grew to nine for six real files. The end-of-run sweep found three units that
could never be settled and, with nothing cancelled, recorded them as **failed**.

`ProgressLedger.abandon` exists for exactly the opposite case, and its own
docstring names this failure mode:

> Using `record` here would inflate `discovered_total` a second time and leave
> `pending` permanently non-zero.

**Fix.** `process_folder` now calls `abandon` when the run is nested (the
container already published the work) and `record` at the top level (a
checkpointed file was filtered out before anything registered it). The container
is named in the `abandon` call so its outstanding work is released.

**Proven fail-before / pass-after.** Reverting the change makes
`test_committed_leaves_are_not_read_again_after_a_kill_post_commit[6]` fail with
`assert 3 == 0`; with the change, all 26 crash-recovery tests pass. Three fast
unit tests were added so the contract is guarded **without** a database.

### 2.2 `core.installer` imported `services.geo` (architectural violation)

`tests/unit/test_architecture_claims.py` pins the rule *core must not import a
route or a service* and counts the exceptions by name, so a new one fails.
`core/installer.py` broke it with a function-local
`from services.geo import gazetteer`. **A deferred import is not an exemption** —
the check walks the AST, which sees it exactly as it would a top-level one.

The seeding is geo domain logic, so it now lives in
`services/geo/gazetteer_seed.py`, and `install.py` and `Api/routes/setup.py`
hand it to the installer via `set_gazetteer_seeder()`. An unregistered seeder
now **raises** rather than returning quietly: a silently unseeded gazetteer
looks, months later, like a place detector that never worked, with nothing in
the install log to explain it.

Verified end-to-end: the installer ran against a live PostgreSQL and applied
5 migrations with the injected seeder.

### 2.3 Two JS smoke harnesses that nothing ran (silent rot)

`tests/js/*.mjs` are only real tests if something executes them. Two were not,
and both had rotted:

- **`places_settings_smoke.mjs`** reported **8/9 forever**. The ninth check
  asserted against a load-history inspector that **does not exist** — no
  `gazetteer-loads-tab`, no `gazetteerLoadsTableBody`, no `gazetteerLoadDetail`
  in the settings template; no load-history table in the schema; no endpoint
  serving one. The harness had invented the markup it was inspecting, so the
  check could never pass and never said which feature was missing.
- **`reports_page_smoke.mjs`** imports `static/js/pages/reports-page.js` and
  reads a `reports-page-labels` element from the Reports template. **Neither
  exists**, so the harness aborted before running a single check.

The load-history check was replaced by one on code that does exist (server text
is inserted as text, never markup) — now **8/8**. The reports harness is now an
**xfail** naming what is missing, so building the page turns it into an XPASS
instead of the harness quietly staying broken. Both are executed by a new
`tests/unit/test_places_and_reports_smoke.py`.

### 2.4 A stale test asserting an API the code never had

`test_identifiers_intel.py` asserted `signal_type == "email" / "phone" /
"ipv4"`, but `identifiers_intel.py` emits one `signal_type` (`"identifier"`) and
carries the kind in `evidence["kind"]` — which is what the explorer's presets
filter on. `SIGNAL_TYPES` is `("identifier",)` and `signal_query.py` builds its
group from that constant, so "fixing" the code to satisfy the assertions would
have broken the explorer's filtering.

**Checked first** that the behaviour the tests meant to protect was intact — it
was: correct values and surfaces, sentence links, and `user@mail.co.uk`
returning exactly one email rather than an email plus an ipv4 fragment. The
tests were then rewritten against the real contract, and coverage was added for
all five kinds.

### 2.5 Documentation and deployment files that did not exist

A scan for every `docs/*.md` and `deploy/**` reference in tests and tooling
found that **a large part of the documentation was never committed**.

Six were **generated by the repository's own generators** (an earlier
conclusion that no generators existed was wrong — it had checked only one
tool). They splice between `BEGIN`/`END` markers in an existing file rather
than creating one, so each needed its skeleton first:

```
python -m core.interfaces.docgen        docs/INTERFACE_REGISTRY.md
core.interfaces.evidence                docs/REGISTRY_EVIDENCE.md  (both blocks)
python -m core.interfaces.evidence --inventory  docs/endpoint_inventory.json
python -m core.experience.action_audit  docs/ACTION_SURFACE_AUDIT.md
python -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md
python -m core.experience.docgen        docs/EXPERIENCE_CONTRACT.md
python -m core.experience.audit         docs/SCREEN_INSPECTOR_EVIDENCE.md
tools/windows/inventory_dependencies.py docs/windows/DEPENDENCY_INVENTORY.md
tools/docs/generate_reference.py        docs/reference/**
tools/docs/generate_schema.py           docs/reference/DATABASE_SCHEMA.md
```

Two details that cost time and are worth recording: `evidence --write` alone
leaves Part B empty (it passes `application=None`), and Part B is what carries
"Endpoint coverage", "Rendered navigation" and the
"Unmanaged user-facing endpoints: 0" claim — so it was produced from a live
application and a test client. `DATABASE_SCHEMA.md` needed a migrated
PostgreSQL; `pgserver`, already a declared test dependency, provides one, and
the document was generated against **5 of 5 migrations, 43 tables**.

**Authored** where no generator exists: `PRODUCTIZATION_MAP.md`, `SECURITY.md`,
`OPERATIONS.md`, `DEVELOPMENT.md`, `deploy/systemd/syltharae.service`,
`deploy/nginx/syltharae.conf`.

`SECURITY.md` quotes the **exact** Content-Security-Policy the application
serves, and a test compares the document against the live header — so the two
cannot drift. The nginx and unit files were written to the contract in
`test_deploy_configs.py`: `X-Forwarded-For` is *set* to `$remote_addr` rather
than appended (appending would forward a client-chosen address and bypass rate
limits and lockout); `Host` and `X-Forwarded-Host` are both forwarded (without
them **every sign-in answers 400**); and `keepalive_requests` is raised to
100 000 because at nginx's default the HTTP/2 connection was closed every ~20
page views and scripts failed with `net::ERR_FAILED`.

### 2.6 Translations

Every locale except English was 6–7 % untranslated, so pages silently fell back
to English: 343 entries in Arabic, 318 Hebrew, 318 Persian, 393 Croatian.

The gap was worse than *missing*. Most fuzzy entries carried an existing
machine translation that was simply **wrong**: `Component` → "Copies",
`Element` → "The event", `Screen Inspector` → "Screen translations",
"The extracted text could not be exported." → "Could not load the tables."
Those never reached users (the compiled `.mo` excludes fuzzy entries), but
un-fuzzing them would have shipped wrong text, so each was **replaced**.

373 distinct strings across four catalogs. Where a JavaScript i18n pack already
shipped a translation, the **pack's wording was used** so the two agree: the
runtime merges pack then catalog, and two different translations of one string
mean the user sees the catalog's text while the pack's is dead weight — or the
two disagree between a server-rendered page and a JS-rendered update of it.
Five Arabic and one Hebrew entry were aligned this way.

---

### 2.7 Folder ingestions were never checkpointed (the reported resume bug)

**Symptom.** "If the system crashes, shuts down, or if operations are
interrupted while processing a batch of files, clicking 'Retry' causes the
process to start from the beginning rather than resuming from the last
completed checkpoint."

**Root cause.** `IngestionService._make_reader` decided whether to hand the
reader a checkpoint file by asking for a parameter of that *name*:

```python
sig = inspect.signature(factory)
if "checkpoint_file" in sig.parameters:
    kwargs["checkpoint_file"] = self._checkpoint_file(request)
```

The service's own default factory is declared
`def _default_reader_factory(**kwargs)` and forwards straight into
`IntegratedFileReader`, which does accept `checkpoint_file`. But
`inspect.signature` reports a `**kwargs` parameter as a single `VAR_KEYWORD`
entry *named `kwargs`*, so the name `"checkpoint_file"` was never found and the
branch never ran.

**Consequence.** No ingestion started through the job manager ever received a
checkpoint file. `IntegratedFileReader.checkpoint_file` was `None`, no
`CheckpointManager` was built, and nothing was written to the checkpoints
directory — so an interrupted folder run had no checkpoint to resume from:

* discovery reported `skipped=0` on every restart, queueing the whole folder
  again;
* containers (archives, mailboxes) are deliberately exempt from the database
  fallback in `_skip_committed`, so they were re-extracted in full;
* the operator saw progress restart at 0 % with no "Resuming: N files already
  processed" line, which is indistinguishable from starting over.

A run of plain files still *appeared* to resume, which is what made this hard
to see: an unrelated durability guard, `_skip_committed`, hashes each file and
looks it up in the database. It only masks the problem — it re-reads and
re-hashes every file before skipping it, and it cannot cover containers at all.

**Fix.** A `**kwargs` parameter now counts as accepting `checkpoint_file`:

```python
accepts = "checkpoint_file" in parameters or any(
    p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
)
```

A narrow factory that genuinely does not accept it is still not handed one, so
no `TypeError` is introduced.

**Proof.** Measured on a folder of four files and three archives (16 logical
files), killed after the seventh store, then retried:

| | before | after |
|---|---|---|
| checkpoint files written | none | 4 |
| discovery on retry | `files=7 skipped=0` | `files=3 skipped=4` |
| archives re-extracted | all 3 | 1 (only the incomplete one) |

**Why the existing tests missed it.** `test_resume_real_database.py` constructs
`IntegratedFileReader` directly and passes `checkpoint_file=` itself, so it
exercised the reader but never the service. The new
`tests/integration/resume_job_worker.py` drives the real operator path:
`JobManager.create_job` → crash → `JobManager.retry`.

**Proven fail-before / pass-after.** With the change reverted, the new unit
tests fail with `assert None` (the factory received no `checkpoint_file`) and
the integration tests fail with *"it wrote `[]`"* and
`assert 0 > 0` (discovery recognised nothing as already processed). With the
change: `5 passed` for the unit module and `3 passed` for the integration
module. The third integration test — every file stored exactly once — passes
both before and after, which is the point: deduplication always prevented
duplicate rows, so it could never have caught this, and resume was being
mistaken for correctness.

## 3. Architectural decisions

- **Preserve, don't rewrite.** Nothing was re-architected. The layer violation
  was fixed by moving one function into the layer that owns it and injecting
  it, not by relaxing the rule or adding an exemption.
- **Generators are the source of truth.** Where a generator exists, the
  document is generated. Hand-writing what code already knows guarantees drift,
  and several tests exist precisely to catch it.
- **A number in prose is a number waiting to go stale.** The authored documents
  deliberately carry no counts; they point at the generated sources instead.
- **Prefer a loud failure to a silent skip.** The unregistered seeder raises;
  the missing reports page is an xfail that names itself; the JS harnesses are
  now executed by the suite.

## 4. Performance and UX

- **Resume correctness** (§2.1) is the significant behavioural improvement: a
  resumed job no longer reports three spurious failures, which is the
  difference between trusting the resume feature and not.
- **OCR engines now work in a headless environment.** `rapidocr-onnxruntime`
  was installed but `import cv2` failed with `libGL.so.1: cannot open shared
  object file` because the non-headless OpenCV wheel had been pulled in. Using
  the headless wheel (which `requirements.txt` already pins) made OCR
  available: **31 OCR and PDF tests went from failing to passing**.
- **UI completeness in four locales** (§2.6): pages no longer silently fall
  back to English for ~373 strings each.

## 5. Tests actually performed

Run with `python -m pytest`, PostgreSQL provided by `pgserver`.

| Suite | Before | After |
|---|---|---|
| `tests/unit` | 153 failed / 53 errors | **0 / 0** |
| `tests/security` | 1 failed | **0 / 0** |
| `tests/integration` | 6 failed | **0 / 0** |
| `tests/browser` + `tests/e2e` | — | **0 / 0** (skipped: no browser) |

Specific verifications, not just suite totals:

- **Fail-before / pass-after** proven for the resume fix by reverting the
  change: `assert 3 == 0`.
- The installer was run against a live PostgreSQL: database created, 5
  migrations applied, with the injected gazetteer seeder.
- Translations were checked by loading the compiled `.mo` and confirming the
  expected Arabic, Persian and Croatian strings come back.
- `places_settings_smoke.mjs` run directly: **8/8**.
- The nginx/systemd contract tests, the CSP test (documented policy vs. served
  header), and the deploy-config assertions all pass.

**Not tested, and not claimed to be:** browser and end-to-end suites (no
browser in this environment); Tesseract (no system binary — the `rapidocr`
fallback was used instead); and the live-browser smoke suite.

## 6. Remaining limitations

1. **The gazetteer load-history UI is not built.** No table, endpoint, template
   or module. Roughly twenty translation-catalog entries exist for it,
   inherited from a feature that was never completed. Recorded in
   `PRODUCTIZATION_MAP.md`; the harness no longer asserts against invented
   markup.
2. **`static/js/pages/reports-page.js` does not exist**, and the Reports
   template has no `reports-page-labels` element. The harness is an xfail that
   will XPASS when the page is built.
3. **Nine tool-referenced documents are still absent** — referenced only by
   tooling, not by tests, so nothing fails on them: `docs/AUDIT_REPORT.md`,
   `docs/DATABASE.md`, `docs/DATA_ENTRY.md`, `docs/JS_ARCHITECTURE_AUDIT.md`,
   `docs/TESTING.md`, `docs/implementation/CRITERIA_SPINE.md`,
   `docs/implementation/PLACE_SIGNALS.md`,
   `docs/search/PERFORMANCE_ARCHITECTURE.md`,
   `docs/windows/OFFLINE_VALIDATION.md`.
4. **Test-isolation fragility.** Seven `test_experience_contract` tests error
   with *"The setup method 'add_url_rule' can no longer be called"* when run in
   a particular ad-hoc selection with other files. They pass alone and in the
   full suite, but the shared application fixture is order-sensitive and should
   be tightened.
5. **The full suite in one process is OOM-killed** at roughly 47 % in this
   sandbox. The suites were therefore run separately; each is green.
6. **Environment sensitivity.** Two recurring traps: `rapidocr-onnxruntime`
   pulls in the non-headless OpenCV (breaking `import cv2`), and package
   presence changes what `generate_reference.py` and the experience docgen
   emit. Install from `requirements.txt` and regenerate documents after any
   dependency change.
7. **Resolved after delivery: folder ingestions were never checkpointed.**
   The investigation recorded as open above is now closed — see §2.4. The
   defect was not in the checkpoint manager, the retry path or the job
   options, but in one line of `IngestionService._make_reader`, which never
   passed a checkpoint file to the reader.

## 7. Run and deploy

```bash
# Local
python install.py            # database, migrations, gazetteer seed, admin, .env
python verify_readiness.py   # environment checks before relying on it
python run_web.py            # serves on 127.0.0.1:5000

# Tests
python -m pytest tests/unit -q
python -m pytest tests/security -q
python -m pytest tests/integration -q     # provisions its own PostgreSQL
```

Full dependency set: `pip install -r requirements.txt`. On a headless host make
sure the **headless** OpenCV wheel is the only one installed (see §6.6).

```bash
# Production (single process, on loopback behind a TLS proxy)
sudo cp deploy/systemd/syltharae.service /etc/systemd/system/
sudo cp deploy/nginx/syltharae.conf /etc/nginx/sites-available/syltharae
sudo ln -s ../sites-available/syltharae /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl enable --now syltharae
```

The service is single-process **by design** — the application keeps bounded
in-process worker threads and a checkpoint journal, so a second instance would
give two writers the same journal. Scale by sizing the host. Start-up
validation refuses dangerous settings and exits `2`, which the unit maps to
`RestartPreventExitStatus` so a misconfigured deployment stops instead of
restart-looping.

After changing dependencies or adding modules, regenerate:

```bash
python -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md
python -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md
python -m core.experience.action_audit docs/ACTION_SURFACE_AUDIT.md
python -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md
python -m core.experience.audit docs/SCREEN_INSPECTOR_EVIDENCE.md
python tools/docs/generate_reference.py
python tools/windows/inventory_dependencies.py
python tools/docs/generate_schema.py     # needs a migrated PostgreSQL
```

See `docs/OPERATIONS.md` for day-to-day operation, `docs/SECURITY.md` for the
security posture, and `docs/DEVELOPMENT.md` for the enforced development rules.
