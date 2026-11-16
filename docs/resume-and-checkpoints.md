# Resume and checkpoints

How SYLTHARAE remembers work that is already done, what happens when you click
**Retry**, and how to find out why a particular run did or did not resume.

Everything below is verified against the code as of this document. Where
something is inferred rather than tested, it says so.

---

## 1. The one-paragraph version

A run records the identity of every file it successfully stored. A retry builds
the same checkpoint identity, loads it, and skips the files it finds. It does
not re-read, re-extract or re-store them. If the checkpoint cannot be found or
cannot be used, the run starts over — and, since this change, says so in the
log, in the job record and on the job detail page.

---

## 2. Where checkpoints live

```
<data root>/checkpoints/checkpoint_<identity>_<source>_<side>.json
<data root>/checkpoints/checkpoint_<identity>_<source>_<side>.json.journal
<data root>/checkpoints/checkpoint_<identity>_<source>_<side>.members/<digest>.json
```

| File | Purpose |
|---|---|
| `.json` | **Snapshot** — the full set of processed identifiers. Written by compaction or at the end of a run. |
| `.json.journal` | **Journal** — append-only log of identifiers since the last snapshot. This is what makes a crash survivable. |
| `.members/…` | Per-container checkpoints for archive members and attachments. |

The **data root** is, in order:

1. `$APP_DATA_DIR` if set;
2. `%LOCALAPPDATA%\file-analysis` on Windows;
3. `~/.local/share/file-analysis` on POSIX (`$XDG_DATA_HOME` if set).

> If `APP_DATA_DIR` is set to a *relative* path, the checkpoint directory moves
> with your working directory, and every launch looks like a first run. Prefer
> an absolute path.

### The identity

| Request type | Identity |
|---|---|
| Folder (`options.path` set) | `sha256(resolved folder path)[:16]` |
| File list / batch (`options.file_paths`) | `sha256(join of sorted resolved file paths)[:16]` |

Sorted, so re-ordering the same selection does not invalidate it. A different
selection gets a different checkpoint, which is intended: it is different work.

The identity does **not** contain the job id. Retry creates a *new* job id on
purpose and still reaches the same checkpoint.

---

## 3. Policies (`processing.checkpoint`)

| Policy | Behaviour |
|---|---|
| `auto` (default) | Resume if a checkpoint exists, else create one. |
| `fresh` | Delete snapshot, journal and member checkpoints, then start clean. **Logged as a WARNING** — it is the one policy that discards resumable work. |
| `off` | No checkpoint at all. A crash means the next run starts from the beginning. |

---

## 4. How a file is identified

```
sha256( "<normalised path>|<modified>|<size>|<mtime_ns>" )[:12]   # 96 bits, kept as an int
```

- **normalised path**: lower-cased, backslashes converted to forward slashes.
- **modified**: the ISO date from `get_standardized_metadata()`.
- **size / mtime_ns**: from stat, so a file that *changed* gets a new identity
  and is reprocessed. That is correct: it is not the same content any more.

Two consequences worth knowing:

- The metadata dicts used when **marking** and when **checking** must come from
  the same helper (`get_standardized_metadata`). If one side hand-rolls a dict
  with different fields, the two hash differently and every file looks new — a
  silent, total loss of resume. There is a test pinning this.
- Archive members are identified by their path *relative to the extraction
  folder* plus size, because their mtime is set at extraction time and changes
  on every run.

### How stable is that identity?

Measured, not inferred (`tests/unit/test_checkpoint_identifier_stability.py`).

**Stable** — all reach the same checkpoint file:

| Difference between run 1 and retry | Result |
|---|---|
| trailing separator (`C:\\data` vs `C:\\data\\`) | same checkpoint |
| `..` segments (`C:\\other\\..\\data`) | same checkpoint |
| a symlink / junction to the corpus | same checkpoint |

**Unstable** — proven by test:

`modified` comes from `datetime.fromtimestamp()` with no timezone, so it is a
*local-time* date. One file with mtime `2026-06-15 00:30 UTC` reports:

```
TZ=UTC                modified=2026-06-15
TZ=Pacific/Kiritimati modified=2026-06-15
TZ=Etc/GMT+12         modified=2026-06-14     <- same instant, different day
```

The identifier embeds that date, so it moves with the timezone. This is the
DST risk made concrete: a run and its retry see different identities for files
whose mtime sits near local midnight. It affects individual files, never a
whole corpus.

**Deliberate:** a changed mtime is a new identity, so a file rewritten or
touched by antivirus, or rounded by a network share, is reprocessed. That is
correct — the content may differ — but it is worth knowing.

**Not reproducible here, recorded as an open question:** path *case*
(`C:\\Data` vs `c:\\data`). The checkpoint digest uses the path as given,
while the file identifier lower-cases it. On a case-sensitive filesystem the
two directories are genuinely different corpora, so differing digests are
correct there and prove nothing about Windows, where `Path.resolve()`
normalises case for existing paths and both spellings are expected to collapse
to one digest. Unverified — check with `checkpoint_inspect.py --list`.

---

## 5. What Retry and Resume actually do

```
POST /api/jobs/<id>/retry     # FAILED, CANCELLED, COMPLETED_WITH_WARNINGS
POST /api/jobs/<id>/resume    # PAUSED only
        │
        ▼
JobManager.retry() / resume()
        │
        ▼
create_job(job_type, source, SAME options)   ← new job id, identical options
        │
        ▼
_run_builtin()  →  IngestionRequest(path=…, source=…, side=…, processing=…)
        │
        ▼
_checkpoint_file(request)  →  same path as the original run
        │
        ▼
CheckpointManager(...)  →  loads snapshot + replays journal  →  skips
```

Because the checkpoint identity comes from `options` (path / file list, source,
side) and not from the job id, a retry lands on the same checkpoint.

`JobManager.recover_stale_jobs()` runs at startup and marks jobs that were
`RUNNING` when the process died as `FAILED`. It does **not** touch checkpoint
files.

---

## 6. Diagnosing "it started from the beginning"

### In the log

Every run now logs what the checkpoint engine found. Look for a line beginning
`Checkpoint `:

```
Checkpoint resumed: 8123 file(s) will be skipped | snapshot=True(8000)
  journal=True(123) file=/…/checkpoint_ab12_SRC_SIDE.json

Checkpoint rejected: 0 file(s) will be skipped | snapshot=True(4)
  journal=False(0) file=/… | checkpoint source mismatch (checkpoint=SRC, current=OTHER)

Checkpoint fresh: 0 file(s) will be skipped | snapshot=False(0)
  journal=False(0) file=/… | no checkpoint or journal found
```

`rejected` is logged at **WARNING**: a checkpoint exists and is about to be
ignored, which means work will be repeated.

Also logged once per run, just before work starts:

```
Checkpoint policy=auto file=/…/checkpoint_ab12_SRC_SIDE.json (job source=SRC side=SIDE)
```

**If a retry logs `resumed`, the checkpoint worked.** Files are being skipped;
if the UI still looks wrong, the problem is reporting, not resume.
**If it logs `fresh` or `rejected`, the reason is on that line.**

### In the job record

`job.statistics.resume_info`:

```json
{
  "status": "resumed",
  "reason": null,
  "checkpoint_file": "/…/checkpoint_ab12_SRC_SIDE.json",
  "snapshot_present": true, "snapshot_entries": 8000,
  "journal_present": true,  "journal_entries": 123,
  "resumed_files": 8123,
  "folder": "/data/corpus", "source": "SRC", "side": "SIDE"
}
```

### In the UI

Job detail → **Resume** row, showing either
`Resumed: 8123 already processed (snapshot 8000) (journal 123)` or
`Started fresh — checkpoint not used: <reason>`.

### From the command line

```bash
python scripts/checkpoint_inspect.py --path /data/corpus --source SRC --side SIDE
python scripts/checkpoint_inspect.py --files a.txt b.txt --source SRC --side SIDE
python scripts/checkpoint_inspect.py --list
python scripts/checkpoint_inspect.py --file /…/checkpoint_ab12_SRC_SIDE.json
```

It prints the path a run *would* use, what is in it, and whether a retry would
resume — using the service's own derivation, so it cannot disagree with it.

---

## 7. Known gaps and residual risks

| # | Gap | Status |
|---|---|---|
| 1 | **Multiprocessing.** `_process_with_pool` ships the file worker to separate processes, so each worker holds a *copy* of the checkpoint manager. Marks made there are not reliably reflected in the parent, and concurrent appends to one journal can interleave. | **Proven unreachable, by test rather than by reading.** Readers built through the real `IngestionService._make_reader` never set `use_multiprocessing` at any worker count (0/1/2/8/16), and two guard tests — an AST scan of every `.py` file and a text scan of `.json`/`.bat`/`.env.example`/`.toml`/`.cfg` — fail if any call site or config key ever enables it. The AST guard was verified to bite by injecting a caller. A latent defect for anyone constructing the engine directly. **Not fixed** (see `ROADMAP.md` §3a). |
| 2 | **Windows directory fsync.** `os.fsync` on a file does not flush the directory entry naming it, so a journal created moments before power loss can vanish — the contents were written but the name never reached the disk. | **Implemented.** `_fsync_dir_windows()` opens a directory handle with `FILE_FLAG_BACKUP_SEMANTICS` and calls `FlushFileBuffers` via ctypes, with every failure falling back instead of propagating. **Not verified against a real power cut** — that depends on the disk, its write cache and its driver, which cannot be exercised from a Linux sandbox. What *is* tested is the call sequence, the flags and every failure path, using an injected ctypes. **Residual risk: the durability itself is unverified.** |
| 3 | **Archive members in a file-list batch.** `process_single_file` deliberately clears the per-container checkpoint base on every top-level call (so a single file cannot inherit a previous run's base). Members inside an archive in a *batch* therefore get no member checkpoint. The container itself is marked once stored, so a retry skips the container and never re-extracts it. **Documented, not changed** — the clearing exists for a reason. |
| 4 | **Timezone / DST.** The identifier includes `modified`, a local-time ISO date. Across a DST boundary the date of a file modified near midnight can shift, giving it a new identity and causing a reprocess. Affects only such files, never the whole corpus. **Reproduced, not fixed** — measured values above. Fixing it means replacing the local date with `mtime_ns`, which changes identity semantics and invalidates existing checkpoints, so it is a decision for the operator. |
| 5 | **A truncated final journal line** (the normal result of a hard kill) is still parsed as a short identifier, so one spurious entry can appear. | **Harmless.** Real identifiers are 64-bit hashes, so a truncated prefix is a small number that cannot collide with one and cannot wrongly skip a file. Asserted explicitly in `test_truncated_final_journal_line_still_keeps_earlier_entries`. |
| 6 | **A checkpoint that cannot be written was invisible.** Journal write failures returned normally, so `mark_failures` stayed at 0 and a run that lost every mark looked healthy; an unusable checkpoint directory aborted setup without saying that resuming was disabled. | **Fixed.** Journal failures now propagate to the warn-once counter, `get_statistics()` reports `mark_failures`, and an unusable directory logs that resuming is DISABLED. |

---

## 8. File map

| Concern | Location |
|---|---|
| Checkpoint engine (journal, snapshot, compaction, resume report) | `core/checkpoint_manager.py` |
| Per-container member checkpoints | `core/checkpoint_scope.py` |
| Checkpoint path + policy → file | `services/ingesting/service.py` (`_checkpoint_file`) |
| Skip on discovery / per phase | `pipeline/integrated_reader.py` (`_inventory_tree`, `_iter_phase_batches`) |
| Mark after a successful store | `pipeline/integrated_reader.py` (`_process_file_worker`) |
| File-list runs: manager + skip + accounting | `services/ingesting/service.py` (`_run_file_list`) |
| Retry / resume entry points | `services/jobs/manager.py` (`retry`, `resume`, `_run_builtin`) |
| Diagnostic CLI | `scripts/checkpoint_inspect.py` |
| Tests | `tests/unit/test_checkpoint_observability.py`, `test_batch_checkpoint_resume.py`, `test_folder_resume_accounting.py`, `test_batch_import_control_flags.py`, `test_multiprocessing_unreachable.py`, `test_checkpoint_identifier_stability.py`, `test_checkpoint_write_failures.py`, `test_checkpoint_rejection_branches.py`, `test_fsync_dir_windows.py`, `test_container_resume.py` |
