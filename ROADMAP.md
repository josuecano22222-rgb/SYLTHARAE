# Roadmap

**This is not a product roadmap.** The repository has never had written goals,
so none are invented here. What follows is: the current state as verified in
code, the work in flight, the known defects with the reason each is or is not
being fixed, and the questions that have to be answered before real goals can
be written down.

Last updated: 2026-11-13 (version 2.2.0).

---

## 1. Current state (verified)

The platform ingests a corpus from disk, stores content and metadata in
PostgreSQL, and exposes search, analysis, mapping and export through a Flask
UI, a CLI and a JSON API. Ingestion runs as a job that can be queued, paused,
cancelled, retried and watched live.

Capabilities below are **inferred** from module layout and have not been
audited feature by feature: identifier extraction, temporal and calendar
analysis, place intelligence with offline map packages, a signal model with
re-detection, retention policies, scheduled jobs, scenario monitoring, and a
translation memory with five gettext locales (`en`, `ar`, `fa`, `he`, `hr`).

---
## 2. In flight: crash-safe resume

**Objective.** When a job crashes, the system shuts down, or an operation is
interrupted, Retry (and Resume) must continue from the last completed
checkpoint and must not reprocess stored files.

**Evidence standard.** "Closed" means a runtime test or a reproduction. Code
reading alone is not accepted as evidence for any row below, and every fix
cites a test that fails on the old code and passes on the new.

### Done

| # | Item | Evidence |
|---|---|---|
| 1 | File-list and `batch_import` runs now get a checkpoint. Previously `_checkpoint_file()` returned `None` whenever `request.path` was unset, so **no** batch was ever checkpointed. | `tests/unit/test_batch_checkpoint_resume.py` (9 tests, incl. an `os._exit` hard-kill subprocess) |
| 2 | Fixed `total_files = inventory['total'] - skipped_count` — skipped was subtracted twice, producing negative work totals and making the "all files already processed" branch unreachable. | `tests/unit/test_folder_resume_accounting.py` (4 tests) |
| 3 | Directory fsync after creating a journal and after the snapshot rename. | `core/checkpoint_manager.py` |
| 4 | Resume is observable: a `resume_report` on every manager, one log line, `resume_info` in job stats, a **Resume** row on the job detail page, and `scripts/checkpoint_inspect.py`. | `tests/unit/test_checkpoint_observability.py` (9 tests) |
| 5 | `safe_resolve()` — a path that cannot be resolved no longer disables checkpointing for the whole corpus. | same file |
| 6 | Failed checkpoint marks are WARNING (once) instead of DEBUG. | same file |
| 7 | `batch_import` can be cancelled and paused (callbacks were never wired). | `tests/unit/test_batch_import_control_flags.py` (3 tests) |
| 8 | File-list runs no longer double-count progress (`files_total` was ~2× real). | `test_batch_checkpoint_resume.py` |
| 9 | `retry()`/`resume()` document and centralise what the `source` column carries. | `services/jobs/manager.py` |
| 10 | `docs/resume-and-checkpoints.md`. | — |
| 11 | **Multiprocessing pool proven unreachable** — not by reading, by constructing readers through the real `IngestionService._make_reader` at every worker count and by an AST scan of the whole repo that fails if any caller ever passes the flag. | `tests/unit/test_multiprocessing_unreachable.py` (4 tests) — commit `798285e` |
| 12 | **Identifier stability measured.** Path form is stable; the local-time date is not. | `tests/unit/test_checkpoint_identifier_stability.py` (8 tests) — commit `798285e` |
| 13 | **A checkpoint that cannot be written is now loud.** Two defects fixed: journal write failures left `mark_failures` at 0, and an unusable checkpoint dir aborted setup silently. | `tests/unit/test_checkpoint_write_failures.py` (3 tests) — commit `b84d8f0` |
| 14 | **Every rejection branch records a reason** and logs it at WARNING. Includes the hard-kill case: a truncated final journal line no longer invalidates the run. | `tests/unit/test_checkpoint_rejection_branches.py` (9 tests) — commit `0454337` |
| 15 | **Windows directory flush implemented** via a ctypes directory handle and `FlushFileBuffers`. | `tests/unit/test_fsync_dir_windows.py` (9 tests) — commit `3264b20` |
| 16 | **A container is skipped on retry**, not re-extracted. | `tests/unit/test_container_resume.py` — commit `ed5fb46` |
| 17 | **The job detail poll harness runs.** It crashed on a missing `clearInterval` and on a stub DOM with no `isConnected`; both fixed. | `tests/js/job_detail_poll_smoke.mjs` — commit `36e5737` |

Full unit suite: **154 failed / 53 errors before and after**, failure sets
byte-identical. Those failures are missing optional dependencies in the
sandbox, not defects. JS harnesses: 19 of 22 pass; the 3 failures are
pre-existing and unchanged by this work.

### Not reproduced

**Folder ingestion restarting from the beginning after Retry — cause not
found.** This is the original report and it is still open.

It is now much narrower than it was. The corpus is local, and checkpoint files
are confirmed to be written, so the surviving possibilities are: the
checkpoint is found and used and the *reporting* makes it look like a restart;
or it is found and rejected for a reason the log states; or the digest differs
between the two runs. One log line separates all three.

Tested with runtime tests, so these are not the cause:

| Hypothesis | Verdict | Proof |
|---|---|---|
| Retry goes through some other endpoint | No — only `/api/jobs/<id>/retry` and `/resume` exist. | earlier investigation |
| `source`/`side` resolve differently on retry | No — `_resolve_identifier` is idempotent by design. | earlier investigation |
| Job options do not round-trip | No — JSONB decoded to dict, `path`/`source`/`side`/`processing` all preserved. | earlier investigation |
| UI sends `checkpoint: fresh` | No — defaults to `auto`. | earlier investigation |
| Multiprocessing worker copies | **No — proven unreachable.** Readers built through the real service never set the flag at any worker count; an AST guard fails if any caller ever does. | `test_multiprocessing_unreachable.py` |
| Marking never happens on the folder path | No — `_process_with_threads` and `_process_sequential` both reach `_process_file_worker`, which marks. | earlier investigation |
| `recover_stale_jobs()` deletes checkpoints | No — it only flips job status. | earlier investigation |
| Checkpoint engine broken | No — verified end-to-end with a stubbed store at 1/4/8 workers, hard kill, and across mid-run compaction. | earlier investigation |
| The checkpoint is discarded on load | **No — every discarding branch was tested.** All report `rejected` with a reason, and a truncated final journal line (the normal hard-kill result) still resumes. | `test_checkpoint_rejection_branches.py` |
| A container is re-extracted | **No.** A retry over a completed checkpoint stores nothing and returns no results. | `test_container_resume.py` |
| The corpus is on a network path, so the identity differs between runs | **No, for this installation.** The operator confirms the corpus is a local path (`C:\…`). Mapped-drive vs UNC is therefore not in play here. | operator answer, 2026-11-13 |
| No checkpoint is ever written to disk | **No, for this installation.** The operator confirms `checkpoint_*.json` / `*.journal` files do appear in the checkpoints directory during a run. So the engine is persisting; the question is what happens to those files on Retry. | operator answer, 2026-11-13 |

Two defects that *could* have caused it were found and fixed while testing
(row 13 above): a run could lose every checkpoint mark while its own
accounting reported zero failures, and an unusable checkpoint directory could
abort setup without saying that resuming was disabled. Neither is confirmed as
the cause of the original report — no log from a failing Retry has been seen.

What is in place to catch it next time: every run logs
`Checkpoint <status>: N file(s) will be skipped | snapshot=… journal=… | <reason>`,
and `rejected` is a WARNING. **One log line from a failing Retry identifies
whether the checkpoint was seen, and why it was not used.**

---

## 3. Known defects, with disposition

| # | Defect | Disposition |
|---|---|---|
| a | Multiprocessing path (`_process_with_pool`) ships the worker to separate processes; each holds a copy of the checkpoint manager, and concurrent appends to one journal can interleave. | **Deferred — proven unreachable.** No call site, config key, CLI flag or env var can enable it. Two guard tests fail if one ever appears. Fixing it properly means marking in the parent from worker results. |
| b | Windows directory fsync was a no-op (`os.open` on a directory fails), so a journal's *name* was not made durable even though its contents were. | **Fixed.** `_fsync_dir_windows` opens a directory handle with `FILE_FLAG_BACKUP_SEMANTICS` and calls `FlushFileBuffers`. **Residual risk, stated plainly:** whether this survives a real power cut depends on the disk, its write cache and its driver, none of which can be exercised from a Linux sandbox. The call sequence and every failure path are tested with an injected ctypes; the durability itself is not. |
| c | Archive members inside a *file-list* batch get no member checkpoint, because `process_single_file` clears the checkpoint base on every top-level call. | **Documented, not changed.** The clearing exists so a single file cannot inherit a previous run's base. The container-level skip covers the common case, and is now tested. |
| d | The identifier includes a local-time ISO date; across a DST boundary a file modified near midnight can get a new identity and be reprocessed. | **Reproduced; accepted by decision.** Measured: one file with mtime 2026-06-15 00:30 UTC reports `modified=2026-06-15` under `TZ=UTC` and `2026-06-14` under `TZ=Etc/GMT+12`, and the identifier changes with it. Affects individual files, never a whole corpus. The tradeoff was put to the operator (fix = replace the local date with `mtime_ns`, invalidating every existing checkpoint) and the decision on 2026-11-13 was to **keep the local date**. No change. |
| e | The `jobs.source` column holds a folder path for ingestions and an entity name for imports. | **Documented, not changed** — it is display-facing and changing it would alter existing screens. |
| f | A truncated final journal line is still parsed as a short identifier, so one spurious entry can appear after a hard kill. | **Harmless, asserted rather than hidden.** Real identifiers are 64-bit hashes, so a truncated prefix is a small number that cannot collide with one and cannot wrongly skip a file. `test_truncated_final_journal_line_still_keeps_earlier_entries` asserts the real entries survive. |

---

## 4. Open questions

Answering these turns the above into a real roadmap. Ranked by how much they
unblock.

**Answered on 2026-11-13** (marked A2/A3 below):

- **A2 — the checkpoint directory IS written to.** The operator confirms
  `checkpoint_*.json` / `*.journal` files appear during a run. The engine is
  persisting, so "the checkpoint was never written" is ruled out for this
  installation.
- **A3 — the corpus is a LOCAL path** (`C:\…`). Mapped-drive vs UNC is not
  in play, so that theory is closed for this installation. It remains a latent
  risk for anyone running over a share.

**Still open:**

1. **What does the log say on a failing Retry?** One line beginning
   `Checkpoint ` — is it `resumed`, `fresh`, or `rejected`, and what reason?
   This alone settles whether the checkpoint is being found, and it is now the
   only thing standing between this and a diagnosis. The operator can supply
   it; the text has not yet been received.
2. **How was the corpus path entered the first time and on Retry?** Both forms
   must produce the same digest. The corpus is local, so the variant that
   matters is path *case* (`C:\Data` vs `c:\data`): the digest uses the path
   as given while the file identifier lowercases it, and Windows
   `Path.resolve()` is expected to normalise case for existing paths —
   unverified. Settled by comparing the digest in
   `checkpoint_inspect.py --list` across both runs.
3. **How large is the corpus, and how far does a run get before it dies?**
4. **What are the product goals?** There is no written statement of what
   SYLTHARAE is for, who uses it, or what "done" means for the platform.
   Everything in section 1 is inferred from code.

---

## 5. Suggested next steps

- Close the folder-retry case using the diagnostics now shipped. **Blocked on
  open question 1**: one log line from a failing Retry. Everything else has
  been either tested away or answered.
- If that line says `resumed`, the engine is working and the remaining defect
  is in reporting — the fix is in the UI/log totals, not the checkpoint.
  If it says `rejected`, the reason in the line names the cause. If it says
  `fresh`, the digest differs between runs and open question 2 settles why.
- Confirm the Windows directory flush (b) against a real power cut, if that
  guarantee is wanted.
- Write down the product goals (4); everything else is prioritised against
  them.
