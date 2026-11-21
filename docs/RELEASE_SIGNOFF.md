# Final Release Sign-Off — Stage 3 (pre-release)

| | |
| --- | --- |
| Branch | `arena/5ea93606-syltharae` (branched from `156cd0f`, `main`) |
| Stage commits | `1007def` licensing · `4a2a02e` concurrency · `7b4ca89` reports harness · `07c953e` translation handoff · `220c5c4` docs · `7b5afd1` RTL audit check · `987a7bb` reference docs |
| Role | Principal Release Engineer / Architect / QA Lead |
| Scope | close the actionable leftovers of Audit Rounds 1–2, hand the decision-gated items over without guessing, and state whether this build is releasable |
| Environment | CPython 3.11, PostgreSQL 16.2 (cluster `/tmp/pgdata`), 2 vCPU / 3.8 GB, `requirements.lock` install |

**Verdict: Ready with Known/Documented Exceptions.** Two exceptions are the same
one — the four incomplete catalogues and the Croatian registry question — and
both are handed over with numbers, worksheets and a decision brief. Nothing else
found in this stage is open.

---

## 1. Changes Implemented

### 1.1 Licensing: two dependency pins reported as "unknown licence"

| | |
| --- | --- |
| Files | `tools/licenses/check_licenses.py`, `tests/unit/test_licensing.py` |
| Root cause | `pymupdf4llm` and `pymupdf_layout` are hard `==2.1` requirements of PyMuPDF 2.1 and are **empty** distributions: their `METADATA` says they exist only to dodge a pip 1.x→2.x upgrade bug and that the code moved into `pymupdf`, their `RECORD` lists only dist-info files, and neither declares a licence. The checker correctly refused to guess. |
| Behaviour change | Two `REVIEWED` entries map them to `AGPL-3.0-only` (PyMuPDF's own licence) with that rationale; the new test `test_the_pymupdf_companion_pins_ship_no_code` re-proves the claim, so a release that starts shipping code in either package fails the test instead of sliding through the exception. |

### 1.2 Concurrency: a thread-only host is described, not stubbed

| | |
| --- | --- |
| Files | `Api/routes/concurrency.py`, `templates/concurrency/dashboard.html`, `static/js/pages/concurrency-dashboard-page.js`, `tests/unit/test_concurrency_metrics_fallback.py` (new, 8), `tests/js/concurrency_fallback_smoke.mjs` (new, 12 checks), `tests/unit/test_concurrency_manager_start.py`, `tests/unit/test_jobs_page_states_smoke.py` |
| Root cause | The five-second bounded start turned a hung manager into `{"status": "error", "error": "Internal error (ERR-…)"}`: a log reference where a reason belongs, no figures for the two managers that need no multiprocessing, and a 500 from `/api/processes` and `/api/pools`. |
| Behaviour change | `/concurrency/api/metrics` reports `mode` (`full` / `threads-only`), `degraded`, `unavailable[]`, keeps the live thread/async cards, answers each unavailable card with a fixed sentence plus `reason_code: multiprocessing_unavailable`, and adds `metrics.worker_capacity` (recommended workers, CPU count) in degraded mode. `/api/processes` and `/api/pools` answer `{success: true, available: false, reason, []}`. The dashboard shows a banner only when the server says it is degraded and explains an empty list instead of claiming there are none. Healthy path verified unchanged on a real request: `mode: full`, four active managers, no `worker_capacity`. |

### 1.3 Reports page: a harness that could never run

| | |
| --- | --- |
| Files | `tests/js/reports_page_smoke.mjs` (deleted), `tests/unit/test_places_and_reports_smoke.py` |
| Root cause | The harness imported `static/js/pages/reports-page.js` and read a `reports-page-labels` element; neither has ever existed in this repository, so it aborted before its first check and the suite ran it as a strict xfail — a permanent "still broken" record that would have failed as XPASS if anyone implemented the module. The page is server-rendered on purpose (its template says so; the CSP has no `unsafe-inline`). |
| Behaviour change | The dead harness is gone; `test_the_reports_page_is_server_rendered_without_a_page_module` asserts the contract that matters — server-side totals in the HTML, no page module, no inline script, and the template keeps the sentence describing the design. |

### 1.4 Translation: the gap is handed over as worksheets

| | |
| --- | --- |
| Files | `tools/i18n/export_translation_gaps.py` (new), `tests/unit/test_translation_gap_export.py` (new, 9), `audit_report/findings/i18n_untranslated_{ar,he,fa,hr}.csv` + `_summary.json` (new), `docs/TRANSLATION_HANDOFF.md` (new) |
| Root cause | The coverage gate is a 100 % gate and 1 236 entries are empty or fuzzy. That is a translator's job; it was recorded only as four red tests. |
| Behaviour change | The exporter writes one CSV per language with `msgid`, `msgid_plural`, `status` (`missing`/`fuzzy`/`partial`), `module`, the untrusted `current_draft` (fuzzy/partial only), `sources` (`path:line`) and a `translation` column that is empty in every row. It never writes into `translations/` (a test proves the catalogues stay byte-identical), and `--check` fails when the committed sheets stop matching the catalogues, so the handoff cannot rot. |

### 1.5 Audit tooling: two checks that could not fail

| | |
| --- | --- |
| Files | `tools/audit/check_structure.py`, `tests/unit/test_audit_i18n_reporting.py` (new, 8) |
| Root cause (a) | `i18n_coverage()` reads a generated inventory; when the file was absent it printed `msgids with an incomplete catalogue: 0` — a pass for a measurement nobody ran. |
| Root cause (b) | `rtl_check()` looked for `dir="{{` and so reported "no dir attribute" for `base.html`'s `dir="{% if … %}rtl{% else %}ltr{% endif %}"`; asking whether the attribute exists is also the weaker question, since a hardcoded `dir="ltr"` would pass. |
| Behaviour change | (a) says "not measured", names `tools/audit/build_inventories.py`, and reports the catalogue gap straight from the `.po` files — `1236 (ar 309, he 284, fa 284, hr 359)` — so the number is real on a clean checkout. (b) reports `direction_is_derived_from_the_language`, with the templated, hardcoded and real-shell cases all pinned. |

### 1.6 Documentation

`docs/AUDIT_ROUND_2.md` gains §1.11–§1.14 and a rewritten §5 (two items closed,
two handed over); `CHANGELOG.md` gains the same entries under Unreleased;
`docs/reference/{README.md,python/tools.md}` regenerated with
`tools/docs/generate_reference.py` (the stage's new module and changed tool moved
them).

---

## 2. Decision-Gated Handoff

### 2.1 Translation completion — 1 236 entries, four locales

| locale | catalogue entries | POT msgids translated | gap | missing | fuzzy | worksheet |
| --- | --- | --- | --- | --- | --- | --- |
| `ar` | 4 270 | 3 404 / 3 715 (91.63 %) | **309** | 74 | 235 | `audit_report/findings/i18n_untranslated_ar.csv` |
| `he` | 4 032 | 3 154 / 3 715 (84.90 %) | **284** | 66 | 218 | `…_he.csv` |
| `fa` | 4 032 | 3 154 / 3 715 (84.90 %) | **284** | 66 | 218 | `…_fa.csv` |
| `hr` | 4 104 | 3 354 / 3 715 (90.28 %) | **359** | 80 | 279 | `…_hr.csv` |
| **total** | | | **1 236** | **286** | **950** | |

The exportable msgid list is exactly those four CSVs (1 236 rows, `translation`
column blank). Largest shared block: the same 50 `Api/routes` flash/error strings
appear in all four locales — one review covers four languages there. Per-area
tables and the apply → recompile → verify workflow are in
`docs/TRANSLATION_HANDOFF.md` §2–§3.

**No `NEEDS-DECISION` string was edited, no catalog was machine-filled, and no
`--previous`/obsolete-stripping regeneration was used.**

### 2.2 Croatian (`hr`) availability — three options, none chosen

State today, all test-pinned: `hr` has a maintained, compiled catalogue (90.28 %
of the POT) but is **not** in `SUPPORTED_LANGUAGES`, so it is not offered by the
switcher, the Settings selector, `/set_language/<code>` or the Translation
Manager (`supported_translation_languages()` filters the registry), and there is
no `static/js/i18n/locales/hr.js` pack.

| option | what it changes | cost | consequence |
| --- | --- | --- | --- |
| **A — keep excluded, documented** (current) | nothing in code; this report + `TRANSLATION_HANDOFF.md` §4 are the record | zero | a Croatian user sees English with no hint a 90 % catalogue exists; nothing breaks, nothing is promised |
| **B — enable in the UI and the editing UI** | registry entry (`settings/languages.py`), the `ImportError` fallback in `settings/settings_models.py:602`, a new `static/js/i18n/locales/hr.js` pack, the 359 `hr` translations, and three tests that pin the exclusion | the 359 translations (dominant) + pack + ~6 files | `hr` becomes supported; the gap becomes user-visible and the gate release-blocking |
| **C — explicit configuration flag** | a setting declaring "maintained but not offered", read by `supported_translation_languages()` / the manager, with per-state tests | ~2 days of design + tests, new config surface | the ambiguity becomes an explicit switch; two states to test, and a half-offered language is a support question |

Recommendation summary, not a decision: **A now**, **B** if Croatian is a target
market (after the translations land), **C** only if more maintained-but-unoffered
locales are expected. The owner must choose; this stage did not.

---

## 3. Final Verification Matrix

Every row was run in this stage on the tree at `7b5afd1` unless marked otherwise.
Counts come from `--junitxml`, never from a log tail.

| # | Check | Status | Result | Command / artifact |
| --- | --- | --- | --- | --- |
| 1 | Unit suite | **[executed]** | 3 965 collected · **0 failed** · 0 errors · 26 skipped (688 s) on the committed tree | `pytest tests/unit --junitxml=/tmp/junit_unit_gate.xml` |
| 2 | Security suite | **[executed]** | 411 collected · **0 failed** · 0 skipped (76 s) | `pytest tests/security --junitxml=/tmp/junit_security.xml` |
| 3 | Integration suite | **[executed]** | 1 494 collected · **4 failed** · 4 skipped (433 s) — all four are the locale gates of §2.1 (`test_no_catalog_entry_is_empty_or_fuzzy[ar|he|fa|hr]`) | `REQUIRE_POSTGRES=1 pytest tests/integration --junitxml=/tmp/junit_integration.xml` |
| 4 | End-to-end suite | **[executed]** | 23 collected · **0 failed** (8.5 s) | `pytest tests/e2e --junitxml=/tmp/junit_e2e.xml` |
| 5 | JS harnesses | **[executed]** | 24 harnesses: 23 green standalone, 1 (`action_lifecycle_smoke.mjs`) requires `--dialog-html` and is executed by `test_action_lifecycle_components.py` (19 passed) | `node tests/js/*_smoke.mjs` |
| 6 | Licensing — supported lock env (see notes) | **[executed]** | 117 compatible · 0 incompatible · 0 unknown · 0 not installed | `/tmp/lockenv/bin/python tools/licenses/check_licenses.py` |
| 7 | Licensing — clean `requirements.txt` venv | **[executed]** | 118 compatible · 0 incompatible · 0 unknown · 0 not installed (+ `--extras`: 2 not installed) | `/tmp/cleanenv/bin/python tools/licenses/check_licenses.py` |
| 8 | Licensing — development image | **[executed]** | 117 compatible · 0 incompatible · 0 unknown · 0 not installed (one fewer transitive distribution present: the image ships only `opencv-python-headless`) | `python tools/licenses/check_licenses.py` |
| 9 | Lock environment integrity | **[executed]** | exit 0; `opencv usable 5.0.0 (owners: opencv-python-headless)`; `import cv2` works | `/tmp/lockenv/bin/python tools/audit/env_lock.py --check` |
| 10 | `test_licensing.py` in two environments | **[executed]** | 23 passed in the dev image, 23 passed in the clean venv, 23 passed in the lock env | `pytest tests/unit/test_licensing.py` |
| 11 | Release readiness probe | **[executed]** | 31 checks · 1 failed · 0 critical → **READY**; the single failure is the deployment WARN (Flask dev server, no reverse proxy, plain HTTP) | `python verify_readiness.py` |
| 12 | Structure audit | **[executed]** | 0 page routes missing from the registry; i18n coverage now reports `not measured (inventory missing)` + `1236` catalogue gaps; RTL: dir attribute present, direction derived from the language | `python tools/audit/check_structure.py` |
| 13 | Frontend wiring | **[executed]** | 59 `render_template` targets · 0 findings | `python tools/audit/check_frontend_wiring.py` |
| 14 | Vocabularies | **[executed]** | 0 findings | `python tools/audit/check_vocabularies.py` |
| 15 | Reference docs freshness | **[executed]** | up to date (2 files regenerated in `220c5c4`) | `python tools/docs/generate_reference.py --check` |
| 16 | Translation worksheets freshness | **[executed]** | agree with the catalogues (1 236 rows) | `python tools/i18n/export_translation_gaps.py --check` |
| 17 | `m0006_index_hygiene` — fresh database | **[executed]** | migrations `0001…0006` applied, head `0006`; `idx_paths_hash_id` **absent**; `idx_alerts_scenario_id` and `idx_alerts_scenario_evaluation_id` **present**; 160 indexes; downgrade restores the dropped index exactly and removes both new ones | scratch database on `/tmp/pgdata` |
| 18 | Migration freeze gate | **[executed]** | 13 passed (`test_migration_sequence.py`, `test_notification_volume_guards.py`) — `m0006` still the recorded, reviewed exception | `pytest tests/unit/test_migration_sequence.py tests/unit/test_notification_volume_guards.py` |
| 19 | Backup / restore runbook | **[executed]** | `pg_dump -Fc` → 278 KB; `pg_restore -l` lists 41 table-data sections; restore into a fresh database reproduced **41 tables / 19 190 rows, 0 mismatches** | commands from `docs/OPERATIONS.md` §Backup and restore |
| 20 | Backup roundtrip suite | **[executed]** | green (with `test_packaging_assets.py`) | `pytest tests/integration/test_backup_roundtrip.py tests/unit/test_packaging_assets.py` |
| 21 | Wheel / packaging | **[executed]** | `file_analysis-2.2.0-py3-none-any.whl`, 11.4 MB, 2 882 files: templates, static (incl. `js/i18n/locales/*.js`), translations (incl. `hr` `.mo`), all migrations incl. `m0006`, `LICENSE` + `THIRD_PARTY_NOTICES.md`; metadata `License-Expression: AGPL-3.0-or-later`, `Requires-Python: >=3.10` | `pip wheel . --no-deps -w /tmp/wheelout` |
| 22 | Live smoke — UI | **[executed]** | **44/44** registered `USER_INTERFACE` GET routes answered 200 as an authenticated admin | Flask server on `0.0.0.0:8123`, real HTTP |
| 23 | Live smoke — APIs | **[executed]** | `/health` 200 `{"status":"healthy","checks":{database,gazetteer,settings}}`; `/concurrency/api/metrics` 200 `mode=full`; `/concurrency/api/{processes,pools,threads}` 200 `available=true`; `/api/export/categories` 200 CSV; `/api/nope` 404 JSON envelope with `request_id` | same server |
| 24 | Live smoke — headers & CSP | **[executed]** | `script-src 'self'` (no `unsafe-inline`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`; rendered page carries 0 executable inline scripts (5 `application/json` data blocks) | same server |
| 25 | Live smoke — degraded concurrency UI | **[executed]** (via tests, not via a degraded host) | `tests/unit/test_concurrency_metrics_fallback.py` 8/8, `concurrency_fallback_smoke.mjs` 12/12 against the real page module | cannot be produced on this host: `multiprocessing` works here |
| 26 | Croatian enablement | **[not run]** | blocked on the decision in §2.2 (option B would require the 359 translations, a client pack and three test flips) | — |
| 27 | Translation completion | **[not run]** | blocked on a human translator; 1 236 entries, worksheets delivered | — |
| 28 | Production topology (Waitress + TLS proxy) | **[not run]** | out of scope for this environment: no reverse proxy is present. `verify_readiness.py` reports it as the single WARN, and `deploy/nginx/syltharae.conf` is the documented answer | — |

### Notes on the numbers

* `requirements.lock` (installed with `--no-deps`) is the *supported* environment,
  and it is the one row 6 measures. A plain `pip install -r requirements.txt`
  resolves, but on Linux it pulls the GUI `opencv-python` through
  `rapidocr-onnxruntime`, which breaks `import cv2` (`libGL.so.1`) — documented in
  both files and excluded from the lock; `tools/audit/env_lock.py --check` (row 9)
  is the guard.

* The unit suite was run three times in this stage. Run 1 (3 962 collected) failed
  `test_reference_docs_are_current` (generated pages stale) and
  `test_run_leaves_no_tracked_changes` (it asserts a clean tree and cannot pass
  while a stage's own edits are uncommitted). Run 2 (3 965 collected, after three
  RTL tests were added) failed only `test_reference_docs_are_current` — the audit
  tool's docstring had moved the generated `docs/reference/python/tools.md` again.
  Both causes are fixed (`220c5c4`, `987a7bb`), and run 3 — row 1 — is the number
  that counts: 0 failures on the committed tree.
* Integration's 4 failures are identical before and after this stage's changes —
  no regression was introduced, and the count matches §2.1 exactly (309+284+284+359).
* `pytest --timeout` is unavailable in this image; long suites were run as single
  processes, never two at once (the host peaks near 3 GB).

---

## 4. Release Readiness Verdict

### **Ready with Known/Documented Exceptions**

| id | exception | why it is acceptable | what closes it |
| --- | --- | --- | --- |
| E1 | 1 236 untranslated/fuzzy entries in `ar`/`he`/`fa`/`hr`; language falls back to the English source string | the fallback is truthful and readable; machine output would ship wrong legal/safety wording in four scripts | the translator handback (`docs/TRANSLATION_HANDOFF.md` §3) |
| E2 | `hr` maintained but not offered | the registry is explicit and test-pinned; no user sees a half-supported language | decision §2.2 (option A keeps it as is) |
| E3 | Production topology not exercised (dev server, plain HTTP, no reverse proxy) | declared by `verify_readiness.py` as the single WARN; `deploy/nginx/syltharae.conf` + `WSGI_SERVER=waitress` are the documented deployment | deploy behind the provided proxy and re-run `verify_readiness.py` |
| E4 | `tests/js/reports_page_smoke.mjs` deleted | the page is server-rendered by design and the property is now tested against the real template | none — closed |

### Sign-off checklist

- [x] Every Round-1/2 leftover is either fixed, or handed over with numbers and an owner decision (E1, E2).
- [x] Security suite fully green (411/411) and the three Round-2 security fixes still covered.
- [x] Unit suite green (3 965 / 0 failed) on the committed tree.
- [x] No regression in integration: the only failures are the handoff gate (4).
- [x] Licensing answers 0 unknown / 0 incompatible in the supported lock environment, a clean install, and the development image.
- [x] `m0006_index_hygiene` verified against a live database, including reversibility; the freeze exception is intact.
- [x] Backup/restore runbook executed end to end (41 tables / 19 190 rows, 0 mismatches).
- [x] Wheel builds and contains templates, static assets, catalogues and migrations.
- [x] Live smoke: 44/44 UI routes, health probe, concurrency APIs, CSP and security headers.
- [x] No `NEEDS-DECISION` translation string was modified; no catalogue was machine-filled or re-serialised.
- [x] Every check in §3 carries `[executed]` with its count, or `[not run]` with its blocker.
- [ ] **Decision owner: choose A / B / C for Croatian, and schedule the translator handback.** ← the only open item this stage cannot close itself.

Signed: Principal Release Engineer / Architect / QA Lead — Stage 3, 2026-11-13.
