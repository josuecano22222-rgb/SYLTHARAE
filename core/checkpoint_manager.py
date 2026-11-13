"""
Checkpoint Manager - Handles pause/resume functionality for file processing

This module provides checkpoint/resume capabilities to allow processing to continue
from where it left off after power outages, device shutdowns, or other interruptions.
"""

import json
import os
import time
import hashlib
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

#: First line of a journal file: identifies the run it belongs to.
JOURNAL_HEADER = "#inforaxis-journal-v1"


class CheckpointManager:
    """
    Manages checkpoint state for file processing operations.
    
    Tracks processed files and allows resuming from the last checkpoint.
    Checkpoints are saved to disk periodically to ensure progress is not lost.
    """
    
    def __init__(
        self,
        checkpoint_file: str,
        folder_path: str,
        storage_source: Optional[str] = None,
        storage_side: Optional[str] = None,
        auto_save_interval: int = 10,
        stable_members: bool = False,
    ):
        """
        Initialize checkpoint manager.
        
        Args:
            checkpoint_file: Path to checkpoint JSON file
            folder_path: Path to folder being processed
            storage_source: Optional storage source name (for checkpoint identification)
            storage_side: Optional storage side name (for checkpoint identification)
            auto_save_interval: Number of files processed before auto-saving checkpoint
        """
        self.checkpoint_file = Path(checkpoint_file)
        self.folder_path = Path(folder_path).resolve()
        self.storage_source = storage_source
        self.storage_side = storage_side
        self.auto_save_interval = auto_save_interval
        #: Identify files by their path relative to ``folder_path`` and their
        #: size, not by mtime. Used for archive members: their extraction-time
        #: mtime changes on every run, which would make every member look new.
        self.stable_members = stable_members
        
        # Thread-safe tracking
        self._lock = threading.Lock()
        self._processed_files: Set[str] = set()
        self._processed_count = 0
        self._last_save_count = 0

        # Incremental (append-only) persistence.
        #
        # Rewriting the full processed set on every save is O(n^2) I/O over a
        # run: at a million files each save wrote a million identifiers, tens
        # of thousands of times - hundreds of gigabytes of checkpoint traffic
        # that grows as the run proceeds (measured as progressive slowdown).
        # Identifiers are now appended to a journal; the snapshot file is only
        # rewritten when the journal is compacted (bounded, amortised O(n)).
        self.journal_file = self.checkpoint_file.with_suffix(
            self.checkpoint_file.suffix + '.journal'
        )
        self._journal_handle = None
        self._journal_pending = 0
        self._journal_last_flush = 0.0
        #: Flush the journal at least this often, so a crash (process kill)
        #: costs at most this many seconds of re-processing rather than the
        #: whole interval between saves.
        self.journal_flush_interval = 5.0
        #: Rewrite the snapshot (and clear the journal) once this many
        #: identifiers have accumulated in it.
        self.compact_after = 20000
        
        # Background thread for non-blocking checkpoint saves
        self._save_thread = None
        self._save_event = threading.Event()
        self._shutdown = False
        
        # Load existing checkpoint if available
        self._load_checkpoint()
        
        # Start background save thread for non-blocking saves (only if enabled)
        # OPTIMIZATION: Use lazy initialization to avoid creating thread if not needed
        self._save_thread = None
        self._thread_started = False
    
    @staticmethod
    def _normalise_identifier(value: Any) -> Optional[int]:
        """Coerce a stored identifier to the compact int form.

        Accepts the compact integer written by current versions and the legacy
        64-character hex digest, so an existing checkpoint/journal keeps working
        across the upgrade (its first 96 bits are the same prefix).
        """
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text, 16)
        except ValueError:
            return None

    def _relative_to_folder(self, file_path: str) -> str:
        """Path of ``file_path`` relative to the folder, normalized; falls back to the full path."""
        try:
            rel = os.path.relpath(os.path.abspath(file_path), str(self.folder_path))
        except ValueError:
            rel = file_path
        return rel.replace('\\', '/').lower()

    @staticmethod
    def _exact_fingerprint(file_info: Dict[str, Any], file_path: str):
        """(size, mtime_ns) for a file; (None, None) when it cannot be read."""
        size = file_info.get('size_bytes')
        mtime_ns = file_info.get('mtime_ns')
        if size is None or mtime_ns is None:
            try:
                st = os.stat(file_path)
                size = st.st_size if size is None else size
                mtime_ns = st.st_mtime_ns if mtime_ns is None else mtime_ns
            except (OSError, TypeError, ValueError):
                return size, mtime_ns
        return size, mtime_ns

    def _get_file_identifier(self, file_info: Dict[str, Any]) -> int:
        """
        Generate unique identifier for a file.
        
        Uses file path + modification time for uniqueness.
        This allows detecting if a file was modified since last processing.
        
        OPTIMIZED: Avoids expensive Path.resolve() call - uses path as-is with normalization.
        
        Args:
            file_info: File metadata dictionary
            
        Returns:
            Unique identifier string
        """
        file_path = file_info.get('path', '')
        modified = file_info.get('modified', '')
        
        # OPTIMIZATION: Use path as-is, just normalize separators (much faster than resolve())
        # Path.resolve() is expensive and not necessary for checkpoint tracking
        # Normalize path separators to handle Windows/Unix differences
        normalized_path = file_path.replace('\\', '/').lower()

        if self.stable_members:
            # Member of a container: identify it by where it sits inside the
            # extraction folder and by its size. The mtime is set at extraction
            # time, so it differs on every run and must not be part of the key.
            rel = self._relative_to_folder(file_path)
            size = file_info.get('size_bytes', file_info.get('size', ''))
            identifier = f"member:{rel}|{size}"
        else:
            # 'modified' from file metadata is a calendar date (YYYY-MM-DD), so
            # a file edited later the same day kept its identifier and was
            # silently skipped on resume. Size and the exact mtime in
            # nanoseconds are part of the identity. Read from disk when the
            # caller did not supply them (one stat per file).
            size, mtime_ns = self._exact_fingerprint(file_info, file_path)
            identifier = f"{normalized_path}|{modified}|{size}|{mtime_ns}"

        # 96 bits of SHA-256, kept as an int.
        #
        # Rationale: this value is held for *every* processed file (and written
        # once to the journal), so its footprint is the pipeline's linear memory
        # term. A 64-character hex string costs ~100 bytes resident; the same
        # 96 bits as an int cost ~40, and the journal/snapshot files shrink 2.7x.
        # 96 bits keeps the accidental-collision probability negligible at
        # 10^8 files (~1e-13), which for a skip decision - a collision would mean
        # silently skipping a file that was never stored - is the safe choice.
        digest = hashlib.sha256(identifier.encode('utf-8')).digest()
        return int.from_bytes(digest[:12], 'big')
    
    # ------------------------------------------------------------------
    # Incremental persistence
    # ------------------------------------------------------------------
    def _journal_header(self) -> str:
        """Identity of the run a journal belongs to.

        The journal is replayed on resume, so it must only ever be applied to
        the same folder/source/side combination the snapshot is validated
        against - otherwise a resume with different storage settings would skip
        files that were never stored under the current source.
        """
        folder = str(self.folder_path).replace('\\', '/').lower()
        return f"{JOURNAL_HEADER}|{folder}|{self.storage_source}|{self.storage_side}"

    def _append_journal_locked(self, file_id: str) -> None:
        """Append one identifier to the journal (caller holds ``self._lock``)."""
        try:
            if self._journal_handle is None:
                self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
                is_new = not self.journal_file.exists() or self.journal_file.stat().st_size == 0
                self._journal_handle = open(self.journal_file, 'a', encoding='utf-8')
                if is_new:
                    self._journal_handle.write(self._journal_header() + "\n")
            self._journal_handle.write(f"{file_id:x}\n")
            # Make every mark durable before returning. A file is only marked
            # after its result is stored, so a process kill or power loss must
            # never drop a mark: a lost mark means the file is stored again on
            # resume. The cost is one fsync per processed file, which is small
            # next to the time spent extracting and storing each file.
            self._journal_handle.flush()
            os.fsync(self._journal_handle.fileno())
            self._journal_pending = 0
            self._journal_last_flush = time.time()
        except Exception as exc:
            logger.warning(f"Checkpoint journal write failed (non-critical): {exc}")
            self._journal_handle = None

    def _close_journal_locked(self) -> None:
        """Flush and close the journal handle (caller holds ``self._lock``)."""
        if self._journal_handle is not None:
            try:
                self._journal_handle.flush()
                self._journal_handle.close()
            except Exception:
                pass
            self._journal_handle = None
            self._journal_pending = 0

    def _replay_journal(self) -> int:
        """Load identifiers appended since the snapshot; returns the new count.

        This is what makes a crash survivable: the snapshot is only rewritten at
        a compaction point (or at the end of a run), so a run that dies early
        has *all* of its progress in the journal.  The journal is therefore
        replayed whether or not a snapshot exists - the previous implementation
        only reached this code when a snapshot was present, which meant a crash
        before the first compaction silently discarded every processed
        identifier and the "resumed" run redid the entire corpus.
        """
        loaded = 0
        expected_header = self._journal_header()
        try:
            if not self.journal_file.exists():
                with self._lock:
                    return self._processed_count
            with open(self.journal_file, 'r', encoding='utf-8') as fh:
                for line in fh:
                    value = line.strip()
                    if not value:
                        continue
                    if value.startswith(JOURNAL_HEADER):
                        if value != expected_header:
                            logger.warning(
                                "Checkpoint journal belongs to a different "
                                "folder/source/side (%s); ignoring it", value
                            )
                            with self._lock:
                                return self._processed_count
                        continue
                    identifier = self._normalise_identifier(value)
                    if identifier is not None and identifier not in self._processed_files:
                        self._processed_files.add(identifier)
                        loaded += 1
        except Exception as exc:
            logger.warning(f"Checkpoint journal could not be read ({exc}); "
                           f"resuming from the last snapshot")
            return self._processed_count
        return self._processed_count + loaded

    def _write_snapshot_locked(self) -> None:
        """Write the full snapshot and truncate the journal (atomic)."""
        processed_files_list = list(self._processed_files)
        processed_count = self._processed_count
        folder_path_str = str(self.folder_path)
        source = self.storage_source
        side = self.storage_side

        data = {
            'folder_path': folder_path_str,
            'storage_source': source,
            'storage_side': side,
            'processed_files': [f"{identifier:x}" for identifier in processed_files_list],
            'processed_count': processed_count,
            'last_updated': datetime.now().isoformat(),
            'version': '1.0'  # For future compatibility
        }

        self._close_journal_locked()
        temp_file = self.checkpoint_file.with_suffix('.tmp')
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
        temp_file.replace(self.checkpoint_file)
        # Drop the journal only after the snapshot that contains it is in place.
        try:
            if self.journal_file.exists():
                self.journal_file.unlink()
        except Exception as exc:
            logger.debug(f"Could not clear checkpoint journal: {exc}")

        self._last_save_count = processed_count
        logger.debug(f"Checkpoint snapshot written: {processed_count} files processed")

    def _load_checkpoint(self) -> None:
        """Load checkpoint state from file if it exists.

        A missing snapshot is *not* a missing checkpoint: the journal may hold
        everything processed so far (see :meth:`_replay_journal`).
        """
        if not self.checkpoint_file.exists():
            if self.journal_file.exists():
                self._processed_count = self._replay_journal()
                self._last_save_count = self._processed_count
                logger.info(
                    f"No snapshot at {self.checkpoint_file}; resumed "
                    f"{self._processed_count} files from the journal"
                )
            else:
                logger.info(f"No existing checkpoint found at {self.checkpoint_file}")
            return
        
        try:
            with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate checkpoint matches current folder and storage settings
            checkpoint_folder = data.get('folder_path', '')
            checkpoint_source = data.get('storage_source')
            checkpoint_side = data.get('storage_side')
            
            # OPTIMIZATION: Normalize paths without expensive resolve()
            # Just normalize separators for comparison
            checkpoint_folder_normalized = checkpoint_folder.replace('\\', '/').lower()
            current_folder_normalized = str(self.folder_path).replace('\\', '/').lower()
            
            if checkpoint_folder_normalized != current_folder_normalized:
                logger.warning(
                    f"Checkpoint folder mismatch: checkpoint={checkpoint_folder_normalized}, "
                    f"current={current_folder_normalized}. Ignoring checkpoint."
                )
                return
            
            # Check storage settings match (if specified)
            if self.storage_source and checkpoint_source != self.storage_source:
                logger.warning(
                    f"Checkpoint source mismatch: checkpoint={checkpoint_source}, "
                    f"current={self.storage_source}. Ignoring checkpoint."
                )
                return
            
            if self.storage_side and checkpoint_side != self.storage_side:
                logger.warning(
                    f"Checkpoint side mismatch: checkpoint={checkpoint_side}, "
                    f"current={self.storage_side}. Ignoring checkpoint."
                )
                return
            
            # Load processed files
            processed_files = data.get('processed_files', [])
            self._processed_files = {
                identifier for identifier in
                (self._normalise_identifier(entry) for entry in processed_files)
                if identifier is not None
            }
            self._processed_count = data.get('processed_count', len(processed_files))
            self._last_save_count = self._processed_count
            
            logger.info(
                f"Loaded checkpoint: {len(self._processed_files)} files already processed "
                f"(from {data.get('last_updated', 'unknown')})"
            )

            # Replay the journal written since the snapshot.  A journal that
            # cannot be read is not fatal: the snapshot alone is still a
            # consistent (if slightly older) resume point.
            self._processed_count = self._replay_journal()
            self._last_save_count = self._processed_count
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid checkpoint file format: {e}. Starting fresh.")
            self._processed_files = set()
        except Exception as e:
            logger.error(f"Error loading checkpoint: {e}. Starting fresh.")
            self._processed_files = set()
    
    def _save_checkpoint(self, blocking: bool = False) -> None:
        """
        Save current checkpoint state to file.
        
        OPTIMIZED: Can save in background thread to avoid blocking processing.
        
        Args:
            blocking: If True, save synchronously. If False, trigger background save.
        """
        if blocking:
            # Synchronous save (for finalization)
            self._save_checkpoint_sync()
        else:
            # Lazy start background thread only when needed
            if not self._thread_started:
                self._save_thread = threading.Thread(
                    target=self._background_save_worker,
                    name="CheckpointSaveThread",
                    daemon=True
                )
                self._save_thread.start()
                self._thread_started = True
            
            # Trigger background save (non-blocking)
            self._save_event.set()
    
    def _save_checkpoint_sync(self) -> None:
        """Persist checkpoint state.

        Cheap by design: the identifiers processed since the last compaction are
        already durable in the append-only journal, so the common case is just a
        flush.  The full snapshot is rewritten only when the journal has grown
        past ``compact_after`` (or when ``compact=True`` is requested, as
        :meth:`finalize` does when the run ends).  Rewriting the whole set on
        every save is what made checkpoint I/O quadratic in the number of
        processed files.
        """
        try:
            with self._lock:
                pending = len(self._processed_files) - self._last_save_count
                if pending >= 0 and self._processed_count >= self.compact_after \
                        and self._journal_pending == 0 and pending > 0:
                    # Journal has reached a compaction point: fold it into the
                    # snapshot once.
                    self._write_snapshot_locked()
                    logger.debug(f"Checkpoint compacted: {self._processed_count} files processed")
                    return
                self._close_journal_locked()
                self._last_save_count = self._processed_count
                logger.debug(f"Checkpoint saved: {self._processed_count} files processed "
                             f"(journal)")
        except Exception as e:
            # Don't let checkpoint errors interfere with processing
            logger.warning(f"Checkpoint save failed (non-critical): {e}")

    def compact(self) -> None:
        """Fold the journal into a fresh snapshot (used at the end of a run)."""
        try:
            with self._lock:
                if not self._processed_files:
                    return
                self._write_snapshot_locked()
        except Exception as e:
            logger.warning(f"Checkpoint compaction failed (non-critical): {e}")

    def _background_save_worker(self) -> None:
        """Background thread worker for non-blocking checkpoint saves."""
        while not self._shutdown:
            # Wait for save event
            if self._save_event.wait(timeout=1.0):
                # Save checkpoint
                self._save_checkpoint_sync()
                # Clear event
                self._save_event.clear()
    
    def is_processed(self, file_info: Dict[str, Any]) -> bool:
        """
        Check if a file has already been processed.
        
        OPTIMIZED: Calculates identifier outside lock to minimize lock time.
        
        Args:
            file_info: File metadata dictionary
            
        Returns:
            True if file was already processed, False otherwise
        """
        # Calculate identifier outside lock (faster)
        file_id = self._get_file_identifier(file_info)
        with self._lock:
            return file_id in self._processed_files
    
    def mark_processed(self, file_info: Dict[str, Any]) -> None:
        """
        Mark a file as processed and save checkpoint if needed.
        
        OPTIMIZED: Non-blocking checkpoint saves with reduced frequency to avoid I/O overload.
        
        Args:
            file_info: File metadata dictionary
        """
        # Calculate identifier outside lock (faster)
        file_id = self._get_file_identifier(file_info)
        
        should_save = False
        with self._lock:
            if file_id not in self._processed_files:
                self._processed_files.add(file_id)
                self._processed_count += 1

                # O(1) durability: the identifier goes to the append-only
                # journal, so each processed file costs one short line of I/O
                # instead of a rewrite of every identifier seen so far.
                self._append_journal_locked(file_id)

                # OPTIMIZATION: Increase save interval to reduce I/O overhead
                # Save less frequently to avoid system overload
                # Check if we need to save (but don't save in lock)
                if self._processed_count - self._last_save_count >= self.auto_save_interval:
                    should_save = True

        # Trigger background save if needed (non-blocking, lazy thread start)
        if should_save:
            try:
                self._save_checkpoint(blocking=False)
            except Exception as e:
                # Don't let checkpoint errors interfere with processing
                logger.warning(f"Checkpoint save failed (non-critical): {e}")
    
    def filter_processed_files(
        self,
        files: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Filter out already processed files from a list.
        
        OPTIMIZED: Batch operation - calculates all identifiers first, then checks in single lock.
        
        Args:
            files: List of file metadata dictionaries
            
        Returns:
            Tuple of (unprocessed_files, skipped_count)
        """
        # OPTIMIZATION: Calculate all identifiers first (outside lock)
        file_ids = [self._get_file_identifier(file_info) for file_info in files]
        
        # Single lock acquisition to check all files at once
        with self._lock:
            processed_set = self._processed_files
        
        # Filter files (no lock needed here)
        unprocessed = []
        skipped = 0
        
        for file_info, file_id in zip(files, file_ids):
            if file_id in processed_set:
                skipped += 1
            else:
                unprocessed.append(file_info)
        
        if skipped > 0:
            logger.info(f"Resuming: Skipping {skipped} already processed files, {len(unprocessed)} remaining")
        
        return unprocessed, skipped
    
    def save_checkpoint(self, blocking: bool = True) -> None:
        """
        Manually save checkpoint (thread-safe).
        
        Args:
            blocking: If True, wait for save to complete. If False, trigger background save.
        """
        self._save_checkpoint(blocking=blocking)
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get checkpoint statistics.
        
        Returns:
            Dictionary with checkpoint statistics
        """
        with self._lock:
            return {
                'processed_count': self._processed_count,
                'processed_files': len(self._processed_files),
                'checkpoint_file': str(self.checkpoint_file),
                'last_save_count': self._last_save_count
            }
    
    def clear_checkpoint(self) -> None:
        """Clear checkpoint and delete checkpoint file."""
        with self._lock:
            self._processed_files.clear()
            self._processed_count = 0
            self._last_save_count = 0
            
            if self.checkpoint_file.exists():
                try:
                    self.checkpoint_file.unlink()
                    logger.info(f"Checkpoint cleared: {self.checkpoint_file}")
                except Exception as e:
                    logger.error(f"Error deleting checkpoint file: {e}")
    
    def finalize(self) -> None:
        """
        Finalize checkpoint - save final state and optionally clean up.
        
        Call this when processing is complete to ensure final state is saved.
        """
        # Shutdown background thread
        self._shutdown = True
        self._save_event.set()  # Trigger final save
        
        # Wait for background thread to finish current save
        if self._save_thread and self._save_thread.is_alive():
            self._save_thread.join(timeout=5.0)
        
        # Final compaction: the snapshot contains everything the journal held,
        # so a resume after this run needs a single file to read.
        self.compact()
        
        with self._lock:
            logger.info(
                f"Checkpoint finalized: {self._processed_count} files processed. "
                f"Checkpoint saved to {self.checkpoint_file}"
            )
