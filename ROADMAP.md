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

Full unit suite: **154 failed / 53 errors before and after**, failure sets
byte-identical. Those failures are missing optional dependencies in the
sandbox, not defects.

### Not reproduced

**Folder ingestion restarting from the beginning after Retry — cause not
found.** This is the original report and it is still open.

Tested and working, so these are not the cause:

| Hypothesis | Verdict |
|---|---|
| Retry goes through some other endpoint | No — only `/api/jobs/<id>/retry` and `/resume` exist. |
| `source`/`side` resolve differently on retry | No — `_resolve_identifier` is idempotent by design. |
| Job options do not round-trip | No — JSONB decoded to dict, `path`/`source`/`side`/`processing` all preserved. |
| UI sends `checkpoint: fresh` | No — defaults to `auto`. |
| Multiprocessing worker copies | Not reachable — `_make_reader` never sets `use_multiprocessing`, so it is always threading. |
| Marking never happens on the folder path | No — `_process_with_threads` and `_process_sequential` both reach `_process_file_worker`, which marks. |
| `recover_stale_jobs()` deletes checkpoints | No — it only flips job status. |
| Checkpoint engine broken | No — verified end-to-end with a stubbed store at 1/4/8 workers, hard kill, and across mid-run compaction. |

What is now in place to catch it next time: every run logs
`Checkpoint <status>: N file(s) will be skipped | snapshot=… journal=… | <reason>`,
and `rejected` is a WARNING. **One log line from a failing Retry identifies
whether the checkpoint was seen, and why it was not used.**

---

## 3. Known defects, with disposition

| # | Defect | Disposition |
|---|---|---|
| a | Multiprocessing path (`_process_with_pool`) ships the worker to separate processes; each holds a copy of the checkpoint manager, and concurrent appends to one journal can interleave. | **Deferred.** Unreachable from the web/job path. Fixing it properly means marking in the parent from worker results. |
| b | Windows directory fsync is a no-op (`os.open` on a directory fails). NTFS is more forgiving, but the power-loss guarantee is weaker than on POSIX. | **Accepted with residual risk.** Documented. A `FlushFileBuffers` implementation via a directory handle is possible if it matters. |
| c | Archive members inside a *file-list* batch get no member checkpoint, because `process_single_file` clears the checkpoint base on every top-level call. | **Documented, not changed.** The clearing exists so a single file cannot inherit a previous run's base. The container-level skip covers the common case. |
| d | The identifier includes a local-time ISO date; across a DST boundary a file modified near midnight can get a new identity and be reprocessed. | **Inferred, not reproduced.** Affects individual files, never a whole corpus. |
| e | The `jobs.source` column holds a folder path for ingestions and an entity name for imports. | **Documented, not changed** — it is display-facing and changing it would alter existing screens. |

---

## 4. Open questions

Answering these turns the above into a real roadmap. Ranked by how much they
unblock.

1. **What does the log say on a failing Retry?** One line beginning
   `Checkpoint ` — is it `resumed`, `fresh`, or `rejected`, and what reason?
   This alone settles whether the checkpoint is being found.
2. **Is the checkpoint directory actually being written to?** On Windows,
   `%LOCALAPPDATA%\file-analysis\checkpoints`. Do `checkpoint_*.json` /
   `*.journal` files appear during a run? Is `APP_DATA_DIR` set (to an
   absolute path)?
3. **Is the corpus local or on a network share / mapped drive?** A mapped
   drive (`Z:\…`) and its UNC form (`\\server\share\…`) produce different
   checkpoint identities — that is a live candidate for the open case.
4. **How large is the corpus, and how far does a run get before it dies?**
5. **What are the product goals?** There is no written statement of what
   SYLTHARAE is for, who uses it, or what "done" means for the platform.
   Everything in section 1 is inferred from code.

---

## 5. Suggested next steps (pending answers above)

- Close the folder-retry case using the diagnostics now shipped.
- Fix the multiprocessing checkpoint path (a) if that code path is ever enabled.
- Decide whether Windows directory-flush (b) is worth a `FlushFileBuffers` call.
- Write down the product goals (5); everything else is prioritised against them.
